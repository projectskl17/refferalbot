import asyncio
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from loguru import logger

from Database.db import db
import config

import base64

def _encode_id(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")

def _decode_id(token: str) -> str:
    padding = 4 - len(token) % 4
    if padding != 4:
        token += "=" * padding
    return base64.urlsafe_b64decode(token).decode()


_CHAT_CACHE: dict[int, dict] = {}

def cache_get(chat_id: int) -> dict | None:
    return _CHAT_CACHE.get(chat_id)

def cache_set(chat_id: int, chat_doc: dict):
    _CHAT_CACHE[chat_id] = chat_doc

def cache_invalidate(chat_id: int):
    _CHAT_CACHE.pop(chat_id, None)

def get_chat_doc(chat_id: int) -> dict | None:
    doc = cache_get(chat_id)
    if doc is None:
        doc = db.get_connected_chat(chat_id)
        if doc:
            cache_set(chat_id, doc)
    return doc


SETTINGS_STATE: dict[int, dict] = {}

_BOT_USERNAME: str | None = None


async def get_bot_username(bot: Client) -> str:
    global _BOT_USERNAME
    if not _BOT_USERNAME:
        me = await bot.get_me()
        _BOT_USERNAME = me.username
    return _BOT_USERNAME


def seconds_to_human(seconds: int) -> str:
    if seconds >= 86400:
        v = seconds // 86400
        return f"{v} day{'s' if v > 1 else ''}"
    elif seconds >= 3600:
        v = seconds // 3600
        return f"{v} hour{'s' if v > 1 else ''}"
    else:
        v = seconds // 60
        return f"{v} minute{'s' if v > 1 else ''}"


async def check_bot_admin(bot: Client, chat_id: int) -> bool:
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id, me.id)
        return member.status in (
            enums.ChatMemberStatus.ADMINISTRATOR,
            enums.ChatMemberStatus.OWNER,
        )
    except Exception:
        return False


@Client.on_message(filters.command("connect") & filters.private, group=0)
async def connect_handler(bot: Client, message: Message):
    uid = message.from_user.id
    SETTINGS_STATE[uid] = {"step": "await_chat_id"}
    await message.reply("Send me the group/channel ID (e.g. `-100xxxxxxxxxx`):")


