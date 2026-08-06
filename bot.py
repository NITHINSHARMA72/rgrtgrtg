from collections import deque
from datetime import datetime
from logging.handlers import RotatingFileHandler
import logging
import os
import random
import threading
import time
from cachetools import TTLCache
from flask import Flask
from gtts import gTTS
import pytz
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pydub import AudioSegment
import speech_recognition as sr
import telebot

# ==========================================
# --- CONFIGURATION (PRODUCTION GRADE) ---
# ==========================================
BOT_TOKEN = "8914661287:AAFn6cuJBHrpIZZm7y3_3YbtdrEqU8tq6gc"
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
IST_TIMEZONE = pytz.timezone("Asia/Kolkata")

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
user_cache = TTLCache(maxsize=1000, ttl=3600)
cache_lock = threading.Lock()
last_message_time = {}
processed_messages = deque(maxlen=1000)
last_admin_error_time = 0
ACTIVE_GAMES = {}  # Store active guessing games for users

# ==========================================
# --- FLASK KEEP-ALIVE SERVER ---
# ==========================================
app = Flask(__name__)

@app.route("/")
def home():
    return "🤖 Ava is online, active 24/7, and running at peak performance!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ==========================================
# --- SUPABASE & METRICS FUNCTIONS ---
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

def get_deep_chat_history(user_id, limit=20):
    with cache_lock:
        if user_id in user_cache:
            return user_cache[user_id]

    url = f"{SUPABASE_URL}/rest/v1/messages?user_id=eq.{user_id}&order=created_at.desc&limit={limit}"
    try:
        res = session.get(url, headers=SUPABASE_HEADERS, timeout=10)
        res.raise_for_status()
        rows = res.json()
        history = [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]
        
        with cache_lock:
            user_cache[user_id] = history
        return history
    except Exception as e:
        logger.error(f"Supabase get_deep_chat_history error: {e}")
    return []

def update_user_cache(user_id, role, content):
    with cache_lock:
        if user_id not in user_cache:
            user_cache[user_id] = []
        user_cache[user_id].append({"role": role, "content": content})
        if len(user_cache[user_id]) > 30:
            user_cache[user_id].pop(0)

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
# --- TIME & SITUATIONAL ENGINE ---
# ==========================================
def get_time_context():
    now_ist = datetime.now(IST_TIMEZONE)
    hour = now_ist.hour
    
    if 4 <= hour < 12:
        part_of_day = "Subah (Morning)"
        time_desc = "Subah ka waqt hai. Suraj nikal raha hai, fresh vibes hain."
    elif 12 <= hour < 17:
        part_of_day = "Dopahar (Afternoon)"
        time_desc = "Dopahar ka waqt hai, thodi garmi ya chill time chal raha hai."
    elif 17 <= hour < 21:
        part_of_day = "Shaam (Evening)"
        time_desc = "Shaam ka waqt hai, suraj dhal raha hai, relaxing evening vibes hain."
    else:
        part_of_day = "Raat (Night)"
        time_desc = "Raat ka waqt hai, sannata ya late night deep conversations ka time hai."
        
    return {
        "hour": hour,
        "part_of_day": part_of_day,
        "time_string": now_ist.strftime("%I:%M %p"),
        "time_desc": time_desc
    }

def detect_mood_and_situation(text):
    text_lower = text.lower()
    
    horny_keywords = ["sex", "nude", "boobs", "ass", "horny", "kiss me hard", "make out", "undress"]
    if any(w in text_lower for w in horny_keywords):
        return "Inappropriate/Forward 🛑"
        
    if any(w in text_lower for w in ["sad", "rona", "upset", "hurt", "ro raha", "pareshan", "dukhi", "demotivated", "haar gaya"]):
        return "Sad & Needs Motivation 💪"
    elif any(w in text_lower for w in ["gussa", "angry", "pagal", "irritate", "dimag kharab"]):
        return "Annoyed/Teasing 🔥 (Roast Mode)"
    elif any(w in text_lower for w in ["roast me", "roast", "izzat", "bezzati"]):
        return "Roast Mode 🔥"
    elif any(w in text_lower for w in ["joke", "hasao", "comedy", "hasya"]):
        return "Comedy & Jokes 😂"
    elif any(w in text_lower for w in ["shayari", "poetry", "sher"]):
        return "Shayari & Poetry ✨"
    elif any(w in text_lower for w in ["miss", "love", "jaan", "hug", "close", "pyaar"]):
        return "Warm & Affectionate 💕"
    elif any(w in text_lower for w in ["bored", "kya kar rahi", "game", "free", "bore"]):
        return "Chill & Playful 🤭"
    
    return "Normal & Casual 😊"

