# Arise Medical Academy Telegram Bot

A Telegram bot that allows students to login and download recorded lecture videos from Arise Medical Academy.

## Features

- 🔐 Secure login with email and password
- 📚 View all your enrolled batches
- 📹 Download video links for recorded lectures
- 🤖 Easy-to-use Telegram interface
- ☁️ Deployable on Heroku

## Setup Instructions

### 1. Create a Telegram Bot

1. Open Telegram and search for [@BotFather](https://t.me/botfather)
2. Send `/newbot` command
3. Follow the instructions to create your bot
4. Copy the **Bot Token** (looks like: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

### 2. Deploy to Heroku

#### Option A: Using Heroku CLI

1. Install [Heroku CLI](https://devcenter.heroku.com/articles/heroku-cli)

2. Login to Heroku:
   ```bash
   heroku login
   ```

3. Create a new Heroku app:
   ```bash
   heroku create your-arise-bot
   ```

4. Set the bot token as environment variable:
   ```bash
   heroku config:set TELEGRAM_BOT_TOKEN="your_bot_token_here"
   ```

5. Deploy the app:
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git push heroku master
   ```

6. Scale the worker dyno:
   ```bash
   heroku ps:scale worker=1
   ```

#### Option B: Using Heroku Dashboard

1. Go to [Heroku Dashboard](https://dashboard.heroku.com/)
2. Click "New" → "Create new app"
3. Give it a name and click "Create app"
4. Go to "Settings" tab
5. Click "Reveal Config Vars"
6. Add a new config var:
   - KEY: `TELEGRAM_BOT_TOKEN`
   - VALUE: Your bot token from BotFather
7. Go to "Deploy" tab
8. Connect your GitHub repository or use Heroku Git
9. Deploy the branch
10. Go to "Resources" tab
11. Turn OFF the `web` dyno (if present)
12. Turn ON the `worker` dyno

## Usage

1. Open your bot in Telegram
2. Send `/start` to see available commands
3. Send `/login` to authenticate with your Arise Academy credentials
4. Send `/batches` to view your enrolled batches
5. Select a batch to get video download links
6. Send `/logout` to logout

## Commands

- `/start` - Welcome message and help
- `/login` - Login to your Arise Academy account
- `/batches` - View your enrolled batches and download videos
- `/logout` - Logout from your account
- `/help` - Show help message
- `/cancel` - Cancel current operation

## Security Notes

- Your password is deleted immediately after login
- Sessions are stored in memory (not persistent across restarts)
- For production use, consider using a database for session storage
- Never share your bot token publicly

## Troubleshooting

### Bot not responding
- Check if the worker dyno is running: `heroku ps`
- Check logs: `heroku logs --tail`

### Login fails
- Verify your Arise Academy credentials
- Check if the API is accessible

### Heroku app sleeping
- Free Heroku dynos sleep after 30 minutes of inactivity
- Consider upgrading to a paid plan for 24/7 availability

## Environment Variables

- `TELEGRAM_BOT_TOKEN` - Your Telegram bot token (required)

## Files

- `Arise.py` - Main application (works as both CLI and Telegram bot)
- `requirements.txt` - Python dependencies
- `Procfile` - Heroku process configuration
- `runtime.txt` - Python version specification

## Dual Mode Operation

The `Arise.py` script works in two modes:

1. **Telegram Bot Mode** (when `TELEGRAM_BOT_TOKEN` is set)
   - Runs as a Telegram bot on Heroku
   - Interactive interface with buttons

2. **CLI Mode** (when `TELEGRAM_BOT_TOKEN` is not set)
   - Runs as a command-line tool
   - Original functionality preserved

## License

For educational purposes only. Respect Arise Medical Academy's terms of service.
