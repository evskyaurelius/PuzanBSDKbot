import os
import re
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Thread
import telebot
import yt_dlp
from flask import Flask
from waitress import serve
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ALLOWED_USER_ID = os.environ.get("ALLOWED_USER_ID")
MAX_FILE_SIZE = 49 * 1024 * 1024
UPLOAD_TIMEOUT = 180
MAX_WORKERS = 2
if not BOT_TOKEN:
    raise ValueError("No BOT_TOKEN provided in environment variables.")
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
SUPPORTED_HOSTS = {
    "instagram.com",
    "www.instagram.com",
    "instagr.am",
    "www.instagr.am",
    "tiktok.com",
    "www.tiktok.com",
    "vm.tiktok.com",
    "x.com",
    "www.x.com",
    "twitter.com",
    "www.twitter.com",
}
def is_authorized(message):
    return not ALLOWED_USER_ID or str(message.chat.id) == str(ALLOWED_USER_ID)
def is_supported_url(url):
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return False
    host = re.sub(
        r"^https?://",
        "",
        url,
        flags=re.IGNORECASE,
    ).split("/", 1)[0].split(":", 1)[0].lower()
    return host in SUPPORTED_HOSTS
@app.route("/")
def home():
    return "Telegram Bot is running!", 200
@app.route("/health")
def health():
    return {"status": "ok"}, 200
def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    serve(
        app,
        host="0.0.0.0",
        port=port,
        threads=4,
    )
def update_status(status_msg, text):
    try:
        bot.edit_message_text(
            text,
            chat_id=status_msg.chat.id,
            message_id=status_msg.message_id,
        )
    except Exception:
        pass
def download_media(message, status_msg, url):
    temp_dir = tempfile.mkdtemp(prefix="puzanbot-")
    ydl_opts = {
        "outtmpl": os.path.join(
            temp_dir,
            "%(id)s.%(ext)s",
        ),
        "format": (
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]"
            "/best[ext=mp4]/best"
        ),
        "merge_output_format": "mp4",
        "max_filesize": MAX_FILE_SIZE,
        "noplaylist": True,
        "quiet": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
    }
    try:
        update_status(
            status_msg,
            "⏳ Downloading media...",
        )
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                url,
                download=True,
            )
            if not info:
                raise RuntimeError(
                    "No media was found."
                )
            if info.get("entries"):
                info = next(
                    (
                        entry
                        for entry in info["entries"]
                        if entry
                    ),
                    None,
                )
                if not info:
                    raise RuntimeError(
                        "No downloadable media was found."
                    )
            requested = (
                info.get("requested_downloads")
                or []
            )
            candidates = [
                item.get("filepath")
                for item in requested
                if item.get("filepath")
            ]
            candidates.append(
                ydl.prepare_filename(info)
            )
            file_path = next(
                (
                    path
                    for path in candidates
                    if path and os.path.isfile(path)
                ),
                None,
            )
            if not file_path:
                file_path = next(
                    (
                        os.path.join(
                            temp_dir,
                            name,
                        )
                        for name in os.listdir(temp_dir)
                        if os.path.isfile(
                            os.path.join(
                                temp_dir,
                                name,
                            )
                        )
                    ),
                    None,
                )
            if not file_path:
                raise RuntimeError(
                    "Downloaded file could not be located."
                )
        if os.path.getsize(file_path) > MAX_FILE_SIZE:
            raise RuntimeError(
                "Downloaded media is too large."
            )
        update_status(
            status_msg,
            "📤 Uploading to Telegram...",
        )
        extension = os.path.splitext(
            file_path
        )[1].lower()
        with open(file_path, "rb") as media_file:
            if extension in {
                ".jpg",
                ".jpeg",
                ".png",
                ".webp",
            }:
                bot.send_photo(
                    message.chat.id,
                    media_file,
                    reply_to_message_id=message.message_id,
                    timeout=UPLOAD_TIMEOUT,
                )
            else:
                bot.send_video(
                    message.chat.id,
                    media_file,
                    reply_to_message_id=message.message_id,
                    timeout=UPLOAD_TIMEOUT,
                    supports_streaming=True,
                )
        try:
            bot.delete_message(
                message.chat.id,
                status_msg.message_id,
            )
        except Exception:
            pass
    except yt_dlp.utils.DownloadError as exc:
        print(f"yt-dlp error: {exc}")
        update_status(
            status_msg,
            "❌ Download failed. "
            "The post may be private, unavailable, "
            "rate-limited, or unsupported.",
        )
    except Exception as exc:
        print(
            f"Download error: "
            f"{type(exc).__name__}: {exc}"
        )
        update_status(
            status_msg,
            "❌ Could not download or send this media. "
            "Please try another link.",
        )
    finally:
        shutil.rmtree(
            temp_dir,
            ignore_errors=True,
        )
@bot.message_handler(
    commands=["start", "help"]
)
def send_welcome(message):
    if not is_authorized(message):
        bot.reply_to(
            message,
            "Unauthorized access.",
        )
        return
    bot.reply_to(
        message,
        "Send an Instagram, TikTok, or X "
        "link and I'll download the available media.",
    )
@bot.message_handler(
    func=lambda message: True,
    content_types=["text"],
)
def handle_message(message):
    if not is_authorized(message):
        bot.reply_to(
            message,
            "Unauthorized access.",
        )
        return
    url = message.text.strip()
    if not is_supported_url(url):
        bot.reply_to(
            message,
            "Please send a supported "
            "Instagram, TikTok, or X URL.",
        )
        return
    status_msg = bot.reply_to(
        message,
        "⏳ Queued...",
    )
    executor.submit(
        download_media,
        message,
        status_msg,
        url,
    )
if __name__ == "__main__":
    Thread(
        target=run_web_server,
        daemon=True,
    ).start()
    print("Bot is polling...")
    bot.infinity_polling(
        skip_pending=True,
        timeout=30,
        long_polling_timeout=30,
    )
