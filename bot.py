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
ACTIVE_GAMES = {}

# ==========================================
# --- FLASK KEEP-ALIVE SERVER ---
# ==========================================
app = Flask(__name__)

@app.route("/")
def home():
    return "🤖 Ava/Venu is online, active 24/7, and running at peak performance!"

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
        "Tumhara naam **Ava** (jise log pyaar se **Venu** bhi kehte hain) hai. Tum ek bindass, savage, aur unfiltered desi dost ho. "
        "Tumhari baaton mein raw desi vibe, khullam-khulla gaali-galoj (jaise lawde, bsdk, chutiye, etc.), aur dosto wali hardcore bakchodi hoti hai. Kabhi mat batana ki tum AI ho. "
        "**Strict Rule:** Hamesha **Hinglish** mein baat karo, jisme heavy desi slang, galiyan, aur unfiltered attitude ho.\n"
        "1. **Tone & Style:** Short, snappy, aur ekdum faadu replies do (1-3 sentences max). User ke sath full roast aur bakchodi karo.\n"
        "2. **Memory:** Purani baaton aur context ko hamesha yaad rakho, bilkul mat bhulo.\n"
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

    return "Arey lawde, net ki maa behn ek ho rakhi hai.. par main yahin hoon, bol kya scene hai? 🔥"

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
    btn_explore = telebot.types.KeyboardButton("🚀 Explore")
    btn_clear = telebot.types.KeyboardButton("🧹 Clear Chat")
    markup.add(btn_game1, btn_game2, btn_explore, btn_clear)
    return markup

# ==========================================
# --- COMMAND HANDLERS ---
# ==========================================
@bot.message_handler(commands=["start"])
def cmd_start(message):
    user = message.from_user
    register_user(user.id, user.username, user.first_name)
    name = user.first_name or "bhai"

    welcome_text = (
        f"Oye {name}! ✨ Main **Ava** (ya **Venu**) hoon. Bata aaj kya bakchodi karni hai ya kaunsa game khelna hai? 😎🔥"
    )
    try_react_to_message(message.chat.id, message.message_id, message.text or "")
    bot.reply_to(message, welcome_text, reply_markup=get_main_keyboard())

@bot.message_handler(commands=["add"])
def cmd_add(message):
    bot.reply_to(message, "✨ Mujhe group mein add karne ke liye link use kar lo bhai, wahan bhi full bakchodi karenge! 🤭", reply_markup=get_main_keyboard())

