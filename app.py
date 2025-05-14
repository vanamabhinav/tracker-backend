from flask import Flask, request, jsonify, redirect, make_response
from flask_cors import CORS
import os
import json
import datetime
import firebase_admin
from firebase_admin import credentials
from firebase_admin import db
from firebase_admin import firestore
import requests
import base64
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

# Initialize Flask app
app = Flask(__name__)
CORS(app)  # Allow cross-origin requests

ALLOWED_ORIGINS = [
    'http://localhost:5173',  # Local development
    'https://alexa-skill.netlify.app',  # Your Netlify app
    'https://elefit-backend.onrender.com',  # Your backend
    'https://*.myshopify.com',  # Shopify stores
]


CORS(app, resources={r"/chat": {"origins": ALLOWED_ORIGINS}}, supports_credentials=True)

# Set this to False to disable test mode (enables Firebase authentication)
TEST_MODE = False

# Configuration for frontend URLs
LOCAL_FRONTEND_URL = 'http://localhost:5173'
PRODUCTION_FRONTEND_URL = 'https://alexa-skill.netlify.app'
SHOPIFY_FRONTEND_URL = 'https://*.myshopify.com'  # Generic Shopify store domain

# Get frontend URL based on environment
def get_frontend_url():
    # If running in production (on Render), use production URL
    if os.environ.get('RENDER'):
        return PRODUCTION_FRONTEND_URL
    # If request is coming from Shopify
    if request and request.headers.get('Origin', '').endswith('myshopify.com'):
        return request.headers.get('Origin')
    # Otherwise use local URL
    return LOCAL_FRONTEND_URL

# Add CORS headers to all responses
@app.after_request
def add_cors_headers(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# Initialize Firebase - with error handling
try:
    if 'FIREBASE_CONFIG' in os.environ:
        firebase_config = json.loads(os.environ.get('FIREBASE_CONFIG'))
        cred = credentials.Certificate(firebase_config)
    else:
        cred_path = os.path.join(os.path.dirname(__file__), 'serviceAccountKey.json')
        cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://theelefit-3f6c6-default-rtdb.firebaseio.com/'
    })
    print("Firebase initialized successfully")
    FIREBASE_INITIALIZED = True
    # Initialize Firestore
    firestore_db = firestore.client()
    print("Firestore initialized successfully")
except Exception as e:
    print(f"Firebase initialization error: {str(e)}")
    FIREBASE_INITIALIZED = False
    firestore_db = None

# Helper function to sync data from Realtime Database to Firestore
def sync_to_firestore(data, log_type):
    """Sync data from Realtime Database to Firestore."""
    try:
        if not FIREBASE_INITIALIZED or not firestore_db:
            print("Firebase or Firestore not initialized, skipping sync")
            return False
            
        print(f"Syncing {log_type} data to Firestore: {json.dumps(data, indent=2)}")
        
        if log_type == 'workout':
            # Map Realtime DB fields to Firestore fields
            workout_data = {
                'workoutType': data.get('workout_type', ''),
                'activityName': data.get('activity_name', ''),
                'duration': data.get('duration', 0),
                'distance': data.get('distance'),
                'sets': data.get('sets'),
                'reps': data.get('reps'),
                'timestamp': data.get('timestamp', datetime.datetime.now().strftime('%Y-%m-%d')),
                'source': data.get('source', 'alexa'),
                'type': 'workout',
                'id': data.get('id', f'alexa_{datetime.datetime.now().timestamp()}')
            }
            
            # Create a workoutLogs array in the users collection
            # Since Alexa doesn't have user identification yet, we'll store it in a generic alexa_user document
            user_ref = firestore_db.collection('users').document('alexa_user')
            user_doc = user_ref.get()
            
            if user_doc.exists:
                # Update the workout logs array
                workout_logs = user_doc.get('workoutLogs', [])
                workout_logs.append(workout_data)
                user_ref.update({
                    'workoutLogs': workout_logs
                })
            else:
                # Create the user document with the workout
                user_ref.set({
                    'workoutLogs': [workout_data],
                    'mealLogs': []
                })
                
            print("Workout synced to Firestore successfully")
            return True
            
        elif log_type == 'meal':
            # Map Realtime DB fields to Firestore fields
            meal_data = {
                'mealType': data.get('meal_type', ''),
                'foodItems': data.get('food_items', []),
                'timestamp': data.get('timestamp', datetime.datetime.now().strftime('%Y-%m-%d')),
                'source': data.get('source', 'alexa'),
                'type': 'meal',
                'id': data.get('id', f'alexa_{datetime.datetime.now().timestamp()}')
            }
            
            # Create a mealLogs array in the users collection
            # Since Alexa doesn't have user identification yet, we'll store it in a generic alexa_user document
            user_ref = firestore_db.collection('users').document('alexa_user')
            user_doc = user_ref.get()
            
            if user_doc.exists:
                # Update the meal logs array
                meal_logs = user_doc.get('mealLogs', [])
                meal_logs.append(meal_data)
                user_ref.update({
                    'mealLogs': meal_logs
                })
            else:
                # Create the user document with the meal
                user_ref.set({
                    'workoutLogs': [],
                    'mealLogs': [meal_data]
                })
                
            print("Meal synced to Firestore successfully")
            return True
            
        else:
            print(f"Unknown log type: {log_type}")
            return False
            
    except Exception as e:
        print(f"Error syncing to Firestore: {str(e)}")
        return False

