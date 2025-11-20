# Flask web application for managing TikTok live stream monitoring and Discord notifications
from flask import Flask, render_template, request, jsonify, redirect, url_for
import json  # For reading/writing JSON files (user list)
import os  # For file system operations and environment variables
from datetime import datetime  # For timestamping when users are added
from dotenv import load_dotenv  # For loading environment variables from .env file
from monitoring_service import MonitoringService  # Background service that monitors TikTok streams
from discord_webhook import DiscordWebhook  # Handles sending notifications to Discord

# Load environment variables from .env file (if it exists)
# This allows users to set DISCORD_WEBHOOK_URL and other config without hardcoding
load_dotenv()

# Get the directory where this script is located
# This ensures the app works regardless of where it's run from
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')  # Path to HTML templates folder

# Initialize Flask app with explicit template folder
# Flask needs to know where to find HTML templates
app = Flask(__name__, template_folder=TEMPLATE_DIR)
# Secret key for Flask sessions (should be changed in production)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')

# Initialize services that will be used throughout the application
monitoring_service = MonitoringService()  # Handles background monitoring of TikTok streams
discord_webhook = DiscordWebhook()  # Handles Discord webhook notifications

# Data file for storing monitored users (JSON format)
# This file persists the list of TikTok usernames to monitor
USERS_FILE = 'monitored_users.json'

def load_users():
    """Load monitored users from JSON file"""
    # Check if the users file exists before trying to read it
    if os.path.exists(USERS_FILE):
        # Open and read the JSON file
        with open(USERS_FILE, 'r') as f:
            return json.load(f)  # Parse JSON and return list of users
    # Return empty list if file doesn't exist yet (first run)
    return []

def save_users(users):
    """Save monitored users to JSON file"""
    # Write the users list to JSON file with pretty formatting (indent=2)
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)  # indent=2 makes the JSON readable

@app.route('/')
def index():
    """Main page with user management UI - renders the web interface"""
    # Load the current list of monitored users from file
    users = load_users()
    # Get webhook URLs from environment variables (or empty string if not set)
    webhook_url = os.environ.get('DISCORD_WEBHOOK_URL', '')  # Main webhook for live notifications
    gift_webhook_url = os.environ.get('DISCORD_GIFT_WEBHOOK_URL', '')  # Optional separate webhook for gifts
    # Check if the monitoring service is currently running
    is_monitoring = monitoring_service.is_running()
    # Render the HTML template with all the data needed for the UI
    return render_template('index.html', users=users, webhook_url=webhook_url, gift_webhook_url=gift_webhook_url, is_monitoring=is_monitoring)

@app.route('/api/users', methods=['GET'])
def get_users():
    """API endpoint: Get list of monitored users (returns JSON)"""
    # Return the list of users as JSON for the frontend to consume
    return jsonify(load_users())

@app.route('/api/users', methods=['POST'])
def add_user():
    """API endpoint: Add a user to the monitored list"""
    # Get the JSON data sent from the frontend
    data = request.json
    # Extract and clean the username (remove whitespace)
    username = data.get('username', '').strip()
    
    # Validate that username was provided
    if not username:
        return jsonify({'error': 'Username is required'}), 400  # 400 = Bad Request
    
    # Load existing users from file
    users = load_users()
    
    # Check if user already exists (case-insensitive comparison)
    # Prevents duplicate entries in the monitoring list
    if any(u.get('username', '').lower() == username.lower() for u in users):
        return jsonify({'error': 'User already in list'}), 400
    
    # Add the new user with a timestamp
    users.append({
        'username': username,
        'added_at': datetime.now().isoformat()  # ISO format timestamp (e.g., "2025-01-15T10:30:00")
    })
    
    # Save the updated list back to file
    save_users(users)
    # Return success message and updated user list
    return jsonify({'message': 'User added successfully', 'users': users})

@app.route('/api/users/<username>', methods=['DELETE'])
def remove_user(username):
    """API endpoint: Remove a user from the monitored list"""
    # Load current users from file
    users = load_users()
    # Filter out the user to remove (case-insensitive matching)
    # List comprehension keeps all users EXCEPT the one matching the username
    users = [u for u in users if u.get('username', '').lower() != username.lower()]
    # Save the updated list (without the removed user)
    save_users(users)
    # Return success message and updated user list
    return jsonify({'message': 'User removed successfully', 'users': users})

