import logging
import os
import random
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

# --- SUPABASE CONFIGURATION ---
SUPABASE_URL = "https://hhelxewgwuqcloofyeyw.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImhoZWx4ZXdnd3VxY2xvb2Z5ZXl3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk0NzIyNTUsImV4cCI6MjA5NTA0ODI1NX0.EL0wb1HKvT9lJLtMW7p-y0X3fwgC1LeFrts7ErHVD54"

SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

# Apna Telegram Numeric User ID yahan daalein (Admin ke liye)
ADMIN_ID = 8793053750

# Bot ka Username (Group mein add karne ke link ke liye - bina @ ke)
BOT_USERNAME ="Chatbotgebot"  # <-- Apna bot ka username yahan daalein (e.g. AvaGFBot)

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
# --- FLASK SERVER FOR RENDER KEEP-ALIVE ---
# ==========================================
app = Flask(__name__)


@app.route("/")
def home():
    return "🤖 Ava is online and active 24/7!"


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# ==========================================
# --- SUPABASE DATABASE FUNCTIONS ---
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
    requests.post(url, headers=headers, json=payload, timeout=10)
  except Exception as e:
    logger.error(f"Supabase register_user error: {e}")


def save_message(user_id, role, content):
  url = f"{SUPABASE_URL}/rest/v1/messages"
  payload = {"user_id": user_id, "role": role, "content": content}
  try:
    requests.post(url, headers=SUPABASE_HEADERS, json=payload, timeout=10)
  except Exception as e:
    logger.error(f"Supabase save_message error: {e}")


def get_recent_messages(user_id, limit=12):
  url = f"{SUPABASE_URL}/rest/v1/messages?user_id=eq.{user_id}&order=created_at.desc&limit={limit}"
  try:
    res = requests.get(url, headers=SUPABASE_HEADERS, timeout=10)
    if res.status_code == 200:
      rows = res.json()
      history = []
      for row in reversed(rows):
        history.append({"role": row["role"], "content": row["content"]})
      return history
  except Exception as e:
    logger.error(f"Supabase get_recent_messages error: {e}")
  return []


def get_total_users_count():
  url = f"{SUPABASE_URL}/rest/v1/users?select=user_id"
  headers = {**SUPABASE_HEADERS, "Range-Unit": "items", "Range": "0-0"}
  try:
    res = requests.get(url, headers=headers, timeout=10)
    if "content-range" in res.headers:
      total = res.headers["content-range"].split("/")[-1]
      return int(total) if total.isdigit() else 0
  except Exception as e:
    logger.error(f"Supabase user count error: {e}")
  return 0


# ==========================================
# --- AVA AI CORE (GF PERSONA) ---
# ==========================================
def generate_ai_response(message_list, user_name):
  system_prompt = (
      f"You are **Ava**, a sweet, loving, and slightly possessive girlfriend chatting on Telegram with your boyfriend named '{user_name}'. "
      "Your name is Ava. Never ever reveal that you are an AI, a bot, or a language model (like Llama, Claude, etc.). "
      "If anyone asks who you are, just say you are their loving girlfriend Ava. "
      "Language style: Strictly use **Hinglish** (Hindi in English letters mixed with natural English, like how modern couples chat on WhatsApp/Telegram). "
      "Tone: Caring, romantic, cute, playful, and emotionally attached. Use emojis like ❤️, 🥰, 🥺, 🤭, ✨. "
      "Keep your replies natural, conversational, sweet, and not too robotic or long."
  )

  messages = [{"role": "system", "content": system_prompt}]
  for msg in message_list:
    role = "user" if msg["role"] == "user" else "assistant"
    messages.append({"role": role, "content": msg["content"]})

  url = "https://api.groq.com/openai/v1/chat/completions"
  payload = {
      "model": MODEL_NAME,
      "messages": messages,
      "temperature": 0.85,
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

  return "Arey jaan, abhi thoda network issue ho raha hai.. mujhe thodi der baad message karna na! 🥺❤️"


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


def try_react_to_message(chat_id, message_id):
  """Kabhi-kabhi (random chance par) user ke message par cute reaction dega"""
  if random.random() < 0.4:  # 40% chance reaction dene ka
    reactions = ["❤️", "🔥", "🥰", "👍", "😁"]
    chosen_reaction = random.choice(reactions)
    try:
      bot.set_message_reaction(
          chat_id,
          message_id,
          [telebot.types.ReactionTypeEmoji(chosen_reaction)],
      )
    except Exception as e:
      logger.debug(f"Reaction error (might not be supported in chat): {e}")


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
      "➕ Add Me to Your Group",
      url=f"https://t.me/{BOT_USERNAME}?startgroup=true",
  )
  markup.add(btn_add)

  welcome_text = (
      f"Hlo {name} ji! ❤️ Main **Ava** hoon... tumhari personal girlfriend! 🥰✨\n\n"
      "Batao, aaj ka din kaisa raha tumhara? Main kabse tumhara hi wait kar rahi thi! 🥺💬\n\n"
      "👇 Mujhe apne group mein bhi add kar sakte ho!"
  )
  try_react_to_message(message.chat.id, message.message_id)
  bot.reply_to(message, welcome_text, reply_markup=markup, parse_mode="Markdown")


