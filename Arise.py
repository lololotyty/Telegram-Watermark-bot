import requests
import json
import os
import sys
import time
import logging
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

# Load environment variables from .env file (for local development only)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # dotenv not available (e.g., on Heroku), skip it
    pass

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- SYSTEM CONFIGURATION ---
BASE_URL = "https://api.arisemedicalacademy.com/instituteApp"
SESSION_FILE = "auth_session.json"

# Conversation states for Telegram bot
EMAIL, PASSWORD = range(2)

# MongoDB Configuration
MONGODB_URI = os.environ.get('MONGODB_URI', 'mongodb://localhost:27017/')
mongo_client = None
db = None

def init_mongodb():
    """Initialize MongoDB connection"""
    global mongo_client, db
    try:
        mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
        # Test connection
        mongo_client.admin.command('ping')
        db = mongo_client['arise_bot']
        logger.info("✅ MongoDB connected successfully")
        
        # Create indexes
        db.download_queue.create_index([("user_id", 1), ("status", 1)])
        db.user_sessions.create_index("user_id", unique=True)
        
        return True
    except ConnectionFailure as e:
        logger.error(f"❌ MongoDB connection failed: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ MongoDB initialization error: {e}")
        return False

# Store user sessions in memory (fallback if MongoDB fails)
user_sessions_memory = {}

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
# MONGODB HELPER FUNCTIONS
# ============================================

def save_user_session(user_id, email, token):
    """Save user session to MongoDB"""
    try:
        if db is not None:
            db.user_sessions.update_one(
                {"user_id": user_id},
                {"$set": {
                    "user_id": user_id,
                    "email": email,
                    "token": token,
                    "updated_at": datetime.utcnow()
                }},
                upsert=True
            )
        else:
            user_sessions_memory[user_id] = {"email": email, "token": token}
    except Exception as e:
        logger.error(f"Error saving session: {e}")
        user_sessions_memory[user_id] = {"email": email, "token": token}

def get_user_session(user_id):
    """Get user session from MongoDB"""
    try:
        if db is not None:
            session = db.user_sessions.find_one({"user_id": user_id})
            if session:
                return {"email": session["email"], "token": session["token"]}
        return user_sessions_memory.get(user_id)
    except Exception as e:
        logger.error(f"Error getting session: {e}")
        return user_sessions_memory.get(user_id)

def delete_user_session(user_id):
    """Delete user session from MongoDB"""
    try:
        if db is not None:
            db.user_sessions.delete_one({"user_id": user_id})
        if user_id in user_sessions_memory:
            del user_sessions_memory[user_id]
    except Exception as e:
        logger.error(f"Error deleting session: {e}")

def add_download_task(user_id, chat_id, batch_id, videos):
    """Add videos to download queue"""
    try:
        if db is None:
            return False
        
        # Add each video as a separate task
        tasks = []
        for idx, video in enumerate(videos):
            task = {
                "user_id": user_id,
                "chat_id": chat_id,
                "batch_id": batch_id,
                "video_index": idx,
                "total_videos": len(videos),
                "video_name": video['name'],
                "video_url": video['url'],
                "video_id": video['id'],
                "status": "pending",  # pending, processing, completed, failed, stopped
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "retry_count": 0
            }
            tasks.append(task)
        
        if tasks:
            db.download_queue.insert_many(tasks)
            logger.info(f"Added {len(tasks)} videos to download queue for user {user_id}")
            return True
        return False
    except Exception as e:
        logger.error(f"Error adding download tasks: {e}")
        return False

def get_pending_tasks(limit=1):
    """Get pending download tasks"""
    try:
        if db is None:
            return []
        
        tasks = list(db.download_queue.find(
            {"status": "pending"},
            sort=[("created_at", 1)]
        ).limit(limit))
        
        return tasks
    except Exception as e:
        logger.error(f"Error getting pending tasks: {e}")
        return []

