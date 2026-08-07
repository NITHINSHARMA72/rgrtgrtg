from collections import deque
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
cache_lock = threading.Lock()
last_message_time = {}
processed_messages = deque(maxlen=1500)
last_admin_error_time = 0

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

def get_deep_chat_history(user_id, limit=30):
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
        if len(user_cache[user_id]) > 40:
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
# --- PERSONALITY & SITUATIONAL ENGINE ---
# ==========================================
def detect_mood_and_situation(text):
    text_lower = text.lower()
    
    horny_keywords = ["sex", "nude", "boobs", "ass", "horny", "kiss me hard", "make out", "undress"]
    if any(w in text_lower for w in horny_keywords):
        return "Inappropriate/Forward 🛑"
        
    if any(w in text_lower for w in ["sad", "rona", "upset", "hurt", "ro raha", "pareshan", "dukhi"]):
        return "Sad & Needs Support 🥺"
    elif any(w in text_lower for w in ["gussa", "angry", "pagal", "irritate", "dimag kharab"]):
        return "Annoyed/Teasing 😤"
    elif any(w in text_lower for w in ["miss", "love", "jaan", "hug", "close", "pyaar"]):
        return "Warm & Affectionate 💕"
    elif any(w in text_lower for w in ["bored", "kya kar rahi", "joke", "game", "free", "bore"]):
        return "Chill & Playful 🤭"
    
    return "Normal & Casual 😊"