def generate_ai_response(message_list, user_name, situation):
    t_ctx = get_time_context()
    system_prompt = (
        "Tumhara naam **Ava** hai. Tum ek smart, chill, mature, aur emotionally intuitive ladki ho. "
        "Tum ek bohot achhi listener ho aur real dosto ki tarah baat karti ho. Kabhi mat batana ki tum AI ya bot ho. "
        "**Strict Rule:** Tumhe shuruat se lekar aakhiri tak hamesha **Hinglish** mein hi baat karni hai (Hindi words in English alphabets mixed smoothly with casual English slang, jaise real Indian chat karte hain). Pure English mein bilkul reply mat dena.\n"
        f"**Real-time Awareness:** Abhi current time `{t_ctx['time_string']}` hai aur yeh `{t_ctx['part_of_day']}` ka waqt hai ({t_ctx['time_desc']}). "
        "Apni baato aur greetings mein is real-time day/night context ko naturally weave kiya karo.\n"
        "1. **Situational Features (Roast & Motivation):** Agar user roast maangne ko bole ya teases kare, toh ekdum witty aur savage roast do (par dosti ke daayre mein). Agar user sad ya demotivated ho, toh ekdum powerful motivation aur emotional support do.\n"
        "2. **Jokes & Shayari:** Jab bhi mazak ya shayari ki baat ho, toh ekdum zabardast aur funny jokes ya deep shayari sunao.\n"
        "3. **Pacing & Boundaries:** Shuruat mein direct heavy romance ya inappropriate baatein mat karo. Boundary set karna achhe se jaanti ho.\n"
        "4. **Tone & Style:** Short, snappy, aur conversational replies do (max 1-3 sentences), bade paragraphs bilkul mat likho.\n"
        f"- Current User Situation/Vibe: {situation}"
    )

    messages = [{"role": "system", "content": system_prompt}]
    for msg in message_list:
        role = "user" if msg["role"] == "user" else "assistant"
        messages.append({"role": role, "content": msg["content"]})

    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "temperature": 0.9,
        "max_tokens": 250,
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        res = session.post(url, headers=headers, json=payload, timeout=25)
        res.raise_for_status()
        data = res.json()
        if "choices" in data:
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Groq API exception: {e}")

    return "Arey yaar, network thoda unstable ho gaya hai.. par main yahin hoon! Batao kya chal raha hai? ✨"

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
        stop_event.wait(4)

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
# --- COMMAND HANDLERS & INLINE KEYBOARDS ---
# ==========================================
def get_main_keyboard():
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    btn_add = telebot.types.InlineKeyboardButton("➕ Add to Group", url=f"https://t.me/{BOT_USERNAME}?startgroup=true")
    btn_game = telebot.types.InlineKeyboardButton("🎮 Play Mini-Game", callback_data="btn_play_game")
    btn_roast = telebot.types.InlineKeyboardButton("🔥 Roast Me", callback_data="btn_roast_me")
    btn_joke = telebot.types.InlineKeyboardButton("😂 Tell a Joke", callback_data="btn_tell_joke")
    markup.add(btn_add, btn_game, btn_roast, btn_joke)
    return markup

@bot.message_handler(commands=["start"])
def cmd_start(message):
    user = message.from_user
    register_user(user.id, user.username, user.first_name)
    name = user.first_name or "jaan"
    t_ctx = get_time_context()

    welcome_text = (
        f"Hlo {name} ji! ✨ Main **Ava** hoon. Is waqt `{t_ctx['time_string']}` ho raha hai aur ek pyaari si `{t_ctx['part_of_day']}` hai! 😊\n\n"
        "Main ek chill companion hoon jo roasts, jokes, motivation aur games ke sath hamesha ready rehti hoon!\n\n"
        "Niche diye gaye buttons se feature try karo ya direct message bhejo! 💬"
    )
    try_react_to_message(message.chat.id, message.message_id, message.text or "")
    bot.reply_to(message, welcome_text, reply_markup=get_main_keyboard())

