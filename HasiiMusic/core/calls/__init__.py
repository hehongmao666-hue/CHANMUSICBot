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
import inspect
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
        self._transition_tasks = {}
        self._stopping = set()
        self._idle_reconcile_task = None

        # Components
        self._utils = CallsUtils(self)
        self._manager = CallsManager(self)
        self._player = CallPlayer(self)
        self._controls = CallControls(self)
        self._queue = CallQueue(self)

    def begin_session(self, chat_id: int) -> int:
        """Start a new user-requested playback session for a chat."""
        self._stopping.discard(chat_id)
        generation = self._session_gen.get(chat_id, 0) + 1
        self._session_gen[chat_id] = generation
        self._track_index[chat_id] = 0
        self._pending_transitions.discard(chat_id)
        return generation

    def _transition_done(self, chat_id: int, task: asyncio.Task) -> None:
        current = self._transition_tasks.get(chat_id)
        if current is task:
            self._transition_tasks.pop(chat_id, None)

    def schedule_transition(self, chat_id: int, expected_index: int) -> None:
        """Schedule at most one stream transition per chat."""
        if chat_id in self._stopping:
            return
        existing = self._transition_tasks.get(chat_id)
        if existing is not None and not existing.done():
            return
        self._pending_transitions.add(chat_id)
        task = asyncio.create_task(
            self._queue.play_next(chat_id, expected_index),
            name=f"play_next:{chat_id}",
        )
        self._transition_tasks[chat_id] = task
        task.add_done_callback(
            lambda done, cid=chat_id: self._transition_done(cid, done)
        )

    def get_lock(self, chat_id: int) -> asyncio.Lock:
        if chat_id not in self._chat_locks:
            self._chat_locks[chat_id] = asyncio.Lock()
        return self._chat_locks[chat_id]

    async def cleanup_chat_state(self, chat_id: int, generation: int) -> None:
        """Remove all lightweight state after a chat has fully stopped."""
        try:
            await asyncio.sleep(0.5)

            if self._session_gen.get(chat_id) != generation:
                return
            if await db.get_call(chat_id):
                return
            if queue.get_current(chat_id) is not None:
                return

            for _ in range(10):
                lock = self._chat_locks.get(chat_id)
                if lock is None or not lock.locked():
                    break
                await asyncio.sleep(0.5)
            else:
                return

            self._pending_transitions.discard(chat_id)
            self._track_index.pop(chat_id, None)
            self._session_gen.pop(chat_id, None)
            self._chat_locks.pop(chat_id, None)
            self._stopping.discard(chat_id)

            transition = self._transition_tasks.pop(chat_id, None)
            if transition is not None and not transition.done():
                transition.cancel()

            logger.debug(f"🧹 Released idle playback state for {chat_id}")
            await asyncio.to_thread(self.release_idle_memory)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"Idle state cleanup failed for {chat_id}: {e}")

    async def _native_call_ids(self):
        """Return active native chat ids when ntgcalls exposes them.

        ntgcalls 3.x exposes ``calls()`` as a mapping keyed by chat id.
        Keeping the ids lets us clean stale chats individually even while
        another group is still playing.  ``None`` means the binding did not
        expose a usable mapping.
        """
        active = set()
        seen = False
        for client in list(self.clients):
            binding = getattr(client, "_binding", None)
            accessor = getattr(binding, "calls", None) if binding is not None else None
            if not callable(accessor):
                continue
            try:
                result = accessor()
                if inspect.isawaitable(result):
                    result = await result
                if isinstance(result, dict):
                    active.update(result.keys())
                    seen = True
                elif isinstance(result, (list, tuple, set, frozenset)):
                    # Some binding builds may return a sequence of chat ids.
                    active.update(result)
                    seen = True
            except Exception:
                continue
        return active if seen else None

    async def _native_call_count(self):
        """Return the total native call count when the binding exposes it."""
        active = await self._native_call_ids()
        if active is not None:
            return len(active)

        # Fallback for bindings that only expose an integer count.
        total = 0
        seen = False
        for client in list(self.clients):
            binding = getattr(client, "_binding", None)
            accessor = getattr(binding, "calls", None) if binding is not None else None
            if not callable(accessor):
                continue
            try:
                result = accessor()
                if inspect.isawaitable(result):
                    result = await result
                if isinstance(result, int):
                    total += result
                    seen = True
            except Exception:
                continue
        return total if seen else None

    def _native_cached_chat_ids(self):
        """Collect PyTgCalls internal cache keys for stale-cache cleanup."""
        cached = set()
        attrs = (
            "_call_sources",
            "_wait_connect",
            "_p2p_configs",
            "_pending_connections",
            "_need_unmute",
            "_presentations",
            "_cache_user_peer",
        )
        for client in list(self.clients):
            for attr in attrs:
                value = getattr(client, attr, None)
                if isinstance(value, dict):
                    cached.update(value.keys())
                elif isinstance(value, (set, list, tuple, frozenset)):
                    cached.update(value)
        return cached

    async def _clear_native_idle_chat(self, chat_id: int, active_ids) -> bool:
        """Clear PyTgCalls/ntgcalls state for one chat known to be idle.

        This is deliberately per-chat.  It never recreates a whole PyTgCalls
        client and therefore cannot interrupt unrelated active groups.
        """
        if active_ids is None or chat_id in active_ids:
            return False

        cleared = False
        for client in list(self.clients):
            # ``_clear_call`` is the native stack's own internal cleanup path:
            # it stops the native chat (ignoring ConnectionNotFound) and then
            # clears _call_sources/_wait_connect/_p2p_configs/etc.
            clear_call = getattr(client, "_clear_call", None)
            if callable(clear_call):
                try:
                    await clear_call(chat_id)
                    cleared = True
                    continue
                except Exception:
                    pass

            # Compatibility fallback for builds without _clear_call.
            leave_call = getattr(client, "leave_call", None)
            if callable(leave_call):
                try:
                    await leave_call(chat_id, close=False)
                    cleared = True
                except Exception:
                    pass
        return cleared

    async def reconcile_idle_state(self) -> int:
        """Reconcile stale Python *and native* state on a per-chat basis.

        V10 waited for the whole native client to reach zero calls before
        cleaning anything.  That is safe but leaves stale state around while
        another group is playing.  V11 uses ntgcalls' chat-id mapping instead:
        active chats are protected, while idle chats are cleaned individually.
        """
        active_ids = await self._native_call_ids()
        if active_ids is None:
            # Unknown native ownership: retain V10's conservative behavior.
            native_count = await self._native_call_count()
            if native_count is None or native_count > 0:
                return 0

        state_chats = set(self._chat_locks)
        state_chats.update(self._session_gen)
        state_chats.update(self._track_index)
        state_chats.update(self._transition_tasks)
        state_chats.update(self._stopping)
        state_chats.update(self._pending_transitions)
        state_chats.update(self._native_cached_chat_ids())

        cleaned = 0
        native_cleaned = 0
        for chat_id in list(state_chats):
            try:
                if active_ids is not None and chat_id in active_ids:
                    continue
                if chat_id in self._stopping:
                    continue
                transition = self._transition_tasks.get(chat_id)
                if transition is not None and not transition.done():
                    continue
                if await db.get_call(chat_id):
                    continue
                if queue.get_current(chat_id) is not None:
                    continue

                lock = self._chat_locks.get(chat_id)
                if lock is not None and lock.locked():
                    continue

                # First release the native per-chat caches. This is important
                # because Python-side state can be empty while PyTgCalls still
                # holds call sources/peers for an old chat.
                if active_ids is not None:
                    if await self._clear_native_idle_chat(chat_id, active_ids):
                        native_cleaned += 1

                self._pending_transitions.discard(chat_id)
                self._track_index.pop(chat_id, None)
                self._session_gen.pop(chat_id, None)
                self._chat_locks.pop(chat_id, None)
                self._stopping.discard(chat_id)
                transition = self._transition_tasks.pop(chat_id, None)
                if transition is not None and not transition.done():
                    transition.cancel()
                cleaned += 1
            except Exception:
                continue

        if cleaned or native_cleaned:
            await asyncio.to_thread(self.release_idle_memory)
            logger.info(
                "🧹 V11 idle reconciliation: python=%d native=%d active_native=%s",
                cleaned,
                native_cleaned,
                sorted(active_ids) if active_ids is not None else "unknown",
            )
        return cleaned + native_cleaned

    async def _idle_reconcile_loop(self) -> None:
        """Periodically remove stale state without requiring /stats."""
        try:
            while True:
                await asyncio.sleep(300)
                await self.reconcile_idle_state()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"Idle reconcile loop stopped: {e}")

    def start_idle_reconciler(self) -> None:
        if self._idle_reconcile_task is None or self._idle_reconcile_task.done():
            self._idle_reconcile_task = asyncio.create_task(
                self._idle_reconcile_loop(),
                name="idle_state_reconciler",
            )

    async def shutdown(self) -> None:
        task = self._idle_reconcile_task
        self._idle_reconcile_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

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
        result = await self._manager.boot()
        self.start_idle_reconciler()
        return result

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
