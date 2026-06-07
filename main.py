from bot import bot
from pyrogram import idle

async def start():
    await bot.start()

bot.loop.create_task(start())
idle()