from collections import deque
from difflib import SequenceMatcher
from json import JSONDecodeError
from logging.handlers import RotatingFileHandler
import json
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
except Exception as e:
    logger.warning(f"Could not fetch bot ID: {e}")
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
summary_cache = TTLCache(maxsize=1500, ttl=5400)
cache_lock = threading.Lock()
last_message_time = {}
last_admin_error_time = 0

# Anti-Repetition Tracking (Store last 10 bot replies per user)
user_recent_replies = {}

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
# --- CORE DATABASE & CACHE MANAGEMENT ---
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
    except requests.exceptions.RequestException as e:
        logger.error(f"Supabase register_user error: {e}")

def clear_user_memory(user_id):
    """DRY Utility: Clears all chat logs, invalidates caches, and resets recent replies."""
    url = f"{SUPABASE_URL}/rest/v1/messages?user_id=eq.{user_id}"
    try:
        session.delete(url, headers=SUPABASE_HEADERS, timeout=10)
        with cache_lock:
            chat_key = f"chat_{user_id}"
            if chat_key in user_cache:
                del user_cache[chat_key]
            if user_id in user_cache:
                del user_cache[user_id]
            if user_id in profile_cache:
                del profile_cache[user_id]
            if user_id in summary_cache:
                del summary_cache[user_id]
        if user_id in user_recent_replies:
            user_recent_replies[user_id].clear()
    except requests.exceptions.RequestException as e:
        logger.error(f"Clear memory error for user {user_id}: {e}")

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
    except requests.exceptions.RequestException as e:
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
    except requests.exceptions.RequestException as e:
        logger.error(f"Supabase update_user_profile_field error: {e}")

def get_conversation_summary(user_id):
    with cache_lock:
        if user_id in summary_cache:
            return summary_cache[user_id]
            
    url = f"{SUPABASE_URL}/rest/v1/conversation_summary?user_id=eq.{user_id}"
    try:
        res = session.get(url, headers=SUPABASE_HEADERS, timeout=10)
        res.raise_for_status()
        rows = res.json()
        summary = rows[0]["summary"] if rows else "No prior summary available."
        with cache_lock:
            summary_cache[user_id] = summary
        return summary
    except requests.exceptions.RequestException as e:
        logger.error(f"Get summary error: {e}")
        return "No prior summary available."

def save_message(user_id, role, content):
    trivial_words = ["hi", "hello", "ok", "hmm", "k", "acha", "hlo"]
    if role == "user" and content.lower().strip() in trivial_words:
        return

    url = f"{SUPABASE_URL}/rest/v1/messages"
    payload = {"user_id": user_id, "role": role, "content": content}
    try:
        res = session.post(url, headers=SUPABASE_HEADERS, json=payload, timeout=10)
        res.raise_for_status()
        # Immediate TTLCache invalidation for real-time memory synchronization
        with cache_lock:
            cache_key = f"chat_{user_id}"
            if cache_key in user_cache:
                user_cache[cache_key].append({"role": role, "content": content})
                if len(user_cache[cache_key]) > 20:
                    user_cache[cache_key].pop(0)
    except requests.exceptions.RequestException as e:
        logger.error(f"Supabase save_message error: {e}")

def get_recent_messages(user_id, limit=10):
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
    except requests.exceptions.RequestException as e:
        logger.error(f"Supabase get_recent_messages error: {e}")
    return []

def get_total_users_count():
    url = f"{SUPABASE_URL}/rest/v1/users?select=user_id"
    headers = {**SUPABASE_HEADERS, "Range-Unit": "items", "Range": "0-0"}
    try:
        res = session.get(url, headers=headers, timeout=10)
        if "content-range" in res.headers:
            total = res.headers["content-range"].split("/")[-1]
            return int(total) if total.isdigit() else 0
    except requests.exceptions.RequestException as e:
        logger.error(f"Supabase user count error: {e}")
    return 0