@Client.on_message(
    filters.private & filters.text & ~filters.command(["start", "connect", "settings"]),
    group=1,
)
async def fsm_text_handler(bot: Client, message: Message):
    uid = message.from_user.id
    state = SETTINGS_STATE.get(uid)
    if not state:
        return

    step = state.get("step")
    text = message.text.strip()

    if step == "await_chat_id":
        try:
            chat_id = int(text)
        except ValueError:
            await message.reply("❌ Invalid ID. Must be a number like `-100xxxxxxxxxx`. Try again:")
            return

        try:
            chat = await bot.get_chat(chat_id)
        except Exception:
            await message.reply("❌ Bot not in that chat or ID wrong. Add bot first, then try again:")
            return

        is_admin = await check_bot_admin(bot, chat_id)
        if not is_admin:
            await message.reply("❌ Bot not admin. Make bot admin first, then try again:")
            return

        try:
            invite = await bot.create_chat_invite_link(chat_id)
            invite_link = invite.invite_link
        except Exception:
            invite_link = ""

        db.connect_chat(chat_id, chat.title or str(chat_id), invite_link)
        cache_invalidate(chat_id)
        SETTINGS_STATE.pop(uid, None)
        await message.reply(
            f"Connected: **{chat.title or chat_id}**\n"
            f"ID: `{chat_id}`\n\n"
            "Use /settings to configure."
        )

    elif step == "await_ban_time":
        t = text.lower()
        try:
            if t.endswith('d'):
                secs = int(t[:-1]) * 86400
            elif t.endswith('h'):
                secs = int(t[:-1]) * 3600
            elif t.endswith('m'):
                secs = int(t[:-1]) * 60
            else:
                raise ValueError
            if secs < 60:
                raise ValueError
        except ValueError:
            await message.reply("❌ Invalid. Use `1d`, `6h`, or `30m`. Try again:")
            return

        chat_doc = get_chat_doc(state["chat_id"])
        s = chat_doc["settings"]
        s["ban_after_seconds"] = secs
        db.update_chat_settings(state["chat_id"], s)
        chat_doc["settings"] = s
        cache_set(state["chat_id"], chat_doc)
        state["step"] = None
        await _show_settings_menu(bot, message, chat_doc, edit=False)

    elif step == "await_ref_count":
        try:
            count = int(text)
            if count < 1:
                raise ValueError
        except ValueError:
            await message.reply("❌ Invalid. Send a number >= 1:")
            return

        chat_doc = get_chat_doc(state["chat_id"])
        s = chat_doc["settings"]
        s["referral_count"] = count
        db.update_chat_settings(state["chat_id"], s)
        chat_doc["settings"] = s
        cache_set(state["chat_id"], chat_doc)
        state["step"] = None
        await _show_settings_menu(bot, message, chat_doc, edit=False)

    elif step == "await_welcome_text":
        chat_doc = get_chat_doc(state["chat_id"])
        s = chat_doc["settings"]
        s.setdefault("welcome", {})["text"] = text
        db.update_chat_settings(state["chat_id"], s)
        chat_doc["settings"] = s
        cache_set(state["chat_id"], chat_doc)
        state["step"] = None
        await message.reply("Welcome text updated.")
        await _show_welcome_menu(bot, message, chat_doc, edit=False)

    elif step == "await_welcome_buttons":
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        parsed = []
        errors = []
        for line in lines:
            if "|" not in line:
                errors.append(line)
                continue
            parts = line.split("|", 1)
            btn_text = parts[0].strip()
            btn_url = parts[1].strip()
            if not btn_text or not btn_url.startswith("http"):
                errors.append(line)
                continue
            parsed.append({"text": btn_text, "url": btn_url})

        if errors:
            await message.reply(
                "❌ Invalid lines (skipped):\n" + "\n".join(f"`{e}`" for e in errors) +
                "\n\nFormat: `Button Text | https://url.com`"
            )
            if not parsed:
                return

        chat_doc = get_chat_doc(state["chat_id"])
        s = chat_doc["settings"]
        s.setdefault("welcome", {})["buttons"] = parsed
        db.update_chat_settings(state["chat_id"], s)
        chat_doc["settings"] = s
        cache_set(state["chat_id"], chat_doc)
        state["step"] = None
        await message.reply(f"{len(parsed)} button(s) saved.")
        await _show_welcome_menu(bot, message, chat_doc, edit=False)


@Client.on_message(
    filters.private & (filters.photo | filters.video | filters.animation),
    group=1,
)
async def fsm_media_handler(bot: Client, message: Message):
    uid = message.from_user.id
    state = SETTINGS_STATE.get(uid)
    if not state or state.get("step") != "await_welcome_media":
        return

    if message.photo:
        media_type = "photo"
        file_id = message.photo.file_id
    elif message.video:
        media_type = "video"
        file_id = message.video.file_id
    elif message.animation:
        media_type = "animation"
        file_id = message.animation.file_id
    else:
        return

    chat_doc = get_chat_doc(state["chat_id"])
    s = chat_doc["settings"]
    s.setdefault("welcome", {})["media"] = {"type": media_type, "file_id": file_id}
    db.update_chat_settings(state["chat_id"], s)
    chat_doc["settings"] = s
    cache_set(state["chat_id"], chat_doc)
    state["step"] = None
    await message.reply(f"Welcome {media_type} saved.")
    await _show_welcome_menu(bot, message, chat_doc, edit=False)


@Client.on_message(filters.command("settings") & filters.private, group=0)
async def settings_handler(bot: Client, message: Message):
    chats = db.get_all_connected_chats()
    if not chats:
        await message.reply("No connected chats. Use /connect first.")
        return

    buttons = [
        [InlineKeyboardButton(c["title"], callback_data=f"settings_chat_{c['chat_id']}")]
        for c in chats
    ]
    await message.reply("Select chat to configure:", reply_markup=InlineKeyboardMarkup(buttons))


@Client.on_callback_query(filters.regex(r"^settings_chat_(-?\d+)$"))
async def settings_chat_select(bot: Client, query: CallbackQuery):
    chat_id = int(query.matches[0].group(1))
    chat_doc = get_chat_doc(chat_id)
    if not chat_doc:
        await query.answer("Chat not found.", show_alert=True)
        return
    SETTINGS_STATE[query.from_user.id] = {"chat_id": chat_id, "step": None}
    await _show_settings_menu(bot, query, chat_doc, edit=True)


