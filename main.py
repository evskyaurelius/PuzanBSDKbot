import os
import telebot
import yt_dlp
from flask import Flask
from threading import Thread

# Environment variables from Render
BOT_TOKEN = os.environ.get('BOT_TOKEN')
ALLOWED_USER_ID = os.environ.get('ALLOWED_USER_ID')

if not BOT_TOKEN:
    raise ValueError("No BOT_TOKEN provided in environment variables.")

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# Dummy web server to satisfy Render's port binding requirement
@app.route('/')
def home():
    return "Telegram Bot is running!"

def run_flask():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    if ALLOWED_USER_ID and str(message.chat.id) != ALLOWED_USER_ID:
        bot.reply_to(message, "Unauthorized access. This bot is for personal use only.")
        return
    bot.reply_to(message, "Send me a link from Instagram, TikTok, or X (Twitter), and I will download the video/photo for you.")

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    # Security check: ensures only you can use the bot
    if ALLOWED_USER_ID and str(message.chat.id) != ALLOWED_USER_ID:
        bot.reply_to(message, "Unauthorized access.")
        return
    
    url = message.text.strip()
    if not url.startswith('http'):
        bot.reply_to(message, "Please send a valid HTTP/HTTPS URL.")
        return

    status_msg = bot.reply_to(message, "⏳ Downloading media... This might take a moment.")
    
    ydl_opts = {
        'outtmpl': '/tmp/%(id)s.%(ext)s',
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'merge_output_format': 'mp4',
        'max_filesize': 49 * 1024 * 1024, # Telegram bot limit is 50MB
        'noplaylist': True,
        'quiet': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            # Extract actual media info if it's part of a gallery/playlist
            if 'entries' in info:
                info = info['entries'][0]
                
            # Locate the downloaded file
            if 'requested_downloads' in info:
                file_path = info['requested_downloads'][0]['filepath']
            else:
                file_path = ydl.prepare_filename(info)

            if os.path.exists(file_path):
                ext = file_path.split('.')[-1].lower()
                
                with open(file_path, 'rb') as media_file:
                    bot.edit_message_text("📤 Uploading to Telegram...", chat_id=message.chat.id, message_id=status_msg.message_id)
                    
                    if ext in ['jpg', 'jpeg', 'png', 'webp']:
                        bot.send_photo(message.chat.id, media_file, reply_to_message_id=message.message_id)
                    else:
                        # Increased timeout because sending video files takes time
                        bot.send_video(message.chat.id, media_file, reply_to_message_id=message.message_id, timeout=120)
                
                # Cleanup server storage
                bot.delete_message(message.chat.id, status_msg.message_id)
                os.remove(file_path)
            else:
                raise Exception("File not found after download.")
                
    except yt_dlp.utils.DownloadError as e:
        bot.edit_message_text(f"❌ Download failed. The link might be private or unsupported.\nError: {str(e)}", chat_id=message.chat.id, message_id=status_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ An error occurred: {str(e)}", chat_id=message.chat.id, message_id=status_msg.message_id)

if __name__ == '__main__':
    # Start the Flask app in a background thread
    server_thread = Thread(target=run_flask)
    server_thread.start()
    
    # Start the Telegram bot polling
    print("Bot is polling...")
    bot.infinity_polling()