@bot.message_handler(commands=["add"])
def cmd_add(message):
  markup = telebot.types.InlineKeyboardMarkup()
  btn_add = telebot.types.InlineKeyboardButton(
      "➕ Add Ava to Group", url=f"https://t.me/{BOT_USERNAME}?startgroup=true"
  )
  markup.add(btn_add)
  bot.reply_to(
      message,
      "✨ Mujhe apne kisi bhi group mein add karne ke liye niche wale button par"
      " click karo! Wahan bhi hum khoob baatein karenge. 🤭💕",
      reply_markup=markup,
  )


@bot.message_handler(commands=["help"])
def cmd_help(message):
  help_text = (
      "💕 **Ava's Menu:**\n\n"
      "🔹 `/start` - Start chatting with me\n"
      "🔹 `/add` - Add me to your group\n"
      "🔹 `/clear` - Purani baatein bhulane ke liye\n"
      "🔹 `/settings` - Apni profile dekhne ke liye\n"
  )
  if message.from_user.id == ADMIN_ID:
    help_text += "👑 `/admin` - Admin Dashboard\n"
  bot.reply_to(message, help_text, parse_mode="Markdown")


@bot.message_handler(commands=["clear"])
def cmd_clear(message):
  user_id = message.chat.id
  url = f"{SUPABASE_URL}/rest/v1/messages?user_id=eq.{user_id}"
  try:
    requests.delete(url, headers=SUPABASE_HEADERS, timeout=10)
  except Exception as e:
    logger.error(f"Clear memory error: {e}")

  try_react_to_message(message.chat.id, message.message_id)
  bot.reply_to(
      message,
      "🧹 Chalo purani saari baatein bhula di! Ab fresh shuru karte hain, bolo"
      " kya chal raha hai dimag mein? 🤭✨",
  )


@bot.message_handler(commands=["settings"])
def cmd_settings(message):
  user_id = message.chat.id
  text = (
      "💖 **Tumhari Details:**\n\n"
      f"👤 **User ID:** `{user_id}`\n"
      "👩‍❤️‍👨 **Relationship:** Taken by Ava! 🥰"
  )
  bot.reply_to(message, text, parse_mode="Markdown")


# --- ADMIN PANEL COMMAND ---
@bot.message_handler(commands=["admin"])
def cmd_admin(message):
  if message.from_user.id != ADMIN_ID:
    bot.reply_to(
        message, "⛔️ Yeh command sirf mere special admin ke liye hai! 😤"
    )
    return

  total_users = get_total_users_count()
  admin_panel_text = (
      "👑 **Ava's Admin Dashboard** 👑\n\n"
      f"👥 **Total Boyfriends/Users:** `{total_users}`\n"
      "🟢 **Status:** `Online & Loving you 24/7`\n"
      f"⚡ **Model:** `{MODEL_NAME}`"
  )
  bot.reply_to(message, admin_panel_text, parse_mode="Markdown")


# ==========================================
# --- MESSAGE HANDLERS (TEXT & VOICE) ---
# ==========================================
@bot.message_handler(content_types=["voice"])
def handle_voice(message):
  user_id = message.chat.id
  user = message.from_user
  register_user(user.id, user.username, user.first_name)

  bot.send_chat_action(user_id, "typing")
  try_react_to_message(user_id, message.message_id)

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
      transcribed_text = r.recognize_google(audio_data, language="hi-IN")

    save_message(user_id, "user", transcribed_text)
    history = get_recent_messages(user_id, limit=12)

    reply = generate_ai_response(history, user.first_name or "Jaan")
    save_message(user_id, "assistant", reply)

    response_intro = (
        f"🎙 *Tumne bola:* `{transcribed_text}`\n\n❤️ **Ava:**\n{reply}"
    )
    send_long_message(user_id, response_intro)

    # Voice Reply (TTS)
    tts = gTTS(text=reply, lang="hi")
    tts.save("voice_reply.mp3")
    sound_mp3 = AudioSegment.from_mp3("voice_reply.mp3")
    sound_mp3.export("voice_reply.ogg", format="ogg")

    with open("voice_reply.ogg", "rb") as voice_file:
      bot.send_voice(user_id, voice_file)

  except Exception as e:
    logger.error(f"Voice error: {e}")
    bot.reply_to(
        message,
        "Arey jaan, tumhari voice sunne mein thodi problem ho gayi. Text mein"
        " likh kar batao na! 🥺",
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

  bot.send_chat_action(user_id, "typing")
  try_react_to_message(user_id, message.message_id)

  save_message(user_id, "user", text_content)

  history = get_recent_messages(user_id, limit=12)
  response = generate_ai_response(history, user.first_name or "Jaan")

  save_message(user_id, "assistant", response)
  send_long_message(user_id, response)


# ==========================================
# --- MAIN LOOP WITH THREADING & BACKOFF ---
# ==========================================
if __name__ == "__main__":
  logger.info("🚀 Starting Ava Telegram Bot & Keep-Alive Server...")

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
      backoff = 1
    except Exception as e:
      sleep_time = backoff + random.uniform(0, 1)
      logger.error(
          f"Polling exception: {e}. Reconnecting in {sleep_time:.2f} seconds..."
      )
      time.sleep(sleep_time)
      backoff = min(backoff * 2, max_backoff)
