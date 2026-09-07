# ==============================================================================
# stats.py - Sudo Stats
# ==============================================================================
# Deep dive into bot and system statistics.
# ==============================================================================

# Copyright (c) 2025 Hasindu Nagolla
# Licensed under the MIT License.
# This file is part of ˹ʜᴀꜱɪɪ ᴍᴜꜱɪᴄ˼

import gc
import os
import platform
import sys
from collections import Counter

import psutil
from pyrogram import __version__, filters, types
from pytgcalls import __version__ as pytgver

from HasiiMusic import app, config, db, lang, logger, userbot
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

    # ------------------------------------------------------------------
    # Python process RSS
    # ------------------------------------------------------------------
    process = psutil.Process(os.getpid())

    try:
        process_rss = process.memory_info().rss
    except Exception:
        process_rss = 0

    process_gb = process_rss / (1024 ** 3)

    # ------------------------------------------------------------------
    # Render / container memory
    # ------------------------------------------------------------------
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


def _read_proc_status():
    """Read a small set of Linux process memory/thread counters."""
    data = {}
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for line in f:
                key, sep, value = line.partition(":")
                if not sep:
                    continue
                data[key.strip()] = value.strip()
    except OSError:
        pass
    return data


def _read_smaps_rollup():
    """Read aggregate memory categories for the current process on Linux."""
    result = {}
    try:
        with open("/proc/self/smaps_rollup", "r", encoding="utf-8") as f:
            for line in f:
                key, sep, value = line.partition(":")
                if not sep:
                    continue
                parts = value.strip().split()
                if not parts:
                    continue
                try:
                    # smaps_rollup reports kB for these fields.
                    result[key.strip()] = int(parts[0]) * 1024
                except ValueError:
                    continue
    except OSError:
        pass
    return result


def _read_smaps_mappings():
    """Aggregate /proc/self/smaps by memory mapping name on Linux."""
    mappings = {}
    current_name = None
    current = None

    try:
        with open("/proc/self/smaps", "r", encoding="utf-8") as f:
            for line in f:
                # Mapping header: address range, permissions, offset, device,
                # inode, optional pathname.  Anonymous mappings have no path.
                parts = line.rstrip("\n").split(maxsplit=5)
                if len(parts) >= 5 and "-" in parts[0] and len(parts[1]) >= 4:
                    if current_name is not None and current is not None:
                        bucket = mappings.setdefault(current_name, {"rss": 0, "pss": 0, "private": 0, "anonymous": 0})
                        for key in bucket:
                            bucket[key] += current.get(key, 0)

                    pathname = parts[5].strip() if len(parts) == 6 else ""
                    if pathname:
                        current_name = pathname
                    elif parts[4] == "0":
                        current_name = "[anonymous]"
                    else:
                        current_name = "[mapped]"
                    current = {"rss": 0, "pss": 0, "private": 0, "anonymous": 0}
                    continue

                if current is None:
                    continue

                key, sep, value = line.partition(":")
                if not sep:
                    continue
                value_parts = value.strip().split()
                if not value_parts:
                    continue
                try:
                    amount = int(value_parts[0]) * 1024
                except ValueError:
                    continue

                if key == "Rss":
                    current["rss"] = amount
                elif key == "Pss":
                    current["pss"] = amount
                elif key == "Private_Clean":
                    current["private"] += amount
                elif key == "Private_Dirty":
                    current["private"] += amount
                elif key == "Anonymous":
                    current["anonymous"] = amount

        if current_name is not None and current is not None:
            bucket = mappings.setdefault(current_name, {"rss": 0, "pss": 0, "private": 0, "anonymous": 0})
            for key in bucket:
                bucket[key] += current.get(key, 0)
    except OSError:
        return {}

    return mappings


