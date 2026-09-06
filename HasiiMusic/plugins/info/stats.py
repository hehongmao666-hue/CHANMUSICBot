# ==============================================================================
# stats.py - Sudo Stats
# ==============================================================================
# Deep dive into bot and system statistics.
# ==============================================================================

# Copyright (c) 2025 Hasindu Nagolla
# Licensed under the MIT License.
# This file is part of ˹ʜᴀꜱɪɪ ᴍᴜꜱɪᴄ˼

import os
import platform
import sys

import psutil
from pyrogram import __version__, filters, types
from pytgcalls import __version__ as pytgver

from HasiiMusic import app, config, db, lang, userbot
from HasiiMusic.plugins import all_modules


def _read_cgroup_value(filename: str):
    """
    Read a Linux cgroup v2 memory value.

    Render/Linux containers normally expose memory information
    through /sys/fs/cgroup.
    """
    try:
        path = f"/sys/fs/cgroup/{filename}"

        with open(path, "r", encoding="utf-8") as f:
            value = f.read().strip()

        if value == "max":
            return None

        return int(value)

    except (OSError, ValueError):
        return None


def _get_container_memory():
    """
    Return container memory usage and limit in GB.

    Uses cgroup v2 first, then cgroup v1 as fallback.
    """
    # ------------------------------------------------------------------
    # cgroup v2
    # ------------------------------------------------------------------
    current = _read_cgroup_value("memory.current")
    limit = _read_cgroup_value("memory.max")

    if current is not None and limit is not None:
        return (
            current / (1024 ** 3),
            limit / (1024 ** 3),
        )

    # ------------------------------------------------------------------
    # cgroup v1 fallback
    # ------------------------------------------------------------------
    try:
        with open(
            "/sys/fs/cgroup/memory/memory.usage_in_bytes",
            "r",
            encoding="utf-8",
        ) as f:
            current = int(f.read().strip())

        with open(
            "/sys/fs/cgroup/memory/memory.limit_in_bytes",
            "r",
            encoding="utf-8",
        ) as f:
            limit = int(f.read().strip())

        # Ignore unrealistic unlimited values.
        if limit > 0 and limit < 1024 ** 5:
            return (
                current / (1024 ** 3),
                limit / (1024 ** 3),
            )

    except (OSError, ValueError):
        pass

    return None, None


def _get_memory_stats():
    """
    Collect process RAM + container RAM.

    Returns:
        process_gb
        container_gb
        limit_gb
        percentage
    """
    # Python process RSS
    process = psutil.Process(os.getpid())

    try:
        process_rss = process.memory_info().rss
    except Exception:
        process_rss = 0

    process_gb = process_rss / (1024 ** 3)

    # Render/container memory
    container_gb, limit_gb = _get_container_memory()

    if container_gb is not None and limit_gb:
        percentage = (container_gb / limit_gb) * 100
    else:
        percentage = 0

    return (
        round(process_gb, 2),
        round(container_gb, 2) if container_gb is not None else None,
        round(limit_gb, 2) if limit_gb is not None else None,
        round(percentage, 1),
    )


@app.on_message(filters.command(["stats"]) & ~app.bl_users)
@lang.language()
async def _stats(_, m: types.Message):

    try:
        await m.delete()
    except Exception:
        pass

    sent = await m.reply_photo(
        photo=config.PING_IMG,
        caption=m.lang["stats_fetching"],
    )

    is_sudo = m.from_user.id in app.sudoers

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

    if is_sudo:

        # ==============================================================
        # MEMORY
        # ==============================================================

        (
            process_mem,
            container_mem,
            memory_limit,
            memory_percent,
        ) = _get_memory_stats()

        if container_mem is not None and memory_limit is not None:
            memory_line = (
                f"{container_mem}GB | {memory_limit}GB "
                f"({memory_percent}%)"
            )
        else:
            virtual_memory = psutil.virtual_memory()

            total_mem = round(
                virtual_memory.total / (1024 ** 3),
                2,
            )

            memory_line = (
                f"{process_mem}GB | {total_mem}GB"
            )

        # ==============================================================
        # CPU
        # ==============================================================

        process = psutil.Process(os.getpid())

        try:
            process_cpu = process.cpu_percent(interval=0.5)
        except Exception:
            process_cpu = 0

        cpu_count = psutil.cpu_count() or 1

        # ==============================================================
        # DISK
        # ==============================================================

        try:
            disk = psutil.disk_usage("/")

            used_disk = round(
                disk.used / (1024 ** 3),
                2,
            )

            total_disk = round(
                disk.total / (1024 ** 3),
                2,
            )

        except Exception:
            used_disk = 0
            total_disk = 0

        # ==============================================================
        # ORIGINAL STATS
        # ==============================================================

        _utext += m.lang["stats_sudo"].format(
            len(all_modules),
            platform.system(),
            memory_line,
            f"{process_cpu}% ({cpu_count} cores)",
            f"{used_disk}GB | {total_disk}GB",
            sys.version.split()[0],
            __version__,
            pytgver,
        )

        # ==============================================================
        # EXTRA MEMORY DIAGNOSTICS
        # ==============================================================

        if container_mem is not None:

            _utext += (
                "\n\n"
                "**ᴍᴇᴍᴏʀʏ ᴅɪᴀɢɴᴏꜱᴛɪᴄ:**"
                "\n"
                f"**ᴘʀᴏᴄᴇꜱꜱ ʀᴀᴍ:** {process_mem}GB"
                "\n"
                f"**ᴄᴏɴᴛᴀɪɴᴇʀ ʀᴀᴍ:** {container_mem}GB"
                "\n"
                f"**ʀᴀᴍ ʟɪᴍɪᴛ:** {memory_limit}GB"
                "\n"
                f"**ʀᴀᴍ ᴜsᴀɢᴇ:** {memory_percent}%"
            )

            # ----------------------------------------------------------
            # Simple warning levels
            # ----------------------------------------------------------

            if memory_percent >= 85:

                _utext += (
                    "\n⚠️ **ʜɪɢʜ ᴍᴇᴍᴏʀʏ ᴜsᴀɢᴇ**"
                )

            elif memory_percent >= 70:

                _utext += (
                    "\n⚠️ **ᴍᴇᴍᴏʀʏ ᴜsᴀɢᴇ ɪs ʀɪsɪɴɢ**"
                )

            else:

                _utext += (
                    "\n✅ **ᴍᴇᴍᴏʀʏ ʟᴇᴠᴇʟ ɴᴏʀᴍᴀʟ**"
                )

    await sent.edit_caption(_utext)