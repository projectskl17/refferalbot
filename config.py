import os
from typing import List

API_ID = int(os.getenv("API_ID", ""))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
MONGO_URI = os.getenv("MONGO_URI", "")
DB_NAME = os.getenv("DB_NAME", "subt")
SUDO = list(map(int, os.getenv("SUDO", "").split()))
LOG_CHNL = int(os.getenv("LOG_CHNL", ""))


BOT_ABOUT = (
    "This bot helps group admins verify members by requiring them to refer "
    "friends before they can stay in the group."
)

BOT_HOW_IT_WORKS = (
    "1. You join a group that uses this bot\n"
    "2. The bot sends you a personal referral link\n"
    "3. Share that link with friends and get them to click it\n"
    "4. Once you hit the required referral count your membership is confirmed"
)

BOT_COMMANDS_TEXT = (
    "/connect — Link a group to this bot (admins only)\n"
    "/settings — Configure referral rules for your group (admins only)"
)

BOT_START_HINT = (
    "If you were sent here from a group, look for the "
    "**Verify Access** button in the welcome message and tap it."
)