def _native_memory_diagnostic():
    """Log native/heap mapping information without changing bot behavior."""
    mappings = _read_smaps_mappings()
    if not mappings:
        logger.info("Native memory map: unavailable")
        return

    heap = mappings.get("[heap]", {})
    anonymous = mappings.get("[anonymous]", {})

    logger.info(
        "Native memory map: heap RSS=%s | anonymous RSS=%s | mappings=%d",
        _format_mb(heap.get("rss", 0)),
        _format_mb(anonymous.get("rss", 0)),
        len(mappings),
    )

    # Show the largest file-backed mappings first. This is useful for spotting
    # native media libraries without flooding Render logs with every mapping.
    file_backed = [
        (name, data)
        for name, data in mappings.items()
        if not name.startswith("[") and data.get("rss", 0) > 0
    ]
    file_backed.sort(key=lambda item: item[1].get("rss", 0), reverse=True)

    for name, data in file_backed[:12]:
        logger.info(
            "MAP %s | RSS=%s | PSS=%s | Private=%s",
            name[:180],
            _format_mb(data.get("rss", 0)),
            _format_mb(data.get("pss", 0)),
            _format_mb(data.get("private", 0)),
        )

    # Explicitly highlight libraries that are relevant to the music/media
    # stack. The match is case-insensitive and only affects diagnostics.
    keywords = (
        "ntgcalls", "pytgcalls", "libav", "ffmpeg", "opus", "srtp",
        "webrtc", "nice", "ssl", "crypto", "sodium",
    )
    found = set()
    for name, data in file_backed:
        lowered = name.lower()
        if any(keyword in lowered for keyword in keywords):
            found.add(name)
            logger.info(
                "NATIVE CANDIDATE %s | RSS=%s | PSS=%s | Private=%s",
                name[:180],
                _format_mb(data.get("rss", 0)),
                _format_mb(data.get("pss", 0)),
                _format_mb(data.get("private", 0)),
            )

    if not found:
        logger.info("NATIVE CANDIDATE: no matching media/native library mapping found")


def _format_mb(value):
    return f"{value / (1024 ** 2):.1f}MB"