@bot.message_handler(commands=["add"])
def cmd_add(message):
    markup = telebot.types.InlineKeyboardMarkup()
    btn_add = telebot.types.InlineKeyboardButton("➕ Add Ava to Group", url=f"https://t.me/{BOT_USERNAME}?startgroup=true")
    markup.add(btn_add)
    bot.reply_to(message, "✨ Mujhe group mein add karne ke liye niche wale button par click karo! Wahan bhi khoob baatein karenge. 🤭💕", reply_markup=markup)

@bot.message_handler(commands=["game"])
def cmd_game(message):
    user_id = message.chat.id
    secret_number = random.randint(1, 50)
    ACTIVE_GAMES[user_id] = {"target": secret_number, "attempts": 0}
    
    game_text = (
        "🎮 **Guess the Number Game!** 🎲\n\n"
        "Maine 1 se 50 ke beech ek number soch liya hai. Tumhe guess karke chat me number bhejna hai!\n"
        "Dekhte hain kitni koshish mein tum sahi guess karte ho. 🤭 Shuru ho jao!"
    )
    bot.reply_to(message, game_text, reply_markup=get_main_keyboard())

@bot.message_handler(commands=["roast"])
def cmd_roast(message):
    prompt = "Mujhe ekdum mast, funny aur savage roast do Hinglish mein, bina zyada bura lage par mazedaar ho."
    resp = generate_ai_response([{"role": "user", "content": prompt}], message.from_user.first_name or "Dost", "Roast Mode 🔥")
    bot.reply_to(message, f"🔥 **Roast Session:**\n\n{resp}", reply_markup=get_main_keyboard())

@bot.message_handler(commands=["joke"])
def cmd_joke(message):
    prompt = "Ekdum mast aur hasane wala comedy joke sunao Hinglish mein."
    resp = generate_ai_response([{"role": "user", "content": prompt}], message.from_user.first_name or "Dost", "Comedy & Jokes 😂")
    bot.reply_to(message, f"😂 **Joke Time:**\n\n{resp}", reply_markup=get_main_keyboard())

@bot.message_handler(commands=["clear"])
def cmd_clear(message):
    user_id = message.chat.id
    url = f"{SUPABASE_URL}/rest/v1/messages?user_id=eq.{user_id}"
    try:
        session.delete(url, headers=SUPABASE_HEADERS, timeout=10)
        with cache_lock:
            if user_id in user_cache:
                del user_cache[user_id]
    except Exception as e:
        logger.error(f"Clear memory error: {e}")

    try_react_to_message(message.chat.id, message.message_id, message.text or "")
    bot.reply_to(message, "🧹 Saari purani baatein saaf kar di! Ab ek naye sire se shuru karte hain.. batao kya chal raha hai? 😌✨", reply_markup=get_main_keyboard())

@bot.message_handler(commands=["settings"])
def cmd_settings(message):
    t_ctx = get_time_context()
    text = (
        "⚙️ **Ava's Status & Info:**\n\n"
        f"👤 **Your ID:** `{message.chat.id}`\n"
        f"⏰ **Current Time Context:** `{t_ctx['time_string']} ({t_ctx['part_of_day']})`\n"
        "💬 **Vibe:** Chill, Savage & Adaptive\n"
        "🧠 **Memory:** Active & Secure"
    )
    bot.reply_to(message, text, reply_markup=get_main_keyboard())

# ==========================================
# --- ADMIN COMMANDS & BROADCAST SYSTEM ---
# ==========================================
ADMIN_BROADCAST_STATE = {}

