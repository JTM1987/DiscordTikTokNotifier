# Background monitoring service that watches TikTok live streams and sends Discord notifications
import os  # For environment variables and file operations
import json  # For reading/writing user list JSON file
import time  # For tracking connection durations and cooldowns
import requests  # For checking TikTok live status via HTTP
from apscheduler.schedulers.background import BackgroundScheduler  # For scheduled background tasks
from datetime import datetime  # For timestamps
from dotenv import load_dotenv  # For loading .env file
from discord_webhook import DiscordWebhook  # For sending Discord notifications
from TikTokLive import TikTokLiveClient  # Library for connecting to TikTok live streams
from TikTokLive.events import ConnectEvent, DisconnectEvent, GiftEvent  # TikTokLive event types
import asyncio  # For async/await operations (TikTokLive uses async)
import threading  # For running async event loop in separate thread

# Load environment variables from .env file
load_dotenv()


class MonitoringService:
    """
    Service that monitors TikTok users for live streams and sends Discord notifications.
    Uses TikTokLive library to connect to TikTok's WebSocket API for real-time events.
    """
    def __init__(self):
        # Background scheduler for periodic checks (runs check_users every 30 seconds)
        self.scheduler = BackgroundScheduler()
        # Flag indicating if monitoring service is currently running
        self._running = False
        # Track last known status for each user (is_live, last_checked, etc.)
        self.last_status = {}
        # Track currently live users and their connection metadata
        self.current_live_users = {}
        # Track which user is the HOST (first to go live when multiple are live)
        self.host_priority = None
        # Track active TikTokLive client connections (username -> TikTokLiveClient)
        self.live_clients = {}
        # Async event loop for TikTokLive operations (runs in separate thread)
        self.loop = None
        # Thread that runs the async event loop
        self.loop_thread = None
        # Store webhook URL (private - set when monitoring starts)
        self._webhook_url = None
        # Store gift webhook URL (private - optional separate webhook for gifts)
        self._gift_webhook_url = None
        # Track connection attempts with timestamps to avoid spam/retry loops
        self.connection_attempts = {}
        # Track users we've sent "go live" notifications for (prevents duplicate notifications)
        self.notified_users = set()
        # Track users we've successfully connected to (received ConnectEvent from TikTokLive)
        self.successfully_connected = set()
        # Track when we successfully connected to each user (for minimum duration check)
        self.connection_start_times = {}
        # Track pending end notifications with timestamps (grace period before sending)
        self.pending_end_notifications = {}
        # Track users we've already sent end notifications for (prevents duplicate end notifications)
        self.end_notification_sent = set()
        # Track when we last sent end notification for each user (cooldown to prevent spam)
        self.end_notification_cooldown = {}

    def start(self, webhook_url=None, gift_webhook_url=None):
        """
        Start the monitoring service.
        
        Args:
            webhook_url: Discord webhook URL for live notifications
            gift_webhook_url: Optional separate Discord webhook URL for gift notifications
        """
        if not self._running:
            # Store webhook URL from parameter or environment variable
            if webhook_url:
                self._webhook_url = webhook_url
                os.environ['DISCORD_WEBHOOK_URL'] = webhook_url  # Also store in environment
            else:
                # Fall back to environment variable if not provided
                self._webhook_url = os.environ.get('DISCORD_WEBHOOK_URL', '')
            
            # Store gift webhook URL (optional - can be different channel)
            if gift_webhook_url:
                self._gift_webhook_url = gift_webhook_url
                os.environ['DISCORD_GIFT_WEBHOOK_URL'] = gift_webhook_url
            else:
                # Fall back to environment variable if not provided
                self._gift_webhook_url = os.environ.get('DISCORD_GIFT_WEBHOOK_URL', '')

            # Print configuration status for debugging
            print(f"🔧 Starting monitoring service...")
            print(f"🔧 Webhook URL: {'✅ Configured' if self._webhook_url else '❌ NOT configured'}")
            if self._webhook_url:
                print(f"🔧 Webhook URL preview: {self._webhook_url[:50]}...")  # Show first 50 chars
            print(f"🔧 Gift Webhook URL: {'✅ Configured' if self._gift_webhook_url else '❌ NOT configured (will use main webhook)'}")
            if self._gift_webhook_url:
                print(f"🔧 Gift Webhook URL preview: {self._gift_webhook_url[:50]}...")

            # Start async event loop in a separate thread
            # TikTokLive requires an async event loop, so we run it in a background thread
            self.loop = asyncio.new_event_loop()
            # Daemon thread means it will exit when main program exits
            self.loop_thread = threading.Thread(target=self._run_event_loop, daemon=True)
            self.loop_thread.start()

            # Schedule periodic user checks (runs every 30 seconds)
            # This proactively tries to connect to users' live streams
            self.scheduler.add_job(
                func=self.check_users,  # Function to run periodically
                trigger="interval",  # Run at regular intervals
                seconds=30,  # Check every 30 seconds
                id='check_users',  # Unique job ID
                replace_existing=True  # Replace if job already exists
            )
            # Start the scheduler (begins running jobs)
            self.scheduler.start()
            self._running = True
            print(f"✅ Monitoring service started")

    def _run_event_loop(self):
        """
        Run the asyncio event loop in a separate thread.
        This is required because TikTokLive uses async/await operations.
        """
        # Set this thread's event loop to our loop
        asyncio.set_event_loop(self.loop)
        # Run the event loop forever (until stop() is called)
        self.loop.run_forever()

    def stop(self):
        """Stop the monitoring service and clean up all connections"""
        if self._running:
            # Disconnect all active TikTokLive clients
            for username, client in list(self.live_clients.items()):
                try:
                    # Disconnect asynchronously (from main thread to async thread)
                    asyncio.run_coroutine_threadsafe(client.disconnect(), self.loop)
                except:
                    pass  # Ignore errors during cleanup
            self.live_clients.clear()  # Clear the clients dictionary

            # Stop the async event loop
            if self.loop:
                # Safely stop the loop from another thread
                self.loop.call_soon_threadsafe(self.loop.stop)

            # Shutdown the scheduler (stops periodic checks)
            self.scheduler.shutdown()
            self._running = False
            
            # Reset all tracking data structures
            self.last_status = {}
            self.current_live_users = {}
            self.host_priority = None
            self.notified_users.clear()
            self.successfully_connected.clear()
            self.connection_start_times.clear()
            self.pending_end_notifications.clear()
            self.end_notification_sent.clear()
            self.end_notification_cooldown.clear()
            print("Monitoring service stopped")

    def is_running(self):
        """Check if monitoring service is currently running"""
        return self._running

    def load_users(self):
        """Load monitored users from JSON file"""
        users_file = 'monitored_users.json'
        if os.path.exists(users_file):
            with open(users_file, 'r') as f:
                return json.load(f)  # Parse JSON and return list
        return []  # Return empty list if file doesn't exist

    def check_tiktok_user_live(self, username):
        """
        Check if a TikTok user is currently live (basic HTTP check).
        Note: This is a simple check - TikTokLive provides more accurate real-time status.
        
        Args:
            username: TikTok username to check
        
        Returns:
            True if user appears to be live, False otherwise
        """
        try:
            # Use TikTok's web interface to check live status
            # This is a simple HTTP check - for real-time events, we use TikTokLive
            url = f"https://www.tiktok.com/@{username}/live"
            # Set User-Agent to mimic a browser (some sites block requests without it)
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            # Make GET request (don't follow redirects - we want to see the status code)
            response = requests.get(url, headers=headers, timeout=10, allow_redirects=False)

            # If user is live, TikTok typically returns 200 or redirects
            # This is a basic check - TikTokLive will provide more accurate real-time status
            return response.status_code == 200
        except Exception as e:
            print(f"Error checking TikTok live status for {username}: {e}")
            return False

    def connect_to_live_stream(self, username):
        """
        Connect to a user's live stream using TikTokLive to monitor real-time events.
        Sets up event handlers for ConnectEvent, DisconnectEvent, and GiftEvent.
        
        Args:
            username: TikTok username to connect to
        """
        # Skip if already connected to this user
        if username in self.live_clients:
            return  # Already connected

        try:
            # Create TikTokLive client for this username
            # TikTokLive connects to TikTok's WebSocket API for real-time events
            client = TikTokLiveClient(unique_id=username)

            # Set up event handlers for TikTokLive events
            # These are async functions that get called when events occur
            
            @client.on(ConnectEvent)
            async def on_connect(event: ConnectEvent):
                """
                Event handler: Called when successfully connected to a live stream.
                This fires when the user goes live or when we connect to an active stream.
                """
                try:
                    print(f"✅ Successfully connected to {username}'s live stream!")
                    connection_time = datetime.now().isoformat()
                    # Extract room_id from event if available (TikTok's internal stream ID)
                    room_id = None
                    if event and hasattr(event, 'room_id'):
                        room_id = event.room_id

                    # Mark that we successfully connected to this user
                    # This is important for preventing false "end live" notifications
                    self.successfully_connected.add(username)
                    # Track connection start time for minimum duration check
                    # Prevents false notifications from very short connection issues
                    self.connection_start_times[username] = time.time()
                    
                    self.current_live_users[username] = {
                        'connected_at': connection_time,
                        'room_id': room_id
                    }
                    # Reset connection attempt counter on success
                    if username in self.connection_attempts:
                        del self.connection_attempts[username]

                    # Check if user just went live
                    last_status = self.last_status.get(username, {})
                    was_live = last_status.get('is_live', False)

                    # CRITICAL: Only send notification if we haven't already sent one for this live session
                    # Check notified_users FIRST to prevent ANY duplicate notifications
                    # This prevents sending multiple "go live" notifications for the same stream
                    if username in self.notified_users:
                        # Already sent notification for this live session - NEVER send again
                        print(f"🚫 {username} already has notification sent for this live session, skipping duplicate (ConnectEvent)")
                        # Still update status but don't send notification
                        self.last_status[username] = {
                            'is_live': True,
                            'last_checked': connection_time,
                            'connected_at': connection_time
                        }
                        return  # Exit early, don't process further
                    
                    # Only send notification if user wasn't previously live
                    if not was_live:
                        # User just went live - determine if HOST
                        currently_live_count = len([u for u, s in self.last_status.items() if s.get('is_live', False)])
                        is_host = currently_live_count == 0  # First to go live is HOST

                        # Get webhook URL (capture it in closure)
                        webhook_url = self._webhook_url or os.environ.get('DISCORD_WEBHOOK_URL', '')
                        print(f"🔔 Sending go live notification for {username} (webhook: {'✅' if webhook_url else '❌'})")

                        webhook = DiscordWebhook(webhook_url=webhook_url)
                        result = webhook.send_go_live_notification(
                            username=username,
                            viewer_count=0,  # Will be updated if available
                            stream_url=f"https://www.tiktok.com/@{username}/live",
                            is_host=is_host
                        )
                        if result:
                            print(f"✅ Go live notification sent for {username}")
                            # Mark that we've sent a notification for this user in this live session
                            self.notified_users.add(username)
                            # Clear any previous end notification tracking (user is live again)
                            self.end_notification_sent.discard(username)
                            if username in self.end_notification_cooldown:
                                del self.end_notification_cooldown[username]
                        else:
                            print(f"❌ Failed to send go live notification for {username} - check webhook URL")
                    elif was_live:
                        # User was already live - this is a reconnection, don't send notification
                        print(f"ℹ️  {username} was already live (reconnection detected), skipping notification")

                    # Always update status to reflect current live state
                    self.last_status[username] = {
                        'is_live': True,
                        'last_checked': connection_time,
                        'connected_at': connection_time
                    }
                except Exception as e:
                    print(f"❌ Error in on_connect handler for {username}: {e}")
                    import traceback
                    traceback.print_exc()

            @client.on(DisconnectEvent)
            async def on_disconnect(event: DisconnectEvent):
                """
                Event handler: Called when disconnected from a live stream.
                This can happen when the stream ends, connection fails, or user goes offline.
                """
                try:
                    print(f"Disconnected from {username}'s live stream")
                    # Remove from current live users tracking
                    if username in self.current_live_users:
                        del self.current_live_users[username]

                    # CRITICAL: Only send "end live" notification if ALL conditions are met:
                    # This prevents false "end live" notifications from connection issues
                    # 1. We successfully connected (received ConnectEvent)
                    # 2. We actually sent a "go live" notification
                    # 3. The user was marked as live
                    # 4. Connection lasted at least 30 seconds (prevents false notifications from connection issues)
                    # 5. We haven't already sent an end notification for this user
                    # 6. It's been at least 60 seconds since last end notification (cooldown)
                    # 7. We verify the user is actually not live anymore (double-check)
                    
                    if username in self.successfully_connected and username in self.notified_users:
                        last_status = self.last_status.get(username, {})
                        
                        # Check if we already sent an end notification
                        if username in self.end_notification_sent:
                            print(f"ℹ️  End notification already sent for {username}, skipping duplicate")
                            # Clean up but don't send again
                            self.successfully_connected.discard(username)
                            if username in self.connection_start_times:
                                del self.connection_start_times[username]
                            return
                        
                        # Check cooldown
                        last_end_time = self.end_notification_cooldown.get(username, 0)
                        time_since_last = time.time() - last_end_time if last_end_time > 0 else 999
                        if time_since_last < 60:
                            print(f"ℹ️  End notification cooldown active for {username} ({60 - int(time_since_last)}s remaining), skipping")
                            return
                        
                        # Double-check: only send if we actually marked them as live
                        if last_status.get('is_live', False):
                            # Check connection duration - if less than 30 seconds, likely a connection issue
                            connection_start = self.connection_start_times.get(username, 0)
                            connection_duration = time.time() - connection_start if connection_start > 0 else 0
                            
                            if connection_duration < 30:
                                # Connection was too short - likely a connection issue, don't send end notification
                                print(f"⚠️  Disconnected from {username} after only {connection_duration:.1f}s - likely connection issue, skipping end notification")
                                # Clean up but don't send notification
                                self.successfully_connected.discard(username)
                                if username in self.connection_start_times:
                                    del self.connection_start_times[username]
                                # Don't update status - keep as live to avoid false "ended" detection
                                return
                            
                            # Verify user is actually not live anymore before sending notification
                            # Wait a moment and check if they're still live
                            await asyncio.sleep(5)  # Wait 5 seconds
                            
                            # Check if user is still live by trying to access their live page
                            # Use asyncio to run the blocking request in a thread
                            try:
                                check_url = f"https://www.tiktok.com/@{username}/live"
                                # Run the blocking request in a thread pool
                                # Note: run_in_executor doesn't support kwargs directly, so we need to create a wrapper function
                                def check_user_live_status(url):
                                    """Helper function to check if user is still live"""
                                    return requests.get(url, timeout=5, allow_redirects=False)
                                
                                loop = asyncio.get_event_loop()
                                check_response = await loop.run_in_executor(
                                    None,
                                    check_user_live_status,
                                    check_url
                                )
                                # If we get a 200, they might still be live (or page exists)
                                # If we get a redirect or error, they're likely not live
                                if check_response.status_code == 200:
                                    # User might still be live - don't send end notification
                                    print(f"⚠️  User {username} may still be live (status code: {check_response.status_code}), skipping end notification")
                                    # Don't clean up - they might reconnect
                                    return
                            except Exception as check_error:
                                # If check fails, assume they're not live (safer to not send than send false)
                                print(f"⚠️  Could not verify live status for {username}: {check_error}, skipping end notification to avoid false positive")
                                return
                            
                            # All checks passed - send end notification
                            webhook_url = self._webhook_url or os.environ.get('DISCORD_WEBHOOK_URL', '')
                            webhook = DiscordWebhook(webhook_url=webhook_url)
                            result = webhook.send_end_live_notification(
                                username=username,
                                profile_image_url=""
                            )
                            if result:
                                print(f"✅ End live notification sent for {username} (connection duration: {connection_duration:.1f}s)")
                                # Mark that we sent the notification
                                self.end_notification_sent.add(username)
                                self.end_notification_cooldown[username] = time.time()
                            else:
                                print(f"❌ Failed to send end live notification for {username}")

                            # Remove from notified users since stream has ended
                            # This allows us to send a new notification if they go live again
                            self.notified_users.discard(username)
                            
                            # Only update status to not live if we actually sent the notification
                            self.last_status[username] = {
                                'is_live': False,
                                'last_checked': datetime.now().isoformat()
                            }
                        
                        # Remove from successfully connected since we've handled the disconnect
                        self.successfully_connected.discard(username)
                        if username in self.connection_start_times:
                            del self.connection_start_times[username]
                    else:
                        # Connection failed before we could successfully connect or send a notification
                        if username not in self.successfully_connected:
                            print(f"ℹ️  Disconnected from {username} before successful connection (connection likely failed)")
                        else:
                            print(f"ℹ️  Disconnected from {username} but no notification was sent (skipping end notification)")
                        
                        # Clean up connection tracking
                        self.successfully_connected.discard(username)
                        if username in self.connection_start_times:
                            del self.connection_start_times[username]
                        # Don't update last_status to is_live=False if we never notified
                        # This prevents check_users from thinking the stream ended

                    # Remove from clients if still there
                    if username in self.live_clients:
                        del self.live_clients[username]
                except Exception as e:
                    print(f"❌ Error in on_disconnect handler for {username}: {e}")
                    import traceback
                    traceback.print_exc()

            @client.on(GiftEvent)
            async def on_gift(event: GiftEvent):
                """
                Event handler: Called when a gift is received during the live stream.
                This provides real-time gift notifications from TikTok's WebSocket.
                """
                try:
                    # Default values in case event data is missing
                    gift_name = 'Gift'
                    repeat_count = 1
                    gifter_username = ''

                    # Extract gift information from event - try multiple possible attribute names
                    # First try to get gift object from event
                    gift = None
                    if event and hasattr(event, 'gift'):
                        gift = event.gift
                    elif event:
                        # Sometimes gift info might be directly on the event
                        gift = event
                    
                    if gift:
                        # Try multiple possible attribute names for gift name
                        # TikTokLive may use different attribute names in different versions
                        gift_name = (
                            getattr(gift, 'name', None) or  # Most common: gift.name
                            getattr(gift, 'gift_name', None) or  # Alternative: gift.gift_name
                            getattr(gift, 'giftName', None) or  # CamelCase variant
                            getattr(gift, 'giftId', None) or  # Sometimes only ID is available
                            getattr(gift, 'gift_id', None) or  # Snake_case variant
                            None
                        )
                        
                        # If we still don't have a name, try accessing as dictionary (some libraries use dict-like access)
                        if not gift_name and hasattr(gift, '__dict__'):
                            gift_dict = gift.__dict__
                            gift_name = (
                                gift_dict.get('name') or
                                gift_dict.get('gift_name') or
                                gift_dict.get('giftName') or
                                None
                            )
                        
                        # Final fallback
                        if not gift_name:
                            gift_name = 'Gift'
                        
                        # If we only got an ID, try to get a more descriptive name
                        if gift_name and (str(gift_name).isdigit() or 'id' in str(gift_name).lower()):
                            # If it's just an ID, try to get the actual name
                            actual_name = getattr(gift, 'name', None) or getattr(gift, 'gift_name', None)
                            if actual_name:
                                gift_name = actual_name
                        
                        # Extract repeat count (number of gifts sent in this batch)
                        repeat_count = (
                            getattr(gift, 'repeat_count', None) or
                            getattr(gift, 'repeatCount', None) or
                            getattr(gift, 'count', None) or
                            getattr(gift, 'amount', None) or
                            (gift.__dict__.get('repeat_count') if hasattr(gift, '__dict__') else None) or
                            (gift.__dict__.get('count') if hasattr(gift, '__dict__') else None) or
                            1  # Default to 1 if not found
                        )
                        
                        # Debug: Print available gift attributes to help troubleshoot
                        if gift_name == 'Gift':
                            # If we couldn't find the name, log available attributes for debugging
                            gift_attrs = [attr for attr in dir(gift) if not attr.startswith('_')]
                            print(f"⚠️  Could not extract gift name for {username}. Available attributes: {gift_attrs[:10]}")
                            # Also try to print the gift object itself for debugging
                            try:
                                print(f"   Gift object type: {type(gift)}, Gift object: {str(gift)[:200]}")
                            except:
                                pass

                    # Extract gifter (person who sent the gift) information
                    if event and hasattr(event, 'user'):
                        gifter = event.user
                        # Try multiple possible attribute names for username
                        gifter_username = (
                            getattr(gifter, 'unique_id', None) or
                            getattr(gifter, 'uniqueId', None) or
                            getattr(gifter, 'nickname', None) or
                            getattr(gifter, 'name', None) or
                            ''
                        ) if gifter else ''

                    print(f"🎁 {username} received gift: {gift_name} x{repeat_count}" + (f" from @{gifter_username}" if gifter_username else ""))
                    
                    # Get webhook URLs (use stored or environment variables)
                    webhook_url = self._webhook_url or os.environ.get('DISCORD_WEBHOOK_URL', '')
                    gift_webhook_url = self._gift_webhook_url or os.environ.get('DISCORD_GIFT_WEBHOOK_URL', '')
                    # Create webhook instance and send gift notification
                    webhook = DiscordWebhook(webhook_url=webhook_url, gift_webhook_url=gift_webhook_url)
                    result = webhook.send_gift_notification(
                        username=username,
                        gift_type=gift_name,
                        gift_amount=repeat_count,
                        gifter_username=gifter_username
                    )
                    if result:
                        print(f"✅ Gift notification sent for {username}: {gift_name} x{repeat_count}")
                    else:
                        print(f"❌ Failed to send gift notification for {username}")
                except Exception as e:
                    print(f"❌ Error in on_gift handler for {username}: {e}")
                    import traceback
                    traceback.print_exc()

            # Note: TikTokLive may not have separate LiveEvent/LiveEndEvent
            # The ConnectEvent fires when user goes live, DisconnectEvent when they end

            # Connect to the live stream asynchronously
            # TikTokLive uses async operations, so we need to run it in the async event loop
            if self.loop and self.loop.is_running():
                print(f"🔗 Starting TikTokLive connection for {username}...")
                try:
                    # Create a wrapper function to handle connection errors gracefully
                    async def start_connection():
                        try:
                            # Start the TikTokLive client (connects to WebSocket)
                            await client.start()
                        except Exception as e:
                            print(f"❌ TikTokLive connection failed for {username}: {e}")
                            # Remove from clients if connection failed
                            if username in self.live_clients:
                                del self.live_clients[username]
                            # Clean up connection tracking - connection never succeeded
                            self.successfully_connected.discard(username)
                            if username in self.connection_start_times:
                                del self.connection_start_times[username]
                            # Also remove from connection attempts so we can retry
                            if username in self.connection_attempts:
                                # Reset after a delay to allow retry
                                pass

                    # Run the async connection function in the event loop thread
                    # run_coroutine_threadsafe allows calling async code from sync code
                    future = asyncio.run_coroutine_threadsafe(start_connection(), self.loop)
                    # Store the client so we can disconnect it later
                    self.live_clients[username] = client
                    # Note: Connection happens in background, errors are handled in start_connection
                except Exception as e:
                    print(f"❌ Error setting up connection for {username}: {e}")
                    import traceback
                    traceback.print_exc()
            else:
                print(f"❌ Event loop not running, cannot connect to {username}")

        except Exception as e:
            print(f"❌ Error setting up connection for {username}: {e}")
            import traceback
            traceback.print_exc()

    def check_users(self):
        """
        Periodic check function (runs every 30 seconds via scheduler).
        Checks all monitored users and proactively tries to connect to their live streams.
        This is a fallback mechanism - most notifications come from TikTokLive event handlers.
        """
        # Load the list of users to monitor from JSON file
        users = self.load_users()

        # Use stored webhook URL or get from environment
        # This ensures we have a webhook URL even if it was set after service started
        if not self._webhook_url:
            self._webhook_url = os.environ.get('DISCORD_WEBHOOK_URL', '')

        # Validate that webhook URL is configured
        if not self._webhook_url:
            print("⚠️ Warning: Discord webhook URL not configured")
            return

        # Skip if no users to monitor
        if not users:
            return

        currently_live = {}
        new_live_users = []
        ended_live_users = []

        # Check each user
        for user_data in users:
            username = user_data.get('username', '').strip()
            if not username:
                continue

            # Remove @ if present
            if username.startswith('@'):
                username = username[1:]

            last_status = self.last_status.get(username, {})
            was_live = last_status.get('is_live', False)

            # Check if user is live based on TikTokLive connection status
            is_live = username in self.current_live_users

            # Proactively try to connect to TikTokLive for all users
            # TikTokLive will fail gracefully if user is not live
            # Only attempt connection if not already connected and not recently attempted
            last_attempt = self.connection_attempts.get(username, 0)
            time_since_attempt = time.time() - last_attempt if last_attempt else 999

            if username not in self.live_clients and time_since_attempt > 15:  # Wait 15 seconds between attempts
                print(f"🔍 Attempting to connect to {username}'s live stream...")
                self.connection_attempts[username] = time.time()
                self.connect_to_live_stream(username)
            elif not is_live and username in self.live_clients:
                # User was connected but is no longer live - disconnect
                print(f"🔌 Disconnecting from {username} (no longer live)")
                try:
                    client = self.live_clients[username]
                    asyncio.run_coroutine_threadsafe(client.disconnect(), self.loop)
                    del self.live_clients[username]
                except Exception as e:
                    print(f"Error disconnecting from {username}: {e}")

            if is_live:
                currently_live[username] = {
                    'connected_at': self.current_live_users.get(username, {}).get('connected_at',
                                                                                  datetime.now().isoformat())
                }

                # User just went live (detected via TikTokLive connection)
                # Only add to new_live_users if:
                # 1. They weren't previously live, AND
                # 2. We haven't already sent a notification for this live session
                if not was_live and username not in self.notified_users:
                    new_live_users.append({
                        'username': username,
                        'connected_at': currently_live[username]['connected_at']
                    })
                    print(f"🟢 {username} is now LIVE!")
                elif was_live and username in self.notified_users:
                    # User is already live and we've already notified - skip
                    print(f"ℹ️  {username} is already live and already notified, skipping duplicate detection")
                elif not was_live and username in self.notified_users:
                    # Edge case: user wasn't live but is in notified_users (shouldn't happen, but clean up)
                    print(f"⚠️  {username} was in notified_users but wasn't live - cleaning up")
                    self.notified_users.discard(username)
                    new_live_users.append({
                        'username': username,
                        'connected_at': currently_live[username]['connected_at']
                    })
                    print(f"🟢 {username} is now LIVE! (after cleanup)")
                
                # Always update last status to reflect they are live
                self.last_status[username] = {
                    'is_live': True,
                    'last_checked': datetime.now().isoformat()
                }
            else:
                # User is not currently live
                # Only mark as "ended" if:
                # 1. We previously marked them as live
                # 2. We successfully connected (not just a failed attempt)
                # 3. We actually sent a notification
                # 4. They're not in live_clients (disconnect already handled)
                if (was_live and 
                    username in self.successfully_connected and 
                    username in self.notified_users and
                    username not in self.live_clients):
                    # Check connection duration before marking as ended
                    connection_start = self.connection_start_times.get(username, 0)
                    connection_duration = time.time() - connection_start if connection_start > 0 else 0
                    
                    if connection_duration >= 10:
                        # Connection was long enough - this might be a real end
                        ended_live_users.append({
                            'username': username
                        })
                        print(f"🔴 {username} ended their stream (detected via polling, duration: {connection_duration:.1f}s)")
                    else:
                        # Connection was too short - likely a false positive, don't mark as ended
                        print(f"⚠️  {username} appears ended but connection was too short ({connection_duration:.1f}s) - ignoring")
                        # Don't update status - keep as live to avoid false notifications
                elif was_live and username not in self.notified_users:
                    # User was marked as live but we never sent a notification
                    # This was likely a failed connection, just reset the status
                    print(f"ℹ️  {username} was marked live but never notified - resetting status")
                    self.last_status[username] = {
                        'is_live': False,
                        'last_checked': datetime.now().isoformat()
                    }
                    # Clean up tracking
                    self.successfully_connected.discard(username)
                    if username in self.connection_start_times:
                        del self.connection_start_times[username]
                else:
                    # User is not live and wasn't live before - no change
                    self.last_status[username] = {
                        'is_live': False,
                        'last_checked': datetime.now().isoformat()
                    }

        # Determine HOST: the user with the earliest connection time among all currently live users
        if currently_live:
            # Find the HOST (earliest connection)
            host_username = None
            earliest_connection = None

            for username, data in currently_live.items():
                connection_time = data.get('connected_at', '')
                if earliest_connection is None or connection_time < earliest_connection:
                    earliest_connection = connection_time
                    host_username = username

            self.host_priority = host_username
        else:
            self.host_priority = None

        # Handle HOST priority: if multiple users are live, only notify for the HOST
        # Filter new_live_users to only include the HOST if multiple users are live
        users_to_notify = []

        if len(currently_live) > 1:
            # Multiple users live - only notify if the new user is the HOST
            for user_data in new_live_users:
                if user_data['username'] == self.host_priority:
                    users_to_notify.append(user_data)
                    print(f"Multiple users live - notifying only for HOST: {user_data['username']}")
        else:
            # Only one user live (or none) - notify normally
            users_to_notify = new_live_users

        # Send notifications for new live streams (only HOST if multiple)
        # Note: Most notifications are handled by TikTokLive event handlers above
        # This section handles cases where we detect live status via polling
        webhook = DiscordWebhook(webhook_url=self._webhook_url or os.environ.get('DISCORD_WEBHOOK_URL', ''))

        for user_data in users_to_notify:
            username = user_data['username']
            is_host = (self.host_priority == username and len(currently_live) > 1)

            # CRITICAL: Only send notification if we haven't already sent one for this live session
            # This is the final check to prevent ANY duplicate notifications
            if username in self.notified_users:
                # Already sent notification for this live session - NEVER send again
                print(f"🚫 {username} already has notification sent for this live session, skipping duplicate (already in notified_users)")
                continue  # Skip to next user
            
            # Only send notification if we haven't already sent via TikTokLive connection
            if username not in self.live_clients:
                result = webhook.send_go_live_notification(
                    username=username,
                    viewer_count=0,
                    stream_url=f"https://www.tiktok.com/@{username}/live",
                    is_host=is_host
                )
                if result:
                    print(f"✅ Go live notification sent for {username}")
                    # Mark that we've sent a notification for this user in this live session
                    # This prevents ANY future notifications until stream ends
                    self.notified_users.add(username)
                else:
                    print(f"❌ Failed to send go live notification for {username}")
            else:
                # User is in live_clients, notification should have been sent by ConnectEvent handler
                print(f"ℹ️  {username} is in live_clients, notification should be handled by ConnectEvent")

        # DISABLED: Polling-based end notifications are completely disabled
        # End notifications should ONLY come from DisconnectEvent handler
        # Polling can have false positives and should not trigger end notifications
        # This prevents false "end live" notifications from polling detection
        # The DisconnectEvent handler has proper verification and cooldown mechanisms
        for user_data in ended_live_users:
            username = user_data['username']
            # Do NOT send end notifications from polling - only log for debugging
            if username in self.notified_users:
                print(f"ℹ️  Polling detected {username} as ended, but end notifications are disabled from polling (only DisconnectEvent sends them)")
                # Don't send notification - let DisconnectEvent handle it if it's real

        # Update current live users
        self.current_live_users = currently_live

