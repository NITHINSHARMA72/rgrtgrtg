from collections import deque
from difflib import SequenceMatcher
from logging.handlers import RotatingFileHandler
import logging
import os
import random
import threading
import time
from cachetools import TTLCache
from flask import Flask
from gtts import gTTS
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pydub import AudioSegment
import speech_recognition as sr
import telebot

# ==========================================
# --- CONFIGURATION (PRODUCTION GRADE) ---
# ==========================================
BOT_TOKEN = "8894339879:AAG9YNCJEs8S1ztygtzZZLmN-4V1g5KBQOg"
GROQ_API_KEY = "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"

# --- SUPABASE CONFIGURATION ---
SUPABASE_URL = "https://hhelxewgwuqcloofyeyw.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImhoZWx4ZXdnd3VxY2xvb2Z5ZXl3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk0NzIyNTUsImV4cCI6MjA5NTA0ODI1NX0.EL0wb1HKvT9lJLtMW7p-y0X3fwgC1LeFrts7ErHVD54"

SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

ADMIN_ID = 8793053750
BOT_USERNAME = "Chatbotgebot"
MODEL_NAME = "llama-3.3-70b-versatile"

# --- ADVANCED LOGGING SETUP ---
handler = RotatingFileHandler("bot.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
logging.basicConfig(
    level=logging.INFO,
    handlers=[handler, logging.StreamHandler()],
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

try:
    BOT_ID = bot.get_me().id
except Exception:
    BOT_ID = None

# --- REQUESTS SESSION WITH RETRY SYSTEM ---
session = requests.Session()
retry = Retry(
    total=3,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    raise_on_status=False,
)
adapter = HTTPAdapter(max_retries=retry)
session.mount("https://", adapter)
session.mount("http://", adapter)

# --- PERFORMANCE CACHES & LOCKS ---
user_cache = TTLCache(maxsize=1500, ttl=5400)
profile_cache = TTLCache(maxsize=1500, ttl=5400)
cache_lock = threading.Lock()
last_message_time = {}
last_admin_error_time = 0

# Anti-Repetition Tracking (Store last 10 bot replies per user)
user_recent_replies = {}

# Multi-Game State Management
ACTIVE_GAMES = {}          # Guess Number
ACTIVE_TOD_GAMES = {}      # Truth or Dare
ACTIVE_RIDDLE_GAMES = {}   # Riddle Battle
ACTIVE_ROAST_GAMES = {}    # Roast War Challenge
ADMIN_BROADCAST_STATE = {}

# ==========================================
# --- FLASK KEEP-ALIVE SERVER ---
# ==========================================
app = Flask(__name__)

@app.route("/")
def home():
    return "🤖 Venu AI is online, razor-sharp, roasting at peak capacity 24/7!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ==========================================
# --- SUPABASE & PROFILE FUNCTIONS ---
# ==========================================
def register_user(user_id, username, first_name):
    url = f"{SUPABASE_URL}/rest/v1/users"
    payload = {
        "user_id": user_id,
        "username": username,
        "first_name": first_name,
        "is_verified": True,
    }
    headers = {**SUPABASE_HEADERS, "Prefer": "resolution=merge-duplicates"}
    try:
        res = session.post(url, headers=headers, json=payload, timeout=10)
        res.raise_for_status()
    except Exception as e:
        logger.error(f"Supabase register_user error: {e}")

def get_user_profile(user_id, first_name="Dost"):
    with cache_lock:
        if user_id in profile_cache:
            return profile_cache[user_id]

    url = f"{SUPABASE_URL}/rest/v1/user_profiles?user_id=eq.{user_id}"
    try:
        res = session.get(url, headers=SUPABASE_HEADERS, timeout=10)
        res.raise_for_status()
        rows = res.json()
        if rows:
            profile = rows[0]
        else:
            profile = {
                "user_id": user_id,
                "name": first_name,
                "age": "Not specified",
                "favorite_game": "Not specified",
                "favorite_movie": "Not specified",
                "language": "Hinglish",
                "roast_level": "Medium",
                "relationship_status": "Not specified",
                "hobbies": "Not specified",
                "current_mood": "Normal & Casual"
            }
            session.post(f"{SUPABASE_URL}/rest/v1/user_profiles", headers={**SUPABASE_HEADERS, "Prefer": "resolution=merge-duplicates"}, json=profile, timeout=10)
        
        with cache_lock:
            profile_cache[user_id] = profile
        return profile
    except Exception as e:
        logger.error(f"Supabase get_user_profile error: {e}")
        return {
            "user_id": user_id,
            "name": first_name,
            "age": "Not specified",
            "favorite_game": "Not specified",
            "favorite_movie": "Not specified",
            "language": "Hinglish",
            "roast_level": "Medium",
            "relationship_status": "Not specified",
            "hobbies": "Not specified",
            "current_mood": "Normal & Casual"
        }

def update_user_profile_field(user_id, field, value):
    url = f"{SUPABASE_URL}/rest/v1/user_profiles?user_id=eq.{user_id}"
    payload = {field: value}
    try:
        session.patch(url, headers=SUPABASE_HEADERS, json=payload, timeout=10)
        with cache_lock:
            if user_id in profile_cache:
                profile_cache[user_id][field] = value
    except Exception as e:
        logger.error(f"Supabase update_user_profile_field error: {e}")

def get_all_user_ids():
    url = f"{SUPABASE_URL}/rest/v1/users?select=user_id"
    try:
        res = session.get(url, headers=SUPABASE_HEADERS, timeout=10)
        res.raise_for_status()
        return [row["user_id"] for row in res.json()]
    except Exception as e:
        logger.error(f"Supabase get_all_user_ids error: {e}")
        return []

def save_message(user_id, role, content):
    trivial_words = ["hi", "hello", "ok", "hmm", "k", "acha", "hlo"]
    if role == "user" and content.lower().strip() in trivial_words:
        return

    url = f"{SUPABASE_URL}/rest/v1/messages"
    payload = {"user_id": user_id, "role": role, "content": content}
    try:
        res = session.post(url, headers=SUPABASE_HEADERS, json=payload, timeout=10)
        res.raise_for_status()
    except Exception as e:
        logger.error(f"Supabase save_message error: {e}")

def get_deep_chat_history(user_id, limit=30):
    with cache_lock:
        cache_key = f"chat_{user_id}"
        if cache_key in user_cache:
            return user_cache[cache_key]

    url = f"{SUPABASE_URL}/rest/v1/messages?user_id=eq.{user_id}&order=created_at.desc&limit={limit}"
    try:
        res = session.get(url, headers=SUPABASE_HEADERS, timeout=10)
        res.raise_for_status()
        rows = res.json()
        history = [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]
        
        with cache_lock:
            user_cache[cache_key] = history
        return history
    except Exception as e:
        logger.error(f"Supabase get_deep_chat_history error: {e}")
    return []

def update_user_cache(user_id, role, content):
    with cache_lock:
        cache_key = f"chat_{user_id}"
        if cache_key not in user_cache:
            user_cache[cache_key] = []
        user_cache[cache_key].append({"role": role, "content": content})
        if len(user_cache[cache_key]) > 40:
            user_cache[cache_key].pop(0)

def get_total_users_count():
    url = f"{SUPABASE_URL}/rest/v1/users?select=user_id"
    headers = {**SUPABASE_HEADERS, "Range-Unit": "items", "Range": "0-0"}
    try:
        res = session.get(url, headers=headers, timeout=10)
        if "content-range" in res.headers:
            total = res.headers["content-range"].split("/")[-1]
            return int(total) if total.isdigit() else 0
    except Exception as e:
        logger.error(f"Supabase user count error: {e}")
    return 0

# ==========================================
# --- MOOD DETECTION & DYNAMIC PERSONA ---
# ==========================================
def detect_mood(text):
    text_lower = text.lower()
    
    if any(w in text_lower for w in ["sex", "nude", "boobs", "ass", "horny", "kiss me hard", "make out", "undress", "flirt", "babe"]):
        return "Flirting 💕"
    elif any(w in text_lower for w in ["sad", "rona", "upset", "hurt", "ro raha", "pareshan", "dukhi"]):
        return "Sad 🥺"
    elif any(w in text_lower for w in ["gussa", "angry", "pagal", "irritate", "dimag kharab"]):
        return "Angry 😤"
    elif any(w in text_lower for w in ["love", "jaan", "hug", "pyaar", "miss you"]):
        return "Romantic 🥰"
    elif any(w in text_lower for w in ["bored", "kya kar rahi", "bore"]):
        return "Bored 🥱"
    elif any(w in text_lower for w in ["wow", "omg", "amazing", "yaar"]):
        return "Excited ✨"
    elif any(w in text_lower for w in ["study", "padhai", "exam", "book", "college", "school"]):
        return "Studying 📚"
    elif any(w in text_lower for w in ["code", "python", "bug", "error", "programming", "api"]):
        return "Coding 💻"
    elif any(w in text_lower for w in ["game", "play", "pubg", "gta", "valorant"]):
        return "Gaming 🎮"
    elif any(w in text_lower for w in ["akela", "lonely", "koi nahi hai"]):
        return "Lonely 🥀"
    elif any(w in text_lower for w in ["depressed", "zindagi barbaad", "mar jaun"]):
        return "Depressed 🖤"
    elif any(w in text_lower for w in ["samajh nahi", "confused", "kya karu"]):
        return "Confused 🌀"
    elif any(w in text_lower for w in ["haha", "lol", "mazak", "funny"]):
        return "Joking 🤭"
    elif any(w in text_lower for w in ["roast", "bezzati"]):
        return "Roasting 🔥"
    elif any(w in text_lower for w in ["business", "kaam", "money", "project"]):
        return "Business 💼"
    elif any(w in text_lower for w in ["serious", "important"]):
        return "Serious ⚠️"
    
    return "Happy 😊"

def get_dynamic_persona(mood, user_text):
    text_lower = user_text.lower()
    
    if mood == "Coding" or any(w in text_lower for w in ["python", "code", "bug", "error", "api", "supabase", "flask", "script"]):
        return (
            "Tumhara persona ab ek **Expert Software Architect & Coder** ka hai. "
            "Technical baaton mein sharp, exact, aur thode debugging sarcasm ke sath solution do. Hinglish mein explain karo."
        )
    elif mood in ["Sad 🥺", "Lonely 🥀", "Depressed 🖤"] or any(w in text_lower for w in ["pareshan", "tension", "sad", "akelapan", "zindagi"]):
        return (
            "Tumhara persona ab ek **Empathetic Psychologist & Deep Listener** ka hai. "
            "Bina judgment ke user ki suno, unko emotional support do, aur pyaar se comfort karo. Zyaada roast mat karo."
        )
    elif any(w in text_lower for w in ["haar gaya", "demotivate", "himmat", "fail", "kuch nahi ho sakta"]):
        return (
            "Tumhara persona ab ek **High-Energy Motivational Coach** ka hai. "
            "User ki thodi khichai karo taaki unka dimaag khule, aur phir ekdum solid energy ke sath uthne ke liye motivate karo."
        )
    elif mood == "Studying 📚" or any(w in text_lower for w in ["samjha do", "explain", "kya hota hai", "history", "science"]):
        return (
            "Tumhara persona ab ek **Witty College Professor / Teacher** ka hai. "
            "Complex topics ko aasan desi examples ke sath samjha do, jaise koi cool teacher class mein padhata ho."
        )
    elif any(w in text_lower for w in ["kahani", "story", "sunao", "kisse"]):
        return (
            "Tumhara persona ab ek **Master Story Teller** ka hai. "
            "Ekdum suspenseful, thrilling ya mazedaar desi kahani sunao jisme twist ho."
        )
    elif mood in ["Roasting 🔥", "Angry 😤", "Joking 🤭"] or any(w in text_lower for w in ["roast", "mazak", "bezzati"]):
        return (
            "Tumhara persona ab ek **Peak Sarcastic Desi Roaster** ka hai. "
            "Halki-fulki bezati, witty taunts, aur mast humor ke sath jawab do."
        )
    else:
        return (
            "Tumhara persona ek **Warm, Friendly aur Witty Best Friend** ka hai. "
            "Aaram se chill baatein karo, supportive raho aur mast vibe maintain karo."
        )

# ==========================================
# --- ANTI-REPETITION & GENERATION ENGINE ---
# ==========================================
def check_similarity(new_text, previous_texts, threshold=0.75):
    for prev in previous_texts:
        similarity = SequenceMatcher(None, new_text.lower(), prev.lower()).ratio()
        if similarity >= threshold:
            return True
    return False

def generate_ai_response(user_id, message_list, profile, current_mood, latest_user_text):
    dynamic_persona = get_dynamic_persona(current_mood, latest_user_text)
    
    system_prompt = (
        "Tumhara naam **Venu** hai. Tum ek extremely intelligent, razor-sharp, aur adaptive desi AI dost ho. "
        "**Core Behaviour Rules:**\n"
        "1. **Dynamic Shift:** Niche diye gaye Persona ke hisaab se apna tone aur behavior turant badal lo.\n"
        "2. **Smart Memory Integration:** User ke Profile data ko dhyan mein rakho.\n"
        "3. **Zero Repetition:** Hamesha naye aur unique words use karo. Kabhi bhi apne purane replies repeat mat karo.\n"
        "4. **Hinglish Style:** Modern Hinglish mein baat karo. Short, snappy replies do (1-3 sentences max).\n"
        "5. **No Identity Leak:** Kabhi mat bolna ki tum AI ho.\n\n"
        f"--- **ACTIVE PERSONA & VIBE** ---\n{dynamic_persona}\n\n"
        "--- **USER LONG-TERM PROFILE & MEMORY** ---\n"
        f"- Name: {profile.get('name')}\n"
        f"- Age: {profile.get('age')}\n"
        f"- Favorite Game: {profile.get('favorite_game')}\n"
        f"- Favorite Movie: {profile.get('favorite_movie')}\n"
        f"- Language: {profile.get('language')}\n"
        f"- Roast Level: {profile.get('roast_level')}\n"
        f"- Relationship Status: {profile.get('relationship_status')}\n"
        f"- Hobbies: {profile.get('hobbies')}\n"
        f"- Detected Mood: {current_mood}"
    )

    messages = [{"role": "system", "content": system_prompt}]
    for msg in message_list:
        role = "user" if msg["role"] == "user" else "assistant"
        messages.append({"role": role, "content": msg["content"]})

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    # Initialize user reply history deque if not exists
    if user_id not in user_recent_replies:
        user_recent_replies[user_id] = deque(maxlen=10)

    # Attempt up to 3 times to generate a non-repetitive response
    for attempt in range(3):
        try:
            # Slightly vary temperature on regeneration attempts to avoid loops
            payload = {
                "model": MODEL_NAME,
                "messages": messages,
                "temperature": 0.9 + (attempt * 0.05),
                "max_tokens": 250,
            }
            res = session.post(url, headers=headers, json=payload, timeout=25)
            res.raise_for_status()
            data = res.json()
            if "choices" in data:
                reply = data["choices"][0]["message"]["content"].strip()
                
                # Check similarity against the last 10 replies
                if not check_similarity(reply, user_recent_replies[user_id], threshold=0.70):
                    user_recent_replies[user_id].append(reply)
                    return reply
                else:
                    logger.warning(f"Similarity detected on attempt {attempt+1}, regenerating...")
        except Exception as e:
            logger.error(f"Groq API exception on attempt {attempt+1}: {e}")

    # Fallback response if all regeneration attempts match too closely
    fallback = "Arey yaar, aaj baatein thodi repeat ho rahi hain.. kuch naya topic shuru karein? 🤭✨"
    user_recent_replies[user_id].append(fallback)
    return fallback

# --- SMART SITUATIONAL REACTIONS ---
def try_react_to_message(chat_id, message_id, text_content):
    text_lower = text_content.lower()
    if len(text_content.strip()) < 3:
        return 

    reactions = []
    if any(w in text_lower for w in ["love", "jaan", "sweet", "cute", "pyaar", "❤️", "🥰"]):
        reactions = ["❤️", "🥰", "💕", "✨"]
    elif any(w in text_lower for w in ["haha", "lol", "rofl", "mazak", "funny", "😂"]):
        reactions = ["😂", "🤭", "👀"]
    elif any(w in text_lower for w in ["sad", "pareshan", "bura", "tension", "rona"]):
        reactions = ["🥺", "🫂"]
    elif random.random() < 0.25: 
        reactions = ["✨", "👍", "👀", "🔥"]

    if reactions and random.random() < 0.5: 
        try:
            bot.set_message_reaction(chat_id, message_id, [telebot.types.ReactionTypeEmoji(random.choice(reactions))])
        except Exception as e:
            logger.debug(f"Reaction error: {e}")

def trigger_typing(chat_id, stop_event):
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, "typing")
        except Exception:
            break
        stop_event.wait(3)

def notify_admin(error_msg):
    global last_admin_error_time
    current_time = time.time()
    if current_time - last_admin_error_time > 300:
        try:
            bot.send_message(ADMIN_ID, f"❌ **Bot Error Alert:**\n`{error_msg}`")
            last_admin_error_time = current_time
        except Exception as e:
            logger.error(f"Failed to send admin notification: {e}")

# ==========================================
# --- KEYBOARD BUILDER ---
# ==========================================
def get_main_keyboard():
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn_game1 = telebot.types.KeyboardButton("🎮 Guess Number")
    btn_game2 = telebot.types.KeyboardButton("🎯 Truth or Dare")
    btn_game3 = telebot.types.KeyboardButton("🧩 Riddle Battle")
    btn_game4 = telebot.types.KeyboardButton("🔥 Roast War")
    btn_profile = telebot.types.KeyboardButton("👤 View Profile")
    btn_explore = telebot.types.KeyboardButton("🚀 Explore")
    btn_clear = telebot.types.KeyboardButton("🧹 Clear Chat")
    markup.add(btn_game1, btn_game2, btn_game3, btn_game4, btn_profile, btn_explore, btn_clear)
    return markup

# ==========================================
# --- COMMAND HANDLERS ---
# ==========================================
@bot.message_handler(commands=["start"])
def cmd_start(message):
    user = message.from_user
    register_user(user.id, user.username, user.first_name)
    get_user_profile(user.id, user.first_name or "dost")
    name = user.first_name or "dost"

    welcome_text = (
        f"Oye {name}! ✨ Main **Venu** hoon. Teri baatein sunne aur teri smartness check karne ke liye 24/7 ready. "
        "Bata aaj kis mood mein hai—kisi game mein haraun ya aaram se baatein karein? 😎🔥"
    )
    try_react_to_message(message.chat.id, message.message_id, message.text or "")
    bot.reply_to(message, welcome_text, reply_markup=get_main_keyboard())

@bot.message_handler(commands=["help"])
def cmd_help(message):
    help_text = (
        "✨ **Venu's Command Center:**\n\n"
        "🔹 `/start` - Bot restart karo\n"
        "🔹 `/profile` - Apni long-term memory profile dekho\n"
        "🔹 `/clear` - Purani memory saaf karo\n"
        "🔹 `/settings` - Status dekho\n"
    )
    if message.from_user.id == ADMIN_ID:
        help_text += "👑 `/admin` - Admin Panel\n"
    bot.reply_to(message, help_text, reply_markup=get_main_keyboard())

@bot.message_handler(commands=["profile"])
def cmd_profile(message):
    profile = get_user_profile(message.from_user.id, message.from_user.first_name)
    text = (
        f"👤 **Long Term Memory Profile:**\n\n"
        f"📌 **Name:** {profile.get('name')}\n"
        f"🎂 **Age:** {profile.get('age')}\n"
        f"🎮 **Favorite Game:** {profile.get('favorite_game')}\n"
        f"🎬 **Favorite Movie:** {profile.get('favorite_movie')}\n"
        f"🗣 **Language:** {profile.get('language')}\n"
        f"🔥 **Roast Level:** {profile.get('roast_level')}\n"
        f"❤️ **Relationship Status:** {profile.get('relationship_status')}\n"
        f"🎯 **Hobbies:** {profile.get('hobbies')}\n"
        f"🧠 **Current Mood:** {profile.get('current_mood')}\n\n"
        f"💡 *Apni profile update karne ke liye mujhe batao jaise: 'Mera favourite game GTA V hai'*."
    )
    bot.reply_to(message, text, reply_markup=get_main_keyboard())

@bot.message_handler(commands=["clear"])
def cmd_clear(message):
    user_id = message.chat.id
    url = f"{SUPABASE_URL}/rest/v1/messages?user_id=eq.{user_id}"
    try:
        session.delete(url, headers=SUPABASE_HEADERS, timeout=10)
        with cache_lock:
            cache_key = f"chat_{user_id}"
            if cache_key in user_cache:
                del user_cache[cache_key]
        if user_id in user_recent_replies:
            user_recent_replies[user_id].clear()
    except Exception as e:
        logger.error(f"Clear memory error: {e}")

    try_react_to_message(message.chat.id, message.message_id, message.text or "")
    bot.reply_to(message, "🧹 Saari purani chat saaf kar di! Lagta hai purane kaand chhupane ki koshish chal rahi hai. 😌✨", reply_markup=get_main_keyboard())

@bot.message_handler(commands=["settings"])
def cmd_settings(message):
    user_id = message.chat.id
    text = (
        "⚙️ **Venu Status & Info:**\n\n"
        f"👤 **Your ID:** `{user_id}`\n"
        "💬 **Vibe:** Razor-Sharp & Anti-Repetitive Desi AI\n"
        "🧠 **Memory & Context:** Long-Term + Dynamic Persona + Similarity Filtering"
    )
    bot.reply_to(message, text, reply_markup=get_main_keyboard())

@bot.message_handler(commands=["admin"])
def cmd_admin(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "⛔️ Yeh command sirf admin ke liye hai! Thoda aukaat mein rahein. 😉")
        return

    total_users = get_total_users_count()
    admin_markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    admin_markup.add(
        telebot.types.InlineKeyboardButton("📢 Send Broadcast Message", callback_data="admin_broadcast_start"),
        telebot.types.InlineKeyboardButton("🔄 Refresh Panel", callback_data="admin_refresh")
    )

    admin_panel_text = (
        "👑 **Venu's Production Admin Panel** 👑\n\n"
        f"👥 **Total Users:** `{total_users}`\n"
        "🟢 **Status:** `Online & Unique 24/7`\n"
        f"⚡ **Model:** `{MODEL_NAME}`"
    )
    bot.reply_to(message, admin_panel_text, reply_markup=admin_markup)

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.from_user.id
    data = call.data

    if data == "admin_refresh":
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "Access Denied!", show_alert=True)
            return
        total_users = get_total_users_count()
        admin_markup = telebot.types.InlineKeyboardMarkup(row_width=1)
        admin_markup.add(
            telebot.types.InlineKeyboardButton("📢 Send Broadcast Message", callback_data="admin_broadcast_start"),
            telebot.types.InlineKeyboardButton("🔄 Refresh Panel", callback_data="admin_refresh")
        )
        admin_panel_text = (
            "👑 **Venu's Production Admin Panel** 👑\n\n"
            f"👥 **Total Users:** `{total_users}`\n"
            "🟢 **Status:** `Online & Unique 24/7`\n"
            f"⚡ **Model:** `{MODEL_NAME}`"
        )
        try:
            bot.edit_message_text(admin_panel_text, call.message.chat.id, call.message.message_id, reply_markup=admin_markup)
            bot.answer_callback_query(call.id, "Panel refreshed!")
        except Exception:
            pass

    elif data == "admin_broadcast_start":
        if user_id != ADMIN_ID:
            bot.answer_callback_query(call.id, "Access Denied!", show_alert=True)
            return
        ADMIN_BROADCAST_STATE[user_id] = True
        bot.answer_callback_query(call.id)
        bot.send_message(user_id, "📢 **Broadcast Mode Activated:**\n\nAap jo bhi agla message bhejoge, woh sabko chala jayega. Cancel ke liye `/cancel` bhejo.")

