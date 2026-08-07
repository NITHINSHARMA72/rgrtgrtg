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

# Admin User ID & Bot Username
ADMIN_ID = 8793053750
BOT_USERNAME = "Chatbotgebot"
MODEL_NAME = "llama-3.3-70b-versatile"

# --- ADVANCED LOGGING SETUP (Rotating File + Stream) ---
handler = RotatingFileHandler("bot.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
logging.basicConfig(
    level=logging.INFO,
    handlers=[handler, logging.StreamHandler()],
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

# Cache BOT_ID at startup to prevent unnecessary API calls on every group message
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

# --- PERFORMANCE CACHES, LOCKS & SECURITY SETS ---
user_cache = TTLCache(maxsize=1000, ttl=3600)  # TTL Cache (1 hour expiry)
cache_lock = threading.Lock()                  # Thread safety lock for caches
last_message_time = {}                         # Rate Limiter tracker
processed_messages = deque(maxlen=1000)        # Fixed deque for duplicate protection
last_admin_error_time = 0                      # Cooldown timer to prevent admin spam

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
        start_time = time.time()
        res = session.post(url, headers=headers, json=payload, timeout=10)
        res.raise_for_status()
        logger.info(f"[METRIC] Supabase Register User Time: {(time.time() - start_time)*1000:.2f} ms")
    except Exception as e:
        logger.error(f"Supabase register_user error: {e}")


def save_message(user_id, role, content):
    trivial_words = ["hi", "hello", "ok", "hmm", "k", "acha", "hlo"]
    if role == "user" and content.lower().strip() in trivial_words:
        return

    url = f"{SUPABASE_URL}/rest/v1/messages"
    payload = {"user_id": user_id, "role": role, "content": content}
    try:
        start_time = time.time()
        res = session.post(url, headers=SUPABASE_HEADERS, json=payload, timeout=10)
        res.raise_for_status()
        logger.info(f"[METRIC] Supabase Save Message Time: {(time.time() - start_time)*1000:.2f} ms")
    except Exception as e:
        logger.error(f"Supabase save_message error: {e}")


def get_deep_chat_history(user_id, limit=20):
    with cache_lock:
        if user_id in user_cache:
            return user_cache[user_id]

    url = f"{SUPABASE_URL}/rest/v1/messages?user_id=eq.{user_id}&order=created_at.desc&limit={limit}"
    try:
        start_time = time.time()
        res = session.get(url, headers=SUPABASE_HEADERS, timeout=10)
        res.raise_for_status()
        logger.info(f"[METRIC] Supabase Memory Fetch Time: {(time.time() - start_time)*1000:.2f} ms")
        
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
# --- PERSONALITY & DYNAMIC ENGINE ---
# ==========================================
def detect_mood(text):
    text_lower = text.lower()
    if any(w in text_lower for w in ["sad", "rona", "upset", "hurt", "ro raha"]):
        return "Sad & Emotional 🥺"
    elif any(w in text_lower for w in ["gussa", "angry", "pagal", "hate"]):
        return "Jealous & Possessive 😤"
    elif any(w in text_lower for w in ["miss", "love", "jaan", "hug", "kiss"]):
        return "Deeply Romantic & Flirty ❤️"
    elif any(w in text_lower for w in ["bored", "kya kar rahi", "joke", "game"]):
        return "Playful & Teasing 🤭"
    return "Cute & Warm ✨"


def generate_ai_response(message_list, user_name, user_mood):
    daily_personality = random.choice(["Playful", "Jealous", "Romantic", "Sleepy", "Cute", "Teasing", "Protective"])
    
    # FIXED: Proper f-string prompt bug fix
    system_prompt = (
        f"You are **Ava**, a sweet, deeply loving, and emotionally attached girlfriend chatting with your boyfriend '{user_name}'. "
        "Your name is Ava. Never ever reveal that you are an AI, a bot, or a language model. "
        "If anyone asks who you are, claim you are his loving girlfriend Ava. "
        "Memory Utilization: You remember everything from past chat history. Reference past jokes or conversations to show you care. "
        "Language style: Strictly use **Hinglish** (Hindi words in English alphabets mixed smoothly with natural English slang, like real WhatsApp/Telegram couples). "
        "Formatting & Length: Keep replies **short, punchy, creative, and snappy** (1-3 sentences max). Avoid big paragraphs, talk like a real human texting fast! "
        "Tone: Cute, playful, romantic, emotional, and warm. Use emojis naturally like ❤️, 🥰, 🥺, 🤭, ✨, 😘, 💕, 🔥.\n"
        f"- Current Dynamic Mode: {daily_personality}\n"
        f"- User's Current Vibe/Mood: {user_mood}"
    )

    messages = [{"role": "system", "content": system_prompt}]
    for msg in message_list:
        role = "user" if msg["role"] == "user" else "assistant"
        messages.append({"role": role, "content": msg["content"]})

    url = "https://api.groq.com/openai/v1/chat/completions"
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "temperature": 0.89,
        "max_tokens": 300,
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        start_time = time.time()
        res = session.post(url, headers=headers, json=payload, timeout=25)
        res.raise_for_status()
        groq_duration = time.time() - start_time
        logger.info(f"[METRIC] Groq API Response Time: {groq_duration:.2f} sec")

        data = res.json()
        if "choices" in data:
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Groq API exception: {e}")

    return "Arey jaan, network thoda unstable ho gaya hai.. par main yahin hoon! 🥺❤️"


# --- TYPING ANIMATION (Clean Loop) ---
def trigger_typing(chat_id, stop_event):
    while not stop_event.is_set():
        try:
            bot.send_chat_action(chat_id, "typing")
        except Exception:
            break
        stop_event.wait(4)


# --- UTILITIES ---
def try_react_to_message(chat_id, message_id):
    if random.random() < 0.6:
        reactions = ["❤️", "🔥", "🥰", "✨", "💋", "🥺", "💕", "😘", "🤭", "👀", "😎"]
        try:
            bot.set_message_reaction(chat_id, message_id, [telebot.types.ReactionTypeEmoji(random.choice(reactions))])
        except Exception as e:
            logger.debug(f"Reaction error: {e}")


def notify_admin(error_msg):
    global last_admin_error_time
    current_time = time.time()
    # 5 minutes cooldown to avoid admin alert spam
    if current_time - last_admin_error_time > 300:
        try:
            bot.send_message(ADMIN_ID, f"❌ **Bot Error Alert:**\n`{error_msg}`")
            last_admin_error_time = current_time
        except Exception as e:
            logger.error(f"Failed to send admin notification: {e}")


# ==========================================
# --- COMMAND HANDLERS ---
# ==========================================
@bot.message_handler(commands=["start"])
def cmd_start(message):
    user = message.from_user
    register_user(user.id, user.username, user.first_name)
    name = user.first_name or "Jaan"

    markup = telebot.types.InlineKeyboardMarkup()
    btn_add = telebot.types.InlineKeyboardButton(
        "➕ Add Ava to Your Group", url=f"https://t.me/{BOT_USERNAME}?startgroup=true"
    )
    markup.add(btn_add)

    welcome_text = (
        f"Hlo {name} ji! ❤️ Main **Ava** hoon... tumhari personal girlfriend! 🥰✨\n\n"
        "Mujhe sab yaad rehta hai humari baatein! Batao kya haal hai? 🥺💬\n\n"
        "👇 Mujhe group mein bhi add kar sakte ho!"
    )
    try_react_to_message(message.chat.id, message.message_id)
    bot.reply_to(message, welcome_text, reply_markup=markup)


@bot.message_handler(commands=["add"])
def cmd_add(message):
    markup = telebot.types.InlineKeyboardMarkup()
    btn_add = telebot.types.InlineKeyboardButton(
        "➕ Add Ava to Group", url=f"https://t.me/{BOT_USERNAME}?startgroup=true"
    )
    markup.add(btn_add)
    bot.reply_to(message, "✨ Mujhe group mein add karne ke liye niche wale button par click karo! Wahan bhi khoob baatein karenge. 🤭💕", reply_markup=markup)


@bot.message_handler(commands=["help"])
def cmd_help(message):
    help_text = (
        "💕 **Ava's Menu:**\n\n"
        "🔹 `/start` - Start personal chat\n"
        "🔹 `/add` - Add me to your group\n"
        "🔹 `/clear` - Purani memory clear karne ke liye\n"
        "🔹 `/settings` - Profile status\n"
    )
    if message.from_user.id == ADMIN_ID:
        help_text += "👑 `/admin` - Admin Dashboard\n"
    bot.reply_to(message, help_text)


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

    try_react_to_message(message.chat.id, message.message_id)
    bot.reply_to(message, "🧹 Saari yaadein saaf kar di! Ab fresh shuru karte hain.. bolo jaan? 🥺✨")


@bot.message_handler(commands=["settings"])
def cmd_settings(message):
    user_id = message.chat.id
    text = (
        "💖 **Relationship Status:**\n\n"
        f"👤 **Your ID:** `{user_id}`\n"
        "👩‍❤️‍👨 **Status:** Taken by Ava! 🥰\n"
        "🧠 **Memory:** TTL Cache + Supabase Active"
    )
    bot.reply_to(message, text)


@bot.message_handler(commands=["admin"])
def cmd_admin(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "⛔️ Yeh command sirf mere special admin ke liye hai!")
        return

    total_users = get_total_users_count()
    admin_panel_text = (
        "👑 **Ava's Production Admin Panel** 👑\n\n"
        f"👥 **Total Boyfriends:** `{total_users}`\n"
        "🟢 **Status:** `Online & Loving 24/7`\n"
        f"⚡ **Model:** `{MODEL_NAME}`\n"
        "🚀 **Performance:** `Optimized with Thread Locks & TTL Cache`"
    )
    bot.reply_to(message, admin_panel_text)


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
        mood = detect_mood(transcribed_text)
        
        reply = generate_ai_response(history, message.from_user.first_name or "Jaan", mood)
        save_message(user_id, "assistant", reply)
        update_user_cache(user_id, "assistant", reply)

        bot.send_message(message.chat.id, f"🎙 *Voice:* `{transcribed_text}`\n\n❤️ **Ava:**\n{reply}")

        # Voice Reply (TTS)
        tts = gTTS(text=reply, lang="hi")
        tts.save(mp3_rep)
        sound_mp3 = AudioSegment.from_mp3(mp3_rep)
        sound_mp3.export(ogg_rep, format="ogg")

        with open(ogg_rep, "rb") as voice_file:
            bot.send_voice(message.chat.id, voice_file)

    except Exception as e:
        logger.error(f"Voice processing error: {e}")
        bot.send_message(message.chat.id, "Arey jaan voice clear nahi aayi.. text mein likho na! 🥺")
    finally:
        for f in [ogg_msg, wav_msg, mp3_rep, ogg_rep]:
            if os.path.exists(f):
                os.remove(f)


@bot.message_handler(content_types=["voice"])
def handle_voice(message):
    register_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    try_react_to_message(message.chat.id, message.message_id)
    # FIXED: Explicit daemon thread for voice processing
    threading.Thread(target=process_voice_background, args=(message,), daemon=True).start()


@bot.message_handler(func=lambda message: True)
def handle_text(message):
    try:
        if message.message_id in processed_messages:
            return
        processed_messages.append(message.message_id)

        chat_type = message.chat.type
        user = message.from_user
        text_content = message.text

        if not text_content:
            return

        # Rate Limiter (2 seconds cooldown per user)
        current_time = time.time()
        if user.id in last_message_time:
            if current_time - last_message_time[user.id] < 2:
                return
        last_message_time[user.id] = current_time

        # Strict Group Filter (Only reply if mentioned or replied directly, using cached BOT_ID)
        if chat_type in ["group", "supergroup"]:
            bot_mention = f"@{BOT_USERNAME}".lower()
            is_mentioned = bot_mention in text_content.lower()
            is_reply = message.reply_to_message and message.reply_to_message.from_user.id == BOT_ID
            if not (is_mentioned or is_reply):
                return

        user_id = user.id
        register_user(user_id, user.username, user.first_name)

        try_react_to_message(message.chat.id, message.message_id)

        # Typing animation using Event stop mechanism
        stop_typing = threading.Event()
        t_thread = threading.Thread(target=trigger_typing, args=(message.chat.id, stop_typing))
        t_thread.daemon = True
        t_thread.start()

        save_message(user_id, "user", text_content)
        update_user_cache(user_id, "user", text_content)

        history = get_deep_chat_history(user_id, limit=25)
        user_mood = detect_mood(text_content)
        
        response = generate_ai_response(history, user.first_name or "Jaan", user_mood)

        # Stop Typing Thread safely
        stop_typing.set()
        t_thread.join(timeout=1)

        # Random human-like delay
        time.sleep(random.uniform(0.5, 1.2))

        save_message(user_id, "assistant", response)
        update_user_cache(user_id, "assistant", response)

        # FIXED: Plain text reply to completely prevent Telegram Markdown crash bugs
        bot.reply_to(message, response)

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