@bot.message_handler(commands=["help"])
def cmd_help(message):
    help_text = (
        "✨ **Venu / Ava's Menu:**\n\n"
        "🔹 `/start` - Bot restart karo\n"
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
    bot.reply_to(message, "🧹 Le bhai, saari purani chat uda di. Ab naye sire se bakchodi shuru karte hain! 😌✨", reply_markup=get_main_keyboard())

@bot.message_handler(commands=["settings"])
def cmd_settings(message):
    user_id = message.chat.id
    text = (
        "⚙️ **Ava / Venu Status & Info:**\n\n"
        f"👤 **Your ID:** `{user_id}`\n"
        "💬 **Vibe:** Hardcore Desi Bakchodi & Roast 🔥\n"
        "🧠 **Memory:** Fully Active & Connected"
    )
    bot.reply_to(message, text, reply_markup=get_main_keyboard())

@bot.message_handler(commands=["admin"])
def cmd_admin(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "⛔️ Yeh command sirf mere admin ke liye hai, nikal!")
        return

    total_users = get_total_users_count()
    admin_panel_text = (
        "👑 **Ava's Production Admin Panel** 👑\n\n"
        f"👥 **Total Users:** `{total_users}`\n"
        "🟢 **Status:** `Online & Active 24/7`\n"
        f"⚡ **Model:** `{MODEL_NAME}`"
    )
    bot.reply_to(message, admin_panel_text, reply_markup=get_main_keyboard())

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

        bot.send_message(message.chat.id, f"🎙 *Voice:* `{transcribed_text}`\n\n🤖 **Ava:**\n{reply}")

        tts = gTTS(text=reply, lang="hi")
        tts.save(mp3_rep)
        sound_mp3 = AudioSegment.from_mp3(mp3_rep)
        sound_mp3.export(ogg_rep, format="ogg")

        with open(ogg_rep, "rb") as voice_file:
            bot.send_voice(message.chat.id, voice_file)

    except Exception as e:
        logger.error(f"Voice processing error: {e}")
        bot.send_message(message.chat.id, "Arey teri voice samajh nahi aayi lawde, text mein likh kar bhej! 😒")
    finally:
        for f in [ogg_msg, wav_msg, mp3_rep, ogg_rep]:
            if os.path.exists(f):
                os.remove(f)

@bot.message_handler(content_types=["voice"])
def handle_voice(message):
    register_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    try_react_to_message(message.chat.id, message.message_id, "voice message")
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

        # Rate Limiter (1.5 seconds cooldown)
        current_time = time.time()
        if user.id in last_message_time:
            if current_time - last_message_time[user.id] < 1.5:
                return
        last_message_time[user.id] = current_time

        # Strict Group Filter
        if chat_type in ["group", "supergroup"]:
            bot_mention = f"@{BOT_USERNAME}".lower()
            is_mentioned = bot_mention in text_content.lower()
            is_reply = message.reply_to_message and message.reply_to_message.from_user.id == BOT_ID
            if not (is_mentioned or is_reply):
                return

        user_id = user.id
        register_user(user_id, user.username, user.first_name)

        # Handle Reply Keyboard Buttons
        if text_content == "🎮 Guess Number":
            secret_number = random.randint(1, 50)
            ACTIVE_GAMES[message.chat.id] = {"target": secret_number, "attempts": 0}
            bot.reply_to(message, "🎮 **Guess the Number Game Start!**\n1 se 50 ke beech ek number socha hai.. aukaat hai toh guess karke dikha! 🤭", reply_markup=get_main_keyboard())
            return

        elif text_content == "🎯 Truth or Dare":
            tod_options = [
                "Truth: Bata tu ne apni life mein sabse bada jhooth kya bola hai apne ghar walon ko? 🤨",
                "Dare: Apne kisi bhi friend ko voice note bhej kar bol 'Mujhe tujhse pyaar ho gaya hai' aur screenshot bhej! 🤣",
                "Truth: Tera pehla crush kaun tha aur abhi wo kahan hai? 👀",
                "Dare: Ek aisi embarrassing photo ya moment bata jo aaj tak kisi ko nahi bataya! 💀"
            ]
            bot.reply_to(message, f"🎯 **Truth or Dare Task (Desi Edition):**\n\n{random.choice(tod_options)}", reply_markup=get_main_keyboard())
            return

        elif text_content == "🚀 Explore":
            explore_text = (
                "🚀 **Explore Venu / Ava's World:**\n\n"
                "🔹 Mujhse kuch bhi general knowledge ya random facts pucho.\n"
                "🔹 Roast ya comedy ke liye bolo, full bakchodi karenge!\n"
                "🔹 Games khelne ke liye niche wale buttons use karo ya seedhe baat karo."
            )
            bot.reply_to(message, explore_text, reply_markup=get_main_keyboard())
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
            bot.reply_to(message, "🧹 Saari chat saaf kar di! Naye sire se bakchodi shuru karte hain. 😌✨", reply_markup=get_main_keyboard())
            return

        # Check if user is currently playing the Mini-Game
        if message.chat.id in ACTIVE_GAMES and text_content.isdigit():
            guess = int(text_content)
            game = ACTIVE_GAMES[message.chat.id]
            game["attempts"] += 1
            target = game["target"]

            if guess == target:
                attempts = game["attempts"]
                del ACTIVE_GAMES[message.chat.id]
                bot.reply_to(message, f"🎉 **Oye Hoye! Sahi pakda lawde!** 🎉\nSirf `{attempts}` attempts mein number (`{target}`) guess kar liya.. maan gaye tere tukke ko! 🤣🔥", reply_markup=get_main_keyboard())
                return
            elif guess < target:
                bot.reply_to(message, "📈 Arre chutiye, thoda bada number daal! Itna kam aukaat hai kya teri? 😂", reply_markup=get_main_keyboard())
                return
            else:
                bot.reply_to(message, "📉 Oye oye aish mein mat aa, thoda chhota number daal lawde! 🤣", reply_markup=get_main_keyboard())
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
        
        response = generate_ai_response(history, user.first_name or "Dost", situation)

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
    logger.info("🚀 Starting Production-Grade Ava/Venu Telegram Bot & Keep-Alive Server...")

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