async def _show_settings_menu(bot: Client, event, chat_doc: dict, edit: bool = True):
    s = chat_doc["settings"]
    ban_status = "ON" if s["ban_enabled"] else "❌ OFF"
    time_human = seconds_to_human(s["ban_after_seconds"])
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Ban: {ban_status}", callback_data="toggle_ban")],
        [InlineKeyboardButton(f"⏱ Ban After: {time_human}", callback_data="set_ban_time")],
        [InlineKeyboardButton(f"👥 Referrals Needed: {s['referral_count']}", callback_data="set_ref_count")],
        [InlineKeyboardButton("💬 Welcome Message", callback_data="welcome_settings")],
        [InlineKeyboardButton("❌ Close", callback_data="settings_close")],
    ])
    text = (
        f"⚙️ Settings — **{chat_doc['title']}**\n\n"
        f"Ban enabled: {ban_status}\n"
        f"Ban after: {time_human}\n"
        f"Referrals needed: {s['referral_count']}"
    )
    if edit and isinstance(event, CallbackQuery):
        await event.edit_message_text(text, reply_markup=buttons)
    elif isinstance(event, CallbackQuery):
        await event.message.reply(text, reply_markup=buttons)
    else:
        await event.reply(text, reply_markup=buttons)


async def _show_welcome_menu(bot: Client, event, chat_doc: dict, edit: bool = True):
    s = chat_doc["settings"]
    w = s.get("welcome", {})
    has_text    = "✅" if w.get("text")    else "❌"
    has_media   = "✅" if w.get("media")   else "❌"
    has_buttons = "✅" if w.get("buttons") else "❌"

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{has_text} Edit Text",       callback_data="welcome_set_text")],
        [InlineKeyboardButton(f"{has_media} Set Photo/Video", callback_data="welcome_set_media")],
        [InlineKeyboardButton(f"{has_buttons} Set Buttons",  callback_data="welcome_set_buttons")],
        [InlineKeyboardButton("🗑 Clear Media",              callback_data="welcome_clear_media")],
        [InlineKeyboardButton("🗑 Clear Buttons",            callback_data="welcome_clear_buttons")],
        [InlineKeyboardButton("👁 Preview",                  callback_data="welcome_preview")],
        [InlineKeyboardButton("◀️ Back",                     callback_data=f"settings_chat_{chat_doc['chat_id']}")],
    ])
    text = (
        f"💬 Welcome Message — **{chat_doc['title']}**\n\n"
        f"Text: {has_text}\n"
        f"Media: {has_media}\n"
        f"Buttons: {has_buttons}\n\n"
        "Variables you can use in text:\n"
        "`{name}` — user's first name\n"
        "`{mention}` — clickable mention\n"
        "`{ref_link}` — referral link\n"
        "`{time}` — ban deadline time\n"
        "`{ref_count}` — referrals needed"
    )
    if edit and isinstance(event, CallbackQuery):
        await event.edit_message_text(text, reply_markup=buttons)
    elif isinstance(event, CallbackQuery):
        await event.message.reply(text, reply_markup=buttons)
    else:
        await event.reply(text, reply_markup=buttons)


@Client.on_callback_query(filters.regex(r"^toggle_ban$"))
async def toggle_ban(bot: Client, query: CallbackQuery):
    state = SETTINGS_STATE.get(query.from_user.id)
    if not state:
        await query.answer("Start from /settings.", show_alert=True)
        return
    chat_doc = get_chat_doc(state["chat_id"])
    s = chat_doc["settings"]
    s["ban_enabled"] = not s["ban_enabled"]
    db.update_chat_settings(state["chat_id"], s)
    chat_doc["settings"] = s
    cache_set(state["chat_id"], chat_doc)
    await _show_settings_menu(bot, query, chat_doc, edit=True)