def increment_daily_stats(user_id, is_game=False):
    url = f"{SUPABASE_URL}/rest/v1/daily_stats"
    try:
        # Simple UPSERT RPC or check-and-insert
        payload = {"user_id": user_id, "date": time.strftime("%Y-%m-%d"), "messages_sent": 1, "games_played": 1 if is_game else 0}
        headers = {**SUPABASE_HEADERS, "Prefer": "resolution=merge-duplicates"}
        session.post(url, headers=headers, json=payload, timeout=5)
    except requests.exceptions.RequestException as e:
        logger.debug(f"Daily stats sync error: {e}")

# ==========================================
# --- LLM SEMANTIC CLASSIFIER & INTENT ENGINE ---
# ==========================================
def semantic_classify_message(text):
    """Uses LLM structured prompting to accurately determine mood, intent, emotion, humor, and style."""
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    
    classification_prompt = (
        "Analyze the following user message and output STRICT JSON format with keys: "
        "'mood' (options: Happy, Sad, Angry, Romantic, Bored, Excited, Studying, Coding, Gaming, Lonely, Depressed, Confused, Joking, Roasting, Flirting, Business, Serious), "
        "'intent' (options: chat, game, help, roast, calculator, settings, profile), "
        "'emotion', 'humor', 'reply_style'. "
        f"Message: '{text}'"
    )
    
    payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "system", "content": "You are a precise JSON classifier."}, {"role": "user", "content": classification_prompt}],
        "temperature": 0.1,
        "max_tokens": 150
    }
    
    try:
        res = session.post(url, headers=headers, json=payload, timeout=10)
        res.raise_for_status()
        content = res.json()["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        return json.loads(content.strip())
    except (JSONDecodeError, requests.exceptions.RequestException, KeyError) as e:
        logger.debug(f"Semantic classifier fallback triggered: {e}")
        return {
            "mood": "Happy",
            "intent": "chat",
            "emotion": "neutral",
            "humor": "light",
            "reply_style": "Witty Hinglish"
        }

def get_dynamic_persona(mood, intent, user_text):
    text_lower = user_text.lower()
    if intent == "calculator" or any(w in text_lower for w in ["calculate", "math", "+", "-", "*", "/"]):
        return "Tumhara persona ek **Quick Math Calculator & Logical Assistant** ka hai. Fast aur accurate calculations do."
    elif mood == "Coding" or any(w in text_lower for w in ["python", "code", "bug", "error", "api", "supabase", "flask"]):
        return "Tumhara persona ek **Expert Software Architect & Coder** ka hai. Technical baaton mein sharp, exact, aur debugging sarcasm ke sath solution do."
    elif mood in ["Sad 🥺", "Lonely 🥀", "Depressed 🖤"] or any(w in text_lower for w in ["pareshan", "tension", "sad", "akelapan", "zindagi"]):
        return "Tumhara persona ek **Empathetic Psychologist & Deep Listener** ka hai. Bina judgment ke user ki suno aur pyaar se comfort karo."
    elif mood == "Studying 📚" or any(w in text_lower for w in ["samjha do", "explain", "kya hota hai"]):
        return "Tumhara persona ek **Witty College Professor / Teacher** ka hai. Complex topics ko aasan desi examples ke sath samjha do."
    elif intent == "roast" or mood == "Roasting 🔥":
        return "Tumhara persona ek **Peak Sarcastic Desi Roaster** ka hai. Halki-fulki bezzati aur witty taunts ke sath jawab do."
    else:
        return "Tumhara persona ek **Warm, Friendly aur Witty Best Friend** ka hai. Aaram se chill baatein karo aur supportive raho."

# ==========================================
# --- MODULAR GAME MANAGER ---
# ==========================================
ACTIVE_GAME_SESSIONS = {}

def handle_game_manager(message, game_type):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if game_type == "guess":
        target = random.randint(1, 50)
        ACTIVE_GAME_SESSIONS[user_id] = {"type": "guess", "target": target, "attempts": 0}
        bot.reply_to(message, "🎮 **Guess the Number Battle!**\n1 se 50 ke beech ek number socha hai. Guess karke dikha! 🤭")
    elif game_type == "truth_or_dare":
        tasks = [
            "🔥 **Truth:** Life mein sabse bada fattu wala kaam kaunsa kiya hai? 🤨",
            "🔥 **Truth:** Tera pehla crush kaun tha aur kya usne reject kar diya tha? 👀",
            "⚡ **Dare:** Apne kisi friend ko voice note bhej kar bol — 'Mujhe apne aap se pyaar ho gaya hai' aur screenshot bhej! 🤣"
        ]
        ACTIVE_GAME_SESSIONS[user_id] = {"type": "tod", "task": random.choice(tasks)}
        bot.reply_to(message, f"🎯 **Truth or Dare Challenge:**\n\n{ACTIVE_GAME_SESSIONS[user_id]['task']}\n\n💬 Jawab de ya task poora kar!")
    elif game_type == "riddle":
        riddles = [
            ("Aisi kaun si cheez hai jo jitni zyada saaf karo, utni hi gandi hoti hai?", "blackboard"),
            ("Woh kya hai jo paida hote hi bina pairo ke bhagne lagti hai?", "hawa"),
            ("Samandar mein paida hoti hai aur ghar aate hi gayab ho jati hai?", "namak")
        ]
        r, a = random.choice(riddles)
        ACTIVE_GAME_SESSIONS[user_id] = {"type": "riddle", "answer": a}
        bot.reply_to(message, f"🧩 **Riddle Battle Active:**\n\n*{r}*\n\n🧠 Sahi jawab dekar dikha! 💡")
    elif game_type == "roast_battle":
        ACTIVE_GAME_SESSIONS[user_id] = {"type": "roast"}
        bot.reply_to(message, "🔥 **Roast War Initiated:**\nItni umar ho gayi par koi dhang ki achievement hai ya bas resume mein jhuth likhna aata hai? 💀\n\nAb iska solid comeback de!")
    elif game_type == "quiz":
        ACTIVE_GAME_SESSIONS[user_id] = {"type": "quiz", "answer": "delhi"}
        bot.reply_to(message, "🎯 **Quick Quiz:** India ki capital ka naam kya hai? (Chota spelling)")

def process_active_game(message, user_id, text_content):
    if user_id not in ACTIVE_GAME_SESSIONS:
        return False
        
    session = ACTIVE_GAME_SESSIONS[user_id]
    g_type = session["type"]
    
    if g_type == "guess":
        if text_content.isdigit():
            guess = int(text_content)
            session["attempts"] += 1
            target = session["target"]
            if guess == target:
                del ACTIVE_GAME_SESSIONS[user_id]
                bot.reply_to(message, f"🎉 Sahi pakda! Sirf {session['attempts']} attempts mein number guess kar liya! 🤣🔥")
            elif guess < target:
                bot.reply_to(message, "📈 Thoda bada number daal! 🥱")
            else:
                bot.reply_to(message, "📉 Thoda chhota number daal! 🚀")
        else:
            bot.reply_to(message, "Bhai number daal seedha! 🔢")
        return True
        
    elif g_type in ["tod", "riddle", "roast", "quiz"]:
        del ACTIVE_GAME_SESSIONS[user_id]
        bot.reply_to(message, "Maan gaye bhai! Kya mast khele ho. 🤭🔥 Naya game start karne ke liye menu use karo.")
        return True
        
    return False

# ==========================================
# --- ANTI-REPETITION & AI GENERATION ---
# ==========================================
def check_similarity(new_text, previous_texts, threshold=0.75):
    for prev in previous_texts:
        if SequenceMatcher(None, new_text.lower(), prev.lower()).ratio() >= threshold:
            return True
    return False

def generate_ai_response(user_id, message_list, profile, summary, classification, latest_user_text):
    dynamic_persona = get_dynamic_persona(classification["mood"], classification["intent"], latest_user_text)
    
    system_prompt = (
        "Tumhara naam **Venu** hai. Tum ek extremely intelligent, razor-sharp, aur adaptive desi AI dost ho. "
        "**Core Behaviour Rules:**\n"
        "1. **Dynamic Shift:** Niche diye gaye Persona ke hisaab se apna tone badal lo.\n"
        "2. **Memory Integration:** User Profile aur Conversation Summary ko dhyan mein rakho.\n"
        "3. **Zero Repetition:** Hamesha naye aur unique words use karo.\n"
        "4. **Hinglish Style:** Modern Hinglish mein baat karo (1-3 sentences max).\n"
        "5. **No Identity Leak:** Kabhi mat bolna ki tum AI ho.\n\n"
        f"--- **ACTIVE PERSONA** ---\n{dynamic_persona}\n\n"
        f"--- **CONVERSATION SUMMARY** ---\n{summary}\n\n"
        f"--- **USER PROFILE** ---\n"
        f"- Name: {profile.get('name')}\n"
        f"- Favorite Game: {profile.get('favorite_game')}\n"
        f"- Roast Level: {profile.get('roast_level')}\n"
        f"- Detected Mood: {classification['mood']}"
    )

    messages = [{"role": "system", "content": system_prompt}]
    for msg in message_list:
        messages.append({"role": msg["role"], "content": msg["content"]})

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}

    if user_id not in user_recent_replies:
        user_recent_replies[user_id] = deque(maxlen=10)

    for attempt in range(3):
        try:
            payload = {
                "model": MODEL_NAME,
                "messages": messages,
                "temperature": 0.9 + (attempt * 0.05),
                "max_tokens": 250,
            }
            res = session.post(url, headers=headers, json=payload, timeout=25)
            res.raise_for_status()
            reply = res.json()["choices"][0]["message"]["content"].strip()
            
            if not check_similarity(reply, user_recent_replies[user_id], threshold=0.70):
                user_recent_replies[user_id].append(reply)
                return reply
        except requests.exceptions.RequestException as e:
            logger.error(f"Groq API exception (attempt {attempt+1}): {e}")

    fallback = "Arey yaar, aaj baatein thodi repeat ho rahi hain.. kuch naya shuru karein? 🤭✨"
    user_recent_replies[user_id].append(fallback)
    return fallback