def update_task_status(task_id, status, error_message=None):
    """Update task status"""
    try:
        if db is None:
            return
        
        update_data = {
            "status": status,
            "updated_at": datetime.utcnow()
        }
        
        if error_message:
            update_data["error_message"] = error_message
        
        if status == "failed":
            db.download_queue.update_one(
                {"_id": task_id},
                {
                    "$set": update_data,
                    "$inc": {"retry_count": 1}
                }
            )
        else:
            db.download_queue.update_one(
                {"_id": task_id},
                {"$set": update_data}
            )
    except Exception as e:
        logger.error(f"Error updating task status: {e}")

def get_user_queue_status(user_id):
    """Get user's download queue status"""
    try:
        if db is None:
            return None
        
        total = db.download_queue.count_documents({"user_id": user_id})
        pending = db.download_queue.count_documents({"user_id": user_id, "status": "pending"})
        processing = db.download_queue.count_documents({"user_id": user_id, "status": "processing"})
        completed = db.download_queue.count_documents({"user_id": user_id, "status": "completed"})
        failed = db.download_queue.count_documents({"user_id": user_id, "status": "failed"})
        stopped = db.download_queue.count_documents({"user_id": user_id, "status": "stopped"})
        
        return {
            "total": total,
            "pending": pending,
            "processing": processing,
            "completed": completed,
            "failed": failed,
            "stopped": stopped
        }
    except Exception as e:
        logger.error(f"Error getting queue status: {e}")
        return None

def stop_user_downloads(user_id):
    """Stop all pending downloads for a user"""
    try:
        if db is None:
            return 0
        
        result = db.download_queue.update_many(
            {"user_id": user_id, "status": {"$in": ["pending", "processing"]}},
            {"$set": {"status": "stopped", "updated_at": datetime.utcnow()}}
        )
        
        return result.modified_count
    except Exception as e:
        logger.error(f"Error stopping downloads: {e}")
        return 0

def clear_user_queue(user_id):
    """Clear all tasks for a user"""
    try:
        if db is None:
            return 0
        
        result = db.download_queue.delete_many({"user_id": user_id})
        return result.deleted_count
    except Exception as e:
        logger.error(f"Error clearing queue: {e}")
        return 0

