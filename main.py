import os
import re
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Thread
from urllib.parse import urlparse

import telebot
import yt_dlp
from flask import Flask
from waitress import serve


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ALLOWED_USER_ID = os.environ.get("ALLOWED_USER_ID")

PORT = int(os.environ.get("PORT", "10000"))

MAX_FILE_SIZE = 49 * 1024 * 1024
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "2"))

DOWNLOAD_TIMEOUT = 180
UPLOAD_TIMEOUT = 180


if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN is missing from environment variables."
    )


# ============================================================
# TELEGRAM / WEB APP
# ============================================================

bot = telebot.TeleBot(
    BOT_TOKEN,
    parse_mode=None,
)

app = Flask(__name__)

executor = ThreadPoolExecutor(
    max_workers=MAX_WORKERS
)


# ============================================================
# SUPPORTED DOMAINS
# ============================================================

SUPPORTED_HOSTS = {
    # Instagram
    "instagram.com",
    "www.instagram.com",
    "instagr.am",
    "www.instagr.am",

    # TikTok
    "tiktok.com",
    "www.tiktok.com",
    "vm.tiktok.com",

    # X / Twitter
    "x.com",
    "www.x.com",
    "twitter.com",
    "www.twitter.com",
}


# ============================================================
# SECURITY / VALIDATION
# ============================================================

def is_authorized(message):
    """
    Restrict the bot to ALLOWED_USER_ID when configured.
    """
    if not ALLOWED_USER_ID:
        return True

    return str(message.chat.id) == str(
        ALLOWED_USER_ID
    )


def normalize_url(url):
    """
    Remove surrounding whitespace.
    """
    return url.strip()


def is_supported_url(url):
    """
    Only allow Instagram, TikTok and X/Twitter domains.
    """

    try:
        parsed = urlparse(url)

        if parsed.scheme.lower() not in {
            "http",
            "https",
        }:
            return False

        hostname = (
            parsed.hostname or ""
        ).lower()

        return hostname in SUPPORTED_HOSTS

    except Exception:
        return False


# ============================================================
# WEB / HEALTH CHECK
# ============================================================

@app.route("/")
def home():
    return "Telegram Bot is running!", 200


@app.route("/health")
def health():
    return {
        "status": "ok"
    }, 200


def run_web_server():
    """
    Production WSGI server for Render.
    """

    serve(
        app,
        host="0.0.0.0",
        port=PORT,
        threads=4,
    )


# ============================================================
# TELEGRAM STATUS
# ============================================================

def update_status(status_message, text):
    """
    Safely update the bot's status message.
    """

    try:
        bot.edit_message_text(
            text,
            chat_id=status_message.chat.id,
            message_id=status_message.message_id,
        )

    except Exception as exc:
        print(
            f"Status update error: {exc}"
        )


# ============================================================
# YT-DLP
# ============================================================

def build_ydl_options(temp_dir):
    """
    yt-dlp configuration.

    curl-cffi is installed through requirements.txt,
    allowing yt-dlp impersonation when supported by the
    installed yt-dlp version.
    """

    return {
        "outtmpl": os.path.join(
            temp_dir,
            "%(id)s.%(ext)s",
        ),

        "format": (
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]"
            "/best[ext=mp4]"
            "/best"
        ),

        "merge_output_format": "mp4",

        "max_filesize": MAX_FILE_SIZE,

        "noplaylist": True,

        "quiet": True,

        "no_warnings": False,

        "socket_timeout": 30,

        "retries": 3,

        "fragment_retries": 3,

        "continuedl": True,

        "overwrites": True,

        # Keep downloads isolated.
        "paths": {
            "home": temp_dir,
            "temp": temp_dir,
        },

        # Avoid unnecessary metadata downloads.
        "writethumbnail": False,
        "writeinfojson": False,
        "writesubtitles": False,
        "writeautomaticsub": False,
    }


# ============================================================
# FIND DOWNLOADED FILE
# ============================================================

def find_downloaded_file(
    ydl,
    info,
    temp_dir,
):
    """
    Locate the final downloaded file safely.
    """

    candidates = []

    requested_downloads = (
        info.get("requested_downloads")
        or []
    )

    for item in requested_downloads:
        filepath = item.get("filepath")

        if filepath:
            candidates.append(filepath)

    try:
        candidates.append(
            ydl.prepare_filename(info)
        )
    except Exception:
        pass

    for filepath in candidates:
        if (
            filepath
            and os.path.isfile(filepath)
        ):
            return filepath

    # Fallback: inspect temp directory.
    files = []

    for name in os.listdir(temp_dir):
        filepath = os.path.join(
            temp_dir,
            name,
        )

        if os.path.isfile(filepath):
            files.append(filepath)

    if not files:
        return None

    # Prefer common media formats.
    media_extensions = {
        ".mp4",
        ".mkv",
        ".webm",
        ".mov",
        ".m4v",
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
    }

    media_files = [
        filepath
        for filepath in files
        if os.path.splitext(
            filepath
        )[1].lower() in media_extensions
    ]

    if media_files:
        return max(
            media_files,
            key=os.path.getsize,
        )

    return max(
        files,
        key=os.path.getsize,
    )


# ============================================================
# TELEGRAM UPLOAD
# ============================================================

