# ==============================================================================
# stats.py - Sudo Stats
# ==============================================================================
# Deep dive into bot and system statistics.
# ==============================================================================

# Copyright (c) 2025 Hasindu Nagolla
# Licensed under the MIT License
# This file is part of ˹ʜᴀꜱɪɪ ᴍᴜꜱɪᴄ˼

import os
import platform
import sys

import psutil
from pyrogram import __version__, filters, types
from pytgcalls import __version__ as pytgver

from HasiiMusic import app, config, db, lang, userbot
from HasiiMusic.plugins import all_modules


@app.on_message(filters.command(["stats"]) & ~app.bl_users)
@lang.language()
async def _stats(_, m: types.Message):
    # ==========================================================================
    # Auto-delete command message
    # ==========================================================================

    try:
        await m.delete()
    except Exception:
        pass

    # ==========================================================================
    # Send loading message
    # ==========================================================================

    sent = await m.reply_photo(
        photo=config.PING_IMG,
        caption=m.lang["stats_fetching"],
    )

    # ==========================================================================
    # Check sudo
    # ==========================================================================

    is_sudo = (
        m.from_user
        and m.from_user.id in app.sudoers
    )

    # ==========================================================================
    # Basic bot statistics
    # ==========================================================================

    _utext = m.lang["stats_user"].format(
        app.name,
        len(userbot.clients),
        config.AUTO_LEAVE,
        len(db.blacklisted),
        len(app.bl_users),
        len(app.sudoers),
        len(await db.get_chats()),
        len(await db.get_users()),
    )

    # ==========================================================================
    # System statistics - SUDO ONLY
    # ==========================================================================

    if is_sudo:

        # ----------------------------------------------------------------------
        # Current Bot Process
        # ----------------------------------------------------------------------
        # IMPORTANT:
        #
        # We intentionally measure the Python process itself instead of using
        # psutil.virtual_memory().used.
        #
        # This is much more useful for monitoring the actual Bot process.
        # ----------------------------------------------------------------------

        process = psutil.Process(os.getpid())

        # Current Python process RSS memory
        process_mem = process.memory_info().rss

        # Convert bytes -> GB
        used_mem = round(
            process_mem / (1024 ** 3),
            2,
        )

        # ----------------------------------------------------------------------
        # Total memory visible to the current environment
        # ----------------------------------------------------------------------

        virtual_memory = psutil.virtual_memory()

        total_mem = round(
            virtual_memory.total / (1024 ** 3),
            2,
        )

        # ----------------------------------------------------------------------
        # Process CPU usage
        # ----------------------------------------------------------------------

        process_cpu = process.cpu_percent(
            interval=0.5
        )

        cpu_count = (
            psutil.cpu_count()
            or 1
        )

        # ----------------------------------------------------------------------
        # Disk usage
        # ----------------------------------------------------------------------

        disk = psutil.disk_usage("/")

        used_disk = round(
            disk.used / (1024 ** 3),
            2,
        )

        total_disk = round(
            disk.total / (1024 ** 3),
            2,
        )

        # ----------------------------------------------------------------------
        # Add system statistics to message
        # ----------------------------------------------------------------------

        _utext += m.lang["stats_sudo"].format(
            len(all_modules),
            platform.system(),

            # RAM
            f"{used_mem}GB | {total_mem}GB",

            # CPU
            f"{process_cpu}% ({cpu_count} cores)",

            # Disk
            f"{used_disk}GB | {total_disk}GB",

            # Python
            sys.version.split()[0],

            # Pyrogram
            __version__,

            # PyTgCalls
            pytgver,
        )

    # ==========================================================================
    # Update message
    # ==========================================================================

    await sent.edit_caption(_utext)