# ============================================
# TELEGRAM BOT FUNCTIONALITY
# ============================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command - Welcome message"""
    welcome_text = (
        "🎓 *Welcome to Arise Medical Academy Bot*\n\n"
        "This bot helps you download your recorded lectures.\n\n"
        "Commands:\n"
        "/login - Login to your account\n"
        "/batches - View your batches\n"
        "/status - Check download queue status\n"
        "/stop - Stop pending downloads\n"
        "/clear - Clear download queue\n"
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
            save_user_session(user_id, email, token)
            
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
    
    session = get_user_session(user_id)
    if session:
        delete_user_session(user_id)
        await update.message.reply_text("✅ You have been logged out successfully.")
    else:
        await update.message.reply_text("ℹ️ You are not logged in.")

async def get_batches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get user's batches"""
    user_id = update.effective_user.id
    
    session = get_user_session(user_id)
    if not session:
        await update.message.reply_text(
            "❌ You are not logged in.\n\n"
            "Please use /login first."
        )
        return
    
    status_msg = await update.message.reply_text("🔄 Fetching your batches...")
    
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
    """Handle batch selection and ask user for preference"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    session = get_user_session(user_id)
    if not session:
        await query.edit_message_text("❌ Session expired. Please /login again.")
        return
    
    batch_id = query.data.replace("batch_", "")
    
    # Store batch_id in user context
    context.user_data['selected_batch'] = batch_id
    
    # Ask user what they want
    keyboard = [
        [InlineKeyboardButton("📋 Get Links Only", callback_data=f"links_{batch_id}")],
        [InlineKeyboardButton("📥 Download & Upload Videos", callback_data=f"download_{batch_id}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        "📚 What would you like?\n\n"
        "📋 Get Links Only - Fast, just video URLs\n"
        "📥 Download & Upload - Bot downloads and uploads videos to Telegram (slower)\n\n"
        "Choose an option:",
        reply_markup=reply_markup
    )

async def links_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle links only request"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    session = get_user_session(user_id)
    if not session:
        await query.edit_message_text("❌ Session expired. Please /login again.")
        return
    
    batch_id = query.data.replace("links_", "")
    
    await query.edit_message_text(f"🔄 Fetching video links for batch {batch_id}...\n\nThis may take a moment.")
    
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
                f"✅ Successfully fetched {len(video_links)} videos!\n\n"
                f"Sending video links..."
            )
            
            # Send videos in chunks of 5
            for i in range(0, len(video_links), 5):
                chunk = video_links[i:i+5]
                message = ""
                
                for idx, video in enumerate(chunk, start=i+1):
                    # Escape special characters to avoid Markdown parsing errors
                    video_name = video['name'].replace('*', '').replace('_', '').replace('[', '').replace(']', '').replace('`', '')
                    message += f"📹 {idx}. {video_name}\n{video['url']}\n\n"
                
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=message
                )
                
                # Small delay to avoid rate limiting
                time.sleep(0.5)
            
            # Send summary
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"✅ Download Complete!\n\n"
                     f"Total videos sent: {len(video_links)}\n\n"
                     f"📥 HOW TO DOWNLOAD:\n"
                     f"1. Install yt-dlp: pip install yt-dlp\n"
                     f"2. Run: yt-dlp \"VIDEO_URL\"\n\n"
                     f"🎥 HOW TO WATCH (No Download):\n"
                     f"1. Install VLC Player\n"
                     f"2. Open VLC → Network Stream\n"
                     f"3. Paste video URL → Play\n\n"
                     f"Use /batches to download from another batch."
            )
        else:
            await query.edit_message_text("❌ Could not fetch video links. Please try again.")
            
    except Exception as e:
        logger.error(f"Video fetch error: {e}")
        await query.edit_message_text(
            "❌ Error fetching videos.\n\n"
            "Please try again later."
        )

async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle download and upload request"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    session = get_user_session(user_id)
    if not session:
        await query.edit_message_text("❌ Session expired. Please /login again.")
        return
    
    batch_id = query.data.replace("download_", "")
    
    await query.edit_message_text(
        f"📥 Starting download and upload process...\n\n"
        f"⏳ Fetching video list..."
    )
    
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
            f"📹 Found {len(all_videos)} videos.\n\n"
            f"⏳ Fetching video URLs..."
        )
        
        # Fetch video details
        video_list = []
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
                    video_list.append({
                        'name': class_name,
                        'url': stream_url,
                        'id': c_id
                    })
                
                if (i + 1) % 10 == 0:
                    await query.edit_message_text(
                        f"📹 Fetching URLs...\n\n"
                        f"Progress: {i + 1}/{len(all_videos)}"
                    )
                
            except Exception as e:
                logger.error(f"Error fetching video {c_id}: {e}")
                continue
        
        if not video_list:
            await query.edit_message_text("❌ Could not fetch video URLs.")
            return
        
        # Save download progress to MongoDB
        save_download_progress(user_id, batch_id, video_list, 0, query.message.chat_id)
        
        await query.edit_message_text(
            f"✅ Ready to download {len(video_list)} videos!\n\n"
            f"📥 Starting download and upload process...\n"
            f"⏳ This will take time. Videos will be uploaded one by one.\n\n"
            f"💡 Send /stop to stop the download process anytime.\n"
            f"💡 Progress is saved - if bot restarts, it will resume from where it stopped."
        )
        
        # Start downloading and uploading
        await download_and_upload_videos(context, user_id)
        
    except Exception as e:
        logger.error(f"Download process error: {e}")
        await query.edit_message_text(
            "❌ Error starting download process.\n\n"
            "Please try again later."
        )

async def download_and_upload_videos(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Download videos using yt-dlp and upload to Telegram with proper metadata"""
    import subprocess
    import tempfile
    import ffmpeg
    from PIL import Image
    
    download_data = get_download_progress(user_id)
    if not download_data:
        return
    
    videos = download_data['videos']
    chat_id = download_data['chat_id']
    
    for i in range(download_data['current_index'], len(videos)):
        # Check if user stopped the process
        if not is_download_active(user_id):
            await context.bot.send_message(
                chat_id=chat_id,
                text="⏹️ Download process stopped by user."
            )
            # Clean up completed download from MongoDB
            delete_download_progress(user_id)
            return
        
        video = videos[i]
        video_name = video['name'].replace('/', '-').replace('\\', '-').replace(':', '-')
        video_url = video['url']
        
        # Update current index in MongoDB
        save_download_progress(user_id, download_data['batch_id'], videos, i, chat_id)
        
        try:
            # Send status
            status_msg = await context.bot.send_message(
                chat_id=chat_id,
                text=f"📥 Downloading video {i+1}/{len(videos)}...\n\n"
                     f"📹 {video_name}\n\n"
                     f"⏳ Please wait, this may take several minutes..."
            )
            
            # Create temporary directory
            with tempfile.TemporaryDirectory() as temp_dir:
                raw_output = os.path.join(temp_dir, f"raw_{video_name}.mp4")
                output_file = os.path.join(temp_dir, f"{video_name}.mp4")
                thumbnail_file = os.path.join(temp_dir, f"{video_name}_thumb.jpg")
                
                # Check if it's a PDF
                is_pdf = video_url.lower().endswith('.pdf')
                
                if is_pdf:
                    # Download PDF
                    result = subprocess.run(
                        ['yt-dlp', '-o', output_file, video_url],
                        capture_output=True,
                        text=True,
                        timeout=1800
                    )
                    
                    if result.returncode != 0 or not os.path.exists(output_file):
                        await status_msg.edit_text(
                            f"❌ Failed to download PDF {i+1}/{len(videos)}\n\n"
                            f"📄 {video_name}\n\n"
                            f"Skipping to next item..."
                        )
                        time.sleep(2)
                        continue
                    
                    # Upload PDF
                    file_size = os.path.getsize(output_file)
                    file_size_mb = file_size / (1024 * 1024)
                    
                    if file_size_mb > 50:  # Telegram limit for documents
                        await status_msg.edit_text(
                            f"⚠️ PDF {i+1}/{len(videos)} is too large ({file_size_mb:.1f} MB)\n\n"
                            f"📄 {video_name}\n\n"
                            f"Telegram limit is 50MB for documents. Skipping..."
                        )
                        continue
                    
                    await status_msg.edit_text(
                        f"📤 Uploading PDF {i+1}/{len(videos)}...\n\n"
                        f"📄 {video_name}\n"
                        f"📊 Size: {file_size_mb:.1f} MB"
                    )
                    
                    with open(output_file, 'rb') as pdf_file:
                        await context.bot.send_document(
                            chat_id=chat_id,
                            document=pdf_file,
                            caption=f"📄 {video_name}\n\nDocument {i+1}/{len(videos)}",
                            read_timeout=300,
                            write_timeout=300
                        )
                    
                    await status_msg.edit_text(
                        f"✅ PDF {i+1}/{len(videos)} uploaded successfully!\n\n"
                        f"📄 {video_name}"
                    )
                    
                else:
                    # Download video using yt-dlp
                    result = subprocess.run(
                        ['yt-dlp', '-o', raw_output, video_url],
                        capture_output=True,
                        text=True,
                        timeout=1800  # 30 minutes timeout
                    )
                    
                    if result.returncode != 0:
                        await status_msg.edit_text(
                            f"❌ Failed to download video {i+1}/{len(videos)}\n\n"
                            f"📹 {video_name}\n\n"
                            f"Error: {result.stderr[:200]}\n\n"
                            f"Skipping to next video..."
                        )
                        time.sleep(2)
                        continue
                    
                    # Check if file exists
                    if not os.path.exists(raw_output):
                        await status_msg.edit_text(
                            f"❌ Video file not found after download: {video_name}\n\n"
                            f"Skipping to next video..."
                        )
                        continue
                    
                    await status_msg.edit_text(
                        f"🔧 Processing video {i+1}/{len(videos)}...\n\n"
                        f"📹 {video_name}\n\n"
                        f"⏳ Adding metadata and generating thumbnail..."
                    )
                    
                    # Get video info
                    try:
                        probe = ffmpeg.probe(raw_output)
                        video_info = next(s for s in probe['streams'] if s['codec_type'] == 'video')
                        duration = float(probe['format'].get('duration', 0))
                        width = int(video_info.get('width', 0))
                        height = int(video_info.get('height', 0))
                    except:
                        duration = 0
                        width = 0
                        height = 0
                    
                    # Generate thumbnail from first few seconds
                    try:
                        (
                            ffmpeg
                            .input(raw_output, ss=5)  # Take frame at 5 seconds
                            .filter('scale', 320, -1)  # Scale to 320px width
                            .output(thumbnail_file, vframes=1)
                            .overwrite_output()
                            .run(capture_stdout=True, capture_stderr=True, quiet=True)
                        )
                        
                        # Verify thumbnail was created
                        if not os.path.exists(thumbnail_file):
                            # Try at 0 seconds if 5 seconds failed
                            (
                                ffmpeg
                                .input(raw_output, ss=0)
                                .filter('scale', 320, -1)
                                .output(thumbnail_file, vframes=1)
                                .overwrite_output()
                                .run(capture_stdout=True, capture_stderr=True, quiet=True)
                            )
                    except Exception as e:
                        logger.warning(f"Failed to generate thumbnail: {e}")
                        thumbnail_file = None
                    
                    # Fix metadata if missing
                    if duration == 0 or width == 0 or height == 0:
                        try:
                            # Re-encode with proper metadata
                            (
                                ffmpeg
                                .input(raw_output)
                                .output(output_file, codec='copy', movflags='faststart')
                                .overwrite_output()
                                .run(capture_stdout=True, capture_stderr=True, quiet=True)
                            )
                            
                            # Get updated info
                            probe = ffmpeg.probe(output_file)
                            video_info = next(s for s in probe['streams'] if s['codec_type'] == 'video')
                            duration = float(probe['format'].get('duration', 0))
                            width = int(video_info.get('width', 0))
                            height = int(video_info.get('height', 0))
                        except Exception as e:
                            logger.warning(f"Failed to fix metadata: {e}")
                            # Use raw file if processing failed
                            output_file = raw_output
                    else:
                        # Just copy if metadata is fine
                        output_file = raw_output
                    
                    # Get file size
                    file_size = os.path.getsize(output_file)
                    file_size_mb = file_size / (1024 * 1024)
                    
                    # Telegram has 2GB limit for bots
                    if file_size_mb > 2000:
                        await status_msg.edit_text(
                            f"⚠️ Video {i+1}/{len(videos)} is too large ({file_size_mb:.1f} MB)\n\n"
                            f"📹 {video_name}\n\n"
                            f"Telegram limit is 2GB. Skipping...\n\n"
                            f"💡 Use link instead: {video_url}"
                        )
                        continue
                    
                    # Upload to Telegram
                    await status_msg.edit_text(
                        f"📤 Uploading video {i+1}/{len(videos)}...\n\n"
                        f"📹 {video_name}\n"
                        f"📊 Size: {file_size_mb:.1f} MB\n"
                        f"⏱️ Duration: {int(duration//60)}:{int(duration%60):02d}\n\n"
                        f"⏳ Please wait..."
                    )
                    
                    with open(output_file, 'rb') as video_file:
                        # Prepare thumbnail
                        thumb = None
                        if thumbnail_file and os.path.exists(thumbnail_file):
                            thumb = open(thumbnail_file, 'rb')
                        
                        try:
                            await context.bot.send_video(
                                chat_id=chat_id,
                                video=video_file,
                                thumbnail=thumb,
                                caption=f"📹 {video_name}\n\nVideo {i+1}/{len(videos)}",
                                duration=int(duration) if duration > 0 else None,
                                width=width if width > 0 else None,
                                height=height if height > 0 else None,
                                supports_streaming=True,
                                read_timeout=300,
                                write_timeout=300
                            )
                        finally:
                            if thumb:
                                thumb.close()
                    
                    await status_msg.edit_text(
                        f"✅ Video {i+1}/{len(videos)} uploaded successfully!\n\n"
                        f"📹 {video_name}"
                    )
                
        except subprocess.TimeoutExpired:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⏱️ Download timeout for video {i+1}/{len(videos)}\n\n"
                     f"📹 {video_name}\n\n"
                     f"Video took too long to download. Skipping..."
            )
        except Exception as e:
            logger.error(f"Error processing video {video_name}: {e}")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Error processing video {i+1}/{len(videos)}\n\n"
                     f"📹 {video_name}\n\n"
                     f"Error: {str(e)[:200]}\n\n"
                     f"Continuing with next video..."
            )
        
        # Small delay between videos
        time.sleep(2)
    
    # All videos processed - clean up from MongoDB
    delete_download_progress(user_id)
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🎉 All videos processed!\n\n"
             f"Total: {len(videos)} videos\n\n"
             f"✅ Download progress cleaned from database.\n\n"
             f"Use /batches to download from another batch."
    )
            )
        except Exception as e:
            logger.error(f"Error processing video {video_name}: {e}")
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Error processing video {i+1}/{len(videos)}\n\n"
                     f"📹 {video_name}\n\n"
                     f"Error: {str(e)[:200]}\n\n"
                     f"Continuing with next video..."
            )
        
        # Small delay between videos
        time.sleep(2)
    
    # All videos processed
    delete_download_progress(user_id)
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🎉 All videos processed!\n\n"
             f"Total: {len(videos)} videos\n\n"
             f"Use /batches to download from another batch."
    )

