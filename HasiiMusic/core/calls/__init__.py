"""
# ==============================================================================
# __init__.py - Calls Facade
# ==============================================================================
# This file serves as the main entry point for PyTgCalls integration.
# Features:
# - Exposes the public API for the TgCall class
# - Initializes and delegates to modular sub-components
# ==============================================================================
"""

import asyncio
import ctypes
import gc
import logging
import sys
from pytgcalls import PyTgCalls
from pyrogram.types import Message
from HasiiMusic import db, logger, queue
from HasiiMusic.helpers import Media, Track

from .utils import CallsUtils, PyTgCallsErrorFilter
from .manager import CallsManager
from .player import CallPlayer
from .controls import CallControls
from .queue import CallQueue

logging.getLogger('pyrogram.dispatcher').addFilter(PyTgCallsErrorFilter())

class TgCall(PyTgCalls):
    def __init__(self):

        
        # Shared state
        self.clients = []
        self._chat_locks = {}
        self._session_gen = {}
        self._track_index = {}
        self._pending_transitions = set()

        # Components
        self._utils = CallsUtils(self)
        self._manager = CallsManager(self)
        self._player = CallPlayer(self)
        self._controls = CallControls(self)
        self._queue = CallQueue(self)

    def get_lock(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._chat_locks:
            self._chat_locks[chat_id] = asyncio.Lock()
        return self._chat_locks[chat_id]

    async def cleanup_chat_state(self, chat_id: int, generation: int) -> None:
        """Release lightweight per-chat Python state after a full stop.

        The cleanup is delayed by one event-loop turn so callers that are
        currently inside ``async with get_lock(chat_id)`` can release the lock
        before we remove it.  The generation check prevents an old cleanup
        task from deleting state belonging to a new playback session.
        """
        try:
            await asyncio.sleep(0.2)

            if self._session_gen.get(chat_id) != generation:
                return
            if await db.get_call(chat_id):
                return
            if queue.get_current(chat_id) is not None:
                return

            lock = self._chat_locks.get(chat_id)
            if lock is not None and lock.locked():
                return

            self._pending_transitions.discard(chat_id)
            self._track_index.pop(chat_id, None)
            self._session_gen.pop(chat_id, None)
            self._chat_locks.pop(chat_id, None)

            logger.debug(f"🧹 Released idle playback state for {chat_id}")

            await asyncio.to_thread(self.release_idle_memory)
        except Exception as e:
            logger.debug(f"Idle state cleanup failed for {chat_id}: {e}")

    @staticmethod
    def release_idle_memory() -> None:
        """Ask the allocator to return unused heap pages to Linux when possible."""
        try:
            gc.collect()
            if sys.platform.startswith("linux"):
                libc = ctypes.CDLL(None)
                malloc_trim = getattr(libc, "malloc_trim", None)
                if malloc_trim is not None:
                    malloc_trim(0)
        except Exception:
            pass

    async def boot(self) -> None:
        return await self._manager.boot()

    async def ping(self) -> float:
        return await self._manager.ping()

    async def pause(self, chat_id: int) -> bool:
        return await self._controls.pause(chat_id)

    async def resume(self, chat_id: int) -> bool:
        return await self._controls.resume(chat_id)

    async def stop(self, chat_id: int) -> None:
        return await self._controls.stop(chat_id)

    async def seek_stream(self, chat_id: int, seconds: int) -> bool:
        return await self._controls.seek_stream(chat_id, seconds)

    async def play_media(
        self,
        chat_id: int,
        message: Message | None,
        media: Media | Track,
        seek_time: int = 0,
    ) -> None:
        return await self._player.play_media(chat_id, message, media, seek_time)

    async def replay(self, chat_id: int) -> None:
        return await self._queue.replay(chat_id)

    async def play_next(self, chat_id: int, expected_index: int = None) -> None:
        return await self._queue.play_next(chat_id, expected_index)