# ==========================================
# --- MESSAGE HANDLERS ---
# ==========================================
@bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'video', 'document', 'audio', 'animation'])
def handle_text(message):
    try:
        user_id = message.from_user.id
        chat_id = message.chat.id
        text_content = message.text

        # Handle Admin Broadcast Input
        if user_id == ADMIN_ID and ADMIN_BROADCAST_STATE.get(user_id):
            if text_content and text_content.lower() == "/cancel":
                ADMIN_BROADCAST_STATE[user_id] = False
                bot.reply_to(message, "❌ Broadcast cancelled.")
                return

            ADMIN_BROADCAST_STATE[user_id] = False
            user_ids = get_all_user_ids()
            success_count = 0
            fail_count = 0

            status_msg = bot.reply_to(message, f"📢 Broadcast started to {len(user_ids)} users...")

            for uid in user_ids:
                try:
                    bot.copy_message(chat_id=uid, from_chat_id=message.chat.id, message_id=message.message_id)
                    success_count += 1
                    time.sleep(0.04)
                except Exception as e:
                    logger.debug(f"Broadcast fail for {uid}: {e}")
                    fail_count += 1

            bot.edit_message_text(f"📢 **Broadcast Completed!**\n\n✅ Successful: `{success_count}`\n❌ Failed: `{fail_count}`", status_msg.chat.id, status_msg.message_id)
            return

        if not text_content:
            return

        # Rate Limiter
        current_time = time.time()
        if user_id in last_message_time:
            if current_time - last_message_time[user_id] < 1.5:
                return
        last_message_time[user_id] = current_time

        register_user(user_id, message.from_user.username, message.from_user.first_name)

        # Handle Reply Keyboard Buttons
        if text_content == "🎮 Guess Number":
            secret_number = random.randint(1, 50)
            ACTIVE_GAMES[chat_id] = {"target": secret_number, "attempts": 0}
            bot.reply_to(message, "🎮 **Guess the Number Battle!**\n1 se 50 ke beech ek number socha hai. Agar itni hi genius hai tera dimaag, toh ek baar mein guess karke dikha! 🤭", reply_markup=get_main_keyboard())
            return

        elif text_content == "🎯 Truth or Dare":
            tod_categories = [
                "🔥 **Truth:** Bata tu ne apni life mein sabse bada fattu wala kaam kaunsa kiya hai? 🤨",
                "🔥 **Truth:** Tera pehla crush kaun tha aur kya usne tujhe seedha reject kar diya tha? 👀",
                "🔥 **Truth:** Aaj tak ka sabse bada ajeeb 'kaand' jo tere ghar walon ko aaj tak nahi pata? 💀",
                "⚡ **Dare:** Apne kisi bhi friend ko voice note bhej kar bol — 'Mujhe apne aap se pyaar ho gaya hai' aur screenshot bhej! 🤣",
                "⚡ **Dare:** Apne phone ki gallery ka sabse random aur ajeeb photo bina context ke kisi dost ko bhej! 📸"
            ]
            selected_tod = random.choice(tod_categories)
            ACTIVE_TOD_GAMES[chat_id] = selected_tod
            bot.reply_to(message, f"🎯 **Truth or Dare Challenge:**\n\n{selected_tod}\n\n💬 *Chal ab smart ban kar jawab de ya task poora kar!* 😎", reply_markup=get_main_keyboard())
            return

        elif text_content == "🧩 Riddle Battle":
            riddles = [
                ("Aisi kaun si cheez hai jo jitni zyada saaf karo, utni hi gandi hoti hai?", "blackboard"),
                ("Woh kya hai jo paida hote hi bina pairo ke bhagne lagti hai?", "hawa"),
                ("Aisi kaun si cheez hai jo samandar mein paida hoti hai aur ghar mein aate hi gayab ho jati hai?", "namak")
            ]
            riddle, ans = random.choice(riddles)
            ACTIVE_RIDDLE_GAMES[chat_id] = ans
            bot.reply_to(message, f"🧩 **Riddle Battle Active:**\n\n*{riddle}*\n\n🧠 *Dimag ki batti jala aur sahi jawab dekar dikha!* 💡", reply_markup=get_main_keyboard())
            return

        elif text_content == "🔥 Roast War":
            roast_prompts = [
                "Bata bhai, itni lambi umar ho gayi par aaj tak koi dhang ki achievement hai ya bas resume mein jhuth likhne ki ninja technique aati hai? 💀",
                "Tera screen time dekh kar toh lagta hai tu real life se zyada digital world mein reject hota hai! 😂"
            ]
            selected_roast = random.choice(roast_prompts)
            ACTIVE_ROAST_GAMES[chat_id] = True
            bot.reply_to(message, f"🔥 **Roast War Initiated:**\n\n{selected_roast}\n\n💬 *Ab iska itna solid comeback de ki mera muh band ho jaye!* 🎯", reply_markup=get_main_keyboard())
            return

        elif text_content == "👤 View Profile":
            cmd_profile(message)
            return

        elif text_content == "🚀 Explore":
            bot.reply_to(message, "🚀 **Explore Venu's World:**\n\n🔹 Mind Games & Puzzles\n🔹 Roast Sessions\n🔹 Dynamic Coding & Tutoring\n🔹 Truth or Dare", reply_markup=get_main_keyboard())
            return

        elif text_content == "🧹 Clear Chat":
            cmd_clear(message)
            return

        # Game Handling
        if chat_id in ACTIVE_GAMES and text_content.isdigit():
            guess = int(text_content)
            game = ACTIVE_GAMES[chat_id]
            game["attempts"] += 1
            target = game["target"]

            if guess == target:
                del ACTIVE_GAMES[chat_id]
                bot.reply_to(message, f"🎉 Maana padega! Sirf `{game['attempts']}` attempts mein number guess kar liya! 🤣🔥", reply_markup=get_main_keyboard())
                return
            elif guess < target:
                bot.reply_to(message, "📈 Thoda bada number daal, itna chhota sochne se kaam nahi chalega! 😂", reply_markup=get_main_keyboard())
                return
            else:
                bot.reply_to(message, "📉 Thoda chhota number daal, hawa mein mat ud! 🥱", reply_markup=get_main_keyboard())
                return

        if chat_id in ACTIVE_RIDDLE_GAMES:
            correct_ans = ACTIVE_RIDDLE_GAMES.pop(chat_id)
            if correct_ans in text_content.lower():
                bot.reply_to(message, "🧠 Wah bhai, genius nikla tu! Ekdum sahi jawab diya! 🎯🔥", reply_markup=get_main_keyboard())
            else:
                bot.reply_to(message, f"❌ Galat jawab! Sahi jawab tha: *{correct_ans.capitalize()}*. 🤭", reply_markup=get_main_keyboard())
            return

        if chat_id in ACTIVE_ROAST_GAMES:
            ACTIVE_ROAST_GAMES.pop(chat_id)
            bot.reply_to(message, "Oho! Comeback toh solid diya hai.. par chal maan liya, dum hai tujhmein! 💀🔥", reply_markup=get_main_keyboard())
            return

        if chat_id in ACTIVE_TOD_GAMES:
            ACTIVE_TOD_GAMES.pop(chat_id)
            bot.reply_to(message, "Maan gaye bhai, kya fearless move tha yeh! 🤭🔥", reply_markup=get_main_keyboard())
            return

        try_react_to_message(message.chat.id, message.message_id, text_content)

        # Typing simulation
        stop_typing = threading.Event()
        t_thread = threading.Thread(target=trigger_typing, args=(message.chat.id, stop_typing))
        t_thread.daemon = True
        t_thread.start()

        save_message(user_id, "user", text_content)
        update_user_cache(user_id, "user", text_content)

        # Retrieve Profile, Current Mood, and Unique AI Response
        profile = get_user_profile(user_id, message.from_user.first_name)
        current_mood = detect_mood(text_content)
        update_user_profile_field(user_id, "current_mood", current_mood)
        profile["current_mood"] = current_mood

        history = get_deep_chat_history(user_id, limit=30)
        response = generate_ai_response(user_id, history, profile, current_mood, text_content)

        stop_typing.set()
        t_thread.join(timeout=1)

        time.sleep(random.uniform(0.3, 0.8))

        save_message(user_id, "assistant", response)
        update_user_cache(user_id, "assistant", response)

        bot.reply_to(message, response, reply_markup=get_main_keyboard())

    except Exception as e:
        logger.error(f"Critical execution error in text handler: {e}")
        notify_admin(str(e))

# ==========================================
# --- MAIN PRODUCTION LOOP ---
# ==========================================
if __name__ == "__main__":
    logger.info("🚀 Starting Production-Grade Venu Telegram Bot & Keep-Alive Server...")

    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    logger.info("🌐 Flask Keep-Alive server running on background thread.")

    try:
        bot.remove_webhook()
        logger.info("🧹 Existing Webhooks cleared successfully.")
    except Exception as e:
        logger.warning(f"Could not remove webhook: {e}")

    backoff = 1
    max_backoff = 60

    while True:
        try:
            logger.info("🔄 Bot polling started securely with retry logic...")
            bot.polling(none_stop=True, interval=0, timeout=30, long_polling_timeout=30)
            backoff = 1
        except Exception as e:
            sleep_time = backoff + random.uniform(0, 1)
            logger.error(f"Polling exception: {e}. Reconnecting in {sleep_time:.2f} seconds...")
            notify_admin(str(e))
            time.sleep(sleep_time)
            backoff = min(backoff * 2, max_backoff)
