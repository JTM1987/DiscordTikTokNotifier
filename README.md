# Discord TikTok Notifier

A web application that monitors TikTok live streamers and sends Discord webhook notifications when they go live, end their stream, or receive gifts. Features a user-friendly web UI for managing monitored users.

## Features

- 🔴 **Go Live Notifications**: Get notified when monitored users start streaming live on TikTok
- ⚫ **End Stream Notifications**: Know when live streams end
- 🎁 **Gift Notifications**: Real-time notifications when users receive gifts during live streams
- 🏠 **HOST Priority**: If multiple users are live, only the HOST (first to go live) triggers a notification
- 🎨 **Modern Web UI**: Easy-to-use interface for managing monitored users
- ⚙️ **Real-time Monitoring**: Uses TikTokLive library for real-time event monitoring

## Prerequisites

- Python 3.8 or higher
- Discord webhook URL
- TikTok usernames to monitor (no API credentials needed!)

## Setup

1. **Clone or download this repository**

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Create a Discord webhook**:
   - Go to your Discord server settings
   - Navigate to Integrations → Webhooks
   - Create a new webhook
   - Copy the webhook URL

4. **Set environment variables** (optional, can also be set in the UI):
   ```bash
   # Windows PowerShell
   $env:DISCORD_WEBHOOK_URL="your_webhook_url"
   
   # Linux/Mac
   export DISCORD_WEBHOOK_URL="your_webhook_url"
   ```

   Or create a `.env` file:
   ```
   DISCORD_WEBHOOK_URL=your_webhook_url
   ```

## Usage

1. **Start the application**:
   ```bash
   python app.py
   ```

2. **Open your browser** and navigate to:
   ```
   http://localhost:5000
   ```

3. **Add users to monitor**:
   - Enter TikTok usernames (without the @ symbol) in the "Add TikTok Username" field
   - Click "Add User"

4. **Configure Discord webhook**:
   - Enter your Discord webhook URL in the "Discord Webhook URL" field

5. **Start monitoring**:
   - Click "Start Monitoring"
   - The app will connect to TikTok live streams and monitor for events in real-time

## How It Works

- The monitoring service uses the `TikTokLive` library to connect to TikTok's live stream WebSocket
- When a user goes live, the app automatically connects and listens for events
- Real-time gift notifications are received directly from TikTok's live stream events
- If multiple users are live simultaneously, only the HOST (earliest to go live) triggers a notification
- When a stream ends, an end-of-stream notification is sent
- User list is saved to `monitored_users.json`

## Gift Notifications

Gift notifications work automatically! The TikTokLive library connects directly to TikTok's live stream WebSocket and receives gift events in real-time. No additional setup required.

## File Structure

```
DiscordNotifier/
├── app.py                 # Flask web application
├── monitoring_service.py   # Background monitoring service with TikTokLive
├── discord_webhook.py     # Discord webhook integration
├── templates/
│   └── index.html         # Web UI
├── requirements.txt       # Python dependencies
├── README.md             # This file
└── monitored_users.json  # Stored user list (created automatically)
```

## Configuration

- **Check Interval**: Background checks every 30 seconds (real-time events via WebSocket)
- **Port**: Default is 5000 (can be changed in `app.py`)

## Troubleshooting

- **"Discord webhook URL not configured"**: Enter the webhook URL in the UI or set `DISCORD_WEBHOOK_URL` environment variable
- **Notifications not sending**: Check that your Discord webhook URL is valid and the webhook hasn't been deleted
- **Connection errors**: Make sure the TikTok username is correct (without @ symbol) and the user is currently live or has gone live recently
- **Gift notifications not working**: Ensure the user is actually live and receiving gifts. The TikTokLive library connects to active live streams.

## Notes

- TikTok usernames should be entered without the @ symbol
- The app connects to live streams in real-time, so users must be live (or have recently gone live) for the connection to work
- TikTokLive library uses TikTok's internal WebSocket API, which may be subject to changes



