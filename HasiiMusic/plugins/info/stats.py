# ==============================================================================
# stats.py - Sudo Stats
# ==============================================================================
# Deep dive into bot and system statistics.
# ==============================================================================

# Copyright (c) 2025 Hasindu Nagolla
# Licensed under the MIT License.
# This file is part of ˹ʜᴀꜱɪɪ ᴍᴜꜱɪᴄ˼

import ctypes
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


def _format_mb(value):
    return f"{value / (1024 ** 2):.1f}MB"


def _get_malloc_stats():
    """Return glibc allocator totals when available."""
    try:
        libc = ctypes.CDLL(None)
        if not hasattr(libc, "mallinfo2"):
            return None

        class Mallinfo2(ctypes.Structure):
            _fields_ = [
                ("arena", ctypes.c_size_t),
                ("ordblks", ctypes.c_size_t),
                ("smblks", ctypes.c_size_t),
                ("hblks", ctypes.c_size_t),
                ("hblkhd", ctypes.c_size_t),
                ("usmblks", ctypes.c_size_t),
                ("fsmblks", ctypes.c_size_t),
                ("uordblks", ctypes.c_size_t),
                ("fordblks", ctypes.c_size_t),
                ("keepcost", ctypes.c_size_t),
            ]

        libc.mallinfo2.restype = Mallinfo2
        return libc.mallinfo2()
    except Exception:
        return None


def _object_retention_diagnostics(objects):
    """Return lightweight object-retention summaries for leak localization."""
    module_counts = Counter()
    module_sizes = Counter()
    large_objects = []
    task_objects = []
    gc_garbage_count = len(getattr(gc, "garbage", ()))

    interesting_modules = (
        "HasiiMusic", "pyrogram", "pytgcalls", "ntgcalls", "yt_dlp",
        "aiohttp", "httpx", "httpcore", "asyncio", "pymongo", "motor",
    )

    for obj in objects:
        try:
            typ = type(obj)
            mod = getattr(typ, "__module__", "unknown")
            size = sys.getsizeof(obj)
        except Exception:
            continue

        # Group project/dependency objects by their top-level module.
        top = mod.split(".", 1)[0]
        if top in interesting_modules or mod.startswith("HasiiMusic."):
            module_counts[mod] += 1
            module_sizes[mod] += size

        if size >= 256 * 1024:
            large_objects.append((size, mod, typ.__name__))

        if typ.__name__ in {"Task", "Future"} and mod == "_asyncio":
            task_objects.append(obj)

    top_modules = module_sizes.most_common(15)
    top_large = sorted(large_objects, reverse=True)[:20]

    return top_modules, top_large, len(task_objects), gc_garbage_count


def _asyncio_task_diagnostics():
    """Summarize live asyncio tasks without retaining references."""
    try:
        import asyncio
        tasks = asyncio.all_tasks()
        states = Counter()
        names = Counter()
        for task in tasks:
            try:
                states["done" if task.done() else "pending"] += 1
                name = task.get_name() if hasattr(task, "get_name") else type(task.get_coro()).__name__
                names[str(name)[:80]] += 1
            except Exception:
                continue
        top_names = ", ".join(f"{k}={v}" for k, v in names.most_common(15))
        return len(tasks), dict(states), top_names
    except Exception:
        return -1, {}, "unavailable"