@Client.on_callback_query(filters.regex(r"^set_ban_time$"))
async def set_ban_time_prompt(bot: Client, query: CallbackQuery):
    state = SETTINGS_STATE.get(query.from_user.id)
    if not state:
        await query.answer("Start from /settings.", show_alert=True)
        return
    state["step"] = "await_ban_time"
    await query.message.reply(
        "Send ban time. Examples:\n"
        "`1d` = 1 day\n`6h` = 6 hours\n`30m` = 30 minutes"
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^set_ref_count$"))
async def set_ref_count_prompt(bot: Client, query: CallbackQuery):
    state = SETTINGS_STATE.get(query.from_user.id)
    if not state:
        await query.answer("Start from /settings.", show_alert=True)
        return
    state["step"] = "await_ref_count"
    await query.message.reply("Send number of referrals required (e.g. `3`):")
    await query.answer()


@Client.on_callback_query(filters.regex(r"^settings_close$"))
async def settings_close(bot: Client, query: CallbackQuery):
    SETTINGS_STATE.pop(query.from_user.id, None)
    await query.message.delete()


@Client.on_callback_query(filters.regex(r"^welcome_settings$"))
async def welcome_settings_cb(bot: Client, query: CallbackQuery):
    state = SETTINGS_STATE.get(query.from_user.id)
    if not state:
        await query.answer("Start from /settings.", show_alert=True)
        return
    chat_doc = get_chat_doc(state["chat_id"])
    await _show_welcome_menu(bot, query, chat_doc, edit=True)


@Client.on_callback_query(filters.regex(r"^welcome_set_text$"))
async def welcome_set_text_cb(bot: Client, query: CallbackQuery):
    state = SETTINGS_STATE.get(query.from_user.id)
    if not state:
        await query.answer("Start from /settings.", show_alert=True)
        return
    state["step"] = "await_welcome_text"
    await query.message.reply(
        "Send the new welcome message text.\n\n"
        "Available variables:\n"
        "`{name}` `{mention}` `{ref_link}` `{time}` `{ref_count}`"
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^welcome_set_media$"))
async def welcome_set_media_cb(bot: Client, query: CallbackQuery):
    state = SETTINGS_STATE.get(query.from_user.id)
    if not state:
        await query.answer("Start from /settings.", show_alert=True)
        return
    state["step"] = "await_welcome_media"
    await query.message.reply("Send a photo, video, or GIF to use as welcome media:")
    await query.answer()


@Client.on_callback_query(filters.regex(r"^welcome_set_buttons$"))
async def welcome_set_buttons_cb(bot: Client, query: CallbackQuery):
    state = SETTINGS_STATE.get(query.from_user.id)
    if not state:
        await query.answer("Start from /settings.", show_alert=True)
        return
    state["step"] = "await_welcome_buttons"
    await query.message.reply(
        "Send buttons, one per line:\n"
        "`Button Label | https://url.com`\n\n"
        "Example:\n"
        "`Join Channel | https://t.me/mychannel`\n"
        "`Our Website | https://example.com`"
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^welcome_clear_media$"))
async def welcome_clear_media_cb(bot: Client, query: CallbackQuery):
    state = SETTINGS_STATE.get(query.from_user.id)
    if not state:
        await query.answer("Start from /settings.", show_alert=True)
        return
    chat_doc = get_chat_doc(state["chat_id"])
    s = chat_doc["settings"]
    s.setdefault("welcome", {})["media"] = None
    db.update_chat_settings(state["chat_id"], s)
    chat_doc["settings"] = s
    cache_set(state["chat_id"], chat_doc)
    await query.answer("Media cleared.")
    await _show_welcome_menu(bot, query, chat_doc, edit=True)


@Client.on_callback_query(filters.regex(r"^welcome_clear_buttons$"))
async def welcome_clear_buttons_cb(bot: Client, query: CallbackQuery):
    state = SETTINGS_STATE.get(query.from_user.id)
    if not state:
        await query.answer("Start from /settings.", show_alert=True)
        return
    chat_doc = get_chat_doc(state["chat_id"])
    s = chat_doc["settings"]
    s.setdefault("welcome", {})["buttons"] = []
    db.update_chat_settings(state["chat_id"], s)
    chat_doc["settings"] = s
    cache_set(state["chat_id"], chat_doc)
    await query.answer("Buttons cleared.")
    await _show_welcome_menu(bot, query, chat_doc, edit=True)


@Client.on_callback_query(filters.regex(r"^welcome_preview$"))
async def welcome_preview_cb(bot: Client, query: CallbackQuery):
    state = SETTINGS_STATE.get(query.from_user.id)
    if not state:
        await query.answer("Start from /settings.", show_alert=True)
        return
    chat_doc = get_chat_doc(state["chat_id"])
    await _send_welcome(bot, query.message.chat.id, query.from_user, chat_doc, preview=True)
    await query.answer("Preview sent!")


DEFAULT_WELCOME_TEXT = (
    "Welcome {mention}!\n\n"
    "**Your membership is pending. Complete the required task within {time} to stay in this group.**\n\n"
    "Click the button below to continue."
)


async def _send_welcome(
    bot: Client,
    chat_id: int,
    user,
    chat_doc: dict,
    preview: bool = False,
) -> int | None:
    s = chat_doc["settings"]
    w = s.get("welcome", {})

    bot_username = await get_bot_username(bot)
    user_id = user.id
    orig_chat_id = chat_doc["chat_id"]

    ref_link = f"https://t.me/{bot_username}?start=ref_{orig_chat_id}_{user_id}"
    user_invite_link = f"https://t.me/{bot_username}?start=join_{_encode_id(str(orig_chat_id))}"
    time_human = seconds_to_human(s["ban_after_seconds"])
    mention = f"[{user.first_name}](tg://user?id={user_id})"

    encoded_ref = quote(ref_link)
    share_text = quote("Join this group via my referral link!")
    share_url = f"https://t.me/share/url?url={encoded_ref}&text={share_text}"

    raw_text = w.get("text") or DEFAULT_WELCOME_TEXT
    text = raw_text.format(
        name=user.first_name,
        mention=mention,
        ref_link=ref_link,
        time=time_human,
        ref_count=s["referral_count"],
    )
    if preview:
        text = f"👁 **Preview:**\n\n{text}"

    custom_btns = w.get("buttons") or []
    button_rows = [[InlineKeyboardButton(b["text"], url=b["url"])] for b in custom_btns]
    button_rows += [
        [InlineKeyboardButton("Verify Access", url=user_invite_link)],
    ]
    markup = InlineKeyboardMarkup(button_rows)

    media = w.get("media")
    try:
        if media and not preview:
            mtype = media.get("type")
            fid = media.get("file_id")
            if mtype == "photo":
                sent = await bot.send_photo(chat_id, fid, caption=text, reply_markup=markup)
            elif mtype == "video":
                sent = await bot.send_video(chat_id, fid, caption=text, reply_markup=markup)
            elif mtype == "animation":
                sent = await bot.send_animation(chat_id, fid, caption=text, reply_markup=markup)
            else:
                sent = await bot.send_message(chat_id, text, reply_markup=markup, disable_web_page_preview=True)
        else:
            sent = await bot.send_message(chat_id, text, reply_markup=markup, disable_web_page_preview=True)
        return sent.id
    except Exception as e:
        logger.error(f"Failed to send welcome in {chat_id}: {e}")
        return None

_LAST_WELCOME: dict[int, int] = {}
_LAST_COMPLETED: dict[int, int] = {}


@Client.on_message(filters.new_chat_members, group=0)
async def member_join_handler(bot: Client, message: Message):
    chat_id = message.chat.id
    chat_doc = get_chat_doc(chat_id)
    if not chat_doc:
        return

    # delete previous welcome message for this chat
    last_msg_id = _LAST_WELCOME.get(chat_id)
    if last_msg_id:
        try:
            await bot.delete_messages(chat_id, last_msg_id)
        except Exception:
            pass
        _LAST_WELCOME.pop(chat_id, None)

    for user in message.new_chat_members:
        if user.is_bot:
            continue

        user_id = user.id
        s = chat_doc["settings"]
        ban_after = s["ban_after_seconds"]
        deadline_dt = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=ban_after)
        deadline_ts = int(deadline_dt.timestamp())

        existing_member = db.get_member(chat_id, user_id)
        was_completed = existing_member and existing_member.get("completed", False)

        try:
            user_specific_link = await bot.create_chat_invite_link(
                chat_id,
                member_limit=s["referral_count"],
            )
            raw_invite_link = user_specific_link.invite_link
        except Exception as e:
            logger.error(f"Failed to create invite link for {user_id} in {chat_id}: {e}")
            raw_invite_link = chat_doc.get("invite_link", "")

        db.members.update_one(
            {"chat_id": chat_id, "user_id": user_id},
            {
                "$set": {
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "invite_link": raw_invite_link,
                    "deadline_ts": deadline_ts,
                    "completed": was_completed,
                    "banned": False,
                    "join_msg_id": None,
                }
            },
            upsert=True,
        )

        msg_id = await _send_welcome(bot, chat_id, user, chat_doc)
        if msg_id:
            db.set_member(chat_id, user_id, {"join_msg_id": msg_id})
            _LAST_WELCOME[chat_id] = msg_id

        asyncio.create_task(schedule_ban(bot, chat_id, user_id, ban_after))


async def schedule_ban(bot: Client, chat_id: int, user_id: int, delay_seconds: int):
    await asyncio.sleep(delay_seconds)
    member = db.get_member(chat_id, user_id)
    if not member:
        return
    if member.get("completed") or member.get("banned"):
        return

    chat_doc = get_chat_doc(chat_id)
    if not chat_doc:
        return
    if not chat_doc["settings"].get("ban_enabled", True):
        return

    try:
        await bot.ban_chat_member(chat_id, user_id)
        await bot.unban_chat_member(chat_id, user_id)
        db.set_member(chat_id, user_id, {"banned": True})
        logger.info(f"Banned user {user_id} from {chat_id}")
    except Exception as e:
        logger.error(f"Ban failed {user_id} from {chat_id}: {e}")


def _build_about_footer(bot_username: str) -> str:
    return (
        f"\n\n─────────────────\n"
        f"**About @{bot_username}**\n"
        f"{config.BOT_ABOUT}"
    )


@Client.on_message(filters.command("start") & filters.private, group=0)
async def start_deep_link(bot: Client, message: Message):
    bot_username = await get_bot_username(bot)

    if len(message.command) < 2:
        await message.reply(
            f"**Welcome to @{bot_username}**\n\n"
            f"{config.BOT_ABOUT}\n\n"
            f"**How it works:**\n"
            f"{config.BOT_HOW_IT_WORKS}\n\n"
            f"**Commands:**\n"
            f"{config.BOT_COMMANDS_TEXT}\n\n"
            f"{config.BOT_START_HINT}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("About this bot", callback_data="about_bot")]
            ]),
        )
        return

    param = message.command[1]

    if param.startswith("join_"):
        parts = param.split("_", 1)
        if len(parts) != 2:
            return
        try:
            chat_id = int(_decode_id(parts[1]))
        except Exception:
            return
        user_id = message.from_user.id

        chat_doc = get_chat_doc(chat_id)
        if not chat_doc:
            await message.reply("This group is no longer connected to the bot.")
            return

        s = chat_doc["settings"]
        ref_token = _encode_id(f"{chat_id}_{user_id}")
        ref_link = f"https://t.me/{bot_username}?start=ref_{ref_token}"
        ref_done = db.count_referrals(user_id, chat_id)
        ref_needed = s["referral_count"]
        remaining = max(0, ref_needed - ref_done)
        completed = remaining == 0

        about = _build_about_footer(bot_username)

        if completed:
            await message.reply(
                f"Your status in **{chat_doc['title']}**:\n\n"
                f"Referrals: {ref_done}/{ref_needed}\n\n"
                f"**TASK COMPLETED — YOUR MEMBERSHIP IS CONFIRMED**"
                f"{about}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("About this bot", callback_data="about_bot")]
                ]),
            )
        else:
            encoded_ref = quote(ref_link)
            share_text = quote("Join this group via my referral link!")
            share_url = f"https://t.me/share/url?url={encoded_ref}&text={share_text}"
            await message.reply(
                f"Your status in **{chat_doc['title']}**:\n\n"
                f"Referrals: {ref_done}/{ref_needed} — {remaining} referral(s) remaining\n\n"
                f"Share your link to avoid being banned:"
                f"{about}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Share Referral Link", url=share_url)],
                    [InlineKeyboardButton("About this bot", callback_data="about_bot")],
                ]),
            )

    elif param.startswith("ref_"):
        parts = param.split("_", 1)
        if len(parts) != 2:
            return
        try:
            decoded = _decode_id(parts[1])
            chat_id_s, referrer_id_s = decoded.split("_")
            chat_id, referrer_id = int(chat_id_s), int(referrer_id_s)
        except Exception:
            return

        chat_doc = get_chat_doc(chat_id)
        if not chat_doc:
            await message.reply("This group is no longer connected.")
            return

        joiner_id = message.from_user.id
        if joiner_id == referrer_id:
            await message.reply("Can't refer yourself.")
            return

        existing = db.referrals.find_one({
            "referrer_id": referrer_id,
            "referred_id": joiner_id,
            "chat_id": chat_id,
        })
        if not existing:
            db.add_referral(referrer_id, joiner_id, chat_id)

            s = chat_doc["settings"]
            ref_needed = s["referral_count"]
            ref_done = db.count_referrals(referrer_id, chat_id)
            remaining = max(0, ref_needed - ref_done)

            try:
                if remaining == 0:
                    db.set_member(chat_id, referrer_id, {"completed": True})

                    referrer_member = db.get_member(chat_id, referrer_id)
                    old_link = referrer_member.get("invite_link") if referrer_member else None

                    if old_link:
                        try:
                            await bot.revoke_chat_invite_link(chat_id, old_link)
                        except Exception as e:
                            logger.error(f"Failed to revoke invite link for {referrer_id}: {e}")

                    try:
                        perm_invite = await bot.create_chat_invite_link(chat_id)
                        perm_link = perm_invite.invite_link
                        db.set_member(chat_id, referrer_id, {"invite_link": perm_link})
                    except Exception as e:
                        logger.error(f"Failed to create permanent invite for {referrer_id}: {e}")
                        perm_link = chat_doc.get("invite_link", "")

                    try:
                        await bot.send_message(
                            referrer_id,
                            f"**TASK COMPLETED**\n\n"
                            f"You have successfully referred enough members to **{chat_doc['title']}**.\n\n"
                            f"Your membership is now confirmed. Use the link below to join:",
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("Join Group", url=perm_link)]
                            ]),
                        )
                    except Exception as e:
                        logger.error(f"Failed to send completion message to {referrer_id}: {e}")

                    try:
                        referrer_user = await bot.get_users(referrer_id)
                        mention = f"[{referrer_user.first_name}](tg://user?id={referrer_id})"
                    except Exception:
                        mention = f"User {referrer_id}"

                    last_comp_id = _LAST_COMPLETED.get(chat_id)
                    if last_comp_id:
                        try:
                            await bot.delete_messages(chat_id, last_comp_id)
                        except Exception:
                            pass

                    try:
                        comp_msg = await bot.send_message(
                            chat_id,
                            f"**MEMBERSHIP CONFIRMED**\n\n"
                            f"{mention} has completed the referral task and is now a verified member.",
                        )
                        _LAST_COMPLETED[chat_id] = comp_msg.id
                    except Exception as e:
                        logger.error(f"Failed to send completion announcement in {chat_id}: {e}")

                else:
                    await bot.send_message(
                        referrer_id,
                        f"Someone joined via your link in **{chat_doc['title']}**!\n"
                        f"{remaining} more referral(s) needed.",
                    )
            except Exception as e:
                logger.error(f"Failed to notify referrer {referrer_id}: {e}")

        referrer_member = db.get_member(chat_id, referrer_id)
        joiner_invite = referrer_member.get("invite_link", chat_doc.get("invite_link", "")) if referrer_member else chat_doc.get("invite_link", "")

        about = _build_about_footer(bot_username)
        await message.reply(
            f"You were referred to **{chat_doc['title']}**!\n\n"
            f"Click below to join:"
            f"{about}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Join Group", url=joiner_invite)],
                [InlineKeyboardButton("About this bot", callback_data="about_bot")],
            ]),
        )

@Client.on_callback_query(filters.regex(r"^about_bot$"))
async def about_bot_cb(bot: Client, query: CallbackQuery):
    bot_username = await get_bot_username(bot)
    await query.answer()
    await query.edit_message_text(
        f"**About @{bot_username}**\n\n"
        f"{config.BOT_ABOUT}\n\n"
        f"**How it works:**\n"
        f"{config.BOT_HOW_IT_WORKS}\n\n"
        f"**Commands:**\n"
        f"{config.BOT_COMMANDS_TEXT}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Back", callback_data="about_back")]
        ]),
    )

@Client.on_callback_query(filters.regex(r"^about_back$"))
async def about_back_cb(bot: Client, query: CallbackQuery):
    bot_username = await get_bot_username(bot)
    await query.answer()
    await query.edit_message_text(
        f"**Welcome to @{bot_username}**\n\n"
        f"{config.BOT_ABOUT}\n\n"
        f"**How it works:**\n"
        f"{config.BOT_HOW_IT_WORKS}\n\n"
        f"**Commands:**\n"
        f"{config.BOT_COMMANDS_TEXT}\n\n"
        f"{config.BOT_START_HINT}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("About this bot", callback_data="about_bot")]
        ]),
    )