# Routes
@app.route('/', methods=['GET'])
def index():
    """Simple test endpoint to check if the server is running."""
    frontend_url = get_frontend_url()
    return jsonify({
        'status': 'online',
        'message': 'EleFit Tracker API is running properly',
        'firebase_status': 'connected' if FIREBASE_INITIALIZED else 'disconnected',
        'test_mode': TEST_MODE,
        'frontend_url': frontend_url,
        'endpoints': [
            '/api/log-workout',
            '/api/log-meal',
            '/api/workout-logs',
            '/api/meal-logs',
            '/api/alexa/log',
            '/alexa/auth/log'
        ]
    })

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for monitoring."""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.datetime.now().isoformat(),
        'firebase': FIREBASE_INITIALIZED
    })

@app.route('/api/log-workout', methods=['POST'])
def log_workout():
    """Log a workout activity."""
    try:
        data = request.json
        
        # Validate required fields
        required_fields = ['workoutType', 'activityName', 'duration', 'timestamp', 'source']
        for field in required_fields:
            if field not in data:
                return jsonify({'success': False, 'message': f'Missing required field: {field}'}), 400
        
        # Create workout document
        workout_data = {
            'workout_type': data['workoutType'],
            'activity_name': data['activityName'],
            'duration': data['duration'],
            'distance': data.get('distance'),
            'sets': data.get('sets'),
            'reps': data.get('reps'),
            'timestamp': data['timestamp'],
            'source': data['source']
        }
        
        # If in test mode, we'll skip actual Firebase storage
        if TEST_MODE:
            return jsonify({
                'success': True,
                'message': f"Workout logged in test mode (Firebase: {FIREBASE_INITIALIZED})",
                'workout_id': f'test-{datetime.datetime.now().timestamp()}'
            })
        
        # Try to store in Firebase if initialized
        if FIREBASE_INITIALIZED:
            try:
                # Add to Realtime Database
                workout_ref = db.reference('workout_logs').push(workout_data)
                workout_id = workout_ref.key
                
                # Add ID to document for Firestore sync
                workout_data['id'] = workout_id
                
                # Sync to Firestore
                sync_to_firestore(workout_data, 'workout')
                
                return jsonify({
                    'success': True,
                    'message': 'Workout logged successfully',
                    'workout_id': workout_id
                })
            except Exception as firebase_error:
                print(f"Firebase error in log_workout: {str(firebase_error)}")
                # If there's a Firebase error but we're coming from Alexa, return success anyway
                if data.get('source') == 'alexa':
                    print("Returning success response for Alexa despite Firebase error")
                    return jsonify({
                        'success': True,
                        'message': 'Workout logged for Alexa (no Firebase)',
                        'workout_id': 'temp_id'
                    })
                else:
                    return jsonify({'success': False, 'message': str(firebase_error)}), 500
        else:
            return jsonify({'success': False, 'message': 'Firebase not initialized'}), 500
    
    except Exception as e:
        print(f"Error in log_workout: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/log-meal', methods=['POST'])
def log_meal():
    """Log a meal."""
    try:
        data = request.json
        
        # Validate required fields
        required_fields = ['mealType', 'foodItems', 'timestamp', 'source']
        for field in required_fields:
            if field not in data:
                return jsonify({'success': False, 'message': f'Missing required field: {field}'}), 400
        
        # Create meal document
        meal_data = {
            'meal_type': data['mealType'],
            'food_items': data['foodItems'],  # Realtime DB can store arrays as JSON
            'timestamp': data['timestamp'],
            'source': data['source']
        }
        
        # If in test mode, we'll skip actual Firebase storage
        if TEST_MODE:
            return jsonify({
                'success': True,
                'message': f"Meal logged in test mode (Firebase: {FIREBASE_INITIALIZED})",
                'meal_id': f'test-{datetime.datetime.now().timestamp()}'
            })
            
        # Try to store in Firebase if initialized
        if FIREBASE_INITIALIZED:
            try:
                # Add to Realtime Database
                meal_ref = db.reference('meal_logs').push(meal_data)
                meal_id = meal_ref.key
                
                # Add ID to document for Firestore sync
                meal_data['id'] = meal_id
                
                # Sync to Firestore
                sync_to_firestore(meal_data, 'meal')
                
                return jsonify({
                    'success': True,
                    'message': 'Meal logged successfully',
                    'meal_id': meal_id
                })
            except Exception as firebase_error:
                print(f"Firebase error in log_meal: {str(firebase_error)}")
                # If there's a Firebase error but we're coming from Alexa, return success anyway
                if data.get('source') == 'alexa':
                    print("Returning success response for Alexa despite Firebase error")
                    return jsonify({
                        'success': True,
                        'message': 'Meal logged for Alexa (no Firebase)',
                        'meal_id': 'temp_id'
                    })
                else:
                    return jsonify({'success': False, 'message': str(firebase_error)}), 500
        else:
            return jsonify({'success': False, 'message': 'Firebase not initialized'}), 500
    
    except Exception as e:
        print(f"Error in log_meal: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/workout-logs', methods=['GET'])
def get_workout_logs():
    """Retrieve workout logs."""
    try:
        # If Firebase is not initialized, return test data
        if not FIREBASE_INITIALIZED or TEST_MODE:
            return jsonify({
                'success': True,
                'logs': [
                    {
                        'id': 'test-1',
                        'workout_type': 'running',
                        'activity_name': 'running',
                        'duration': 30,
                        'distance': 5,
                        'timestamp': '2023-08-11',
                        'source': 'test'
                    }
                ]
            })
            
        # Get workout logs from Realtime Database
        logs_ref = db.reference('workout_logs')
        logs = logs_ref.get()
        
        # Convert to list of dicts with IDs
        result = []
        if logs:
            for log_id, log_data in logs.items():
                log_data['id'] = log_id  # Add document ID
                result.append(log_data)
            
            # Sort by timestamp (descending)
            result.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        
        return jsonify({
            'success': True,
            'logs': result
        })
    
    except Exception as e:
        print(f"Error in get_workout_logs: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/meal-logs', methods=['GET'])
def get_meal_logs():
    """Retrieve meal logs."""
    try:
        # If Firebase is not initialized, return test data
        if not FIREBASE_INITIALIZED or TEST_MODE:
            return jsonify({
                'success': True,
                'logs': [
                    {
                        'id': 'test-1',
                        'meal_type': 'lunch',
                        'food_items': ['sandwich', 'apple'],
                        'timestamp': '2023-08-11',
                        'source': 'test'
                    }
                ]
            })
            
        # Get meal logs from Realtime Database
        logs_ref = db.reference('meal_logs')
        logs = logs_ref.get()
        
        # Convert to list of dicts with IDs
        result = []
        if logs:
            for log_id, log_data in logs.items():
                log_data['id'] = log_id  # Add document ID
                result.append(log_data)
            
            # Sort by timestamp (descending)
            result.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        
        return jsonify({
            'success': True,
            'logs': result
        })
    
    except Exception as e:
        print(f"Error in get_meal_logs: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

# Helper function to find slot values, trying multiple possible slot names
def get_slot_value(slots, possible_slot_names, default_value=''):
    """Get a slot value by trying multiple possible slot names."""
    for slot_name in possible_slot_names:
        if slot_name in slots and slots[slot_name].get('value'):
            return slots[slot_name].get('value')
    return default_value

# Alexa endpoint - Will handle both workout and meal logging from Alexa
@app.route('/api/alexa/log', methods=['POST'])
def alexa_log():
    """Handle logging from Alexa."""
    try:
        # Get the request data
        request_data = request.json
        print("Received Alexa request:", json.dumps(request_data, indent=2))
        
        # Check if this is an Alexa Skills Kit request
        if 'request' in request_data and request_data.get('request', {}).get('type'):
            # This is an Alexa Skills Kit request
            alexa_request = request_data
            request_type = alexa_request['request']['type']
            print(f"Processing Alexa request type: {request_type}")
            
            # Handle LaunchRequest (when skill is opened)
            if request_type == 'LaunchRequest':
                print("Handling LaunchRequest")
                return jsonify({
                    "version": "1.0",
                    "response": {
                        "outputSpeech": {
                            "type": "PlainText",
                            "text": "Welcome to EleFit Tracker. You can log a workout or a meal."
                        },
                        "reprompt": {
                            "outputSpeech": {
                                "type": "PlainText",
                                "text": "Try saying: log a running workout for 30 minutes, or log breakfast with oatmeal."
                            }
                        },
                        "shouldEndSession": False
                    }
                })
                
            # Handle SessionEndedRequest
            elif request_type == 'SessionEndedRequest':
                print("Handling SessionEndedRequest")
                return jsonify({
                    "version": "1.0",
                    "response": {
                        "outputSpeech": {
                            "type": "PlainText",
                            "text": "Goodbye!"
                        },
                        "shouldEndSession": True
                    }
                })
                
            # Handle IntentRequests
            elif request_type == 'IntentRequest':
                intent_request = alexa_request['request']
                intent = intent_request.get('intent', {})
                intent_name = intent.get('name', '')
                slots = intent.get('slots', {})
                
                print(f"Handling IntentRequest: {intent_name}")
                print(f"Slots: {json.dumps(slots, indent=2)}")
                
                if intent_name == 'LogWorkoutIntent':
                    # Get workout details from slots
                    workout_type = get_slot_value(slots, ['WorkoutType', 'workoutType'], 'cardio').lower()
                    duration = 30  # Default
                    
                    duration_str = get_slot_value(slots, ['Duration', 'duration'])
                    if duration_str:
                        try:
                            duration = int(duration_str)
                        except ValueError:
                            print(f"Invalid duration value: {duration_str}")
                    
                    # Print debug information
                    print(f"Received slots: {json.dumps(slots, indent=2)}")
                    print(f"Workout type: {workout_type}, Duration: {duration}")
                        
                    # Create workout data
                    workout_data = {
                        'workoutType': workout_type,
                        'duration': duration,
                        'timestamp': datetime.datetime.now().strftime('%Y-%m-%d'),
                        'source': 'alexa',
                        'type': 'workout',
                        'id': f'alexa_{datetime.datetime.now().timestamp()}'
                    }
                    
                    print(f"Prepared workout data: {json.dumps(workout_data, indent=2)}")
                    
                    # Log workout using direct API method
                    try:
                        # Make a new request to our own API
                        workout_response = log_direct_workout(workout_data)
                        
                        # Create Alexa response
                        return jsonify({
                            "version": "1.0",
                            "response": {
                                "outputSpeech": {
                                    "type": "PlainText",
                                    "text": f"Your {workout_type} workout has been logged successfully for {duration} minutes."
                                },
                                "card": {
                                    "type": "Simple",
                                    "title": "EleFit Tracker",
                                    "content": f"Logged {workout_type} workout for {duration} minutes."
                                },
                                "shouldEndSession": True
                            }
                        })
                    except Exception as e:
                        print(f"Error logging workout from Alexa: {str(e)}")
                        # Return error response to Alexa
                        return jsonify({
                            "version": "1.0",
                            "response": {
                                "outputSpeech": {
                                    "type": "PlainText",
                                    "text": f"Sorry, there was an error logging your workout. {str(e)}"
                                },
                                "shouldEndSession": True
                            }
                        })
                    
                elif intent_name == 'LogMealIntent':
                    # Extract meal details from slots
                    meal_type = get_slot_value(slots, ['MealType', 'mealType'], 'snack').lower()
                    
                    # Extract food items
                    food_items = []
                    food_item = get_slot_value(slots, ['FoodItem', 'foodItems'])
                    if food_item:
                        food_items.append(food_item)
                    
                    # Print debug information
                    print(f"Received slots for meal: {json.dumps(slots, indent=2)}")
                    print(f"Meal type: {meal_type}, Food Items: {food_items}")
                    
                    # Create meal data
                    meal_data = {
                        'mealType': meal_type,
                        'foodItems': food_items,
                        'timestamp': datetime.datetime.now().strftime('%Y-%m-%d'),
                        'source': 'alexa',
                        'type': 'meal',
                        'id': f'alexa_{datetime.datetime.now().timestamp()}'
                    }
                    
                    print(f"Prepared meal data: {json.dumps(meal_data, indent=2)}")
                    
                    # Log meal using direct API method
                    try:
                        # Make a new request to our own API
                        meal_response = log_direct_meal(meal_data)
                        
                        # Create response text
                        food_text = f" with {food_item}" if food_item else ""
                        
                        # Create Alexa response
                        return jsonify({
                            "version": "1.0",
                            "response": {
                                "outputSpeech": {
                                    "type": "PlainText",
                                    "text": f"Your {meal_type}{food_text} has been logged successfully."
                                },
                                "card": {
                                    "type": "Simple",
                                    "title": "EleFit Tracker",
                                    "content": f"Logged {meal_type}{food_text}."
                                },
                                "shouldEndSession": True
                            }
                        })
                    except Exception as e:
                        print(f"Error logging meal from Alexa: {str(e)}")
                        # Return error response to Alexa
                        return jsonify({
                            "version": "1.0",
                            "response": {
                                "outputSpeech": {
                                    "type": "PlainText",
                                    "text": f"Sorry, there was an error logging your meal. {str(e)}"
                                },
                                "shouldEndSession": True
                            }
                        })
                
                else:
                    # Handle unknown intent
                    print(f"Unknown intent: {intent_name}")
                    return jsonify({
                        "version": "1.0",
                        "response": {
                            "outputSpeech": {
                                "type": "PlainText",
                                "text": "I'm not sure what you want to log. You can log a workout or a meal."
                            },
                            "shouldEndSession": False
                        }
                    })
            
            # Handle any other Alexa request types
            else:
                print(f"Unhandled request type: {request_type}")
                return jsonify({
                    "version": "1.0",
                    "response": {
                        "outputSpeech": {
                            "type": "PlainText",
                            "text": "I'm not sure how to handle that request. You can log a workout or a meal."
                        },
                        "shouldEndSession": False
                    }
                })
        
        # Handle direct API calls (as your current implementation expects)
        else:
            data = request_data
            log_type = data.get('logType')
            print(f"Direct API call with log_type: {log_type}")
        
            if log_type == 'workout':
                # Process workout log
                return log_workout()
            elif log_type == 'meal':
                # Process meal log
                return log_meal()
            else:
                return jsonify({'success': False, 'message': 'Invalid log type'}), 400
    
    except Exception as e:
        print(f"Error in alexa_log: {str(e)}")
        import traceback
        traceback.print_exc()
        # Return Alexa-compatible error response
        return jsonify({
            "version": "1.0",
            "response": {
                "outputSpeech": {
                    "type": "PlainText",
                    "text": "Sorry, there was an error processing your request."
                },
                "shouldEndSession": True
            }
        })

# Helper function to log workout directly without modifying request.json
def log_direct_workout(workout_data):
    """Log a workout without modifying request.json."""
    try:
        # Validate required fields
        required_fields = ['workoutType', 'activityName', 'duration', 'timestamp', 'source']
        for field in required_fields:
            if field not in workout_data:
                return {'success': False, 'message': f'Missing required field: {field}'}
        
        # Create workout document
        workout_doc = {
            'workout_type': workout_data['workoutType'],
            'activity_name': workout_data['activityName'],
            'duration': workout_data['duration'],
            'distance': workout_data.get('distance'),
            'sets': workout_data.get('sets'),
            'reps': workout_data.get('reps'),
            'timestamp': workout_data['timestamp'],
            'source': workout_data['source']
        }
        
        # If in test mode, we'll skip actual Firebase storage
        if TEST_MODE:
            workout_id = f'test-{datetime.datetime.now().timestamp()}'
            print(f"Test mode: Created workout with ID {workout_id}")
            return {
                'success': True,
                'message': f"Workout logged in test mode (Firebase: {FIREBASE_INITIALIZED})",
                'workout_id': workout_id
            }
        
        # Try to store in Firebase if initialized
        if FIREBASE_INITIALIZED:
            try:
                # Add to Realtime Database
                workout_ref = db.reference('workout_logs').push(workout_doc)
                workout_id = workout_ref.key
                
                # Add ID to document for Firestore sync
                workout_doc['id'] = workout_id
                
                # Sync to Firestore
                sync_to_firestore(workout_doc, 'workout')
                
                return {
                    'success': True,
                    'message': 'Workout logged successfully',
                    'workout_id': workout_id
                }
            except Exception as firebase_error:
                print(f"Firebase error in log_direct_workout: {str(firebase_error)}")
                if workout_data.get('source') == 'alexa':
                    print("Returning success response for Alexa despite Firebase error")
                    return {
                        'success': True,
                        'message': 'Workout logged for Alexa (no Firebase)',
                        'workout_id': 'temp_id'
                    }
                else:
                    return {'success': False, 'message': str(firebase_error)}
        else:
            return {'success': False, 'message': 'Firebase not initialized'}
    
    except Exception as e:
        print(f"Error in log_direct_workout: {str(e)}")
        return {'success': False, 'message': str(e)}

# Helper function to log meal directly without modifying request.json
def log_direct_meal(meal_data):
    """Log a meal without modifying request.json."""
    try:
        # Validate required fields
        required_fields = ['mealType', 'foodItems', 'timestamp', 'source']
        for field in required_fields:
            if field not in meal_data:
                return {'success': False, 'message': f'Missing required field: {field}'}
                
        print(f"Processing meal data: {json.dumps(meal_data, indent=2)}")
        
        # Create meal document
        meal_doc = {
            'meal_type': meal_data['mealType'],
            'food_items': meal_data['foodItems'],
            'timestamp': meal_data['timestamp'],
            'source': meal_data['source']
        }
        
        # If in test mode, we'll skip actual Firebase storage
        if TEST_MODE:
            meal_id = f'test-{datetime.datetime.now().timestamp()}'
            print(f"Test mode: Created meal with ID {meal_id}")
            return {
                'success': True,
                'message': f"Meal logged in test mode (Firebase: {FIREBASE_INITIALIZED})",
                'meal_id': meal_id
            }
            
        # Try to store in Firebase if initialized
        if FIREBASE_INITIALIZED:
            try:
                # Add to Realtime Database
                meal_ref = db.reference('meal_logs').push(meal_doc)
                meal_id = meal_ref.key
                
                # Add ID to document for Firestore sync
                meal_doc['id'] = meal_id
                
                # Sync to Firestore
                sync_to_firestore(meal_doc, 'meal')
                
                print(f"Successfully logged meal: {json.dumps(meal_doc, indent=2)}")
                
                return {
                    'success': True,
                    'message': 'Meal logged successfully',
                    'meal_id': meal_id
                }
            except Exception as firebase_error:
                print(f"Firebase error in log_direct_meal: {str(firebase_error)}")
                if meal_data.get('source') == 'alexa':
                    print("Returning success response for Alexa despite Firebase error")
                    return {
                        'success': True,
                        'message': 'Meal logged for Alexa (no Firebase)',
                        'meal_id': 'temp_id'
                    }
                else:
                    return {'success': False, 'message': str(firebase_error)}
        else:
            return {'success': False, 'message': 'Firebase not initialized'}
    
    except Exception as e:
        print(f"Error in log_direct_meal: {str(e)}")
        return {'success': False, 'message': str(e)}

# Debug endpoint to help test Alexa functionality
@app.route('/api/debug/alexa', methods=['GET'])
def debug_alexa():
    """Return test payloads for Alexa."""
    return jsonify({
        'success': True,
        'test_payloads': {
            'launch_request': {
                "version": "1.0",
                "session": {
                    "new": True,
                    "sessionId": "test-session-123",
                    "application": {"applicationId": "test-app-id"},
                    "user": {"userId": "test-user-123"}
                },
                "request": {
                    "type": "LaunchRequest",
                    "requestId": "test-launch-request-id",
                    "timestamp": "2023-08-11T12:00:00Z",
                    "locale": "en-US"
                }
            },
            'log_workout_intent': {
                "version": "1.0",
                "session": {
                    "new": False,
                    "sessionId": "test-session-123",
                    "application": {"applicationId": "test-app-id"},
                    "user": {"userId": "test-user-123"}
                },
                "request": {
                    "type": "IntentRequest",
                    "requestId": "test-intent-request-id",
                    "timestamp": "2023-08-11T12:00:00Z",
                    "locale": "en-US",
                    "intent": {
                        "name": "LogWorkoutIntent",
                        "slots": {
                            "WorkoutType": {"name": "WorkoutType", "value": "running"},
                            "Duration": {"name": "Duration", "value": "45"}
                        }
                    }
                }
            },
            'log_meal_intent': {
                "version": "1.0",
                "session": {
                    "new": False,
                    "sessionId": "test-session-123",
                    "application": {"applicationId": "test-app-id"},
                    "user": {"userId": "test-user-123"}
                },
                "request": {
                    "type": "IntentRequest",
                    "requestId": "test-intent-request-id",
                    "timestamp": "2023-08-11T12:00:00Z",
                    "locale": "en-US",
                    "intent": {
                        "name": "LogMealIntent",
                        "slots": {
                            "MealType": {"name": "MealType", "value": "breakfast"},
                            "FoodItem": {"name": "FoodItem", "value": "oatmeal"}
                        }
                    }
                }
            },
            'direct_workout_payload': {
                "logType": "workout",
                "workoutType": "running",
                "activityName": "running",
                "duration": 30,
                "timestamp": "2023-08-11",
                "source": "alexa"
            },
            'direct_meal_payload': {
                "logType": "meal",
                "mealType": "lunch",
                "foodItems": ["sandwich", "apple"],
                "timestamp": "2023-08-11",
                "source": "alexa"
            }
        }
    })

# Debug endpoint for Alexa workout logging
@app.route('/api/debug/alexa/workout', methods=['POST'])
def debug_alexa_workout():
    """Test endpoint for Alexa workout logging."""
    try:
        # Get the request data
        request_data = request.json
        print("Received debug Alexa workout request:", json.dumps(request_data, indent=2))
        
        # Extract workout data directly from the Alexa request
        if 'request' in request_data and request_data.get('request', {}).get('type') == 'IntentRequest':
            # This is an Alexa Skills Kit request
            intent = request_data.get('request', {}).get('intent', {})
            intent_name = intent.get('name', '')
            slots = intent.get('slots', {})
            
            if intent_name == 'LogWorkoutIntent':
                # Get workout details from slots
                workout_type = get_slot_value(slots, ['WorkoutType', 'workoutType'], 'cardio').lower()
                duration = 30  # Default
                
                duration_str = get_slot_value(slots, ['Duration', 'duration'])
                if duration_str:
                    try:
                        duration = int(duration_str)
                    except ValueError:
                        print(f"Invalid duration value: {duration_str}")
                
                # Log workout in test mode
                workout_id = f"test-{datetime.datetime.now().timestamp()}"
                
                return jsonify({
                    "version": "1.0",
                    "response": {
                        "outputSpeech": {
                            "type": "PlainText",
                            "text": f"Your {workout_type} workout has been logged successfully for {duration} minutes."
                        },
                        "card": {
                            "type": "Simple",
                            "title": "EleFit Tracker",
                            "content": f"Logged {workout_type} workout for {duration} minutes."
                        },
                        "shouldEndSession": True
                    }
                })
            else:
                return jsonify({
                    "version": "1.0",
                    "response": {
                        "outputSpeech": {
                            "type": "PlainText",
                            "text": "Sorry, I only understand workout logging requests."
                        },
                        "shouldEndSession": True
                    }
                })
        else:
            return jsonify({
                "success": False,
                "message": "This endpoint only supports Alexa IntentRequests for LogWorkoutIntent"
            }), 400
    
    except Exception as e:
        print(f"Error in debug_alexa_workout: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "version": "1.0",
            "response": {
                "outputSpeech": {
                    "type": "PlainText",
                    "text": "Sorry, there was an error processing your request."
                },
                "shouldEndSession": True
            }
        })

# Alexa account linked endpoint for authenticated logging
@app.route('/alexa/auth/log', methods=['POST'])
def alexa_auth_log():
    """Handle logging from Alexa with user authentication via Google OAuth."""
    try:
        # Parse the incoming request from Alexa
        request_data = request.json
        print(f"Received Alexa auth request: {json.dumps(request_data, indent=2)}")
        
        # Extract the access token from the request
        context = request_data.get('context', {})
        system = context.get('System', {})
        user = system.get('user', {})
        
        # Get the access token
        token = user.get('accessToken')
        
        if not token:
            print("Missing authorization token")
            return jsonify({
                "version": "1.0",
                "response": {
                    "outputSpeech": {
                        "type": "PlainText",
                        "text": "Please link your account in the Alexa app first."
                    },
                    "card": {
                        "type": "LinkAccount"
                    },
                    "shouldEndSession": True
                }
            })
        
        print(f"Access token: {token[:20]}... (truncated)")
        
        # Verify the token with Firebase
        try:
            # Use Google's OAuth2 toolkit to verify the token
            user_info = id_token.verify_oauth2_token(token, google_requests.Request())
            print(f"Successfully verified token. User info: {json.dumps(user_info, indent=2)}")
            user_email = user_info.get('email', 'unknown_email')
        except Exception as e:
            print(f"Error verifying token: {str(e)}")
            # This is a fallback - may not be reliable in production
            # For demo, we'll try to extract email from the token payload
            try:
                token_parts = token.split('.')
                if len(token_parts) >= 2:
                    padding = '=' * (4 - len(token_parts[1]) % 4)
                    payload = json.loads(base64.b64decode(token_parts[1] + padding).decode('utf-8'))
                    user_email = payload.get('email', 'unknown_email')
                    print(f"Extracted email from token payload: {user_email}")
                else:
                    user_email = 'unknown_email'
            except Exception as token_error:
                print(f"Error parsing token: {str(token_error)}")
                user_email = 'unknown_email'

        print(f"Authenticated Alexa request for user: {user_email}")
        
        # Handle IntentRequests
        if request_data.get('request', {}).get('type') == 'IntentRequest':
            intent_request = request_data.get('request', {})
            intent = intent_request.get('intent', {})
            intent_name = intent.get('name', '')
            slots = intent.get('slots', {})
            
            print(f"Processing intent: {intent_name}")
            
            # Launch the skill
            if intent_name == 'AMAZON.LaunchIntent':
                print("Handling LaunchIntent")
                return jsonify({
                    "version": "1.0",
                    "response": {
                        "outputSpeech": {
                            "type": "PlainText",
                            "text": f"Welcome to EleFit Tracker. You can say 'log a workout' or 'log a meal'."
                        },
                        "shouldEndSession": False
                    }
                })
                
            # Help intent
            elif intent_name == 'AMAZON.HelpIntent':
                print("Handling HelpIntent")
                return jsonify({
                    "version": "1.0",
                    "response": {
                        "outputSpeech": {
                            "type": "PlainText",
                            "text": "You can say 'log a workout' to record exercise, or 'log a meal' to record what you ate. How can I help you?"
                        },
                        "shouldEndSession": False
                    }
                })
                
            # Stop or cancel intent
            elif intent_name in ['AMAZON.StopIntent', 'AMAZON.CancelIntent']:
                print("Handling StopIntent or CancelIntent")
                return jsonify({
                    "version": "1.0",
                    "response": {
                        "outputSpeech": {
                            "type": "PlainText",
                            "text": "Goodbye!"
                        },
                        "shouldEndSession": True
                    }
                })
                
            # Handle logging workouts
            elif intent_name == 'LogWorkoutIntent':
                # Extract workout details from slots
                workout_type = get_slot_value(slots, ['WorkoutType', 'workoutType'], 'cardio').lower()
                duration = 30  # Default
                
                duration_str = get_slot_value(slots, ['Duration', 'duration'])
                if duration_str:
                    try:
                        duration = int(duration_str)
                    except ValueError:
                        print(f"Invalid duration value: {duration_str}")
                    
                # Print debug information
                print(f"Received slots: {json.dumps(slots, indent=2)}")
                print(f"Workout type: {workout_type}, Duration: {duration}")
                    
                # Create workout data
                workout_data = {
                    'workoutType': workout_type,
                    'duration': duration,
                    'timestamp': datetime.datetime.now().strftime('%Y-%m-%d'),
                    'source': 'alexa',
                    'type': 'workout',
                    'id': f'alexa_{datetime.datetime.now().timestamp()}'
                }
                
                # Store directly to Firestore for the authenticated user
                if firestore_db:
                    try:
                        # Get user document
                        user_ref = firestore_db.collection('users').document(user_email)
                        
                        # Add workout to Firestore
                        workout_ref = user_ref.collection('workout_logs').document()
                        workout_data['id'] = workout_ref.id
                        workout_ref.set(workout_data)
                        
                        print(f"Workout logged successfully for user: {user_email}")
                        
                        # Create Alexa response
                        return jsonify({
                            "version": "1.0",
                            "response": {
                                "outputSpeech": {
                                    "type": "PlainText",
                                    "text": f"Your {workout_type} workout for {duration} minutes has been logged successfully."
                                },
                                "card": {
                                    "type": "Simple",
                                    "title": "EleFit Tracker",
                                    "content": f"Logged {workout_type} workout for {duration} minutes."
                                },
                                "shouldEndSession": True
                            }
                        })
                    except Exception as e:
                        print(f"Error logging workout to Firestore: {str(e)}")
                        return jsonify({
                            "version": "1.0",
                            "response": {
                                "outputSpeech": {
                                    "type": "PlainText",
                                    "text": f"Sorry, there was an error logging your workout."
                                },
                                "shouldEndSession": True
                            }
                        })
                
            elif intent_name == 'LogMealIntent':
                # Extract meal details from slots
                meal_type = get_slot_value(slots, ['MealType', 'mealType'], 'snack').lower()
                
                # Extract food items
                food_items = []
                food_item = get_slot_value(slots, ['FoodItem', 'foodItems'])
                if food_item:
                    food_items.append(food_item)
                
                # Print debug information
                print(f"Received slots for meal: {json.dumps(slots, indent=2)}")
                print(f"Meal type: {meal_type}, Food Items: {food_items}")
                
                # Create meal data
                meal_data = {
                    'mealType': meal_type,
                    'foodItems': food_items,
                    'timestamp': datetime.datetime.now().strftime('%Y-%m-%d'),
                    'source': 'alexa',
                    'type': 'meal',
                    'id': f'alexa_{datetime.datetime.now().timestamp()}'
                }
                
                # Store directly to Firestore for the authenticated user
                if firestore_db:
                    try:
                        # Add meal to Firestore
                        user_ref = firestore_db.collection('users').document(user_email)
                        meal_ref = user_ref.collection('meal_logs').document()
                        meal_data['id'] = meal_ref.id
                        meal_ref.set(meal_data)
                        
                        print(f"Meal logged successfully for user: {user_email}")
                        
                        # Get food item text for response
                        food_text = f" with {food_item}" if food_item else ""
                        
                        # Create Alexa response
                        return jsonify({
                            "version": "1.0",
                            "response": {
                                "outputSpeech": {
                                    "type": "PlainText",
                                    "text": f"Your {meal_type}{food_text} has been logged successfully."
                                },
                                "card": {
                                    "type": "Simple",
                                    "title": "EleFit Tracker",
                                    "content": f"Logged {meal_type}{food_text}."
                                },
                                "shouldEndSession": True
                            }
                        })
                    except Exception as e:
                        print(f"Error logging meal to Firestore: {str(e)}")
                        return jsonify({
                            "version": "1.0",
                            "response": {
                                "outputSpeech": {
                                    "type": "PlainText",
                                    "text": f"Sorry, there was an error logging your meal."
                                },
                                "shouldEndSession": True
                            }
                        })
                else:
                    print("Firestore not initialized")
                    return jsonify({
                        "version": "1.0",
                        "response": {
                            "outputSpeech": {
                                "type": "PlainText",
                                "text": "Sorry, the database is not available right now. Please try again later."
                            },
                            "shouldEndSession": True
                        }
                    })
            
            # Unknown intent
            else:
                print(f"Unknown intent: {intent_name}")
                return jsonify({
                    "version": "1.0",
                    "response": {
                        "outputSpeech": {
                            "type": "PlainText",
                            "text": "I'm not sure how to help with that. You can say 'log a workout' or 'log a meal'."
                        },
                        "shouldEndSession": False
                    }
                })
                
        # Handle launch requests
        elif request_data.get('request', {}).get('type') == 'LaunchRequest':
            print("Handling LaunchRequest")
            return jsonify({
                "version": "1.0",
                "response": {
                    "outputSpeech": {
                        "type": "PlainText",
                        "text": f"Welcome to EleFit Tracker. You can say 'log a workout' or 'log a meal'."
                    },
                    "shouldEndSession": False
                }
            })
            
        # Handle session ended requests
        elif request_data.get('request', {}).get('type') == 'SessionEndedRequest':
            print("Handling SessionEndedRequest")
            return jsonify({
                "version": "1.0",
                "response": {
                    "shouldEndSession": True
                }
            })
            
        # Unknown request type
        else:
            print(f"Unknown request type: {request_data.get('request', {}).get('type')}")
            return jsonify({
                "version": "1.0",
                "response": {
                    "outputSpeech": {
                        "type": "PlainText",
                        "text": "I'm not sure how to handle that request."
                    },
                    "shouldEndSession": True
                }
            })
            
    except Exception as e:
        print(f"Error in alexa_auth_log: {str(e)}")
        return jsonify({
            "version": "1.0",
            "response": {
                "outputSpeech": {
                    "type": "PlainText",
                    "text": "Sorry, there was an error processing your request."
                },
                "shouldEndSession": True
            }
        })

# Redirect to frontend
@app.route('/privacy', methods=['GET'])
def privacy_redirect():
    """Redirect to the privacy policy page on the frontend."""
    frontend_url = get_frontend_url()
    return redirect(f"{frontend_url}/privacy")

@app.route('/api/alexa/link-account', methods=['POST', 'OPTIONS'])
def alexa_link_account():
    """Handle Alexa account linking."""
    # Handle CORS preflight request
    if request.method == 'OPTIONS':
        response = make_response()
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
        response.headers.add('Access-Control-Allow-Methods', 'POST,OPTIONS')
        return response
    
    try:
        # Verify authorization header
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            print("Missing or invalid authorization header")
            return jsonify({'success': False, 'message': 'Missing or invalid authorization'}), 401
        
        # Extract the token
        id_token = auth_header.split('Bearer ')[1]
        
        # Verify the token with Google (simplified for now)
        token_info_url = f"https://www.googleapis.com/oauth2/v3/tokeninfo?id_token={id_token}"
        token_response = requests.get(token_info_url)
        
        if token_response.status_code != 200:
            print(f"Invalid token: {token_response.text}")
            return jsonify({'success': False, 'message': 'Invalid token'}), 401
        
        # Get user info from token
        token_data = token_response.json()
        user_email = token_data.get('email')
        
        if not user_email:
            print("No email found in token data")
            return jsonify({'success': False, 'message': 'User email not found in token'}), 401
        
        # Get the authorization code from the request
        data = request.json
        code = data.get('code')
        redirect_uri = data.get('redirect_uri')
        
        if not code or not redirect_uri:
            print("Missing code or redirect_uri in request")
            return jsonify({'success': False, 'message': 'Missing code or redirect_uri'}), 400
        
        # Exchange code for tokens with Amazon
        client_id = 'elefit-alexa-client'
        client_secret = 'your-alexa-client-secret'  # In production, use environment variables
        
        # Prepare token exchange request
        token_url = 'https://api.amazon.com/auth/o2/token'
        payload = {
            'grant_type': 'authorization_code',
            'code': code,
            'client_id': client_id,
            'client_secret': client_secret,
            'redirect_uri': redirect_uri
        }
        
        # Make token exchange request to Amazon
        token_exchange = requests.post(
            token_url,
            data=payload,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        
        if token_exchange.status_code != 200:
            print(f"Token exchange failed: {token_exchange.text}")
            return jsonify({
                'success': False, 
                'message': f'Amazon token exchange failed: {token_exchange.text}'
            }), 400
        
        # Extract tokens from response
        token_data = token_exchange.json()
        access_token = token_data.get('access_token')
        refresh_token = token_data.get('refresh_token')
        
        # Store tokens in Firestore if available
        if FIREBASE_INITIALIZED and firestore_db:
            user_ref = firestore_db.collection('users').document(user_email)
            
            # Update user document with Amazon tokens
            user_ref.set({
                'amazonTokens': {
                    'accessToken': access_token,
                    'refreshToken': refresh_token,
                    'linked': True,
                    'linkedAt': datetime.datetime.now().isoformat()
                }
            }, merge=True)
            
            print(f"Alexa account linked successfully for user: {user_email}")
        else:
            print("Firebase not initialized, skipping token storage")
        
        # Return success response
        return jsonify({
            'success': True,
            'message': 'Account linked successfully'
        })
        
    except Exception as e:
        print(f"Error in Alexa account linking: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/alexa/check-link-status', methods=['GET'])
def check_alexa_link_status():
    """Check if the user has linked their Alexa account."""
    try:
        # Verify authorization header
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            print("Missing or invalid authorization header")
            return jsonify({'success': False, 'message': 'Missing or invalid authorization'}), 401
        
        # Extract the token
        id_token = auth_header.split('Bearer ')[1]
        
        # Verify the token with Google (simplified for now)
        token_info_url = f"https://www.googleapis.com/oauth2/v3/tokeninfo?id_token={id_token}"
        token_response = requests.get(token_info_url)
        
        if token_response.status_code != 200:
            print(f"Invalid token: {token_response.text}")
            return jsonify({'success': False, 'message': 'Invalid token'}), 401
        
        # Get user info from token
        token_data = token_response.json()
        user_email = token_data.get('email')
        
        if not user_email:
            print("No email found in token data")
            return jsonify({'success': False, 'message': 'User email not found in token'}), 401
        
        # Check if the user has linked their Alexa account in Firestore
        linked = False
        if FIREBASE_INITIALIZED and firestore_db:
            try:
                user_ref = firestore_db.collection('users').document(user_email)
                user_doc = user_ref.get()
                
                if user_doc.exists:
                    amazon_tokens = user_doc.get('amazonTokens', {})
                    linked = amazon_tokens.get('linked', False)
                    
                return jsonify({
                    'success': True,
                    'isLinked': linked
                })
            except Exception as e:
                print(f"Error checking link status: {str(e)}")
                return jsonify({'success': False, 'message': str(e)}), 500
        else:
            return jsonify({'success': True, 'isLinked': False})
    
    except Exception as e:
        print(f"Error in check_alexa_link_status: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/alexa/unlink-account', methods=['POST', 'OPTIONS'])
def unlink_alexa_account():
    """Unlink a user's Alexa account."""
    # Handle CORS preflight request
    if request.method == 'OPTIONS':
        response = make_response()
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
        response.headers.add('Access-Control-Allow-Methods', 'POST,OPTIONS')
        return response
    
    try:
        # Verify authorization header
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            print("Missing or invalid authorization header")
            return jsonify({'success': False, 'message': 'Missing or invalid authorization'}), 401
        
        # Extract the token
        id_token = auth_header.split('Bearer ')[1]
        
        # Verify the token with Google
        token_info_url = f"https://www.googleapis.com/oauth2/v3/tokeninfo?id_token={id_token}"
        token_response = requests.get(token_info_url)
        
        if token_response.status_code != 200:
            print(f"Invalid token: {token_response.text}")
            return jsonify({'success': False, 'message': 'Invalid token'}), 401
        
        # Get user info from token
        token_data = token_response.json()
        user_email = token_data.get('email')
        
        if not user_email:
            print("No email found in token data")
            return jsonify({'success': False, 'message': 'User email not found in token'}), 401
        
        # Unlink the Alexa account in Firestore
        if FIREBASE_INITIALIZED and firestore_db:
            try:
                user_ref = firestore_db.collection('users').document(user_email)
                
                # Remove Amazon tokens and set linked to false
                user_ref.set({
                    'amazonTokens': {
                        'linked': False,
                        'unlinkedAt': datetime.datetime.now().isoformat()
                    }
                }, merge=True)
                
                print(f"Alexa account unlinked successfully for user: {user_email}")
                return jsonify({
                    'success': True,
                    'message': 'Account unlinked successfully'
                })
            except Exception as e:
                print(f"Error unlinking account: {str(e)}")
                return jsonify({'success': False, 'message': str(e)}), 500
        else:
            return jsonify({'success': True, 'message': 'Account unlinked successfully (Firebase not initialized)'})
    
    except Exception as e:
        print(f"Error in unlink_alexa_account: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500

# Run the app
if __name__ == '__main__':
    app.run(debug=True, port=5000) 