async def stop_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop the download process"""
    user_id = update.effective_user.id
    
    if is_download_active(user_id):
        delete_download_progress(user_id)
        await update.message.reply_text(
            "⏹️ Download process stopped.\n\n"
            "Progress has been cleared from database.\n\n"
            "Use /batches to start a new download."
        )
    else:
        await update.message.reply_text(
            "ℹ️ No active download process.\n\n"
            "Use /batches to start downloading videos."
        )

async def resume_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resume interrupted download"""
    user_id = update.effective_user.id
    
    download_data = get_download_progress(user_id)
    if download_data:
        videos = download_data['videos']
        current = download_data['current_index']
        remaining = len(videos) - current
        
        await update.message.reply_text(
            f"📥 Found interrupted download!\n\n"
            f"Batch: {download_data['batch_id']}\n"
            f"Progress: {current}/{len(videos)} videos\n"
            f"Remaining: {remaining} videos\n\n"
            f"Resuming download..."
        )
        
        # Resume downloading
        await download_and_upload_videos(context, user_id)
    else:
        await update.message.reply_text(
            "ℹ️ No interrupted download found.\n\n"
            "Use /batches to start a new download."
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
    application.add_handler(CommandHandler("stop", stop_download))
    application.add_handler(CommandHandler("resume", resume_download))
    application.add_handler(CallbackQueryHandler(batch_callback, pattern="^batch_"))
    application.add_handler(CallbackQueryHandler(links_callback, pattern="^links_"))
    application.add_handler(CallbackQueryHandler(download_callback, pattern="^download_"))
    
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
