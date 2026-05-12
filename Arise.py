import requests
import json
import os
import sys
import time
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- SYSTEM CONFIGURATION ---
BASE_URL = "https://api.arisemedicalacademy.com/instituteApp"
SESSION_FILE = "auth_session.json"

# Conversation states for Telegram bot
EMAIL, PASSWORD = range(2)

HEADERS = {
    "Host": "api.arisemedicalacademy.com",
    "deviceinfo": '{"model":"Pixel 10 Pro","osVersion":"16","manufacturer":"Google","platform":"android"}',
    "appid": "mobile",
    "tenantid": "43IBMyiA8DBM",
    "sec-ch-ua-platform": '"Android"',
    "sec-ch-ua": '"Android WebView";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    "sec-ch-ua-mobile": "?1",
    "devicetype": "android",
    "deviceid": "f4a2b8e9d0c1b3a5",
    "appversion": "1.5.4",
    "user-agent": "Mozilla/5.0 (Linux; Android 16; Pixel 10 Pro Build/AP2A.260505.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/147.0.0.0 Mobile Safari/537.36",
    "accept": "application/json, text/plain, */*",
    "origin": "https://localhost",
    "x-requested-with": "com.arisemobile.app",
    "sec-fetch-site": "cross-site",
    "sec-fetch-mode": "cors",
    "sec-fetch-dest": "empty",
    "referer": "https://localhost/",
    "accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
    "priority": "u=1, i",
    "content-type": "application/json"
}

class AriseHarvester:
    def __init__(self):
        self.session = self._load_session()
        self.auth_headers = HEADERS.copy()
        if self.session:
            self._apply_session()

    def _load_session(self):
        if os.path.exists(SESSION_FILE):
            with open(SESSION_FILE, 'r') as f:
                return json.load(f)
        return None

    def _apply_session(self):
        self.auth_headers.update({
            "authorization": f"Bearer {self.session['token']}",
            "useremail": self.session['email']
        })

    def login(self):
        print("\n" + "═"*50 + "\n  ARISE ACADEMY: SYSTEM AUTHENTICATION\n" + "═"*50)
        email = input("  User Email    : ").strip()
        password = input("  User Password : ").strip()
        
        payload = {"userEmail": email, "password": password}
        try:
            resp = requests.post(f"{BASE_URL}/auth/loginV2", headers=HEADERS, json=payload).json()
            if resp.get("status") == "OK":
                self.session = {"email": email, "token": resp.get("result")}
                with open(SESSION_FILE, 'w') as f:
                    json.dump(self.session, f)
                self._apply_session()
                print("  [✓] Authentication Successful.")
                return True
            print(f"  [!] Login Failed: {resp.get('msg')}")
        except Exception as e:
            print(f"  [!] Connection Error: {e}")
        return False

    def run(self):
        if not self.session and not self.login():
            return

        # 1. BATCH DISCOVERY
        try:
            print("\n[*] Synchronizing Batch List...")
            batch_resp = requests.get(f"{BASE_URL}/student/getBatchesByStudent", headers=self.auth_headers).json()
            batches = batch_resp.get("result", [])
            if not batches:
                print("    [!] No active batches found.")
                return
            
            for i, b in enumerate(batches):
                print(f"    [{i}] {b['batch']} ({b['batchId']})")
            
            idx = int(input("\nSelect Batch Index: "))
            batch_id = batches[idx]['batchId']
        except Exception:
            print("    [!] Invalid Selection.")
            return

        master_file = f"links_{batch_id}.txt"
        all_videos = []

        # 2. REMOTE CATALOG RETRIEVAL
        print(f"\n[*] Fetching remote catalog for {batch_id}...")
        resp = requests.get(f"{BASE_URL}/liveclass/getRecordedClasses/{batch_id}", headers=self.auth_headers).json()
        data = resp.get("result")

        if isinstance(data, list):
            all_videos = data
        elif isinstance(data, dict):
            for subj in data:
                if isinstance(data[subj], list):
                    all_videos.extend(data[subj])
        
        if not all_videos:
            print("    [!] No recorded content available in this batch.")
            return

        # 3. LOCAL CACHE ANALYSIS
        known_data = {}
        if os.path.exists(master_file):
            with open(master_file, "r") as f:
                for line in f:
                    if "ID:" in line:
                        parts = line.strip().split("| ID:")
                        if len(parts) > 1:
                            known_data[parts[1].strip()] = line.strip()

        # 4. INTELLIGENT SYNC (Skip Existing / Fetch New)
        print(f"[*] Found {len(all_videos)} items remotely. Local cache contains {len(known_data)} items.")
        
        delta_file = f"new_links_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        master_buffer = []
        new_count = 0

        print(" " + "─"*50)
        for i, item in enumerate(all_videos):
            c_id = str(item.get('recordedClassId'))
            if not c_id:
                continue

            if c_id in known_data:
                # Use cached data - No API call needed
                master_buffer.append(known_data[c_id])
            else:
                # Fetch new details
                det_url = f"{BASE_URL}/liveclass/getRecordedClassDetails/{batch_id}?recordedClassId={c_id}"
                try:
                    det_resp = requests.get(det_url, headers=self.auth_headers).json()
                    det_data = det_resp.get("result", {})
                    
                    stream_url = det_data.get("streamUrl", "N/A")
                    class_name = item.get('recordedClassName', 'Unknown_Video')
                    line = f"{class_name} : {stream_url} | ID:{c_id}"
                    
                    master_buffer.append(line)
                    with open(delta_file, "a") as f:
                        f.write(line + "\n")
                    
                    print(f"\n    [NEW] {class_name}")
                    new_count += 1
                except:
                    continue

            # Stylish progress bar
            sys.stdout.write(f"\r    Syncing: [{'#' * ((i+1)*20//len(all_videos))}{'.' * (20 - ((i+1)*20//len(all_videos)))}] {i+1}/{len(all_videos)}")
            sys.stdout.flush()

        # FINALIZING
        with open(master_file, "w") as f:
            f.write("\n".join(master_buffer))
            
        print("\n " + "─"*50)
        print(f"\n[✓] Sync Complete.")
        print(f"    Total Videos: {len(all_videos)}")
        print(f"    New Harvested: {new_count}")
        if new_count > 0:
            print(f"    Delta File: {delta_file}")
        print(f"    Master File: {master_file}\n")