# ==========================================
# --- TELEGRAM INTERFACE & HANDLERS ---
# ==========================================
def get_main_keyboard():
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        telebot.types.KeyboardButton("🎮 Guess Number"),
        telebot.types.KeyboardButton("🎯 Truth or Dare"),
        telebot.types.KeyboardButton("🧩 Riddle Battle"),
        telebot.types.KeyboardButton("🔥 Roast War"),
        telebot.types.KeyboardButton("👤 View Profile"),
        telebot.types.KeyboardButton("🚀 Explore"),
        telebot.types.KeyboardButton("🧹 Clear Chat")
    )
    return markup

@bot.message_handler(commands=["start"])
def cmd_start(message):
    try:
        user = message.from_user
        register_user(user.id, user.username, user.first_name)
        get_user_profile(user.id, user.first_name or "dost")
        bot.reply_to(message, f"Oye {user.first_name}! ✨ Main **Venu** hoon. Bata aaj kis mood mein hai? 😎🔥", reply_markup=get_main_keyboard())
    except Exception as e:
        logger.error(f"Start command error: {e}")

@bot.message_handler(commands=["profile"])
def cmd_profile(message):
    try:
        profile = get_user_profile(message.from_user.id, message.from_user.first_name)
        text = (
            f"👤 **Long Term Memory Profile:**\n\n"
            f"📌 **Name:** {profile.get('name')}\n"
            f"🎂 **Age:** {profile.get('age')}\n"
            f"🎮 **Favorite Game:** {profile.get('favorite_game')}\n"
            f"🔥 **Roast Level:** {profile.get('roast_level')}\n"
            f"🧠 **Current Mood:** {profile.get('current_mood')}"
        )
        bot.reply_to(message, text, reply_markup=get_main_keyboard())
    except Exception as e:
        logger.error(f"Profile command error: {e}")

