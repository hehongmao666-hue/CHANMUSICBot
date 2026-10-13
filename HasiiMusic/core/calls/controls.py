"""
# ==============================================================================
# controls.py - Calls Playback Controls
# ==============================================================================
# This file manages playback controls for PyTgCalls.
# Features:
# - Pause, Resume, Stop
# - Seek stream functionality
# ==============================================================================
"""

import asyncio
from ntgcalls import ConnectionNotFound
from pytgcalls import exceptions
from HasiiMusic import app, db, logger, preload, queue, lang
from HasiiMusic.core.calls.diagnostics import log_call_lifecycle_snapshot

class CallControls:
    def __init__(self, controller):
        self.controller = controller

    async def pause(self, chat_id: int) -> bool:
        async with self.controller.get_lock(chat_id):
            client = await db.get_assistant(chat_id)
            try:
                await client.pause(chat_id)
                await db.playing(chat_id, paused=True)
                return True
            except (ConnectionNotFound, exceptions.NotInCallError):
                # A missing native VC is not an explicit user stop. Preserve the
                # active-call marker and queue so the per-chat watchdog can recover.
                if await db.get_call(chat_id):
                    await db.playing(chat_id, paused=False)
                logger.warning(
                    f"Pause found no native VC for {chat_id}; preserving queue/session for watchdog recovery")
                return False
            except Exception as e:
                await db.playing(chat_id, paused=False)
                logger.error(f"Pause failed for {chat_id}: {e}")
                return False

    async def resume(self, chat_id: int) -> bool:
        async with self.controller.get_lock(chat_id):
            client = await db.get_assistant(chat_id)
            try:
                await client.resume(chat_id)
                await db.playing(chat_id, paused=False)
                return True
            except (ConnectionNotFound, exceptions.NotInCallError):
                # Do not erase the only recovery source on a transient VC loss.
                if await db.get_call(chat_id):
                    await db.playing(chat_id, paused=False)
                logger.warning(
                    f"Resume found no native VC for {chat_id}; preserving queue/session for watchdog recovery")
                return False
            except Exception as e:
                logger.error(f"Resume failed for {chat_id}: {e}")
                return False

    async def stop(self, chat_id: int) -> None:
        # Do not wait on the per-chat playback lock here. A stuck play/recovery
        # task may own it indefinitely; /stop must remain an escape hatch.
        await self._stop_impl(chat_id)

    async def _stop_impl(self, chat_id: int) -> None:
        """Best-effort hard stop that always clears local playback state.

        This method deliberately does not acquire the per-chat lock because it is
        also called by queue/player code that may already hold it, and a network
        failure can otherwise make /stop wait forever.
        """
        self.controller._stopping.add(chat_id)
        self.controller._vc_network_grace.pop(chat_id, None)
        self.controller._vc_recovery_state.pop(chat_id, None)
        self.controller._session_gen[chat_id] = self.controller._session_gen.get(chat_id, 0) + 1

        current_task = asyncio.current_task()
        transition = self.controller._transition_tasks.get(chat_id)
        if transition is not None and transition is not current_task and not transition.done():
            transition.cancel()
            try:
                await asyncio.wait_for(asyncio.gather(transition, return_exceptions=True), timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                logger.warning(f"Stop: transition task did not finish promptly for {chat_id}")
        self.controller._transition_tasks.pop(chat_id, None)
        self.controller._pending_transitions.discard(chat_id)

        # Clear user-visible/local state first. A database outage must not leave
        # the bot permanently reporting that playback is active.
        try:
            queue.clear(chat_id)
        except Exception as e:
            logger.warning(f"Stop: queue.clear failed for {chat_id}: {e}")
        try:
            self.controller._autoplay_recent.pop(chat_id, None)
            self.controller._autoplay_failed.pop(chat_id, None)
        except Exception:
            pass
        try:
            await db.remove_call(chat_id)
        except Exception as e:
            logger.warning(f"Stop: active-call marker cleanup failed for {chat_id}: {e}")

        # Cancel background producers with timeouts so a stuck task cannot block
        # the remainder of stop cleanup.
        for label, operation in (
            ("playlist", self.controller._queue.cancel_playlist_tasks(chat_id)),
            ("preload", preload.cancel_preload(chat_id)),
        ):
            try:
                await asyncio.wait_for(operation, timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning(f"Stop: {label} cancellation timed out for {chat_id}")
            except Exception as e:
                logger.debug(f"Stop: {label} cancellation failed for {chat_id}: {e}")

        # Resolve the assistant only after local state is already cleared. MongoDB
        # may be unreachable, so fall back to clients that report this call.
        clients_to_leave = []
        try:
            client = await asyncio.wait_for(db.get_assistant(chat_id), timeout=3.0)
            if client is not None:
                clients_to_leave.append(client)
        except Exception as e:
            logger.warning(f"Stop: could not resolve assigned assistant for {chat_id}: {e}")

        try:
            native_ids = await asyncio.wait_for(self.controller._native_call_ids(), timeout=3.0)
        except Exception:
            native_ids = None
        if native_ids is None or chat_id in native_ids:
            try:
                public_clients = list(self.controller.clients)
            except Exception:
                public_clients = []
            for candidate in public_clients:
                if candidate not in clients_to_leave:
                    try:
                        calls = await asyncio.wait_for(candidate.calls, timeout=2.0)
                        if calls and chat_id in calls:
                            clients_to_leave.append(candidate)
                    except Exception:
                        # If call ownership cannot be queried, only use the assigned
                        # client; do not issue leave_call against every assistant.
                        continue

        try:
            for client in clients_to_leave:
                try:
                    await log_call_lifecycle_snapshot("BEFORE_LEAVE", client, chat_id)
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(client.leave_call(chat_id, close=False), timeout=5.0)
                    try:
                        await log_call_lifecycle_snapshot("AFTER_LEAVE", client, chat_id)
                    except Exception:
                        pass
                    logger.info(f"Stop: leave_call completed for {chat_id}")
                except (ConnectionNotFound, exceptions.NotInCallError):
                    pass
                except asyncio.TimeoutError:
                    logger.warning(f"Stop: leave_call timed out for {chat_id}")
                except Exception as e:
                    message = str(e).lower()
                    if not any(ignore in message for ignore in (
                        "not in a call", "not in the group call", "groupcall_forbidden",
                        "no active group call", "call was already stopped", "call already disconnected"
                    )):
                        logger.warning(f"Stop: leave_call failed for {chat_id}: {e}")
        finally:
            # This is the essential anti-deadlock guarantee: stale stop/recovery
            # markers must not permanently block /play or the watchdog.
            self.controller._vc_recovery_state.pop(chat_id, None)
            self.controller._vc_network_grace.pop(chat_id, None)
            self.controller._pending_transitions.discard(chat_id)
            self.controller._play_started_at.pop(chat_id, None)
            self.controller._play_duration.pop(chat_id, None)
            self.controller._track_index.pop(chat_id, None)
            self.controller._stopping.discard(chat_id)
            generation = self.controller._session_gen.get(chat_id, 0)
            asyncio.create_task(self.controller.cleanup_chat_state(chat_id, generation))

    async def seek_stream(self, chat_id: int, seconds: int) -> bool:
        """seek to a position in the current stream"""
        try:
            if not await db.get_call(chat_id):
                return False

            media = queue.get_current(chat_id)
            if not media or getattr(media, "is_live", False):
                return False

            _lang = await lang.get_lang(chat_id)

            media.time = seconds

            try:
                msg = await app.get_messages(chat_id, media.message_id)
            except Exception:
                msg = None

            if not msg:
                _lang = await lang.get_lang(chat_id)
                msg = await app.send_message(chat_id=chat_id, text=_lang["seeking"])

            await self.controller._player._play_media_impl(chat_id, msg, media, seek_time=seconds)
            return True
        except Exception as e:
            logger.warning(f"Seek stream failed for {chat_id}: {e}")
            return False