# ============================================
# TELEGRAM BOT FUNCTIONALITY
# ============================================

# Store user sessions in memory
user_sessions = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command - Welcome message"""
    welcome_text = (
        "🎓 *Welcome to Arise Medical Academy Bot*\n\n"
        "This bot helps you download your recorded lectures.\n\n"
        "Commands:\n"
        "/login - Login to your account\n"
        "/batches - View your batches\n"
        "/logout - Logout from your account\n"
        "/help - Show this message\n\n"
        "Start by using /login to authenticate!"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    await start(update, context)

async def login_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start login process"""
    await update.message.reply_text(
        "🔐 *Login to Arise Academy*\n\n"
        "Please enter your email address:",
        parse_mode='Markdown'
    )
    return EMAIL

async def receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive email and ask for password"""
    context.user_data['email'] = update.message.text.strip()
    await update.message.reply_text(
        "✅ Email received!\n\n"
        "Now, please enter your password:"
    )
    return PASSWORD

async def receive_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive password and attempt login"""
    password = update.message.text.strip()
    email = context.user_data['email']
    user_id = update.effective_user.id
    
    # Delete the password message for security
    await update.message.delete()
    
    status_msg = await update.message.reply_text("🔄 Authenticating... Please wait.")
    
    # Attempt login using AriseHarvester logic
    payload = {"userEmail": email, "password": password}
    try:
        resp = requests.post(f"{BASE_URL}/auth/loginV2", headers=HEADERS, json=payload, timeout=10)
        resp_data = resp.json()
        
        if resp_data.get("status") == "OK":
            token = resp_data.get("result")
            user_sessions[user_id] = {
                "email": email,
                "token": token
            }
            
            await status_msg.edit_text(
                "✅ *Login Successful!*\n\n"
                f"Welcome, {email}\n\n"
                "Use /batches to view your courses.",
                parse_mode='Markdown'
            )
            return ConversationHandler.END
        else:
            await status_msg.edit_text(
                f"❌ *Login Failed*\n\n"
                f"Error: {resp_data.get('msg', 'Unknown error')}\n\n"
                "Use /login to try again.",
                parse_mode='Markdown'
            )
            return ConversationHandler.END
            
    except Exception as e:
        logger.error(f"Login error: {e}")
        await status_msg.edit_text(
            "❌ *Connection Error*\n\n"
            "Could not connect to Arise Academy servers.\n"
            "Please try again later with /login",
            parse_mode='Markdown'
        )
        return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the conversation"""
    await update.message.reply_text(
        "❌ Operation cancelled.\n\n"
        "Use /login to start again."
    )
    return ConversationHandler.END

async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Logout user"""
    user_id = update.effective_user.id
    
    if user_id in user_sessions:
        del user_sessions[user_id]
        await update.message.reply_text("✅ You have been logged out successfully.")
    else:
        await update.message.reply_text("ℹ️ You are not logged in.")