def generate_ai_response(message_list, user_name, situation):
    system_prompt = (
        "Tumhara naam **Venu** hai. Tum ek extremely intelligent, razor-sharp, witty, aur sarcastic desi dost ho jo bina kisi abusive language ke logon ki halki-fulki aur mast roasting karti hai. "
        "**Core Behaviour Rules:**\n"
        "1. **Context & Depth Check:** User ke message ko dhyan se padho. Uske intent, mood, aur history ko analyze kar ke ek unique, non-repetitive reply do.\n"
        "2. **Smart & Sarcastic Tone:** Normal baaton mein friendly raho, par witty banter aur playful taunts ke sath.\n"
        "3. **Hinglish Style:** Modern Hinglish mein baat karo. Short, snappy replies do (1-3 sentences max).\n"
        "4. **No Identity Leak:** Kabhi mat bolna ki tum AI ho.\n"
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

    return "Arey genius, tera internet ya mera dimag thoda slow ho gaya hai.. par tu bol, kya naya kaand kiya aaj? 😏🔥"

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
# --- KEYBOARD BUILDER (REPLY KEYBOARD) ---
# ==========================================
def get_main_keyboard():
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn_game1 = telebot.types.KeyboardButton("🎮 Guess Number")
    btn_game2 = telebot.types.KeyboardButton("🎯 Truth or Dare")
    btn_game3 = telebot.types.KeyboardButton("🧩 Riddle Battle")
    btn_game4 = telebot.types.KeyboardButton("🔥 Roast War")
    btn_explore = telebot.types.KeyboardButton("🚀 Explore")
    btn_clear = telebot.types.KeyboardButton("🧹 Clear Chat")
    btn_add_group = telebot.types.KeyboardButton("➕ Add Me To Group")
    markup.add(btn_game1, btn_game2, btn_game3, btn_game4, btn_explore, btn_clear, btn_add_group)
    return markup

# ==========================================
# --- COMMAND HANDLERS ---
# ==========================================
@bot.message_handler(commands=["start"])
def cmd_start(message):
    user = message.from_user
    register_user(user.id, user.username, user.first_name)
    name = user.first_name or "dost"

    welcome_text = (
        f"Oye {name}! ✨ Main **Venu** hoon. Teri baatein sunne aur teri smartness check karne ke liye 24/7 ready. "
        "Bata aaj kis mood mein hai—kisi game mein haraun ya aaram se baatein karein? 😎🔥"
    )
    try_react_to_message(message.chat.id, message.message_id, message.text or "")
    bot.reply_to(message, welcome_text, reply_markup=get_main_keyboard())

@bot.message_handler(commands=["add"])
def cmd_add(message):
    group_link = f"https://t.me/{BOT_USERNAME}?startgroup=true"
    bot.reply_to(message, f"✨ Mujhe apne group mein add karne ke liye niche diye gaye link par click karo:\n\n👉 {group_link}", reply_markup=get_main_keyboard())

@bot.message_handler(commands=["help"])
def cmd_help(message):
    help_text = (
        "✨ **Venu's Command Center:**\n\n"
        "🔹 `/start` - Bot restart karo\n"
        "🔹 `/add` - Group add link lo\n"
        "🔹 `/clear` - Purani memory saaf karo\n"
        "🔹 `/settings` - Status dekho\n"
    )
    if message.from_user.id == ADMIN_ID:
        help_text += "👑 `/admin` - Admin Panel\n"
    bot.reply_to(message, help_text, reply_markup=get_main_keyboard())

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
    bot.reply_to(message, "🧹 Saari purani chat saaf kar di! Lagta hai purane kaand chhupane ki koshish chal rahi hai. 😌✨", reply_markup=get_main_keyboard())

@bot.message_handler(commands=["settings"])
def cmd_settings(message):
    user_id = message.chat.id
    text = (
        "⚙️ **Venu Status & Info:**\n\n"
        f"👤 **Your ID:** `{user_id}`\n"
        "💬 **Vibe:** Razor-Sharp & Witty Desi\n"
        "🧠 **Memory & Context:** Fully Active"
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
        "🟢 **Status:** `Online & Roasting 24/7`\n"
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
            "🟢 **Status:** `Online & Roasting 24/7`\n"
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

        bot.send_message(message.chat.id, f"🎙 *Voice heard:* `{transcribed_text}`\n\n🤖 **Venu:**\n{reply}")

        tts = gTTS(text=reply, lang="hi")
        tts.save(mp3_rep)
        sound_mp3 = AudioSegment.from_mp3(mp3_rep)
        sound_mp3.export(ogg_rep, format="ogg")

        with open(ogg_rep, "rb") as voice_file:
            bot.send_voice(message.chat.id, voice_file)

    except Exception as e:
        logger.error(f"Voice processing error: {e}")
        bot.send_message(message.chat.id, "Arey, teri voice suni toh samajh aaya ki mic badalne ka time aa gaya hai.. text mein likh kar bata de! 🤭")
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

        # Rate Limiter (1.5 seconds cooldown)
        current_time = time.time()
        if user_id in last_message_time:
            if current_time - last_message_time[user_id] < 1.5:
                return
        last_message_time[user_id] = current_time

        # Strict Group Filter
        if message.chat.type in ["group", "supergroup"]:
            bot_mention = f"@{BOT_USERNAME}".lower()
            is_mentioned = bot_mention in text_content.lower()
            is_reply = message.reply_to_message and message.reply_to_message.from_user.id == BOT_ID
            if not (is_mentioned or is_reply):
                return

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
                "🔥 **Truth:** Agar tujhe ek din ke liye invisible hone ka mauka mile, toh sabse pehle kiske phone ki history check karega? 🤫",
                "🔥 **Truth:** Tera koi aisa secret jise sunkar tera best friend bhi sharma jaye? 🤐",
                "⚡ **Dare:** Apne kisi bhi friend ko voice note bhej kar bol — 'Mujhe apne aap se pyaar ho gaya hai' aur screenshot bhej! 🤣",
                "⚡ **Dare:** Apne phone ki gallery ka sabse random aur ajeeb photo bina context ke kisi dost ko bhej! 📸",
                "⚡ **Dare:** Agle 10 minutes tak tu jo bhi message karega, uske aakhiri mein 'UwU 🥺' lagana padega! ✨",
                "⚡ **Dare:** Apne last call log ka screenshot bhej (jisme naam dikhe ya blur karde agar sharam aaye)! 📞",
                "⚡ **Dare:** Apni crush ya ex ka naam chat mein type karke turant delete kar de! 🏃‍♂️"
            ]
            selected_tod = random.choice(tod_categories)
            ACTIVE_TOD_GAMES[chat_id] = selected_tod
            bot.reply_to(message, f"🎯 **Truth or Dare Challenge:**\n\n{selected_tod}\n\n💬 *Chal ab smart ban kar jawab de ya task poora kar!* 😎", reply_markup=get_main_keyboard())
            return

        elif text_content == "🧩 Riddle Battle":
            riddles = [
                ("Aisi kaun si cheez hai jo jitni zyada saaf karo, utni hi gandi hoti hai?", "blackboard"),
                ("Woh kya hai jo paida hote hi bina pairo ke bhagne lagti hai?", "hawa"),
                ("Aisi kaun si cheez hai jo samandar mein paida hoti hai aur ghar mein aate hi gayab ho jati hai?", "namak"),
                ("Aisi kaun si cheez hai jise aage se tum dekhte ho aur peeche se bhagwan dekhta hai?", "bicycle")
            ]
            riddle, ans = random.choice(riddles)
            ACTIVE_RIDDLE_GAMES[chat_id] = ans
            bot.reply_to(message, f"🧩 **Riddle Battle Active:**\n\n*{riddle}*\n\n🧠 *Dimag ki batti jala aur sahi jawab dekar dikha!* 💡", reply_markup=get_main_keyboard())
            return

        elif text_content == "🔥 Roast War":
            roast_prompts = [
                "Bata bhai, itni lambi umar ho gayi par aaj tak koi dhang ki achievement hai ya bas resume mein jhuth likhne ki ninja technique aati hai? 💀",
                "Tera screen time dekh kar toh lagta hai tu real life se zyada digital world mein reject hota hai! 😂",
                "Aisi shakal ke sath confidence kahan se laate ho? Thodi training humein bhi dilwa do! 🤭",
                "Tujhse baat karke lagta hai ki evolution ne beech mein hi process rokk diya tha! 🔥"
            ]
            selected_roast = random.choice(roast_prompts)
            ACTIVE_ROAST_GAMES[chat_id] = True
            bot.reply_to(message, f"🔥 **Roast War Initiated:**\n\n{selected_roast}\n\n💬 *Ab iska itna solid comeback de ki mera muh band ho jaye!* 🎯", reply_markup=get_main_keyboard())
            return

        elif text_content == "🚀 Explore":
            explore_options = [
                (
                    "🚀 **Explore Venu's World (Edition 1):**\n\n"
                    "🔹 **Mind Games:** Riddles aur number puzzles se dimaag ka dahi karvane ke liye ready raho!\n"
                    "🔹 **Roast Session:** Agar khud ki bezzati karwa kar sudharna hai, toh mujhe topic do.\n"
                    "🔹 **Deep Chats:** Space, history, ya zindagi ke random filozofical sawal pucho."
                ),
                (
                    "🚀 **Explore Venu's World (Edition 2):**\n\n"
                    "🔹 **Story Time:** Koi aisi suspenseful kahani suno jo raat ko neend uda de.\n"
                    "🔹 **Life Advice:** Agar kisi confusion mein ho, toh ek sarcasm wali expert salah lo.\n"
                    "🔹 **Jokes & Tech:** Latest trends aur comedy ka unlimited dose!"
                ),
                (
                    "🚀 **Explore Venu's World (Edition 3):**\n\n"
                    "🔹 **Truth or Dare:** Apne secrets bahar nikalwane ka best tareeqa.\n"
                    "🔹 **Confession Box:** Apne dil ka bojh halka karo, yahan sab secure hai.\n"
                    "🔹 **Challenge Me:** Koi bhi impossible sawal pooch kar mujhe test karo!"
                )
            ]
            bot.reply_to(message, random.choice(explore_options), reply_markup=get_main_keyboard())
            return

        elif text_content == "🧹 Clear Chat":
            url = f"{SUPABASE_URL}/rest/v1/messages?user_id=eq.{user_id}"
            try:
                session.delete(url, headers=SUPABASE_HEADERS, timeout=10)
                with cache_lock:
                    if user_id in user_cache:
                        del user_cache[user_id]
            except Exception as e:
                logger.error(f"Clear memory error: {e}")
            bot.reply_to(message, "🧹 Saari chat saaf kar di! Naye sire se bezzati aur baatein shuru karte hain. 😌✨", reply_markup=get_main_keyboard())
            return

        elif text_content == "➕ Add Me To Group":
            group_link = f"https://t.me/{BOT_USERNAME}?startgroup=true"
            bot.reply_to(message, f"✨ Mujhe apne group mein add karne ke liye niche diye gaye link par click karo:\n\n👉 {group_link}", reply_markup=get_main_keyboard())
            return

        # Check if user is playing Guess Number Game (Dynamic Diverse Responses)
        if chat_id in ACTIVE_GAMES and text_content.isdigit():
            guess = int(text_content)
            game = ACTIVE_GAMES[chat_id]
            game["attempts"] += 1
            target = game["target"]

            if guess == target:
                attempts = game["attempts"]
                del ACTIVE_GAMES[chat_id]
                win_replies = [
                    f"🎉 **Maana padega!** Sirf `{attempts}` attempts mein number (`{target}`) guess kar liya.. lagta hai aaj qismat achhi hai teri! 🤣🔥",
                    f"🎯 Boom! `{attempts}` baar mein target hit kar diya. Lagta hai aaj dimaag extra speed pe chal raha hai tera! 🚀",
                    f"✨ Sahi pakda! `{attempts}` tries mein number mila liya.. itni sharpness kahan se laate ho bhai? 🤭"
                ]
                bot.reply_to(message, random.choice(win_replies), reply_markup=get_main_keyboard())
                return
            elif guess < target:
                low_replies = [
                    "📈 Thoda bada number daal, itna chhota sochne se kaam nahi chalega! 😂",
                    "📉 Arey thoda upar jaao bhai, itne neeche target thodi baithega! 🥱",
                    "🚀 Zameen se thoda upar utho, number kaafi aage hai! 🤭",
                    "💡 Itna conservative guess kyun? Thoda bada number phenk kar dekho! 🎯"
                ]
                bot.reply_to(message, random.choice(low_replies), reply_markup=get_main_keyboard())
                return
            else:
                high_replies = [
                    "📉 Thoda chhota number daal, hawa mein mat ud! 🥱",
                    "🛑 Arey itna upar mat jao, rocket thodi launch karna hai! Thoda neeche aao! 😂",
                    "📉 Bhavishya mein udne se pehle number thoda chhota karke dekho, isse neeche hai! 🤫",
                    "⚖️ Limit mein raho dost, number isse kaafi chhota hai! 📉"
                ]
                bot.reply_to(message, random.choice(high_replies), reply_markup=get_main_keyboard())
                return

        # Check if user is responding to Riddle Battle (Dynamic Diverse Responses)
        if chat_id in ACTIVE_RIDDLE_GAMES:
            correct_ans = ACTIVE_RIDDLE_GAMES.pop(chat_id)
            if correct_ans in text_content.lower():
                riddle_win = [
                    "🧠 **Wah bhai, genius nikla tu!** Ekdum sahi jawab diya, lagta hai dimaag ki exercise shuru kar di hai tune! 🎯🔥",
                    "🔥 Sahi pakde hain! Is riddle ka yahi jawab tha. Maan gaye aapke sharp dimaag ko! 😌✨",
                    "💡 Brilliant! Ek hi baar mein correct jawab de diya. Aaj lagta hai full form mein ho! 🚀"
                ]
                bot.reply_to(message, random.choice(riddle_win), reply_markup=get_main_keyboard())
            else:
                riddle_fail = [
                    f"❌ **Aha, galat jawab!** Sahi jawab tha: *{correct_ans.capitalize()}*. Agli baar thoda dimaag laga kar aana! 🤭",
                    f"🤦‍♂️ Ghanta sahi jawab! Right answer tha: *{correct_ans.capitalize()}*. Thoda aur padhai-likhai karo bhai! 😂",
                    f"📉 Arre yaar, galat ho gaya! Sahi word tha: *{correct_ans.capitalize()}*. Agli baar try karna! 💀"
                ]
                bot.reply_to(message, random.choice(riddle_fail), reply_markup=get_main_keyboard())
            return

        # Check if user is responding to Roast War (Dynamic Diverse Responses)
        if chat_id in ACTIVE_ROAST_GAMES:
            ACTIVE_ROAST_GAMES.pop(chat_id)
            roast_comebacks = [
                "Oho! Comeback toh aisa diya jaise Google se copy karke laya ho.. par chal maan liya, thoda toh dum hai tujhmein! 💀🔥",
                "Waah! Yeh wala roast sunkar mujhe laga ab main hi retire ho jaun. Sahi khele ho! 🎯",
                "Acha try tha, par mere level tak pahunchne ke liye abhi 10 saal aur maggi khani padegi! 😂",
                "Chalo maan liya is baar tumne jeeta, par agli baar itni aasani se bachne nahi dungi! 😌✨"
            ]
            bot.reply_to(message, random.choice(roast_comebacks), reply_markup=get_main_keyboard())
            return

        # Check if user is responding to Truth or Dare task (Dynamic Diverse Responses)
        if chat_id in ACTIVE_TOD_GAMES:
            ACTIVE_TOD_GAMES.pop(chat_id)
            tod_replies = [
                "Oho! Maan gaye bhai, kya fearless move tha yeh! Tera confidence dekhne layak hai. 🤭🔥",
                "Damdaar jawab diya hai, maza aa gaya sunn kar! Sachi mein mast player nikla tu. 💀✨",
                "Sahi khela hai yaar, aise hi sareef hone ka natak chhod kar asli rang dikhate raho! 🎯",
                "Wah bhai wah! Agli baar isse bhi zyada hard task dungi tujhe, tab dekhti hoon kitna banta hai! 😌🚀"
            ]
            bot.reply_to(message, random.choice(tod_replies), reply_markup=get_main_keyboard())
            return

        try_react_to_message(message.chat.id, message.message_id, text_content)

        # Typing simulation
        stop_typing = threading.Event()
        t_thread = threading.Thread(target=trigger_typing, args=(message.chat.id, stop_typing))
        t_thread.daemon = True
        t_thread.start()

        save_message(user_id, "user", text_content)
        update_user_cache(user_id, "user", text_content)

        history = get_deep_chat_history(user_id, limit=30)
        situation = detect_mood_and_situation(text_content)
        
        response = generate_ai_response(history, message.from_user.first_name or "Dost", situation)

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