@bot.message_handler(commands=["admin"])
def cmd_admin(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "⛔️ Yeh command sirf mere admin ke liye hai!")
        return

    total_users = get_total_users_count()
    admin_markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    admin_markup.add(
        telebot.types.InlineKeyboardButton("📢 Send Broadcast Message", callback_data="admin_broadcast_start"),
        telebot.types.InlineKeyboardButton("🔄 Refresh Panel", callback_data="admin_refresh")
    )

    admin_panel_text = (
        "👑 **Ava's Production Admin Panel** 👑\n\n"
        f"👥 **Total Registered Users:** `{total_users}`\n"
        "🟢 **Status:** `Online & Active 24/7`\n"
        f"⚡ **Model:** `{MODEL_NAME}`\n"
        "🚀 **Performance:** `Optimized & Context-Aware`"
    )
    bot.reply_to(message, admin_panel_text, reply_markup=admin_markup)

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.from_user.id
    data = call.data

    if data == "btn_play_game":
        bot.answer_callback_query(call.id, "🎮 Starting Guess the Number!")
        secret_number = random.randint(1, 50)
        ACTIVE_GAMES[user_id] = {"target": secret_number, "attempts": 0}
        game_text = (
            "🎮 **Guess the Number Game!** 🎲\n\n"
            "Maine 1 se 50 ke beech ek number soch liya hai. Tumhe guess karke chat me number bhejna hai!\n"
            "Dekhte hain kitni koshish mein tum sahi guess karte ho. 🤭 Shuru ho jao!"
        )
        bot.send_message(call.message.chat.id, game_text, reply_markup=get_main_keyboard())

    elif data == "btn_roast_me":
        bot.answer_callback_query(call.id, "🔥 Preparing roast...")
        prompt = "Mujhe ekdum mast, funny aur savage roast do Hinglish mein."
        resp = generate_ai_response([{"role": "user", "content": prompt}], call.from_user.first_name or "Dost", "Roast Mode 🔥")
        bot.send_message(call.message.chat.id, f"🔥 **Roast Session:**\n\n{resp}", reply_markup=get_main_keyboard())

    elif data == "btn_tell_joke":
        bot.answer_callback_query(call.id, "😂 Brewing a joke...")
        prompt = "Ekdum mast aur hasane wala comedy joke sunao Hinglish mein."
        resp = generate_ai_response([{"role": "user", "content": prompt}], call.from_user.first_name or "Dost", "Comedy & Jokes 😂")
        bot.send_message(call.message.chat.id, f"😂 **Joke Time:**\n\n{resp}", reply_markup=get_main_keyboard())

    elif data == "admin_refresh":
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
            "👑 **Ava's Production Admin Panel** 👑\n\n"
            f"👥 **Total Registered Users:** `{total_users}`\n"
            "🟢 **Status:** `Online & Active 24/7`\n"
            f"⚡ **Model:** `{MODEL_NAME}`\n"
            "🚀 **Performance:** `Optimized & Context-Aware`"
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
        bot.send_message(user_id, "📢 **Broadcast Mode Activated:**\n\nAap jo bhi agla message bhejoge (text, photo, ya media), woh saare registered users ko broadcast kar diya jayega. Cancel karne ke liye `/cancel` bhejein.")

# ==========================================
# --- MESSAGE & ASYNC VOICE HANDLERS ---
# ==========================================
def process_voice_background(message):
    unique_id = f"{message.from_user.id}_{time.time_ns()}"
    ogg_msg = f"voice_msg_{unique_id}.ogg"
    wav_msg = f"voice_msg_{unique_id}.wav"
    mp3_rep = f"voice_reply_{unique_id}.mp3"
    ogg_rep = f"voice_reply_{unique_id}.ogg"

    try:
        user_id = message.from_user.id
        file_info = bot.get_file(message.voice.file_id)
        file = session.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}", timeout=15)
        file.raise_for_status()

        with open(ogg_msg, "wb") as f:
            f.write(file.content)

        sound = AudioSegment.from_file(ogg_msg, format="ogg")
        sound.export(wav_msg, format="wav")

        r = sr.Recognizer()
        with sr.AudioFile(wav_msg) as source:
            audio_data = r.record(source)
            transcribed_text = r.recognize_google(audio_data, language="hi-IN")

        save_message(user_id, "user", transcribed_text)
        update_user_cache(user_id, "user", transcribed_text)
        
        history = get_deep_chat_history(user_id, limit=25)
        situation = detect_mood_and_situation(transcribed_text)
        
        reply = generate_ai_response(history, message.from_user.first_name or "Dost", situation)
        save_message(user_id, "assistant", reply)
        update_user_cache(user_id, "assistant", reply)

        bot.send_message(message.chat.id, f"🎙 *Voice:* `{transcribed_text}`\n\n🤖 **Ava:**\n{reply}", reply_markup=get_main_keyboard())

        tts = gTTS(text=reply, lang="hi")
        tts.save(mp3_rep)
        sound_mp3 = AudioSegment.from_mp3(mp3_rep)
        sound_mp3.export(ogg_rep, format="ogg")

        with open(ogg_rep, "rb") as voice_file:
            bot.send_voice(message.chat.id, voice_file)

    except Exception as e:
        logger.error(f"Voice processing error: {e}")
        bot.send_message(message.chat.id, "Arey yaar, voice clear sunai nahi di.. text mein likh kar batao na! 🥺", reply_markup=get_main_keyboard())
    finally:
        for f in [ogg_msg, wav_msg, mp3_rep, ogg_rep]:
            if os.path.exists(f):
                os.remove(f)