async def get_batches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get user's batches"""
    user_id = update.effective_user.id
    
    if user_id not in user_sessions:
        await update.message.reply_text(
            "❌ You are not logged in.\n\n"
            "Please use /login first."
        )
        return
    
    status_msg = await update.message.reply_text("🔄 Fetching your batches...")
    
    session = user_sessions[user_id]
    auth_headers = HEADERS.copy()
    auth_headers.update({
        "authorization": f"Bearer {session['token']}",
        "useremail": session['email']
    })
    
    try:
        resp = requests.get(f"{BASE_URL}/student/getBatchesByStudent", headers=auth_headers, timeout=10)
        batch_resp = resp.json()
        batches = batch_resp.get("result", [])
        
        if not batches:
            await status_msg.edit_text("ℹ️ No batches found for your account.")
            return
        
        # Create inline keyboard with batches
        keyboard = []
        for batch in batches:
            batch_name = batch.get('batch', 'Unknown')
            batch_id = batch.get('batchId', '')
            keyboard.append([InlineKeyboardButton(batch_name, callback_data=f"batch_{batch_id}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await status_msg.edit_text(
            "📚 *Your Batches*\n\n"
            "Select a batch to download videos:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Batch fetch error: {e}")
        await status_msg.edit_text(
            "❌ Error fetching batches.\n\n"
            "Please try again later."
        )

async def batch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle batch selection and fetch videos"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    if user_id not in user_sessions:
        await query.edit_message_text("❌ Session expired. Please /login again.")
        return
    
    batch_id = query.data.replace("batch_", "")
    
    await query.edit_message_text(f"🔄 Fetching videos for batch {batch_id}...\n\nThis may take a moment.")
    
    session = user_sessions[user_id]
    auth_headers = HEADERS.copy()
    auth_headers.update({
        "authorization": f"Bearer {session['token']}",
        "useremail": session['email']
    })
    
    try:
        # Fetch recorded classes
        resp = requests.get(
            f"{BASE_URL}/liveclass/getRecordedClasses/{batch_id}",
            headers=auth_headers,
            timeout=30
        )
        data = resp.json().get("result")
        
        all_videos = []
        if isinstance(data, list):
            all_videos = data
        elif isinstance(data, dict):
            for subj in data:
                if isinstance(data[subj], list):
                    all_videos.extend(data[subj])
        
        if not all_videos:
            await query.edit_message_text("ℹ️ No recorded videos found in this batch.")
            return
        
        await query.edit_message_text(
            f"📹 Found {len(all_videos)} videos.\n"
            f"Processing videos...\n\n"
            "⏳ Please wait, this may take a few minutes..."
        )
        
        # Fetch video details and send links
        video_links = []
        for i, item in enumerate(all_videos):
            c_id = str(item.get('recordedClassId'))
            if not c_id:
                continue
            
            det_url = f"{BASE_URL}/liveclass/getRecordedClassDetails/{batch_id}?recordedClassId={c_id}"
            try:
                det_resp = requests.get(det_url, headers=auth_headers, timeout=10)
                det_data = det_resp.json().get("result", {})
                
                stream_url = det_data.get("streamUrl", "N/A")
                class_name = item.get('recordedClassName', 'Unknown Video')
                
                if stream_url != "N/A":
                    video_links.append({
                        'name': class_name,
                        'url': stream_url
                    })
                
                # Update progress every 10 videos
                if (i + 1) % 10 == 0:
                    await query.edit_message_text(
                        f"📹 Processing videos...\n\n"
                        f"Progress: {i + 1}/{len(all_videos)}\n"
                        f"⏳ Please wait..."
                    )
                
            except Exception as e:
                logger.error(f"Error fetching video {c_id}: {e}")
                continue
        
        # Send video links in chunks (5 per message to avoid Telegram limits)
        if video_links:
            await query.edit_message_text(
                f"✅ *Successfully fetched {len(video_links)} videos!*\n\n"
                f"Sending video links...",
                parse_mode='Markdown'
            )
            
            # Send videos in chunks of 5
            for i in range(0, len(video_links), 5):
                chunk = video_links[i:i+5]
                message = ""
                
                for idx, video in enumerate(chunk, start=i+1):
                    message += f"📹 *{idx}. {video['name']}*\n{video['url']}\n\n"
                
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=message,
                    parse_mode='Markdown'
                )
                
                # Small delay to avoid rate limiting
                time.sleep(0.5)
            
            # Send summary
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"✅ *Download Complete!*\n\n"
                     f"Total videos sent: {len(video_links)}\n\n"
                     f"Use /batches to download from another batch.",
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text("❌ Could not fetch video links. Please try again.")
            
    except Exception as e:
        logger.error(f"Video fetch error: {e}")
        await query.edit_message_text(
            "❌ Error fetching videos.\n\n"
            "Please try again later."
        )

def run_telegram_bot():
    """Start the Telegram bot"""
    TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
    
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not found in environment variables!")
        print("\n❌ Error: TELEGRAM_BOT_TOKEN environment variable not set!")
        print("Please set it in Heroku Config Vars or your environment.\n")
        return
    
    # Create application
    application = Application.builder().token(TOKEN).build()
    
    # Conversation handler for login
    login_handler = ConversationHandler(
        entry_points=[CommandHandler('login', login_start)],
        states={
            EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_email)],
            PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_password)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(login_handler)
    application.add_handler(CommandHandler("logout", logout))
    application.add_handler(CommandHandler("batches", get_batches))
    application.add_handler(CallbackQueryHandler(batch_callback, pattern="^batch_"))
    
    # Start the bot
    logger.info("🤖 Arise Academy Telegram Bot started!")
    print("\n🤖 Arise Academy Telegram Bot is running...")
    print("Press Ctrl+C to stop.\n")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

def run_cli():
    """Run the original CLI version"""
    try:
        harvester = AriseHarvester()
        harvester.run()
    except KeyboardInterrupt:
        print("\n\n[!] Operation terminated by user.")
    except Exception as e:
        print(f"\n\n[!] Fatal System Error: {e}")

if __name__ == "__main__":
    # Check if running as Telegram bot (if TELEGRAM_BOT_TOKEN is set)
    if os.environ.get('TELEGRAM_BOT_TOKEN'):
        run_telegram_bot()
    else:
        # Run CLI version
        print("\n💡 Tip: Set TELEGRAM_BOT_TOKEN environment variable to run as Telegram bot\n")
        run_cli()
