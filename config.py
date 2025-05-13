import os

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "7383270877:AAEbWRGgDIIccsFozcdwxn4vxBI3f19VeA")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "374573193")

# Database path
DB_PATH = os.getenv("DB_PATH", "/root/asterisk-webhook/asterisk_events.db")

# Admin notifications chat
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", TELEGRAM_CHAT_ID)
