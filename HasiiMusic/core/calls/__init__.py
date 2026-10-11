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
from HasiiMusic import app, db, logger, queue
from HasiiMusic.helpers import Media, Track

from .utils import CallsUtils, PyTgCallsErrorFilter
from .manager import CallsManager
from .player import CallPlayer
from .controls import CallControls
from .queue import CallQueue
from collections import deque

logging.getLogger('pyrogram.dispatcher').addFilter(PyTgCallsErrorFilter())

class TgCall(PyTgCalls):
    def __init__(self):

        
        # Shared state
        self.clients = []
        self._chat_locks = {}
        self._session_gen = {}
        self._track_index = {}
        # Playback timing used to reject delayed/duplicate StreamEnded events.
        self._play_started_at = {}
        self._play_duration = {}
        self._pending_transitions = set()
        self._transition_tasks = {}
        self._stopping = set()
        self._idle_reconcile_task = None
        self._vc_watchdog_task = None
        self._vc_recovery_state = {}
        # Network interruptions are not explicit user stops. Preserve the current
        # track/session for a 60-second grace window before attempting recovery.
        self._vc_network_grace = {}
        # Per-session autoplay memory.  Prevents the recommendation engine from
        # immediately selecting the same song/ID again under a different search
        # result or title variant.
        self._autoplay_recent = {}
        self._autoplay_failed = {}

        # Components
        self._utils = CallsUtils(self)
        self._manager = CallsManager(self)
        self._player = CallPlayer(self)
        self._controls = CallControls(self)
        self._queue = CallQueue(self)

    def mark_network_interruption(self, chat_id: int, reason: str = "network error") -> None:
        """Preserve playback state for 60s after a transient network failure."""
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:
            return

        previous_until = self._vc_network_grace.get(chat_id, 0.0)
        if previous_until <= now:
            self._vc_network_grace[chat_id] = now + 60.0
            state = self._vc_recovery_state.setdefault(chat_id, {})
            state.update({
                "missing": 0,
                "attempts": 0,
                "next_at": 0.0,
                "missing_since": now,
            })
        logger.warning(
            "NETWORK_GRACE chat=%s seconds_remaining=%.1f reason=%s; preserving playback state",
            chat_id,
            max(0.0, self._vc_network_grace.get(chat_id, now) - now),
            str(reason)[:160],
        )

    def begin_session(self, chat_id: int) -> int:
        """Start a new user-requested playback session for a chat."""
        self._stopping.discard(chat_id)
        generation = self._session_gen.get(chat_id, 0) + 1
        self._session_gen[chat_id] = generation
        self._track_index[chat_id] = 0
        self._pending_transitions.discard(chat_id)
        self._autoplay_recent[chat_id] = deque(maxlen=12)
        self._autoplay_failed[chat_id] = set()
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
            self._play_started_at.pop(chat_id, None)
            self._play_duration.pop(chat_id, None)
            self._autoplay_recent.pop(chat_id, None)
            self._autoplay_failed.pop(chat_id, None)
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

    async def _native_playback_idle(self, chat_id: int) -> bool:
        """Inspect native playback state; log failures for diagnosis."""
        clients = list(self.clients)
        found_call = False

        for index, client in enumerate(clients):
            try:
                calls = await client.calls
                call = calls.get(chat_id) if calls is not None else None

                if call is None:
                    logger.debug(
                        f"Playback diagnostic: chat={chat_id}, "
                        f"client_index={index}, call_not_found"
                    )
                    continue

                found_call = True
                playback = getattr(call, "playback", None)
                status_name = getattr(
                    playback, "name", str(playback)
                ).upper()

                logger.info(
                    f"Playback diagnostic: chat={chat_id}, "
                    f"client_index={index}, "
                    f"playback_type={type(playback).__name__}, "
                    f"playback_repr={playback!r}, "
                    f"status_name={status_name}"
                )
                return "IDLE" in status_name

            except Exception:
                logger.exception(
                    f"Playback state inspection failed: "
                    f"chat={chat_id}, client_index={index}"
                )

        if not found_call:
            logger.warning(
                f"Playback diagnostic: no native call found "
                f"for chat={chat_id} across {len(clients)} clients"
            )

        # Do not force recovery when the state cannot be confirmed.
        return False

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

            # Do NOT call public leave_call() here.
            # This chat is already confirmed absent from native active calls.
            # Calling leave_call() can trigger the normal stop/cleanup flow
            # again and produce repeated BEFORE_LEAVE events.

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
        state_chats.update(self._play_started_at)
        state_chats.update(self._play_duration)
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
                self._play_started_at.pop(chat_id, None)
                self._play_duration.pop(chat_id, None)
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

    async def _recover_ghost_chat(self, chat_id: int, force_stream_recovery: bool = False):
        """Recover one chat whose Python playback state is alive but native VC is gone.

        Returns:
            True  -> recovered / already recovered
            False -> recovery attempted but did not restore native VC
            None  -> skipped because another operation currently owns the chat lock
        """
        if chat_id in self._stopping:
            return False

        lock = self.get_lock(chat_id)
        if lock.locked():
            return None

        async with lock:
            if chat_id in self._stopping:
                return False
            media = queue.get_current(chat_id)
            if media is None:
                logger.warning(f"👻 Ghost VC has no current media; cannot recover {chat_id}")
                return False

            # A previous pause/resume failure or legacy cleanup may have removed
            # the Python active-call marker while leaving the queue intact. A
            # queued current track plus confirmed missing native VC is sufficient
            # evidence to restore the in-memory session for this chat only.
            if not await db.get_call(chat_id):
                await db.add_call(chat_id)
                await db.playing(chat_id, paused=False)
                logger.warning(f"♻️ Restored stale playback marker from queue for {chat_id}")
            else:
                try:
                    if not await db.playing(chat_id):
                        return False
                except (KeyError, TypeError):
                    await db.playing(chat_id, paused=False)

            active_ids = await self._native_call_ids()
            if active_ids is None:
                return False
            if chat_id in active_ids and not force_stream_recovery:
                return True

            if not getattr(media, "file_path", None):
                logger.warning(
                    f"👻 Ghost VC current media has no file_path for {chat_id}; "
                    "waiting for the normal queue/download path"
                )
                return False

            # Reuse the current media and approximate playback position.
            # Do not start a new session: this is recovery of the existing one.
            seek_time = max(1, int(getattr(media, "time", 1) or 1))
            message = None
            message_id = getattr(media, "message_id", None)
            if message_id:
                try:
                    message = await app.get_messages(chat_id, message_id)
                except Exception:
                    message = None

            logger.warning(
                f"🔄 Recovering Ghost VC for {chat_id} from ~{seek_time}s"
            )

            await self._player._play_media_impl(
                chat_id,
                message,
                media,
                seek_time=seek_time,
                recovery=True,
            )

            await asyncio.sleep(0.75)
            active_after = await self._native_call_ids()
            if (active_after is not None and chat_id in active_after
                    and not await self._native_playback_idle(chat_id)):
                try:
                    from HasiiMusic import preload
                    await preload.start_preload(chat_id, count=1)
                except Exception as e:
                    logger.debug(
                        f"Ghost VC preload failed for {chat_id}: {e}"
                    )
                logger.info(f"✅ Ghost VC recovered for {chat_id}")
                return True

            logger.warning(
                f"⚠️ Ghost VC recovery did not restore native call for {chat_id}; "
                f"native_calls={sorted(active_after) if active_after is not None else 'unknown'}"
            )
            return False

    async def _vc_watchdog_loop(self) -> None:
        """Detect active playback whose native voice call disappeared.

        This is intentionally separate from idle reconciliation: a chat with
        an active DB playback record is *not* idle and therefore must be
        recovered rather than cleaned.
        """
        try:
            while True:
                await asyncio.sleep(10)
                logger.info('VC watchdog heartbeat: loop alive')

                active_ids = await self._native_call_ids()
                if active_ids is None:
                    continue

                active_calls = getattr(db, "active_calls", {})
                # Queue-backed chats are included so a stale/missing active_calls
                # marker cannot permanently hide an orphaned playback session.
                candidate_chats = set(active_calls.keys())
                candidate_chats.update(getattr(queue, "queues", {}).keys())
                logger.info(
                    f"VC watchdog candidates={sorted(candidate_chats)}, "
                    f"native_ids={sorted(active_ids)}"
                )
                for chat_id in list(candidate_chats):
                    try:
                        if chat_id in self._stopping:
                            logger.warning(
                                f"VC watchdog skipped chat={chat_id}: "
                                f"marked stopping; stopping={sorted(self._stopping)}"
                            )
                            self._vc_recovery_state.pop(chat_id, None)
                            continue

                        has_call = await db.get_call(chat_id)
                        has_current_media = queue.get_current(chat_id) is not None
                        logger.info(
                            f"VC watchdog candidate chat={chat_id}, "
                            f"has_call={has_call}, "
                            f"has_current_media={has_current_media}, "
                            f"native_present={chat_id in active_ids}"
                        )
                        if not has_call and not has_current_media:
                            self._vc_recovery_state.pop(chat_id, None)
                            continue

                        if has_call and not await db.playing(chat_id):
                            logger.info(
                                f"VC watchdog skipped chat={chat_id}: "
                                f"DB says not playing"
                            )
                            # Paused playback is intentionally not auto-rejoined.
                            self._vc_recovery_state.pop(chat_id, None)
                            continue

                        if not has_current_media:
                            logger.info(
                                f"VC watchdog skipped chat={chat_id}: "
                                f"no current media"
                            )
                            self._vc_recovery_state.pop(chat_id, None)
                            continue

                        loop = asyncio.get_running_loop()
                        now = loop.time()
                        grace_until = self._vc_network_grace.get(chat_id, 0.0)
                        if now < grace_until:
                            continue
                        if grace_until:
                            self._vc_network_grace.pop(chat_id, None)

                        force_stream_recovery = False
                        if chat_id in active_ids:
                            # A stale low-level native id can survive after the
                            # public PyTgCalls call has disappeared. Cross-check
                            # both views before deciding the voice chat is healthy.
                            public_call_found = False
                            public_state_known = False
                            for client in list(self.clients):
                                try:
                                    public_calls = await client.calls
                                    public_state_known = True
                                    if public_calls and chat_id in public_calls:
                                        public_call_found = True
                                        break
                                except Exception:
                                    continue

                            if public_state_known and not public_call_found:
                                force_stream_recovery = True
                                logger.warning(
                                    f"Native binding still lists chat={chat_id}, "
                                    "but PyTgCalls has no public call; treating it "
                                    "as stale after network interruption"
                                )
                            else:
                                force_stream_recovery = (
                                    await self._native_playback_idle(chat_id)
                                )
                            if not force_stream_recovery:
                                if chat_id in self._vc_recovery_state:
                                    logger.info(
                                        f"VC watchdog cleared recovery state for {chat_id}"
                                    )
                                self._vc_recovery_state.pop(chat_id, None)
                                continue

                            logger.warning(
                                f"Native call exists but playback is IDLE "
                                f"for {chat_id}; scheduling stream recovery"
                            )

                        state = self._vc_recovery_state.setdefault(
                            chat_id,
                            {
                                "missing": 0,
                                "attempts": 0,
                                "next_at": 0.0,
                                "missing_since": now,
                            },
                        )
                        state["missing"] += 1
                        state.setdefault("missing_since", now)

                        # Grace period for short network interruptions: keep the
                        # playback account/session state untouched for 60 seconds.
                        # Only attempt rejoin after the native call has remained
                        # missing for the full grace window.
                        missing_for = now - state["missing_since"]
                        grace_period = 20 if force_stream_recovery else 60
                        if missing_for < grace_period:
                            if state["missing"] == 1:
                                logger.warning(
                                    f"👻 Native VC missing for {chat_id}; "
                                    "preserving session for 60s grace period"
                                )
                            continue

                        if now < state["next_at"]:
                            continue

                        result = await self._recover_ghost_chat(
                            chat_id,
                            force_stream_recovery=force_stream_recovery,
                        )
                        if result is None:
                            continue

                        if result:
                            self._vc_recovery_state.pop(chat_id, None)
                            self._vc_network_grace.pop(chat_id, None)
                            continue

                        state["attempts"] += 1
                        # 5s, 10s, 20s, 40s, then cap at 60s.
                        delay = min(60, 5 * (2 ** min(state["attempts"] - 1, 4)))
                        state["next_at"] = now + delay
                        logger.warning(
                            f"⏳ Ghost VC recovery retry scheduled for {chat_id} "
                            f"in {delay}s (attempt={state['attempts']})"
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.debug(
                            f"VC watchdog check failed for {chat_id}: {e}"
                        )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"VC watchdog stopped unexpectedly: {e}", exc_info=True)

    def start_vc_watchdog(self) -> None:
        if self._vc_watchdog_task is None or self._vc_watchdog_task.done():
            self._vc_watchdog_task = asyncio.create_task(
                self._vc_watchdog_loop(),
                name="vc_ghost_watchdog",
            )
            logger.info("🛡️ VC Ghost Watchdog started (10s interval)")

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
        watchdog = self._vc_watchdog_task
        self._vc_watchdog_task = None
        if watchdog is not None and not watchdog.done():
            watchdog.cancel()
            try:
                await watchdog
            except asyncio.CancelledError:
                pass

        self._vc_recovery_state.clear()

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
        self.start_vc_watchdog()
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