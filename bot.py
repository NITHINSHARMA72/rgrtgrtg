import logging
import os
import random
import sqlite3
import threading
import time
from flask import Flask
from gtts import gTTS
import requests
from pydub import AudioSegment
import speech_recognition as sr
import telebot

# ==========================================
# --- CONFIGURATION (YAHAN SE EDIT KAREIN) ---
# ==========================================
BOT_TOKEN = "8914661287:AAFn6cuJBHrpIZZm7y3_3YbtdrEqU8tq6gc"
GROQ_API_KEY = "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"

# Apna Telegram Numeric User ID yahan daalein (Admin ke liye)
ADMIN_ID = 987654321

# Sirf ek Private Channel (ID aur Invite Link)
CHANNEL_ID = -1004358883410
CHANNEL_URL = "https://t.me/+ovbQggX8Ikg3MTY1"

# Groq High-Speed Model
MODEL_NAME = "llama-3.3-70b-versatile"

# --- LOGGING SETUP ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)


# ==========================================
# --- FLASK SERVER & STYLISH KEEP-ALIVE ---
# ==========================================
app = Flask(__name__)


@app.route("/")
def home():
  html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Telegram AI Bot - Status</title>
        <style>
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background-color: #0f172a;
                color: #f8fafc;
                margin: 0;
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
            }
            .card {
                background: #1e293b;
                padding: 40px;
                border-radius: 16px;
                box-shadow: 0 10px 25px rgba(0, 0, 0, 0.5);
                text-align: center;
                max-width: 450px;
                width: 100%;
                border: 1px solid #334155;
            }
            h1 {
                color: #38bdf8;
                font-size: 24px;
                margin-bottom: 10px;
            }
            p {
                color: #94a3b8;
                font-size: 14px;
                line-height: 1.5;
            }
            .status-badge {
                display: inline-block;
                background: rgba(34, 197, 94, 0.15);
                color: #22c55e;
                padding: 6px 14px;
                border-radius: 20px;
                font-weight: 600;
                font-size: 14px;
                margin: 15px 0;
                border: 1px solid rgba(34, 197, 94, 0.3);
            }
            .footer {
                margin-top: 20px;
                font-size: 12px;
                color: #64748b;
            }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>🤖 Advanced Groq Telegram Bot</h1>
            <div class="status-badge">● Online & Running 24/7</div>
            <p>This web service acts as a keep-alive server to prevent Render from spinning down the Telegram bot instance.</p>
            <div class="footer">Powered by Flask & Render Free Tier</div>
        </div>
    </body>
    </html>
    """
  return html_content


def run_flask():
  port = int(os.environ.get("PORT", 10000))
  app.run(host="0.0.0.0", port=port)


# ==========================================
# --- DATABASE SETUP ---
# ==========================================
def init_db():
  conn = sqlite3.connect("advanced_bot.db", check_same_thread=False)
  cursor = conn.cursor()

  cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            language TEXT DEFAULT 'en',
            reply_style TEXT DEFAULT 'friendly',
            is_verified INTEGER DEFAULT 0,
            last_active DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

  cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

  conn.commit()
  conn.close()


init_db()


def get_db_connection():
  return sqlite3.connect("advanced_bot.db", check_same_thread=False)


def register_user(user_id, username, first_name):
  conn = get_db_connection()
  cursor = conn.cursor()
  cursor.execute(
      """
        INSERT INTO users (user_id, username, first_name, last_active)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name,
            last_active = CURRENT_TIMESTAMP
    """,
      (user_id, username, first_name),
  )
  conn.commit()
  conn.close()


def save_message(user_id, role, content):
  conn = get_db_connection()
  cursor = conn.cursor()
  cursor.execute(
      "INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
      (user_id, role, content),
  )
  conn.commit()
  conn.close()


def get_recent_messages(user_id, limit=10):
  conn = get_db_connection()
  cursor = conn.cursor()
  cursor.execute(
      "SELECT role, content FROM messages WHERE user_id = ? ORDER BY id DESC"
      " LIMIT ?",
      (user_id, limit),
  )
  rows = cursor.fetchall()
  conn.close()

  history = []
  for role, content in reversed(rows):
    history.append({"role": role, "content": content})
  return history


def get_user_settings(user_id):
  conn = get_db_connection()
  cursor = conn.cursor()
  cursor.execute(
      "SELECT language, reply_style, is_verified FROM users WHERE user_id = ?",
      (user_id,),
  )
  row = cursor.fetchone()
  conn.close()
  if row:
    return {
        "language": row[0],
        "reply_style": row[1],
        "is_verified": bool(row[2]),
    }
  return {"language": "en", "reply_style": "friendly", "is_verified": False}


def update_verification_status(user_id, status=1):
  conn = get_db_connection()
  cursor = conn.cursor()
  cursor.execute(
      "UPDATE users SET is_verified = ? WHERE user_id = ?", (status, user_id)
  )
  conn.commit()
  conn.close()


def get_total_users_count():
  conn = get_db_connection()
  cursor = conn.cursor()
  cursor.execute("SELECT COUNT(*) FROM users")
  count = cursor.fetchone()[0]
  conn.close()
  return count


# ==========================================
# --- CHANNEL MEMBERSHIP CHECKER ---
# ==========================================
def check_channel_membership(user_id):
  try:
    member = bot.get_chat_member(CHANNEL_ID, user_id)
    valid_status = ["member", "administrator", "creator"]
    return member.status in valid_status
  except Exception as e:
    logger.error(f"Membership check error: {e}")
    return False


def send_channel_join_prompt(chat_id):
  markup = telebot.types.InlineKeyboardMarkup(row_width=1)
  btn_join = telebot.types.InlineKeyboardButton(
      "🔒 Join Private Channel", url=CHANNEL_URL
  )
  btn_check = telebot.types.InlineKeyboardButton(
      "✅ Verify Membership", callback_data="verify_channels"
  )
  markup.add(btn_join, btn_check)

  text = (
      "⚠️ **Access Restricted!**\n\n"
      "Bot ko use karne ke liye aapko hamara private channel join karna hoga:\n\n"
      f"👉 {CHANNEL_URL}\n\n"
      "Channel join karne ke baad niche **'Verify Membership'** button par click"
      " karein! 🚀"
  )
  bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")


# ==========================================
# --- ADVANCED GROQ AI CORE ---
# ==========================================
def generate_ai_response(message_list, user_name, user_prefs):
  system_prompt = (
      f"You are an ultra-smart, ultra-friendly, and engaging AI assistant on"
      f" Telegram. The user's real name is '{user_name}'. Address them warmly"
      f" by name when appropriate. Preferred Language:"
      f" {user_prefs['language']}. Style: Friendly, casual yet deeply"
      f" intelligent.\n\nFormatting Guidelines:\n- Keep replies concise,"
      " punchy, and structured (avoid giant text walls).\n- Use emojis, bold"
      " text (**highlight**), and inline code (`tags`) extensively to make"
      " responses visually striking.\n- Use numbered lists (1., 2., 3.) for steps"
      " or multi-point answers.\n- Keep strong contextual memory of past chat"
      " turns."
  )

  messages = [{"role": "system", "content": system_prompt}]
  for msg in message_list:
    role = "user" if msg["role"] == "user" else "assistant"
    messages.append({"role": role, "content": msg["content"]})

  url = "https://api.groq.com/openai/v1/chat/completions"
  payload = {
      "model": MODEL_NAME,
      "messages": messages,
      "temperature": 0.8,
      "max_tokens": 1024,
  }
  headers = {
      "Authorization": f"Bearer {GROQ_API_KEY}",
      "Content-Type": "application/json",
  }

  try:
    res = requests.post(url, headers=headers, json=payload, timeout=25)
    if res.status_code == 200:
      data = res.json()
      if "choices" in data:
        return data["choices"][0]["message"]["content"].strip()
    logger.warning(
        f"Groq API error status: {res.status_code}, response: {res.text}"
    )
  except Exception as e:
    logger.error(f"Groq API exception: {e}")

  return (
      "😅 Arre yaar, abhi thoda network issue ya high traffic chal raha hai. "
      "Ek baar fir se try karo na!"
  )


# --- TELEGRAM UX HELPERS ---
def send_long_message(chat_id, text):
  MAX_LEN = 4000
  for i in range(0, len(text), MAX_LEN):
    chunk = text[i : i + MAX_LEN]
    try:
      bot.send_message(chat_id, chunk, parse_mode="Markdown")
    except Exception:
      bot.send_message(chat_id, chunk)
    time.sleep(0.3)


# ==========================================
# --- COMMAND HANDLERS ---
# ==========================================
@bot.message_handler(commands=["start"])
def cmd_start(message):
  user = message.from_user
  register_user(user.id, user.username, user.first_name)
  prefs = get_user_settings(user.id)

  if prefs["is_verified"] or check_channel_membership(user.id):
    update_verification_status(user.id, 1)
    name = user.first_name or "Dost"
    welcome_text = (
        f"👋 Hey **{name}**! Welcome back to your advanced AI companion.\n\n"
        "✨ **Features Enabled:**\n"
        "🔹 Powered by Groq Llama 3.3\n"
        "🔹 Strong Memory & Context\n"
        "🔹 Friendly, Highlighted & Tagged Chat\n"
        "🔹 Voice & Text Support\n\n"
        "Type `/help` or just start chatting right now! 🚀"
    )
    bot.reply_to(message, welcome_text, parse_mode="Markdown")
  else:
    send_channel_join_prompt(message.chat.id)


@bot.message_handler(commands=["help"])
def cmd_help(message):
  prefs = get_user_settings(message.from_user.id)
  if not prefs["is_verified"] and not check_channel_membership(
      message.from_user.id
  ):
    send_channel_join_prompt(message.chat.id)
    return

  help_text = (
      "🛠 **Bot Command Hub:**\n\n"
      "🔹 `/start` - Restart bot\n"
      "🔹 `/help` - Show this menu\n"
      "🔹 `/clear` - Wipe active chat memory\n"
      "🔹 `/settings` - View your profile info\n"
  )
  if message.from_user.id == ADMIN_ID:
    help_text += "👑 `/admin` - Open Admin Dashboard\n"

  bot.reply_to(message, help_text, parse_mode="Markdown")


@bot.message_handler(commands=["clear"])
def cmd_clear(message):
  user_id = message.chat.id
  conn = get_db_connection()
  cursor = conn.cursor()
  cursor.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
  conn.commit()
  conn.close()
  bot.reply_to(
      message,
      "🧹 Memory wiped clean! Fresh start ready. What's on your mind? 💡",
  )


@bot.message_handler(commands=["settings"])
def cmd_settings(message):
  user_id = message.chat.id
  prefs = get_user_settings(user_id)
  text = (
      "⚙️ **Your Profile & Settings:**\n\n"
      f"👤 **User ID:** `{user_id}`\n"
      f"🌐 **Language:** `{prefs['language']}`\n"
      f"💬 **Style:** `{prefs['reply_style']}`\n"
      f"✅ **Verified Member:** `Yes`\n"
  )
  bot.reply_to(message, text, parse_mode="Markdown")


# --- ADMIN PANEL COMMAND ---
@bot.message_handler(commands=["admin"])
def cmd_admin(message):
  if message.from_user.id != ADMIN_ID:
    bot.reply_to(
        message, "⛔️ Unauthorized! Yeh command sirf bot admin ke liye hai."
    )
    return

  total_users = get_total_users_count()
  admin_panel_text = (
      "👑 **Admin Control Dashboard** 👑\n\n"
      f"👥 **Total Registered Users:** `{total_users}`\n"
      f"🟢 **Bot Status:** `Online & Running smoothly`\n"
      f"⚡ **Active Model:** `{MODEL_NAME}`\n\n"
      "Use this panel to monitor bot health and performance."
  )
  bot.reply_to(message, admin_panel_text, parse_mode="Markdown")


# ==========================================
# --- CALLBACK QUERY HANDLER (VERIFY JOIN) ---
# ==========================================
@bot.callback_query_handler(func=lambda call: call.data == "verify_channels")
def verify_callback(call):
  user_id = call.from_user.id
  if check_channel_membership(user_id):
    update_verification_status(user_id, 1)
    bot.answer_callback_query(call.id, "✅ Verification Successful!")
    bot.edit_message_text(
        "🎉 **Awesome!** Aapne channel join kar liya hai. Ab aap bot ko freely"
        " use kar sakte hain. Kuch bhi type karke baat shuru karein! 🚀",
        call.message.chat.id,
        call.message.message_id,
        parse_mode="Markdown",
    )
  else:
    bot.answer_callback_query(
        call.id,
        "❌ Aapne abhi tak channel join nahi kiya hai! Pehle join karein.",
        show_alert=True,
    )


# ==========================================
# --- MESSAGE HANDLERS (TEXT & VOICE) ---
# ==========================================
@bot.message_handler(content_types=["voice"])
def handle_voice(message):
  user_id = message.chat.id
  user = message.from_user
  register_user(user.id, user.username, user.first_name)
  prefs = get_user_settings(user_id)

  if not prefs["is_verified"] and not check_channel_membership(user.id):
    send_channel_join_prompt(user_id)
    return

  bot.send_chat_action(user_id, "typing")

  try:
    file_info = bot.get_file(message.voice.file_id)
    file = requests.get(
        f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}",
        timeout=15,
    )

    with open("voice_msg.ogg", "wb") as f:
      f.write(file.content)

    sound = AudioSegment.from_file("voice_msg.ogg", format="ogg")
    sound.export("voice_msg.wav", format="wav")

    r = sr.Recognizer()
    with sr.AudioFile("voice_msg.wav") as source:
      audio_data = r.record(source)
      transcribed_text = r.recognize_google(audio_data)

    save_message(user_id, "user", transcribed_text)
    history = get_recent_messages(user_id, limit=10)

    reply = generate_ai_response(history, user.first_name or "Dost", prefs)
    save_message(user_id, "model", reply)

    response_intro = f"🎙 *Sun liya:* `{transcribed_text}`\n\n🤖 **AI Reply:**\n{reply}"
    send_long_message(user_id, response_intro)

    # Voice Reply (TTS)
    tts = gTTS(reply)
    tts.save("voice_reply.mp3")
    sound_mp3 = AudioSegment.from_mp3("voice_reply.mp3")
    sound_mp3.export("voice_reply.ogg", format="ogg")

    with open("voice_reply.ogg", "rb") as voice_file:
      bot.send_voice(user_id, voice_file)

  except Exception as e:
    logger.error(f"Voice error: {e}")
    bot.reply_to(
        message,
        "😅 Ops! Voice process karne mein thodi problem aayi. Text message"
        " bhej kar try karein.",
    )
  finally:
    for f in [
        "voice_msg.ogg",
        "voice_msg.wav",
        "voice_reply.mp3",
        "voice_reply.ogg",
    ]:
      if os.path.exists(f):
        os.remove(f)


@bot.message_handler(func=lambda message: True)
def handle_text(message):
  user_id = message.chat.id
  user = message.from_user
  text_content = message.text

  if not text_content:
    return

  register_user(user.id, user.username, user.first_name)
  prefs = get_user_settings(user_id)

  if not prefs["is_verified"] and not check_channel_membership(user.id):
    send_channel_join_prompt(user_id)
    return

  bot.send_chat_action(user_id, "typing")
  save_message(user_id, "user", text_content)

  history = get_recent_messages(user_id, limit=12)
  response = generate_ai_response(history, user.first_name or "Dost", prefs)

  save_message(user_id, "model", response)
  send_long_message(user_id, response)


# ==========================================
# --- MAIN LOOP WITH THREADING & BACKOFF ---
# ==========================================
if __name__ == "__main__":
  logger.info("🚀 Starting Advanced Groq Telegram Bot...")

  # Start Flask web server in a background thread for Render keep-alive
  flask_thread = threading.Thread(target=run_flask)
  flask_thread.daemon = True
  flask_thread.start()
  logger.info("🌐 Flask Keep-Alive server started in background thread.")

  # Clean webhook state to prevent conflicts
  try:
    bot.remove_webhook()
    logger.info("🧹 Existing Webhooks cleared successfully.")
  except Exception as e:
    logger.warning(f"Could not remove webhook: {e}")

  backoff = 1
  max_backoff = 60

  while True:
    try:
      logger.info("🔄 Bot polling started...")
      bot.polling(none_stop=True, interval=0, timeout=30, long_polling_timeout=30)
      backoff = 1  # Reset backoff if polling exits cleanly
    except Exception as e:
      sleep_time = backoff + random.uniform(0, 1)
      logger.error(
          f"Polling exception: {e}. Reconnecting in {sleep_time:.2f} seconds..."
      )
      time.sleep(sleep_time)
      backoff = min(backoff * 2, max_backoff)
