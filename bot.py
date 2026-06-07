import sys
import time
from datetime import datetime

from pyrogram import Client
from loguru import logger

from config import API_ID, API_HASH, BOT_TOKEN, LOG_CHNL

# ── Colorful logger setup ─────────────────────────────────────────────────────

logger.remove()  # drop default handler

logger.add(
    sys.stdout,
    colorize=True,
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    ),
    level="DEBUG",
)

logger.add(
    "logs/bot.log",
    rotation="10 MB",
    retention="7 days",
    compression="zip",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}",
    level="INFO",
    encoding="utf-8",
)

# Custom log levels with colors
logger.level("STARTUP", no=25, color="<bold><magenta>", icon="🚀")
logger.level("SHUTDOWN", no=25, color="<bold><red>", icon="🛑")


# ── Bot class ─────────────────────────────────────────────────────────────────

class Bot(Client):
    def __init__(self):
        super().__init__(
            name="bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            workers=200,
            plugins={"root": "plugins"},
            sleep_threshold=15,
        )

        self.START_TIME = time.time()
        self.uptime: datetime | None = None

    async def start(self):
        logger.log("STARTUP", "Connecting to Telegram...")
        await super().start()

        me = await self.get_me()
        self.id = me.id
        self.name = me.first_name
        self.username = me.username
        self.uptime = datetime.now()

        logger.log("STARTUP", f"Bot online ✦ {self.name} (@{self.username})")
        logger.info(f"User ID   : {self.id}")
        logger.info(f"Started at: {self.uptime.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.success("All plugins loaded. Bot is ready.")

        try:
            await self.send_message(LOG_CHNL, "Bot restarted ♻️")
            logger.debug(f"Restart notification sent to admin ({LOG_CHNL})")
        except Exception as e:
            logger.warning(f"Could not notify admin: {e}")

    async def stop(self, *args):
        me = await self.get_me()
        logger.log("SHUTDOWN", f"Stopping {me.first_name}...")
        await super().stop()
        logger.log("SHUTDOWN", f"{me.first_name} stopped. Goodbye.")


bot = Bot()