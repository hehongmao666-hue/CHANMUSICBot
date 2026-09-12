"""Low-overhead native/OS memory diagnostics for voice-call lifecycle events."""

import ctypes
import inspect
import os

import psutil

from HasiiMusic import logger


def _mb(value: int) -> float:
    return round(value / (1024 ** 2), 1)


def _malloc_info():
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


def _proc_snapshot():
    process = psutil.Process(os.getpid())
    try:
        rss = process.memory_info().rss
    except Exception:
        rss = 0

    child_rss = 0
    children = 0
    try:
        for child in process.children(recursive=True):
            try:
                child_rss += child.memory_info().rss
                children += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception:
        pass

    threads = "?"
    vm_data = "?"
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for line in f:
                key, sep, value = line.partition(":")
                if sep and key.strip() in {"Threads", "VmData"}:
                    if key.strip() == "Threads":
                        threads = value.strip()
                    else:
                        vm_data = value.strip()
    except OSError:
        pass

    smaps = {}
    try:
        with open("/proc/self/smaps_rollup", "r", encoding="utf-8") as f:
            for line in f:
                key, sep, value = line.partition(":")
                if not sep:
                    continue
                parts = value.strip().split()
                if parts:
                    try:
                        smaps[key.strip()] = int(parts[0]) * 1024
                    except ValueError:
                        pass
    except OSError:
        pass

    return {
        "rss": rss,
        "child_rss": child_rss,
        "children": children,
        "threads": threads,
        "vm_data": vm_data,
        "anonymous": smaps.get("Anonymous", 0),
        "private": smaps.get("Private_Clean", 0) + smaps.get("Private_Dirty", 0),
        "pss": smaps.get("Pss", 0),
    }


async def log_call_lifecycle_snapshot(label: str, client=None, chat_id=None) -> None:
    """Log a compact before/after voice-call memory snapshot.

    This function only observes state. It never joins, leaves, stops, or
    mutates a PyTgCalls client.
    """
    snap = _proc_snapshot()
    malloc = _malloc_info()

    native_ids = None
    if client is not None:
        binding = getattr(client, "_binding", None)
        accessor = getattr(binding, "calls", None) if binding is not None else None
        if callable(accessor):
            try:
                result = accessor()
                if inspect.isawaitable(result):
                    result = await result
                if isinstance(result, dict):
                    native_ids = sorted(result.keys())
                elif isinstance(result, (list, tuple, set, frozenset)):
                    native_ids = sorted(result)
                elif isinstance(result, int):
                    native_ids = f"count={result}"
            except Exception as exc:
                native_ids = f"error={type(exc).__name__}"

    if malloc is not None:
        malloc_text = (
            f"arena={_mb(malloc.arena)}MB "
            f"in_use={_mb(malloc.uordblks)}MB "
            f"free={_mb(malloc.fordblks)}MB "
            f"mmap={_mb(malloc.hblkhd)}MB"
        )
    else:
        malloc_text = "unavailable"

    logger.info(
        "VOICE LIFECYCLE %s chat=%s | RSS=%sMB PSS=%sMB Anonymous=%sMB "
        "Private=%sMB ChildRSS=%sMB children=%d Threads=%s VmData=%s | "
        "malloc[%s] | native_calls=%s",
        label,
        chat_id,
        _mb(snap["rss"]),
        _mb(snap["pss"]),
        _mb(snap["anonymous"]),
        _mb(snap["private"]),
        _mb(snap["child_rss"]),
        snap["children"],
        snap["threads"],
        snap["vm_data"],
        malloc_text,
        native_ids if native_ids is not None else "unavailable",
    )
