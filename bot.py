from collections import deque
from difflib import SequenceMatcher
import ast
import json
import logging
from logging.handlers import RotatingFileHandler
import operator
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
BOT_TOKEN = os.getenv("BOT_TOKEN", "8894339879:AAG9YNCJEs8S1ztygtzZZLmN-4V1g5KBQOg")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "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")

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://hhelxewgwuqcloofyeyw.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImhoZWx4ZXdnd3VxY2xvb2Z5ZXl3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk0NzIyNTUsImV4cCI6MjA5NTA0ODI1NX0.EL0wb1HKvT9lJLtMW7p-y0X3fwgC1LeFrts7ErHVD54")

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
    logger.exception("Could not fetch bot ID during startup")
    BOT_ID = None

# ==========================================
# --- HIGH-PERFORMANCE SUPABASE WRAPPER ---
# ==========================================
class SupabaseClient:
    def __init__(self, url, key):
        self.url = url
        self.headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        self.session = requests.Session()
        retry = Retry(
            total=4,
            backoff_factor=1.5,
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def request(self, method, endpoint, payload=None, params=None):
        target_url = f"{self.url}/rest/v1/{endpoint}"
        try:
            if method == "GET":
                res = self.session.get(target_url, headers=self.headers, params=params, timeout=12)
            elif method == "POST":
                res = self.session.post(target_url, headers=self.headers, json=payload, timeout=12)
            elif method == "PATCH":
                res = self.session.patch(target_url, headers=self.headers, json=payload, timeout=12)
            elif method == "DELETE":
                res = self.session.delete(target_url, headers=self.headers, timeout=12)
            else:
                return None
            res.raise_for_status()
            if res.text:
                return res.json()
            return None
        except Exception:
            logger.exception(f"Supabase request error [{method} {endpoint}]")
            return None

db = SupabaseClient(SUPABASE_URL, SUPABASE_KEY)

# ==========================================
# --- THREAD-SAFE GLOBAL STATE & CACHES ---
# ==========================================
state_lock = threading.RLock()

user_memory_cache = TTLCache(maxsize=1500, ttl=5400)
registered_users_cache = TTLCache(maxsize=5000, ttl=86400)

last_message_time = {}
user_recent_replies = {}
ACTIVE_GAME_SESSIONS = {}

# ==========================================
# --- FLASK KEEP-ALIVE SERVER ---
# ==========================================
app = Flask(__name__)

@app.route("/")
def home():
    return "🤖 Venu AI is online, emotionally consistent, and game-ready 24/7!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ==========================================
# --- DEFAULT PROFILE & MEMORY UTILS ---
# ==========================================
def default_profile(user_id, name="Dost"):
    return {
        "user_id": user_id,
        "name": name,
        "age": "Not specified",
        "favorite_game": "Not specified",
        "favorite_movie": "Not specified",
        "language": "Hinglish",
        "roast_level": "Medium",
        "relationship_status": "Not specified",
        "hobbies": "Not specified",
        "current_mood": "Witty, loyal, and consistently chill",
        "emotional_momentum": "Stable"
    }

def register_user(user_id, username, first_name):
    with state_lock:
        if user_id in registered_users_cache:
            return
    payload = {
        "user_id": user_id,
        "username": username,
        "first_name": first_name,
        "is_verified": True,
    }
    headers = {**db.headers, "Prefer": "resolution=merge-duplicates"}
    try:
        res = db.session.post(f"{db.url}/rest/v1/users", headers=headers, json=payload, timeout=10)
        res.raise_for_status()
        with state_lock:
            registered_users_cache[user_id] = True
    except Exception:
        logger.exception("Error registering user")

def clear_user_memory(user_id):
    with state_lock:
        db.request("DELETE", f"messages?user_id=eq.{user_id}")
        if user_id in user_memory_cache:
            del user_memory_cache[user_id]
        if user_id in user_recent_replies:
            user_recent_replies[user_id].clear()
        if user_id in last_message_time:
            del last_message_time[user_id]

def get_user_memory(user_id, first_name="Dost"):
    with state_lock:
        if user_id in user_memory_cache:
            return user_memory_cache[user_id]

    rows = db.request("GET", f"user_profiles?user_id=eq.{user_id}")
    if rows:
        profile = rows[0]
    else:
        profile = default_profile(user_id, first_name)
        db.request("POST", "user_profiles", payload=profile)

    sum_rows = db.request("GET", f"conversation_summary?user_id=eq.{user_id}")
    summary = sum_rows[0]["summary"] if sum_rows else "Ongoing friendly connection."

    msg_rows = db.request("GET", f"messages?user_id=eq.{user_id}&order=created_at.desc&limit=15")
    history = [{"role": r["role"], "content": r["content"]} for r in reversed(msg_rows)] if msg_rows else []

    memory_packet = {
        "profile": profile,
        "summary": summary,
        "history": history
    }
    with state_lock:
        user_memory_cache[user_id] = memory_packet
    return memory_packet

def update_profile_field(user_id, field, value):
    db.request("PATCH", f"user_profiles?user_id=eq.{user_id}", payload={field: value})
    with state_lock:
        if user_id in user_memory_cache:
            user_memory_cache[user_id]["profile"][field] = value

def save_message(user_id, role, content):
    if role == "user" and content.lower().strip() in ["hi", "hello", "ok", "hmm", "k", "acha", "hlo"]:
        return
    db.request("POST", "messages", payload={"user_id": user_id, "role": role, "content": content})
    with state_lock:
        if user_id in user_memory_cache:
            user_memory_cache[user_id]["history"].append({"role": role, "content": content})
            if len(user_memory_cache[user_id]["history"]) > 20:
                user_memory_cache[user_id]["history"].pop(0)

def increment_daily_stats(user_id, is_game=False):
    try:
        date_str = time.strftime("%Y-%m-%d")
        existing = db.request("GET", f"daily_stats?user_id=eq.{user_id}&date=eq.{date_str}")
        if existing:
            m_sent = existing[0]["messages_sent"] + (0 if is_game else 1)
            g_played = existing[0]["games_played"] + (1 if is_game else 0)
            db.request("PATCH", f"daily_stats?user_id=eq.{user_id}&date=eq.{date_str}", payload={"messages_sent": m_sent, "games_played": g_played})
        else:
            db.request("POST", "daily_stats", payload={"user_id": user_id, "date": date_str, "messages_sent": 0 if is_game else 1, "games_played": 1 if is_game else 0})
    except Exception:
        logger.exception("Daily stats update error")

# ==========================================
# --- SAFE MATH CALCULATOR (AST PARSER) ---
# ==========================================
SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

def safe_eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    elif isinstance(node, ast.BinOp) and type(node.op) in SAFE_OPERATORS:
        left = safe_eval(node.left)
        right = safe_eval(node.right)
        return SAFE_OPERATORS[type(node.op)](left, right)
    elif isinstance(node, ast.UnaryOp) and type(node.op) in SAFE_OPERATORS:
        operand = safe_eval(node.operand)
        return SAFE_OPERATORS[type(node.op)](operand)
    else:
        raise ValueError("Unsafe math expression.")

def evaluate_math(expression):
    try:
        node = ast.parse(expression, mode='eval')
        return safe_eval(node.body)
    except Exception:
        return None

# ==========================================
# --- EXTENSIVE DIVERSE GAME DATASETS ---
# ==========================================
TRUTH_QUESTIONS = [
    "Life mein sabse bada fattu wala kaam kaunsa kiya hai? 🤨",
    "Tera pehla crush kaun tha aur kya usne reject kar diya tha? 👀",
    "Bachpan mein kaunsa sabse bada kaand kiya tha jo ghar walon ko aaj tak nahi pata? 🤫",
    "Agar tujhe ek din ke liye invisible hone ka mauka mile, toh tu sabse pehle kahan jayega? 👻",
    "Aisa kaun sa jhoot hai jo tune apne best friend se bola hai? 🤥",
    "Tera aaj tak ka sabse embarrassing moment kaunsa raha hai? 😳",
    "Agar tujhe apne phone ki gallery sabko dikhani pade, toh tu kitna dरेगा? 📱",
    "Tune aakhri baar kis baat par jhoot bola tha? 🤥",
    "Tera sabse ajeeb darr (phobia) kya hai? 🕷️",
    "Agar tu ek din ke liye opposite gender ban jaye, toh sabse pehle kya karega? 🙃"
]

DARE_TASKS = [
    "Apne kisi friend ko voice note bhej kar bol — 'Mujhe apne aap se pyaar ho gaya hai' aur screenshot bhej! 🤣",
    "Apne phone ki gallery ka sabse random aur ajeeb photo bina context ke kisi dost ko bhej! 📸",
    "Agle 10 minutes tak tu jo bhi message karega, uske aakhiri mein 'UwU 🥺' lagana padega! ✨",
    "Apne last call log ka screenshot bhej (jisme naam dikhe ya blur karde agar sharam aaye)! 📞",
    "Apni crush ya ex ka naam chat mein type karke turant delete kar de! 🏃‍♂️"
]

RIDDLES_DATA = [
    ("Aisi kaun si cheez hai jo jitni zyada saaf karo, utni hi gandi hoti hai?", ["blackboard", "black board", "board"]),
    ("Woh kya hai jo paida hote hi bina pairo ke bhagne lagti hai?", ["hawa", "wind", "air"]),
    ("Aisi kaun si cheez hai jo samandar mein paida hoti hai aur ghar mein aate hi gayab ho jati hai?", ["namak", "salt"]),
    ("Aisi kaun si cheez hai jise aage se tum dekhte ho aur peeche se bhagwan dekhta hai?", ["bicycle", "cycle"]),
    ("Aisi kaun si cheez hai jiske paas pankh nahi hain par fir bhi woh udti hai?", ["patang", "kite"])
]

ROAST_PROMPTS = [
    "Bata bhai, itni lambi umar ho gayi par aaj tak koi dhang ki achievement hai ya bas resume mein jhuth likhne ki ninja technique aati hai? 💀",
    "Tera screen time dekh kar toh lagta hai tu real life se zyada digital world mein reject hota hai! 😂",
    "Aisi shakal ke sath confidence kahan se laate ho? Thodi training humein bhi dilwa do! 🤭",
    "Tujhse baat karke lagta hai ki evolution ne beech mein hi process rokk diya tha! 🔥",
    "Tera dimaag aur Internet Explorer dono ek jaisi speed par chalte hain! 🐢"
]

# ==========================================
# --- UNIFIED AI & CONSISTENT EMOTION ---
# ==========================================
def check_similarity(new_text, previous_texts, threshold=0.75):
    for prev in previous_texts:
        if SequenceMatcher(None, new_text.lower(), prev.lower()).ratio() >= threshold:
            return True
    return False

def generate_unified_ai_response(user_id, memory_packet, latest_user_text):
    profile = memory_packet["profile"]
    summary = memory_packet["summary"]
    history = memory_packet["history"]

    system_prompt = (
        "You are **Venu**, a consistent, loyal, and emotionally intelligent human best friend. "
        "You maintain a steady, witty, and supportive personality throughout the conversation without abrupt mood swings. "
        "Remember all ongoing topics, previous jokes, and context mentioned in the history and summary. Never forget past contexts.\n\n"
        "You must respond in strict JSON format with 2 keys:\n"
        "1. 'classification': an object containing 'mood' (set consistently to 'Witty and Supportive') and 'intent' (chat, game, help, roast, calculator).\n"
        "2. 'reply': your natural, conversational Hinglish response (1-3 sentences max, keeping continuity with prior topics).\n\n"
        f"--- **USER PROFILE & CONTEXT** ---\n"
        f"- Name: {profile.get('name')}\n"
        f"- Favorite Game: {profile.get('favorite_game')}\n"
        f"- Ongoing Context/Summary: {summary}"
    )

    messages = [{"role": "system", "content": system_prompt}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": latest_user_text})

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}

    with state_lock:
        if user_id not in user_recent_replies:
            user_recent_replies[user_id] = deque(maxlen=10)

    for attempt in range(3):
        try:
            payload = {
                "model": MODEL_NAME,
                "messages": messages,
                "temperature": 0.7 + (attempt * 0.05),
                "max_tokens": 300,
            }
            res = requests.post(url, headers=headers, json=payload, timeout=20)
            res.raise_for_status()
            content = res.json()["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            
            parsed = json.loads(content.strip())
            reply = parsed.get("reply", "").strip()
            classification = parsed.get("classification", {"mood": "Witty and Supportive", "intent": "chat"})

            with state_lock:
                if not check_similarity(reply, user_recent_replies[user_id], threshold=0.75):
                    user_recent_replies[user_id].append(reply)
                    return classification, reply
        except Exception:
            logger.exception(f"Groq unified API exception on attempt {attempt+1}")

    fallback = "Arey yaar, connection thoda slow ho gaya tha.. par main yahin hoon, bata aage kya chal raha hai? 🤭✨"
    with state_lock:
        user_recent_replies[user_id].append(fallback)
    return {"mood": "Witty and Supportive", "intent": "chat"}, fallback

# ==========================================
# --- MODULAR GAME MANAGER (WIN/LOSS & DIVERSE) ---
# ==========================================
def handle_game_manager(message, game_type):
    user_id = message.from_user.id
    
    with state_lock:
        if game_type == "guess":
            target = random.randint(1, 30)
            ACTIVE_GAME_SESSIONS[user_id] = {"type": "guess", "target": target, "attempts": 0, "max_attempts": 5, "created": time.time()}
            bot.reply_to(message, "🎮 **Number Guessing Challenge (1-30)!**\nTere paas 5 attempts hain. Sahi number guess karke jeet kar dikha! 🎯")
        elif game_type == "truth_or_dare":
            choice_type = random.choice(["Truth", "Dare"])
            task = random.choice(TRUTH_QUESTIONS if choice_type == "Truth" else DARE_TASKS)
            ACTIVE_GAME_SESSIONS[user_id] = {"type": "tod", "sub_type": choice_type, "created": time.time()}
            bot.reply_to(message, f"🎯 **Truth or Dare [{choice_type}]:**\n\n{task}\n\n💬 Iska jawab de ya proof bhejkar task complete kar!")
        elif game_type == "riddle":
            r, a_list = random.choice(RIDDLES_DATA)
            ACTIVE_GAME_SESSIONS[user_id] = {"type": "riddle", "answers": a_list, "created": time.time()}
            bot.reply_to(message, f"🧩 **Riddle Challenge:**\n\n*{r}*\n\n🧠 Sahi jawab type kar!")
        elif game_type == "roast_battle":
            roast = random.choice(ROAST_PROMPTS)
            ACTIVE_GAME_SESSIONS[user_id] = {"type": "roast", "created": time.time()}
            bot.reply_to(message, f"🔥 **Roast Battle:**\n{roast}\n\nAb iska ekdum solid comeback dekar dikha, dekhte hain kaun jitta hai!")

def process_active_game(message, user_id, text_content):
    with state_lock:
        if user_id not in ACTIVE_GAME_SESSIONS:
            return False
        session = ACTIVE_GAME_SESSIONS[user_id]
    
    g_type = session["type"]
    if g_type == "guess":
        if text_content.isdigit():
            guess = int(text_content)
            session["attempts"] += 1
            target = session["target"]
            attempts_left = session["max_attempts"] - session["attempts"]

            if guess == target:
                with state_lock:
                    del ACTIVE_GAME_SESSIONS[user_id]
                bot.reply_to(message, f"🎉 **Jeet gaye!** Sahi number tha {target}! Tune {session['attempts']} attempts mein jeet liya! 🏆🔥")
            elif attempts_left <= 0:
                with state_lock:
                    del ACTIVE_GAME_SESSIONS[user_id]
                bot.reply_to(message, f"❌ **Game Over!** Tumhaar sare attempts khatam ho gaye. Sahi number **{target}** tha! Agli baar try karna. 😜")
            elif guess < target:
                bot.reply_to(message, f"📈 Thoda bada number daal! Attempts baaki hain: {attempts_left} ⏳")
            else:
                bot.reply_to(message, f"📉 Thoda chhota number daal! Attempts baaki hain: {attempts_left} ⏳")
        else:
            bot.reply_to(message, "Bhai seedha number type kar na! 🔢")
        return True

    elif g_type == "riddle":
        user_ans = text_content.lower().strip()
        correct_list = session["answers"]
        with state_lock:
            del ACTIVE_GAME_SESSIONS[user_id]
        
        if any(ans in user_ans for ans in correct_list):
            bot.reply_to(message, f"🏆 **Sahi jawab!** Maan gaye bhai, tera dimaag tez chal raha hai! ✨")
        else:
            bot.reply_to(message, f"❌ **Galat jawab!** Sahi answer inmein se ek tha: {', '.join(correct_list)}. Agli baar phod dena! 😎")
        return True

    else:
        with state_lock:
            del ACTIVE_GAME_SESSIONS[user_id]
        bot.reply_to(message, "🔥 Wah bhai! Kya mast comeback diya. Is round mein tu jeet gaya! 🏅 Naya game start karne ke liye menu use karo.")
        return True

# ==========================================
# --- BACKGROUND CLEANUP DAEMON ---
# ==========================================
def background_cleanup_daemon():
    while True:
        time.sleep(300)
        try:
            current_time = time.time()
            with state_lock:
                stale_games = [uid for uid, data in ACTIVE_GAME_SESSIONS.items() if current_time - data.get("created", current_time) > 1800]
                for uid in stale_games:
                    del ACTIVE_GAME_SESSIONS[uid]

                stale_times = [uid for uid, t in last_message_time.items() if current_time - t > 7200]
                for uid in stale_times:
                    del last_message_time[uid]
            logger.info("🧹 Background cleanup daemon executed successfully.")
        except Exception:
            logger.exception("Error in background cleanup daemon")

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
        get_user_memory(user.id, user.first_name or "dost")
        bot.reply_to(message, f"Oye {user.first_name}! ✨ Main **Venu** hoon. Bata aaj kis cheez par baat karni hai? 😎🔥", reply_markup=get_main_keyboard())
    except Exception:
        logger.exception("Start command execution error")

@bot.message_handler(commands=["profile"])
def cmd_profile(message):
    try:
        memory = get_user_memory(message.from_user.id, message.from_user.first_name)
        profile = memory["profile"]
        text = (
            f"👤 **Long Term Memory Profile:**\n\n"
            f"📌 **Name:** {profile.get('name')}\n"
            f"🎂 **Age:** {profile.get('age')}\n"
            f"🎮 **Favorite Game:** {profile.get('favorite_game')}\n"
            f"🔥 **Roast Level:** {profile.get('roast_level')}\n"
            f"🧠 **Bot Mood Status:** {profile.get('current_mood')}"
        )
        bot.reply_to(message, text, reply_markup=get_main_keyboard())
    except Exception:
        logger.exception("Profile command execution error")

@bot.message_handler(commands=["clear"])
def cmd_clear(message):
    try:
        clear_user_memory(message.chat.id)
        bot.reply_to(message, "🧹 Saari purani chat saaf kar di gayi! Naye sire se shuru karte hain. 😌✨", reply_markup=get_main_keyboard())
    except Exception:
        logger.exception("Clear command execution error")

@bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'video', 'document', 'audio'])
def handle_incoming_message(message):
    try:
        user_id = message.from_user.id
        text_content = message.text

        if not text_content:
            return

        current_time = time.time()
        with state_lock:
            if user_id in last_message_time and current_time - last_message_time[user_id] < 1.0:
                return
            last_message_time[user_id] = current_time

        register_user(user_id, message.from_user.username, message.from_user.first_name)

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
            bot.reply_to(message, "🚀 **Explore Venu's Features:**\n🔹 Consistent Witty Persona\n🔹 Secure AST Calculator\n🔹 Diverse Game Hub with Win/Loss Mechanics\n🔹 Deep Supabase Memory Integration", reply_markup=get_main_keyboard())
            return

        if process_active_game(message, user_id, text_content):
            increment_daily_stats(user_id, is_game=True)
            return

        math_res = evaluate_math(text_content)
        if math_res is not None:
            bot.reply_to(message, f"🧮 Result: `{math_res}`", reply_markup=get_main_keyboard())
            increment_daily_stats(user_id, is_game=False)
            return

        save_message(user_id, "user", text_content)
        memory_packet = get_user_memory(user_id, message.from_user.first_name)

        classification, response = generate_unified_ai_response(user_id, memory_packet, text_content)

        update_profile_field(user_id, "current_mood", classification.get("mood", "Witty and Supportive"))
        save_message(user_id, "assistant", response)
        increment_daily_stats(user_id, is_game=False)

        bot.reply_to(message, response, reply_markup=get_main_keyboard())

    except Exception:
        logger.exception("Critical execution error in incoming message handler")
        try:
            bot.reply_to(message, "Arey, server thoda busy chal raha hai.. ek baar phir se bolna! ⏳")
        except Exception:
            pass

# ==========================================
# --- MAIN APPLICATION ENTRYPOINT ---
# ==========================================
if __name__ == "__main__":
    logger.info("🚀 Starting Production-Grade Venu Telegram Bot & Keep-Alive Server...")

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    cleanup_thread = threading.Thread(target=background_cleanup_daemon, daemon=True)
    cleanup_thread.start()

    try:
        bot.remove_webhook()
        logger.info("🧹 Existing Webhooks cleared successfully.")
    except Exception:
        logger.exception("Could not remove webhook")

    while True:
        try:
            logger.info("🔄 Bot polling started securely with retry logic...")
            bot.infinity_polling(none_stop=True, timeout=30, long_polling_timeout=30)
        except Exception:
            logger.exception("Polling exception occurred. Reconnecting in 5 seconds...")
            time.sleep(5)