def _memory_diagnostic_snapshot():
    """
    Collect diagnostic information only. This intentionally does not change
    the /stats UI or playback behavior. The result is written to Render logs.
    """
    process = psutil.Process(os.getpid())

    try:
        rss = process.memory_info().rss
    except Exception:
        rss = 0

    status = _read_proc_status()
    smaps = _read_smaps_rollup()

    # Count Python objects without forcing a collection. This is diagnostic
    # only; avoiding gc.collect() here prevents the diagnostic itself from
    # changing the memory profile we are trying to measure.
    try:
        object_count = len(gc.get_objects())
        top_types = Counter(type(obj).__name__ for obj in gc.get_objects()).most_common(8)
        top_types_text = ", ".join(f"{name}={count}" for name, count in top_types)
    except Exception:
        object_count = -1
        top_types_text = "unavailable"

    # Child processes are especially useful for detecting FFmpeg/ntgcalls
    # helpers that remain alive after playback has stopped.
    children = []
    try:
        for child in process.children(recursive=True):
            try:
                child_rss = child.memory_info().rss
                cmd = " ".join(child.cmdline())[:180]
                children.append(
                    f"pid={child.pid} name={child.name()} rss={_format_mb(child_rss)} cmd={cmd}"
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass

    threads = status.get("Threads", "?")
    vm_data = status.get("VmData", "?")
    vm_swap = status.get("VmSwap", "?")

    anonymous = smaps.get("Anonymous", 0)
    anon_huge = smaps.get("AnonHugePages", 0)
    file_pages = smaps.get("Pss_File", 0)
    shared = smaps.get("Shared_Clean", 0) + smaps.get("Shared_Dirty", 0)
    private = smaps.get("Private_Clean", 0) + smaps.get("Private_Dirty", 0)
    swap = smaps.get("Swap", 0)
    pss = smaps.get("Pss", 0)

    logger.info("=" * 72)
    logger.info("🧠 MEMORY DIAGNOSTIC SNAPSHOT (UI unchanged)")
    logger.info("Process RSS: %s | PSS: %s | Anonymous: %s | File-backed PSS: %s",
                _format_mb(rss), _format_mb(pss), _format_mb(anonymous), _format_mb(file_pages))
    logger.info("Private pages: %s | Shared pages: %s | Swap: %s | AnonHugePages: %s",
                _format_mb(private), _format_mb(shared), _format_mb(swap), _format_mb(anon_huge))
    logger.info("VmData: %s | VmSwap: %s | Threads: %s | Python objects: %s",
                vm_data, vm_swap, threads, object_count)
    logger.info("Top Python object types: %s", top_types_text)

    # Detailed Linux memory mappings are diagnostic-only and do not alter
    # the existing /stats UI or playback behavior.
    try:
        _native_memory_diagnostic()
    except Exception as e:
        logger.info("Native memory diagnostic unavailable: %s", e)

    if children:
        logger.info("Child processes: %d", len(children))
        for child in children:
            logger.info("CHILD %s", child)
    else:
        logger.info("Child processes: 0")

    # Active bot-side state counts help distinguish idle application state
    # from native/library memory retained outside Python containers.
    try:
        from HasiiMusic import preload, queue, tune
        preload_tasks = sum(len(v) for v in getattr(preload, "_preload_tasks", {}).values())
        preloading = sum(len(v) for v in getattr(preload, "_preloading", {}).values())
        queue_chats = len(getattr(queue, "queues", {}))
        call_states = len(getattr(tune, "_chat_locks", {}))
        track_states = len(getattr(tune, "_track_index", {}))
        pending = len(getattr(tune, "_pending_transitions", set()))
        logger.info(
            "Bot state: queues=%d preload_tasks=%d preloading=%d call_locks=%d track_index=%d pending=%d",
            queue_chats, preload_tasks, preloading, call_states, track_states, pending,
        )
    except Exception as e:
        logger.info("Bot state diagnostics unavailable: %s", e)

    logger.info("=" * 72)


@app.on_message(filters.command(["stats"]) & ~app.bl_users)
@lang.language()
async def _stats(_, m: types.Message):

    # ------------------------------------------------------------------
    # Delete command message
    # ------------------------------------------------------------------
    try:
        await m.delete()
    except Exception:
        pass

    # ------------------------------------------------------------------
    # Temporary stats message
    # ------------------------------------------------------------------
    sent = await m.reply_photo(
        photo=config.PING_IMG,
        caption=m.lang["stats_fetching"],
    )

    is_sudo = m.from_user.id in app.sudoers

    # ------------------------------------------------------------------
    # Basic statistics
    # ------------------------------------------------------------------
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

        # ------------------------------------------------------------------
        # Main RAM line
        # ------------------------------------------------------------------
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
        # MEMORY DIAGNOSTICS
        # ==============================================================

        # Keep the existing /stats UI exactly as-is. Detailed diagnostics
        # are emitted to Render logs so we can locate the 1.4GB resident
        # memory without changing user-facing text or playback logic.
        try:
            _memory_diagnostic_snapshot()
        except Exception as e:
            logger.warning("Memory diagnostic failed: %s", e)

        if container_mem is not None and memory_limit is not None:

            # ----------------------------------------------------------
            # Memory status
            # ----------------------------------------------------------

            if memory_percent >= 85:

                memory_status = (
                    "⚠️ ʜɪɢʜ ᴍᴇᴍᴏʀʏ ᴜꜱᴀɢᴇ"
                )

            elif memory_percent >= 70:

                memory_status = (
                    "⚠️ ᴍᴇᴍᴏʀʏ ᴜꜱᴀɢᴇ ɪꜱ ʀɪꜱɪɴɢ"
                )

            else:

                memory_status = (
                    "✅ ᴍᴇᴍᴏʀʏ ʟᴇᴠᴇʟ ɴᴏʀᴍᴀʟ"
                )

            # ----------------------------------------------------------
            # Diagnostic quote block
            # ----------------------------------------------------------

            _utext += (
                "\n\n"
                "<blockquote>"
                "ᴍᴇᴍᴏʀʏ ᴅɪᴀɢɴᴏꜱᴛɪᴄ"
                "\n\n"
                f"ᴘʀᴏᴄᴇꜱꜱ ʀᴀᴍ: {process_mem}GB"
                "\n"
                f"ᴄᴏɴᴛᴀɪɴᴇʀ ʀᴀᴍ: {container_mem}GB"
                "\n"
                f"ʀᴀᴍ ʟɪᴍɪᴛ: {memory_limit}GB"
                "\n"
                f"ʀᴀᴍ ᴜꜱᴀɢᴇ: {memory_percent}%"
                "\n\n"
                f"{memory_status}"
                "</blockquote>"
            )

    # ------------------------------------------------------------------
    # Update final stats message
    # ------------------------------------------------------------------
    await sent.edit_caption(_utext)