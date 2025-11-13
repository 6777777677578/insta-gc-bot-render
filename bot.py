import os
import logging
import time
import asyncio
import threading
from dotenv import load_dotenv
from instagrapi import Client
from instagrapi.exceptions import LoginRequired, PleaseWaitFewMinutes
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from flask import Flask, request

# === LOAD ENV ===
load_dotenv()

IG_USER = os.getenv("INSTAGRAM_USERNAME")
IG_PASS = os.getenv("INSTAGRAM_PASSWORD")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# === GLOBALS ===
cl = Client()
active_threads = {}  # {thread_id: reply_text}
seen_messages = set()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

# === INSTAGRAM LOGIN & MONITOR ===
def ig_login():
    try:
        if os.path.exists("session.json"):
            cl.load_settings("session.json")
            cl.get_timeline_feed()
            log.info("Session loaded")
        else:
            cl.login(IG_USER, IG_PASS)
            cl.dump_settings("session.json")
            log.info("Logged in & session saved")
    except LoginRequired:
        if os.path.exists("session.json"):
            os.remove("session.json")
        cl.login(IG_USER, IG_PASS)
        cl.dump_settings("session.json")
    except PleaseWaitFewMinutes:
        log.warning("Rate limited. Waiting 5 min...")
        time.sleep(300)
    except Exception as e:
        log.error(f"Login failed: {e}")
        raise

async def monitor():
    while True:
        try:
            ig_login()
            for thread_id, reply in list(active_threads.items()):
                try:
                    thread = cl.direct_thread(thread_id)
                    for msg in thread.messages:
                        if msg.id in seen_messages or msg.user_id == cl.user_id:
                            continue
                        seen_messages.add(msg.id)
                        log.info(f"New: {msg.text[:40]} in {thread_id}")
                        cl.direct_send(text=reply, thread_ids=[thread_id])
                        log.info(f"Sent: {reply}")
                        await asyncio.sleep(2)
                except Exception as e:
                    log.error(f"Thread error {thread_id}: {e}")
        except Exception as e:
            log.error(f"Monitor error: {e}")
        await asyncio.sleep(8)

# === TELEGRAM COMMANDS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != TG_CHAT_ID:
        return
    await update.message.reply_text(
        "Instagram GC Auto-Reply Bot\n\n"
        "/add <thread_id> <message>\n"
        "/remove <thread_id>\n"
        "/list"
    )

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != TG_CHAT_ID:
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /add <thread_id> <msg>")
        return
    tid, msg = args[0], " ".join(args[1:])
    active_threads[tid] = msg
    await update.message.reply_text(f"Added `{tid}`\nReply: `{msg}`", parse_mode="Markdown")

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != TG_CHAT_ID:
        return
    tid = context.args[0]
    if tid in active_threads:
        del active_threads[tid]
        await update.message.reply_text(f"Removed `{tid}`", parse_mode="Markdown")
    else:
        await update.message.reply_text("Not found")

async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != TG_CHAT_ID:
        return
    if not active_threads:
        await update.message.reply_text("No active threads")
    else:
        txt = "*Active:*\n"
        for t, m in active_threads.items():
            txt += f"• `{t}`: `{m}`\n"
        await update.message.reply_text(txt, parse_mode="Markdown")

# === FLASK APP ===
app = Flask(__name__)
application = Application.builder().token(TG_TOKEN).build()

# Add handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("add", add))
application.add_handler(CommandHandler("remove", remove))
application.add_handler(CommandHandler("list", list_cmd))

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        update = Update.de_json(request.get_json(), application.bot)
        asyncio.run(application.process_update(update))
    except Exception as e:
        log.error(f"Webhook error: {e}")
    return "OK", 200

# === MAIN ===
if __name__ == "__main__":
    # Start monitor
    monitor_thread = threading.Thread(target=lambda: asyncio.run(monitor()), daemon=True)
    monitor_thread.start()

    # Set webhook
    try:
        application.bot.set_webhook(url=WEBHOOK_URL)
        log.info(f"Webhook set: {WEBHOOK_URL}")
    except Exception as e:
        log.error(f"Webhook failed: {e}")

    # Run Flask
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
