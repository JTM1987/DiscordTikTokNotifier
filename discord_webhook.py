# Discord webhook integration for sending notifications to Discord channels
import os  # For accessing environment variables
import requests  # For making HTTP POST requests to Discord webhook URLs
from datetime import datetime  # For timestamping notifications
import random  # For generating random colors for embeds
try:
    from zoneinfo import ZoneInfo  # Python 3.9+ timezone support
    HAS_ZONEINFO = True
except ImportError:
    # Fallback for Python < 3.9 using pytz
    import pytz
    HAS_ZONEINFO = False

class DiscordWebhook:
    """
    Handles sending formatted notifications to Discord via webhooks.
    Supports separate webhooks for live notifications and gift notifications.
    """
    def __init__(self, webhook_url=None, gift_webhook_url=None):
        # Get webhook URL from parameter, or fall back to environment variable
        # Main webhook URL for live stream notifications
        self.webhook_url = webhook_url or os.environ.get('DISCORD_WEBHOOK_URL', '')
        # Optional separate webhook URL for gift notifications (can be different channel)
        self.gift_webhook_url = gift_webhook_url or os.environ.get('DISCORD_GIFT_WEBHOOK_URL', '')
    
    def _generate_random_color(self):
        """
        Generate a random color for Discord embeds.
        Returns a random hex color value (0x000000 to 0xFFFFFF).
        """
        # Generate random RGB values and convert to hex
        # Using vibrant colors (avoiding too dark/light colors for better visibility)
        r = random.randint(50, 255)  # Red component (50-255 for vibrancy)
        g = random.randint(50, 255)  # Green component (50-255 for vibrancy)
        b = random.randint(50, 255)  # Blue component (50-255 for vibrancy)
        # Convert RGB to hex color format (0xRRGGBB)
        return (r << 16) | (g << 8) | b
    
    def _get_eastern_time(self):
        """
        Get the current time in Eastern Time (handles EST/EDT automatically).
        Returns a timezone-aware datetime object in Eastern Time.
        """
        # Get current time in Eastern Time (America/New_York handles DST automatically)
        if HAS_ZONEINFO:
            # Python 3.9+ with zoneinfo
            eastern_tz = ZoneInfo('America/New_York')
            return datetime.now(eastern_tz)
        else:
            # Fallback for pytz (Python < 3.9)
            eastern_tz = pytz.timezone('America/New_York')
            return datetime.now(eastern_tz)
    
    def send(self, embed, mention_everyone=False):
        """
        Send an embed message to Discord webhook.
        
        Args:
            embed: Dictionary containing Discord embed data (title, description, color, etc.)
            mention_everyone: If True, adds @everyone mention to the message
        
        Returns:
            True if sent successfully, False otherwise
        """
        # Validate that webhook URL is configured
        if not self.webhook_url:
            print("Warning: Discord webhook URL not configured")
            return False
        
        # Build the payload for Discord webhook API
        # Discord webhooks accept JSON with 'embeds' array
        payload = {
            'embeds': [embed]  # Discord expects an array of embed objects
        }
        
        # Add @everyone mention if requested (pings everyone in the channel)
        if mention_everyone:
            payload['content'] = '@everyone'
        
        try:
            # Send POST request to Discord webhook URL
            # timeout=10 prevents hanging if Discord is unreachable
            response = requests.post(self.webhook_url, json=payload, timeout=10)
            # Raise exception if HTTP status code indicates error (4xx, 5xx)
            response.raise_for_status()
            print(f"✅ Discord webhook sent successfully (status: {response.status_code})")
            return True
        except requests.exceptions.HTTPError as e:
            # HTTP error (e.g., 404 webhook not found, 401 unauthorized)
            print(f"❌ HTTP Error sending Discord webhook: {e}")
            print(f"   Response: {e.response.text if hasattr(e, 'response') else 'N/A'}")
            return False
        except requests.exceptions.RequestException as e:
            # Network error (connection failed, timeout, etc.)
            print(f"❌ Request Error sending Discord webhook: {e}")
            return False
        except Exception as e:
            # Catch any other unexpected errors
            print(f"❌ Unexpected error sending Discord webhook: {e}")
            return False
    
    def send_go_live_notification(self, username, viewer_count=0, stream_url="", is_host=False, title=""):
        """
        Send notification when a user goes live on TikTok.
        
        Args:
            username: TikTok username of the streamer
            viewer_count: Current number of viewers (optional)
            stream_url: Direct URL to the live stream (optional)
            is_host: If True, marks this user as the HOST (first to go live)
            title: Optional stream title/description
        
        Returns:
            True if sent successfully, False otherwise
        """
        # Generate a random vibrant color for this notification
        random_color = self._generate_random_color()
        
        # Build the notification description
        description = f'**{username}** is now streaming live on TikTok!'
        # Prepend title if provided
        if title:
            description = f'**{title}**\n\n{description}'
        
        # If this user is the HOST, add HOST indicator
        # HOST is the first user to go live when multiple users are live
        if is_host:
            description = f'🏠 **HOST** - {description}'
        
        # Build fields array for additional information
        fields = []
        
        # Add viewer count as a field if available
        if viewer_count > 0:
            fields.append({
                'name': '👁️ Viewers',
                'value': f'{viewer_count:,}',  # Format with commas for large numbers
                'inline': True  # Display inline (side by side if space allows)
            })
        
        # Add stream link as a field
        stream_link = stream_url or f'https://www.tiktok.com/@{username}/live'
        fields.append({
            'name': '🔗 Stream Link',
            'value': f'[Watch Live]({stream_link})',  # Clickable link in Discord
            'inline': True
        })
        
        # Add profile link
        fields.append({
            'name': '👤 Profile',
            'value': f'[@{username}](https://www.tiktok.com/@{username})',
            'inline': True
        })
        
        # Create robust Discord embed object with enhanced details
        embed = {
            'title': f'🔴 {username} is now LIVE on TikTok!',  # Embed title (shown at top)
            'description': description,  # Main message content
            'url': stream_link,  # Clickable link (makes title clickable)
            'color': random_color,  # Random vibrant color for each notification
            'author': {
                'name': f'@{username}',
                'url': f'https://www.tiktok.com/@{username}',
                'icon_url': 'https://www.tiktok.com/favicon.ico'  # TikTok icon
            },
            'fields': fields,  # Additional structured information
            'thumbnail': {
                'url': f'https://www.tiktok.com/api/img/?itemId={username}'  # Profile thumbnail (if available)
            },
            'footer': {
                'text': f'Discord TikTok Notifier • {self._get_eastern_time().strftime("%Y-%m-%d %H:%M:%S")}',
                'icon_url': 'https://www.tiktok.com/favicon.ico'
            },
            'timestamp': self._get_eastern_time().isoformat()  # ISO timestamp in Eastern Time for Discord's time display
        }
        
        # Send the embed with @everyone mention to alert all users
        return self.send(embed, mention_everyone=True)
    
    def send_end_live_notification(self, username, profile_image_url=''):
        """
        Send notification when a user ends their TikTok live stream.
        
        Args:
            username: TikTok username of the streamer who ended their stream
            profile_image_url: Optional profile image URL to display in embed
        
        Returns:
            True if sent successfully, False otherwise
        """
        # Generate a random color for this notification (different from go-live)
        random_color = self._generate_random_color()
        
        # Build fields array for additional information
        fields = [
            {
                'name': '📊 Status',
                'value': 'Stream Ended',
                'inline': True
            },
            {
                'name': '👤 Profile',
                'value': f'[@{username}](https://www.tiktok.com/@{username})',
                'inline': True
            },
            {
                'name': '⏰ Ended At',
                'value': self._get_eastern_time().strftime('%H:%M:%S'),
                'inline': True
            }
        ]
        
        # Create robust Discord embed for end-of-stream notification
        embed = {
            'title': f'⚫ {username} ended their TikTok live stream',  # Notification title
            'description': f'**{username}** has ended their live stream on TikTok.',  # Description text
            'url': f'https://www.tiktok.com/@{username}',  # Clickable link to profile
            'color': random_color,  # Random color for each notification
            'author': {
                'name': f'@{username}',
                'url': f'https://www.tiktok.com/@{username}',
                'icon_url': 'https://www.tiktok.com/favicon.ico'
            },
            'fields': fields,  # Additional structured information
            'footer': {
                'text': f'Discord TikTok Notifier • {self._get_eastern_time().strftime("%Y-%m-%d %H:%M:%S")}',
                'icon_url': 'https://www.tiktok.com/favicon.ico'
            },
            'timestamp': self._get_eastern_time().isoformat()  # ISO timestamp in Eastern Time
        }
        
        # Add profile image thumbnail if URL is provided
        if profile_image_url:
            embed['thumbnail'] = {'url': profile_image_url}
        else:
            # Use default TikTok icon if no profile image
            embed['thumbnail'] = {
                'url': 'https://www.tiktok.com/favicon.ico'
            }
        
        # Send with @everyone mention to notify users
        return self.send(embed, mention_everyone=True)
    
    def send_gift_notification(self, username, gift_type, gift_amount, gifter_username=''):
        """
        Send notification when a user receives a gift on TikTok during a live stream.
        
        Args:
            username: TikTok username of the streamer who received the gift
            gift_type: Type/name of the gift (e.g., "Rose", "Heart")
            gift_amount: Number of gifts received
            gifter_username: Optional username of the person who sent the gift
        
        Returns:
            True if sent successfully, False otherwise
        """
        # Generate a random vibrant color for this gift notification
        random_color = self._generate_random_color()
        
        # Build description with gift details - show gift name if available
        if gift_type and gift_type != 'Gift':
            # We have a specific gift name, use it in the description
            description = f'**{username}** received **{gift_type}** during their live stream!'
        else:
            # Generic gift (name not available)
            description = f'**{username}** received a gift during their live stream!'
        
        # Build fields array for structured gift information
        fields = [
            {
                'name': '🎁 Gift Type',
                'value': gift_type,
                'inline': True
            },
            {
                'name': '📦 Quantity',
                'value': f'{gift_amount:,}',  # Format with commas
                'inline': True
            }
        ]
        
        # Add gifter information if available
        if gifter_username:
            fields.append({
                'name': '👤 From',
                'value': f'[@{gifter_username}](https://www.tiktok.com/@{gifter_username})',
                'inline': True
            })
        
        # Add streamer profile link
        fields.append({
            'name': '📺 Streamer',
            'value': f'[@{username}](https://www.tiktok.com/@{username})',
            'inline': True
        })
        
        # Add live stream link
        fields.append({
            'name': '🔴 Watch Live',
            'value': f'[Join Stream](https://www.tiktok.com/@{username}/live)',
            'inline': True
        })
        
        # Create robust Discord embed for gift notification
        embed = {
            'title': f'🎁 Gift Received on TikTok!',  # Notification title
            'description': description,  # Gift details
            'url': f'https://www.tiktok.com/@{username}/live',  # Clickable link to live stream
            'color': random_color,  # Random vibrant color for each notification
            'author': {
                'name': f'@{username}',
                'url': f'https://www.tiktok.com/@{username}',
                'icon_url': 'https://www.tiktok.com/favicon.ico'
            },
            'fields': fields,  # Structured gift information
            'thumbnail': {
                'url': 'https://www.tiktok.com/favicon.ico'  # Gift icon placeholder
            },
            'footer': {
                'text': f'Discord TikTok Notifier • {self._get_eastern_time().strftime("%Y-%m-%d %H:%M:%S")}',
                'icon_url': 'https://www.tiktok.com/favicon.ico'
            },
            'timestamp': self._get_eastern_time().isoformat()  # ISO timestamp in Eastern Time
        }
        
        # Use gift webhook URL if available (allows sending gifts to different channel)
        # Otherwise fall back to main webhook URL
        original_webhook_url = self.webhook_url  # Save current webhook URL
        if self.gift_webhook_url:
            # Temporarily switch to gift webhook URL
            self.webhook_url = self.gift_webhook_url
        
        # Send notification (without @everyone mention - gifts are less urgent)
        result = self.send(embed, mention_everyone=False)
        
        # Restore original webhook URL so future live notifications go to main webhook
        self.webhook_url = original_webhook_url
        
        return result

