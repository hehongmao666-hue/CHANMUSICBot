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
from ntgcalls import ConnectionNotFound, TelegramServerError
import ntgcalls
from pytgcalls import PyTgCalls, exceptions, types
from pytgcalls.pytgcalls_session import PyTgCallsSession
from HasiiMusic import db, logger, userbot

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
                        # A delayed StreamEnded can arrive after /stop or queue end.
                        # Do not recreate locks/track state for an already-idle chat.
                        if not await db.get_call(chat_id):
                            self.controller._pending_transitions.discard(chat_id)
                            return
                        expected_index = self.controller._track_index.get(chat_id, 0)
                        self.controller.schedule_transition(chat_id, expected_index)
                elif isinstance(update, types.ChatUpdate):
                    if update.status in [
                        types.ChatUpdate.Status.KICKED,
                        types.ChatUpdate.Status.LEFT_GROUP,
                        types.ChatUpdate.Status.CLOSED_VOICE_CHAT,
                    ]:
                        await self.controller._controls.stop(update.chat_id)
            except (ConnectionNotFound, exceptions.NotInCallError, TelegramServerError):
                return
            except Exception as e:
                logger.debug(f"Ignoring update handler error: {e}")