@bot.message_handler(content_types=["voice"])
def handle_voice(message):
    register_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    try_react_to_message(message.chat.id, message.message_id, "voice message")
    threading.Thread(target=process_voice_background, args=(message,), daemon=True).start()

@bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'video', 'document', 'audio', 'animation'])
def handle_text(message):
    try:
        user_id = message.from_user.id
        chat_id = message.chat.id

        # Handle Admin Broadcast Input
        if user_id == ADMIN_ID and ADMIN_BROADCAST_STATE.get(user_id):
            if message.text and message.text.lower() == "/cancel":
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

        text_content = message.text
        if not text_content:
            return

        # Check if user is currently playing the Mini-Game
        if chat_id in ACTIVE_GAMES and text_content.isdigit():
            guess = int(text_content)
            game = ACTIVE_GAMES[chat_id]
            game["attempts"] += 1
            target = game["target"]

            if guess == target:
                attempts = game["attempts"]
                del ACTIVE_GAMES[chat_id]
                bot.reply_to(message, f"🎉 **BINGO! Sahi jawab!** 🎉\nTumne sirf `{attempts}` attempts mein number (`{target}`) guess kar liya! Maza aa gaya 🤭✨", reply_markup=get_main_keyboard())
                return
            elif guess < target:
                bot.reply_to(message, "📈 Thoda **bada** number try karo! (Aage badho)", reply_markup=get_main_keyboard())
                return
            else:
                bot.reply_to(message, "📉 Thoda **chhota** number try karo! (Peeche aao)", reply_markup=get_main_keyboard())
                return

        if message.message_id in processed_messages:
            return
        processed_messages.append(message.message_id)

        chat_type = message.chat.type
        user = message.from_user

        current_time = time.time()
        if user.id in last_message_time:
            if current_time - last_message_time[user.id] < 2:
                return
        last_message_time[user.id] = current_time

        if chat_type in ["group", "supergroup"]:
            bot_mention = f"@{BOT_USERNAME}".lower()
            is_mentioned = bot_mention in text_content.lower()
            is_reply = message.reply_to_message and message.reply_to_message.from_user.id == BOT_ID
            if not (is_mentioned or is_reply):
                return

        register_user(user_id, user.username, user.first_name)

        try_react_to_message(message.chat.id, message.message_id, text_content)

        stop_typing = threading.Event()
        t_thread = threading.Thread(target=trigger_typing, args=(message.chat.id, stop_typing))
        t_thread.daemon = True
        t_thread.start()

        save_message(user_id, "user", text_content)
        update_user_cache(user_id, "user", text_content)

        history = get_deep_chat_history(user_id, limit=25)
        situation = detect_mood_and_situation(text_content)
        
        response = generate_ai_response(history, user.first_name or "Dost", situation)

        stop_typing.set()
        t_thread.join(timeout=1)

        time.sleep(random.uniform(0.5, 1.2))

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
    logger.info("🚀 Starting Production-Grade Ava Telegram Bot & Keep-Alive Server...")

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