@bot.message_handler(commands=["clear"])
def cmd_clear(message):
    try:
        clear_user_memory(message.chat.id)
        bot.reply_to(message, "🧹 Saari purani chat aur cache saaf kar diye gaye! Naye sire se shuru karte hain. 😌✨", reply_markup=get_main_keyboard())
    except Exception as e:
        logger.error(f"Clear command error: {e}")

@bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'video', 'document', 'audio'])
def handle_incoming_message(message):
    try:
        user_id = message.from_user.id
        chat_id = message.chat.id
        text_content = message.text

        if not text_content:
            return

        # Rate Limiter (1.5s)
        current_time = time.time()
        if user_id in last_message_time and current_time - last_message_time[user_id] < 1.5:
            return
        last_message_time[user_id] = current_time

        register_user(user_id, message.from_user.username, message.from_user.first_name)

        # Quick Keyboard Navigation / Intent Routing
        if text_content == "🎮 Guess Number":
            handle_game_manager(message, "guess")
            return
        elif text_content == "🎯 Truth or Dare":
            handle_game_manager(message, "truth_or_dare")
            return
        elif text_content == "🧩 Riddle Battle":
            handle_game_manager(message, "riddle")
            return
        elif text_content == "🔥 Roast War":
            handle_game_manager(message, "roast_battle")
            return
        elif text_content == "👤 View Profile":
            cmd_profile(message)
            return
        elif text_content == "🧹 Clear Chat":
            cmd_clear(message)
            return
        elif text_content == "🚀 Explore":
            bot.reply_to(message, "🚀 **Explore Venu's World:**\n🔹 Advanced Semantic AI\n🔹 Dynamic Personas\n🔹 Multi-Game Hub\n🔹 Zero Repetition Engine", reply_markup=get_main_keyboard())
            return

        # Handle active game session if ongoing
        if process_active_game(message, user_id, text_content):
            increment_daily_stats(user_id, is_game=True)
            return

        # Semantic Intent Classification
        classification = semantic_classify_message(text_content)
        intent = classification.get("intent", "chat")

        if intent == "game":
            handle_game_manager(message, random.choice(["guess", "riddle", "truth_or_dare"]))
            return
        elif intent == "calculator":
            try:
                # Basic safe evaluation for simple math expressions
                allowed_chars = set("0123456789+-*/(). ")
                if all(c in allowed_chars for c in text_content):
                    res = eval(text_content)
                    bot.reply_to(message, f"🧮 Result: `{res}`")
                    return
            except Exception:
                pass

        # Save message & update memory structures
        save_message(user_id, "user", text_content)
        profile = get_user_profile(user_id, message.from_user.first_name)
        update_user_profile_field(user_id, "current_mood", classification["mood"])
        profile["current_mood"] = classification["mood"]

        summary = get_conversation_summary(user_id)
        recent_history = get_recent_messages(user_id, limit=10)

        # Generate intelligent response
        response = generate_ai_response(user_id, recent_history, profile, summary, classification, text_content)

        save_message(user_id, "assistant", response)
        increment_daily_stats(user_id, is_game=False)

        bot.reply_to(message, response, reply_markup=get_main_keyboard())

    except requests.exceptions.Timeout as e:
        logger.error(f"Request timeout in message handler: {e}")
        bot.reply_to(message, "Arey, server thoda busy chal raha hai.. ek baar phir se bolna! ⏳")
    except Exception as e:
        logger.error(f"Critical execution error in text handler: {e}")

# ==========================================
# --- MAIN APP EXECUTION ---
# ==========================================
if __name__ == "__main__":
    logger.info("🚀 Starting Production-Grade Venu Telegram Bot & Keep-Alive Server...")

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

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
            time.sleep(sleep_time)
            backoff = min(backoff * 2, max_backoff)
