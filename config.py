import os

# Telegram Bot credentials
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "7383270877:AAEbWRGgDIIccsFozcdxwxn4vxBI3f19VeA")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "374573193")

# Path to events database
DB_PATH = os.getenv("DB_PATH", "/root/asterisk-webhook/asterisk_events.db")

# Admin notifications chat (можно не трогать)
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", TELEGRAM_CHAT_ID)