@app.route('/api/monitoring/start', methods=['POST'])
def start_monitoring():
    """API endpoint: Start the monitoring service"""
    # Get webhook URLs from the request (user entered in UI)
    webhook_url = request.json.get('webhook_url', '').strip()  # Main webhook for live notifications
    gift_webhook_url = request.json.get('gift_webhook_url', '').strip()  # Optional separate webhook for gifts
    
    # Validate that at least the main webhook URL is provided
    if not webhook_url:
        return jsonify({'error': 'Discord webhook URL is required'}), 400
    
    # Store webhook URLs in environment variables so other parts of the app can access them
    os.environ['DISCORD_WEBHOOK_URL'] = webhook_url
    if gift_webhook_url:
        # If gift webhook is provided, store it
        os.environ['DISCORD_GIFT_WEBHOOK_URL'] = gift_webhook_url
    else:
        # Clear gift webhook URL if not provided (user removed it)
        if 'DISCORD_GIFT_WEBHOOK_URL' in os.environ:
            del os.environ['DISCORD_GIFT_WEBHOOK_URL']
    
    # Start the background monitoring service with the provided webhook URLs
    # This begins checking TikTok users and sending Discord notifications
    monitoring_service.start(webhook_url=webhook_url, gift_webhook_url=gift_webhook_url if gift_webhook_url else None)
    return jsonify({'message': 'Monitoring started'})

@app.route('/api/monitoring/stop', methods=['POST'])
def stop_monitoring():
    """API endpoint: Stop the monitoring service"""
    # Stop the background monitoring service
    # This disconnects from TikTok streams and stops checking for live status
    monitoring_service.stop()
    return jsonify({'message': 'Monitoring stopped'})

@app.route('/api/monitoring/status', methods=['GET'])
def monitoring_status():
    """API endpoint: Get monitoring service status"""
    # Return current status information for the frontend
    return jsonify({
        'is_running': monitoring_service.is_running(),  # True if monitoring is active
        'users_count': len(load_users())  # Number of users being monitored
    })

@app.route('/api/webhook/test', methods=['POST'])
def test_webhook():
    """API endpoint: Test the Discord webhook by sending a test message"""
    # Get webhook URL from request, or try to find it from environment/service
    webhook_url = request.json.get('webhook_url', '').strip()
    if not webhook_url:
        # Fallback: try to get from environment variable or monitoring service
        webhook_url = os.environ.get('DISCORD_WEBHOOK_URL', '') or getattr(monitoring_service, '_webhook_url', None)
    
    # Validate that we have a webhook URL
    if not webhook_url:
        return jsonify({'error': 'Discord webhook URL is required'}), 400
    
    try:
        # Create a DiscordWebhook instance with the test URL
        webhook = DiscordWebhook(webhook_url=webhook_url)
        # Send a test notification with fake data
        result = webhook.send_go_live_notification(
            username="TEST_USER",  # Fake username for testing
            viewer_count=999,  # Fake viewer count
            stream_url="https://www.tiktok.com/@test/live",  # Fake stream URL
            is_host=False,  # Not a host in test
            title="🧪 Test Notification"  # Test title
        )
        
        # Check if the notification was sent successfully
        if result:
            return jsonify({'message': 'Test notification sent successfully! Check your Discord channel.'})
        else:
            # Notification failed (webhook returned False)
            return jsonify({'error': 'Failed to send test notification. Check console for details.'}), 500
    except Exception as e:
        # Catch any unexpected errors during testing
        return jsonify({'error': f'Error sending test notification: {str(e)}'}), 500

# Main entry point - only runs when script is executed directly (not imported)
if __name__ == '__main__':
    # Start the Flask development server
    # debug=True enables auto-reload on code changes and detailed error pages
    # host='0.0.0.0' makes the server accessible from any network interface
    # port=5000 is the default Flask port
    app.run(debug=True, host='0.0.0.0', port=5000)