async def _memory_diagnostic_snapshot():
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
    # Keep every diagnostic field initialized so one failing sub-check never
    # aborts the rest of the snapshot.
    object_count = -1
    top_types_text = "unavailable"
    top_modules = []
    top_large = []
    task_object_count = -1
    gc_garbage_count = -1
    task_total = -1
    task_states = {}
    task_names = "unavailable"

    try:
        objects = gc.get_objects()
        object_count = len(objects)
        try:
            top_types = Counter(type(obj).__name__ for obj in objects).most_common(8)
            top_types_text = ", ".join(f"{name}={count}" for name, count in top_types)
        except Exception as exc:
            logger.warning("Memory diagnostic object-type scan failed: %s", exc)

        try:
            (top_modules, top_large, task_object_count, gc_garbage_count) = _object_retention_diagnostics(objects)
        except Exception as exc:
            logger.warning("Memory diagnostic retention scan failed: %s", exc)

        del objects
    except Exception as exc:
        logger.warning("Memory diagnostic object scan failed: %s", exc)

    try:
        task_total, task_states, task_names = _asyncio_task_diagnostics()
    except Exception as exc:
        logger.warning("Memory diagnostic asyncio scan failed: %s", exc)

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
    if top_modules:
        module_text = ", ".join(f"{mod}={_format_mb(size)}" for mod, size in top_modules)
        logger.info("Top tracked module shallow sizes: %s", module_text)
    if top_large:
        large_text = ", ".join(
            f"{_format_mb(size)}:{mod}.{name}" for size, mod, name in top_large
        )
        logger.info("Large Python objects (shallow): %s", large_text)
    logger.info(
        "Asyncio tasks: total=%s | states=%s | live task objects=%s | gc.garbage=%s",
        task_total, task_states, task_object_count, gc_garbage_count,
    )
    logger.info("Asyncio task names: %s", task_names)

    malloc_info = _get_malloc_stats()
    if malloc_info is not None:
        logger.info(
            "glibc malloc: arena=%s | in_use=%s | free=%s | mmap=%s | keepcost=%s",
            _format_mb(malloc_info.arena),
            _format_mb(malloc_info.uordblks),
            _format_mb(malloc_info.fordblks),
            _format_mb(malloc_info.hblkhd),
            _format_mb(malloc_info.keepcost),
        )

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
        preload_tasks_map = getattr(preload, "_preload_tasks", {})
        preloading_map = getattr(preload, "_preloading", {})
        queues_map = getattr(queue, "queues", {})
        locks_map = getattr(tune, "_chat_locks", {})
        track_map = getattr(tune, "_track_index", {})
        session_map = getattr(tune, "_session_gen", {})
        pending_map = getattr(tune, "_pending_transitions", set())

        preload_tasks = sum(len(v) for v in preload_tasks_map.values())
        preloading = sum(len(v) for v in preloading_map.values())
        queue_chats = len(queues_map)
        call_states = len(locks_map)
        track_states = len(track_map)
        pending = len(pending_map)
        logger.info(
            "Bot state: queues=%d preload_tasks=%d preloading=%d call_locks=%d track_index=%d pending=%d",
            queue_chats, preload_tasks, preloading, call_states, track_states, pending,
        )

        if queues_map:
            for chat_id, q in list(queues_map.items())[:20]:
                current = q[0] if q else None
                logger.info(
                    "QUEUE STATE chat=%s len=%d current_id=%s file=%s",
                    chat_id,
                    len(q),
                    getattr(current, "id", None) if current else None,
                    getattr(current, "file_path", None) if current else None,
                )

        if locks_map:
            for chat_id, lock in list(locks_map.items())[:20]:
                logger.info(
                    "CALL STATE chat=%s lock_locked=%s track_index=%s session_gen=%s pending=%s",
                    chat_id,
                    bool(lock.locked()),
                    track_map.get(chat_id),
                    session_map.get(chat_id),
                    chat_id in pending_map,
                )

        # Compare persisted DB playback records with in-memory state.
        try:
            db_chats = await db.get_chats()
            active_db = []
            for chat_id in db_chats[:50]:
                try:
                    if await db.get_call(chat_id):
                        active_db.append(chat_id)
                except Exception:
                    continue
            logger.info(
                "DB playback state: chats_checked=%d active_calls=%s",
                len(db_chats[:50]),
                active_db[:20],
            )
        except Exception as e:
            logger.info("DB playback diagnostics unavailable: %s", e)
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
            await _memory_diagnostic_snapshot()
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