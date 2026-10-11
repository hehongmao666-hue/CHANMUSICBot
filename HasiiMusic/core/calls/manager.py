"""
# ==============================================================================
# manager.py - Calls Lifecycle Manager
# ==============================================================================
# This file manages the PyTgCalls lifecycle and update events.
# Features:
# - Boots up PyTgCalls clients for all userbots
# - Registers event decorators (stream ended, group call closed)
# - Pings client latency
# ==============================================================================
"""

import asyncio
import time
from ntgcalls import ConnectionNotFound, TelegramServerError
import ntgcalls
from pytgcalls import PyTgCalls, exceptions, types
from pytgcalls.pytgcalls_session import PyTgCallsSession
from HasiiMusic import db, logger, queue, userbot

class CallsManager:
    def __init__(self, controller):
        self.controller = controller

    async def boot(self) -> None:
        PyTgCallsSession.notice_displayed = True
        for ub in userbot.clients:
            client = PyTgCalls(ub, workers=4, cache_duration=30)
            await client.start()
            self.controller.clients.append(client)
            await self.decorators(client)
        logger.info(
            "📞 PyTgCalls client(s) started. py-tgcalls=%s ntgcalls=%s",
            getattr(__import__("pytgcalls"), "__version__", "unknown"),
            getattr(ntgcalls, "__version__", "unknown"),
        )

    async def ping(self) -> float:
        if not self.controller.clients:
            return 0.0
        pings = [client.ping for client in self.controller.clients]
        return round(sum(pings) / len(pings), 2)

    async def decorators(self, client: PyTgCalls) -> None:
        @client.on_update()
        async def update_handler(_, update: types.Update) -> None:
            try:
                if isinstance(update, types.StreamEnded):
                    if update.stream_type == types.StreamEnded.Type.AUDIO:
                        chat_id = update.chat_id
                        if chat_id in self.controller._stopping:
                            return
                        grace_until = getattr(self.controller, "_vc_network_grace", {}).get(chat_id, 0.0)
                        if asyncio.get_running_loop().time() < grace_until:
                            logger.warning(
                                "Ignoring StreamEnded during network grace for %s; current track/queue preserved",
                                chat_id,
                            )
                            return
                        # A delayed StreamEnded can arrive after /stop or queue end.
                        # Do not recreate locks/track state for an already-idle chat.
                        if not await db.get_call(chat_id):
                            self.controller._pending_transitions.discard(chat_id)
                            return
                        # PyTgCalls can emit a delayed/duplicate StreamEnded update
                        # after a track has already been replaced. Without this guard,
                        # each stale event can consume the newly-started autoplay track
                        # and immediately trigger another recommendation.
                        started_at = self.controller._play_started_at.get(chat_id)
                        if started_at is not None:
                            elapsed = time.monotonic() - started_at
                            duration = float(self.controller._play_duration.get(chat_id) or 0)
                            # Require a reasonable portion of the current track to have
                            # elapsed before accepting StreamEnded. Cap the guard so very
                            # short clips still transition normally.
                            min_elapsed = min(15.0, max(4.0, duration * 0.25)) if duration > 0 else 5.0
                            if elapsed < min_elapsed:
                                logger.debug(
                                    "Ignoring early/delayed StreamEnded for %s: elapsed=%.1fs threshold=%.1fs",
                                    chat_id, elapsed, min_elapsed,
                                )
                                return
                        expected_index = self.controller._track_index.get(chat_id, 0)
                        self.controller.schedule_transition(chat_id, expected_index)
                elif isinstance(update, types.StreamFrames):
                    # Diagnostic only: do not change playback behavior.
                    if not hasattr(self.controller, "_frame_diag_last"):
                        self.controller._frame_diag_last = {}
                    now = time.monotonic()
                    key = (
                        update.chat_id,
                        str(update.direction),
                        str(update.device),
                    )
                    last = self.controller._frame_diag_last.get(key, 0)
                    # Log at most once every 30 seconds per stream.
                    if now - last >= 30:
                        self.controller._frame_diag_last[key] = now
                        frames = update.frames or []
                        total_bytes = sum(
                            len(frame.frame)
                            for frame in frames
                            if frame.frame is not None
                        )
                        logger.info(
                            "StreamFrames diagnostic: "
                            f"chat={update.chat_id}, "
                            f"direction={update.direction}, "
                            f"device={update.device}, "
                            f"frames={len(frames)}, "
                            f"bytes={total_bytes}"
                        )
                elif isinstance(update, types.ChatUpdate):
                    if update.status == types.ChatUpdate.Status.KICKED:
                        # A real kick is not a transient network interruption.
                        await self.controller._controls.stop(update.chat_id)
                    elif update.status in [
                        types.ChatUpdate.Status.LEFT_GROUP,
                        types.ChatUpdate.Status.CLOSED_VOICE_CHAT,
                    ]:
                        chat_id = update.chat_id
                        has_session = await db.get_call(chat_id) and queue.get_current(chat_id) is not None
                        if has_session and await db.playing(chat_id):
                            # Do not call stop(): transient disconnects can produce
                            # these updates and stop() clears the queue and DB marker.
                            self.controller.mark_network_interruption(
                                chat_id, reason=f"ChatUpdate:{update.status}"
                            )
                        else:
                            await self.controller._controls.stop(chat_id)
            except (ConnectionNotFound, exceptions.NotInCallError, TelegramServerError):
                return
            except Exception as e:
                logger.debug(f"Ignoring update handler error: {e}")