def send_media(
    message,
    file_path,
):
    """
    Send the downloaded file to Telegram.
    """

    extension = (
        os.path.splitext(file_path)[1]
        .lower()
    )

    with open(
        file_path,
        "rb",
    ) as media_file:

        # Images
        if extension in {
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
        }:

            return bot.send_photo(
                message.chat.id,
                media_file,
                reply_to_message_id=message.message_id,
                timeout=UPLOAD_TIMEOUT,
            )

        # Video
        if extension in {
            ".mp4",
            ".m4v",
            ".mov",
            ".webm",
            ".mkv",
        }:

            return bot.send_video(
                message.chat.id,
                media_file,
                reply_to_message_id=message.message_id,
                timeout=UPLOAD_TIMEOUT,
                supports_streaming=True,
            )

        # Unknown format:
        # send as document rather than failing.
        return bot.send_document(
            message.chat.id,
            media_file,
            reply_to_message_id=message.message_id,
            timeout=UPLOAD_TIMEOUT,
        )


# ============================================================
# DOWNLOAD PROCESS
# ============================================================

def download_media(
    message,
    status_message,
    url,
):
    """
    Complete download → validate → upload workflow.
    """

    temp_dir = tempfile.mkdtemp(
        prefix="puzanbot-"
    )

    try:

        # ----------------------------------------------------
        # DOWNLOAD
        # ----------------------------------------------------

        update_status(
            status_message,
            "⏳ Downloading media...",
        )

        ydl_opts = build_ydl_options(
            temp_dir
        )

        with yt_dlp.YoutubeDL(
            ydl_opts
        ) as ydl:

            info = ydl.extract_info(
                url,
                download=True,
            )

            if not info:
                raise RuntimeError(
                    "No media was returned."
                )

            # Some extractors return entries.
            if info.get("entries"):

                entries = [
                    entry
                    for entry in info["entries"]
                    if entry
                ]

                if not entries:
                    raise RuntimeError(
                        "No downloadable media found."
                    )

                info = entries[0]

            file_path = find_downloaded_file(
                ydl,
                info,
                temp_dir,
            )

        # ----------------------------------------------------
        # FILE VALIDATION
        # ----------------------------------------------------

        if not file_path:
            raise RuntimeError(
                "Downloaded file could not be located."
            )

        if not os.path.isfile(file_path):
            raise RuntimeError(
                "Downloaded file does not exist."
            )

        file_size = os.path.getsize(
            file_path
        )

        if file_size <= 0:
            raise RuntimeError(
                "Downloaded file is empty."
            )

        if file_size > MAX_FILE_SIZE:
            raise RuntimeError(
                "Downloaded media is too large "
                "for Telegram."
            )

        # ----------------------------------------------------
        # UPLOAD
        # ----------------------------------------------------

        update_status(
            status_message,
            "📤 Uploading to Telegram...",
        )

        send_media(
            message,
            file_path,
        )

        # ----------------------------------------------------
        # SUCCESS
        # ----------------------------------------------------

        try:
            bot.delete_message(
                message.chat.id,
                status_message.message_id,
            )
        except Exception:
            pass

    except yt_dlp.utils.DownloadError as exc:

        print(
            f"yt-dlp DownloadError: {exc}"
        )

        update_status(
            status_message,
            (
                "❌ Download failed.\n\n"
                "The post may be private, "
                "deleted, unavailable, "
                "rate-limited, or unsupported."
            ),
        )

    except Exception as exc:

        print(
            f"Download error: "
            f"{type(exc).__name__}: {exc}"
        )

        update_status(
            status_message,
            (
                "❌ Could not download or "
                "send this media.\n\n"
                "Please try another link."
            ),
        )

    finally:

        # Always remove temporary files.
        shutil.rmtree(
            temp_dir,
            ignore_errors=True,
        )


# ============================================================
# /START / HELP
# ============================================================

@bot.message_handler(
    commands=[
        "start",
        "help",
    ]
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
        (
            "👋 Send me an Instagram, "
            "TikTok, or X link.\n\n"
            "I'll download the available media "
            "and send it back."
        ),
    )


# ============================================================
# MESSAGE HANDLER
# ============================================================

@bot.message_handler(
    func=lambda message: True,
    content_types=[
        "text"
    ],
)
def handle_message(message):

    if not is_authorized(message):

        bot.reply_to(
            message,
            "Unauthorized access.",
        )

        return

    url = normalize_url(
        message.text
    )

    if not is_supported_url(url):

        bot.reply_to(
            message,
            (
                "❌ Unsupported URL.\n\n"
                "Please send an Instagram, "
                "TikTok, or X link."
            ),
        )

        return

    status_message = bot.reply_to(
        message,
        "⏳ Queued...",
    )

    # Run downloads outside Telegram's
    # main polling thread.
    executor.submit(
        download_media,
        message,
        status_message,
        url,
    )


# ============================================================
# START BOT
# ============================================================

if __name__ == "__main__":

    # Render health server.
    Thread(
        target=run_web_server,
        daemon=True,
    ).start()

    print(
        f"Web server listening on port {PORT}"
    )

    print(
        "Telegram bot polling..."
    )

    bot.infinity_polling(
        skip_pending=True,
        timeout=30,
        long_polling_timeout=30,
    )
