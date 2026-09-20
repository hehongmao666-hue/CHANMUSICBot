# ==============================================================================
# ping.py - System Ping
# ==============================================================================
# Check bot latency and server metrics like CPU/RAM usage.
# ==============================================================================

import time

import psutil
from pyrogram import filters, types

from HasiiMusic import app, tune, boot, config, lang
from HasiiMusic.helpers import buttons


@app.on_message(filters.command(["alive", "ping"]) & ~app.bl_users)
@lang.language()
async def ping(_, m: types.Message):
    # ==========================================================================
    # Auto-delete command message
    # ==========================================================================

    try:
        await m.delete()
    except Exception:
        pass

    # ==========================================================================
    # Initial response
    # ==========================================================================

    start = time.time()

    sent = await m.reply_text(
        m.lang["pinging"]
    )

    # ==========================================================================
    # Uptime
    # ==========================================================================

    def get_time(seconds):
        values = [
            f"{seconds % 60}s",
            f"{(seconds // 60) % 60}m",
            f"{(seconds // 3600) % 24}h",
            f"{seconds // 86400}days",
        ]

        result = list(reversed(values))

        if result[-1][:-1] == "0":
            return ":".join(result[:-1])

        return f"{result[-1]}, " + ":".join(result[:-1])

    uptime = get_time(
        int(time.time() - boot)
    )

    # ==========================================================================
    # Latency
    # ==========================================================================

    latency = round(
        (time.time() - start) * 1000,
        2,
    )

    # ==========================================================================
    # System Stats
    # ==========================================================================

    mem = psutil.virtual_memory()

    ram_usage = (
        f"{round(mem.used / (1024 ** 3), 1)}GB / "
        f"{round(mem.total / (1024 ** 3), 1)}GB"
    )

    cpu_percent = psutil.cpu_percent(
        interval=0.5
    )

    # ==========================================================================
    # Active Chats
    # ==========================================================================

    from HasiiMusic import db

    active_chats = len(
        await db.get_chats()
    )

    # ==========================================================================
    # Bot Name
    # ==========================================================================

    try:
        bot_name = (
            getattr(app.me, "first_name", None)
            or getattr(app.me, "username", None)
            or "Music Bot"
        )
    except Exception:
        bot_name = "Music Bot"

    # ==========================================================================
    # PyTgCalls Ping
    # ==========================================================================

    pytgcalls_ping = await tune.ping()

    # ==========================================================================
    # Build Ping Message
    # ==========================================================================

    caption_text = m.lang["ping_pong"].format(
        bot_name=bot_name,
        latency=latency,
        uptime=uptime,
        pytgcalls_ping=pytgcalls_ping,
        ram_usage=ram_usage,
        cpu_percent=cpu_percent,
        active_chats=active_chats,
    )

    # ==========================================================================
    # Send / Edit Ping Message
    # ==========================================================================

    try:
        await sent.edit_media(
            media=types.InputMediaPhoto(
                media=config.PING_IMG,
                caption=caption_text,
            ),
            reply_markup=buttons.ping_markup(
                m.lang["support"]
            ),
        )

    except Exception:
        # Fallback to text if media fails
        await sent.edit_text(
            text=caption_text,
            reply_markup=buttons.ping_markup(
                m.lang["support"]
            ),
        )