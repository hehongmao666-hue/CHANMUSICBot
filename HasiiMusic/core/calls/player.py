"""
# ==============================================================================
# player.py - Calls Playback Engine
# ==============================================================================
# This file manages the core logic for starting streams in PyTgCalls.
# Features:
# - Validates media and fetches thumbnail
# - Sets up ffmpeg arguments based on media type and seek time
# - Handles PyTgCalls start with robust retry logic (FloodWaits, ghost streams)
# - Sends UI messages with playback status
# ==============================================================================
"""

import asyncio
import re
import time
from ntgcalls import ConnectionNotFound, TelegramServerError, TransportParseException
from pyrogram import enums, errors
from pyrogram.types import Message
from pytgcalls import exceptions, types

from HasiiMusic import app, config, db, lang, logger, preload
from HasiiMusic.core.calls.diagnostics import log_call_lifecycle_snapshot
from HasiiMusic.helpers import Media, Track, buttons, thumb

class CallPlayer:
    def __init__(self, controller):
        self.controller = controller

    async def play_media(
        self,
        chat_id: int,
        message: Message | None,
        media: Media | Track,
        seek_time: int = 0,
    ) -> None:
        async with self.controller.get_lock(chat_id):
            self.controller.begin_session(chat_id)
            await self._play_media_impl(
                chat_id, message, media, seek_time
            )

    async def _play_media_impl(
        self,
        chat_id: int,
        message: Message | None,
        media: Media | Track,
        seek_time: int = 0,
        recovery: bool = False,
    ) -> None:
        """Play media in voice chat.

        Args:
            chat_id: Where to stream audio
            message: Message to edit/delete (if any)
            media: Media object to play
            seek_time: Position to seek to (seconds)
            recovery: Recover an existing playback session without clearing its state.
        """
        client = await db.get_assistant(chat_id)
        _lang = await lang.get_lang(chat_id)

        # Generate thumbnail only if THUMB_GEN is enabled. otherwise use default
        if config.THUMB_GEN and isinstance(media, Track):
            if not getattr(media, "thumbnail", None) and re.fullmatch(r"[A-Za-z0-9_-]{11}", media.id):
                media.thumbnail = f"https://i.ytimg.com/vi/{media.id}/hqdefault.jpg"
            _thumb = await thumb.generate(media)
        else:
            _thumb = config.DEFAULT_THUMB

        if not media.file_path:
            if message:
                return await message.edit_text(_lang["error_no_file"].format(config.SUPPORT_CHAT))
            else:
                logger.error(f"No file path for media in {chat_id}")
                return

        # make sure this is a valid group chat
        try:
            chat = await app.get_chat(chat_id)
            if chat.type not in [enums.ChatType.SUPERGROUP, enums.ChatType.GROUP]:
                logger.error(f"Invalid chat type for {chat_id}: {chat.type}")
                if message:
                    await message.edit_text("❌ ᴄᴀɴ ᴏɴʟʏ ᴘʟᴀʏ ɪɴ ɢʀᴏᴜᴘꜱ.")
                return
        except errors.RPCError as e:
            raise

        # Stable realtime audio pipeline.
        # Avoid ``-sync ext`` here: for local audio files it can cause timestamp
        # corrections that show up as small skips/stutters in long playback.
        # Audio is also explicitly stripped of video/subtitle/data streams so
        # FFmpeg does not spend work probing or processing unused streams.
        is_video = getattr(media, "video", False)
        if seek_time > 1:
            ffmpeg_params = (
                f"-ss {seek_time} -probesize 2M -analyzeduration 1M "
                "-fflags +genpts+igndts"
            )
        else:
            ffmpeg_params = (
                "-probesize 2M -analyzeduration 1M "
                "-fflags +genpts+igndts"
            )

        if not is_video:
            ffmpeg_params += " -vn -sn -dn"
        video_flags = (
            types.MediaStream.Flags.AUTO_DETECT
            if is_video
            else types.MediaStream.Flags.IGNORE
        )

        kwargs = {
            "media_path": media.file_path,
            # MEDIUM is a better balance for Telegram voice chats: it lowers
            # uplink pressure while remaining clear for music playback.
            "audio_parameters": types.AudioQuality.MEDIUM,
            "audio_flags": types.MediaStream.Flags.REQUIRED,
            "video_flags": video_flags,
            "ffmpeg_parameters": ffmpeg_params,
        }
        
        if is_video:
            # use VIDEO_MAX_HEIGHT for the playback resolution.
            # lower resolution and FPS use less CPU.
            h = config.VIDEO_MAX_HEIGHT or 720
            if h <= 360:
                w, fps = 640, 15
            elif h <= 480:
                w, fps = 854, 20
            elif h <= 720:
                w, fps = 1280, 25
            else:
                w, fps = 1920, 30
            kwargs["video_parameters"] = types.raw.VideoParameters(
                width=w, height=h, frame_rate=fps,
            )
            
        stream = types.MediaStream(**kwargs)

        # IMPORTANT: do not leave/rejoin the voice call for every track.
        # PyTgCalls.play() is designed to replace the active stream in-place.
        # Repeated leave_call() -> play() cycles force native ntgcalls teardown
        # and reinitialization for every song and can retain large native heaps.
        max_retries = 3
        retry_delay = 1

        try:
            await log_call_lifecycle_snapshot("BEFORE_PLAY", client, chat_id)

            for attempt in range(max_retries):
                try:
                    await client.play(
                        chat_id=chat_id,
                        stream=stream,
                        config=types.GroupCallConfig(auto_start=True),
                    )
                    await log_call_lifecycle_snapshot("AFTER_PLAY", client, chat_id)
                    # Record the start of the stream. The update handler uses this
                    # timestamp to reject stale StreamEnded events from the previous
                    # stream, which is especially important for autoplay.
                    self.controller._play_started_at[chat_id] = time.monotonic()
                    self.controller._play_duration[chat_id] = float(
                        getattr(media, "duration_sec", 0) or 0
                    )
                    break

                except (exceptions.NoActiveGroupCall, errors.RPCError) as e:
                    error_msg = str(e)

                    if (
                        "GROUPCALL_INVALID" in error_msg
                        or "GROUPCALL" in error_msg
                        or isinstance(e, exceptions.NoActiveGroupCall)
                    ):
                        if attempt < max_retries - 1:
                            delay = retry_delay * (attempt + 1)
                            logger.warning(
                                f"Group call transitioning for {chat_id}; "
                                f"retry {attempt + 2}/{max_retries} in {delay}s"
                            )
                            await asyncio.sleep(delay)
                            continue
                        raise

                    raise

                except TransportParseException:
                    if attempt < max_retries - 1:
                        delay = retry_delay * (attempt + 1)

                        logger.warning(
                            f"Transport error for {chat_id}; retrying "
                            f"current chat only "
                            f"(attempt {attempt + 1}/{max_retries})"
                        )

                        # A transient transport parse error can happen during a
                        # brief network interruption. Do not leave the VC here:
                        # tearing down the native call can strand playback state
                        # and make recovery depend on a later rejoin.
                        logger.warning(
                            "VC_RETRY_WITHOUT_LEAVE chat_id=%s reason=TransportParseException attempt=%s",
                            chat_id,
                            attempt + 1,
                        )
                        await asyncio.sleep(delay)
                        continue

                    raise

                except TimeoutError as e:
                    if attempt < max_retries - 1:
                        delay = retry_delay * (attempt + 1)

                        logger.warning(
                            f"Voice chat timeout for {chat_id}; "
                            f"retry {attempt + 2}/{max_retries} in {delay}s"
                        )

                        try:
                            await log_call_lifecycle_snapshot(
                                "JOIN_TIMEOUT",
                                client,
                                chat_id,
                            )
                        except Exception:
                            pass

                        # A timeout during a network outage is not proof that
                        # the Telegram VC itself must be torn down. Keep the call
                        # and retry in place; final handlers preserve active state
                        # so the watchdog can recover if native state disappears.
                        logger.warning(
                            "VC_RETRY_WITHOUT_LEAVE chat_id=%s reason=TimeoutError attempt=%s",
                            chat_id,
                            attempt + 1,
                        )
                        await asyncio.sleep(delay)
                        continue

                    raise

                except Exception as e:
                    error_msg = str(e).lower()
                    # Only the known duplicate-initialization state warrants a
                    # targeted leave/rebuild. Generic connection/transport errors
                    # may be transient network failures; do not tear down the VC.
                    needs_native_reset = (
                        "cannot be initialized more than once" in error_msg
                    )
                    recoverable = (
                        needs_native_reset
                        or "connection" in error_msg
                        or "not in a call" in error_msg
                        or "transport" in error_msg
                    )

                    if recoverable:
                        # A duplicate native initialization error normally invites a
                        # leave/rebuild cycle. During an active playback session this
                        # is unsafe: a short network outage can produce the same error,
                        # and leave_call() destroys the call before the 60s grace period
                        # can protect it. Preserve the session and let the watchdog
                        # recover after the grace window instead.
                        active_session = recovery or (
                            await db.get_call(chat_id) and await db.playing(chat_id)
                        )
                        if needs_native_reset and active_session:
                            self.controller.mark_network_interruption(
                                chat_id,
                                reason="duplicate native initialization during active playback",
                            )
                            logger.warning(
                                "VC_GRACE_NO_LEAVE chat_id=%s reason=DuplicateNativeInitialization attempt=%s/%s; preserving active session",
                                chat_id,
                                attempt + 1,
                                max_retries,
                            )
                        else:
                            logger.warning(
                                "VC_RETRY_WITHOUT_LEAVE chat_id=%s reason=RecoverableCallError error=%s attempt=%s/%s",
                                chat_id,
                                error_msg,
                                attempt + 1,
                                max_retries,
                            )
                            if needs_native_reset and not active_session:
                                logger.warning(
                                    "VC_REBUILD_REASON chat_id=%s reason=DuplicateNativeInitialization without active session",
                                    chat_id,
                                )
                                try:
                                    await log_call_lifecycle_snapshot(
                                        "BEFORE_LEAVE", client, chat_id
                                    )
                                except Exception:
                                    pass
                                try:
                                    await client.leave_call(chat_id, close=False)
                                except Exception as leave_error:
                                    logger.debug(
                                        f"Duplicate-init cleanup failed for {chat_id}: {leave_error}"
                                    )

                        if attempt < max_retries - 1:
                            await asyncio.sleep(retry_delay * (attempt + 1))
                            continue

                        # Do not propagate a duplicate-init failure into the generic
                        # handler, which would stop and clear the active playback.
                        if needs_native_reset and active_session:
                            return
                    raise

            if seek_time:
                media.time = seek_time
            else:
                media.time = 1

            if not seek_time:
                await db.add_call(chat_id)
                text = _lang["play_media"].format(
                    media.url,
                    media.title,
                    media.duration,
                    media.user,
                )
                if getattr(media, "playlist_name", None):
                    pl_url = getattr(media, "playlist_url", None)
                    pl_type = getattr(media, "playlist_type", None)
                    if pl_type == "album":
                        label = "ᴀʟʙᴜᴍ"
                    elif pl_type == "artist":
                        label = "ᴀʀᴛɪꜱᴛ"
                    else:
                        label = "ᴘʟᴀʏʟɪꜱᴛ"

                    if pl_url:
                        pl_display = f"<a href={pl_url}>{media.playlist_name}</a>"
                    else:
                        pl_display = media.playlist_name

                    text = text.replace(
                        "➤ <b>ᴅᴜʀᴀᴛɪᴏɴ :</b>",
                        f"➤ <b>{label} :</b> {pl_display}\n➤ <b>ᴅᴜʀᴀᴛɪᴏɴ :</b>",
                    )
                if not getattr(media, "is_live", False) and getattr(media, "duration_sec", 0):
                    import time as time_module
                    played = media.time
                    duration = media.duration_sec
                    bar_length = 12
                    if duration == 0:
                        percentage = 0
                    else:
                        percentage = min((played / duration) * 100, 100)
                    filled = int(round(bar_length * percentage / 100))
                    timer_bar = "—" * filled + "●" + \
                        "—" * (bar_length - filled)
                    if duration >= 3600:
                        played_time = time_module.strftime(
                            '%H:%M:%S', time_module.gmtime(played))
                        total_time = time_module.strftime(
                            '%H:%M:%S', time_module.gmtime(duration))
                    else:
                        played_time = time_module.strftime(
                            '%M:%S', time_module.gmtime(played))
                        total_time = time_module.strftime(
                            '%M:%S', time_module.gmtime(duration))
                    timer_text = f"{played_time} {timer_bar} {total_time}"
                    keyboard = buttons.controls(chat_id, timer=timer_text)
                else:
                    keyboard = buttons.controls(chat_id)

                if message:
                    try:
                        await message.delete()
                    except Exception:
                        pass

                lock = self.controller.get_lock(chat_id)
                current_session = self.controller._session_gen.get(chat_id, 0)
                lock.release()
                try:
                    sent_photo = await self.controller._utils.send_photo_with_retry(
                        chat_id=chat_id,
                        photo=_thumb,
                        caption=text,
                        reply_markup=keyboard,
                    )
                finally:
                    await lock.acquire()
                    
                if self.controller._session_gen.get(chat_id, 0) != current_session:
                    logger.info(f"Session invalidated during send_photo for {chat_id}")
                    return

                if sent_photo:
                    media.message_id = sent_photo.id

                try:
                    await preload.start_preload(chat_id, count=1)
                except Exception as e:
                    logger.debug(f"Error starting preload for {chat_id}: {e}")
        except FileNotFoundError:
            if recovery:
                logger.warning(f"⚠️ Recovery file disappeared for {chat_id}")
                return
            if message:
                try:
                    await message.edit_text(_lang["error_no_file"].format(config.SUPPORT_CHAT))
                except Exception:
                    pass
            await self.controller._queue._play_next_impl(chat_id)
        except exceptions.NoActiveGroupCall:
            if recovery:
                logger.warning(f"⚠️ Recovery found no active group call for {chat_id}")
                return
            await self.controller._controls._stop_impl(chat_id)
            if message:
                try:
                    await message.edit_text(_lang["error_vc_disabled"])
                except Exception:
                    pass
        except errors.RPCError as e:
            error_str = str(e)

            if recovery:
                logger.warning(f"⚠️ Recovery RPC error for {chat_id}: {e}")
                return

            if any(x in error_str for x in ["CHAT_ADMIN_REQUIRED", "phone.CreateGroupCall", "GROUPCALL_FORBIDDEN", "GROUPCALL_CREATE_FORBIDDEN", "VOICE_MESSAGES_FORBIDDEN"]):
                await self.controller._controls._stop_impl(chat_id)
                if message:
                    try:
                        await message.edit_text(_lang["error_vc_disabled"])
                    except Exception:
                        pass
            elif "GROUPCALL_INVALID" in error_str or "GROUPCALL" in error_str:
                await self.controller._controls._stop_impl(chat_id)
                if message:
                    try:
                        await message.edit_text(_lang["error_no_call"])
                    except Exception:
                        pass
            else:
                logger.error(f"RPC error in play_media for {chat_id}: {e}")
                await self.controller._controls._stop_impl(chat_id)
        except exceptions.NoAudioSourceFound:
            if recovery:
                logger.warning(f"⚠️ Recovery found no audio source for {chat_id}")
                return
            if message:
                try:
                    await message.edit_text(_lang["error_no_audio"])
                except Exception:
                    pass
            await self.controller._queue._play_next_impl(chat_id)
        except TransportParseException as e:
            # Preserve an already-running session on transient network loss;
            # the watchdog waits 60 seconds before attempting native VC recovery.
            logger.warning(f"🌐 Voice transport unavailable for {chat_id}: {e}")
            if recovery or (await db.get_call(chat_id) and await db.playing(chat_id)):
                self.controller.mark_network_interruption(chat_id, reason=f"TransportParseException: {e}")
                logger.warning(f"⏳ Preserving active playback state for recovery: {chat_id}")
                return
            await self.controller._controls._stop_impl(chat_id)
            if message:
                try:
                    await message.edit_text(_lang["error_no_call"])
                except Exception:
                    pass
        except (ConnectionNotFound, TelegramServerError) as e:
            logger.warning(f"🌐 Telegram/native connection unavailable for {chat_id}: {e}")
            if recovery or (await db.get_call(chat_id) and await db.playing(chat_id)):
                self.controller.mark_network_interruption(chat_id, reason=f"Telegram/native connection: {e}")
                logger.warning(f"⏳ Preserving active playback state for recovery: {chat_id}")
                return
            await self.controller._controls._stop_impl(chat_id)
            if message:
                try:
                    await message.edit_text(_lang["error_tg_server"])
                except Exception:
                    pass
        except TimeoutError as e:
            error_msg = str(e)
            logger.warning(
                f"⏱️ Timeout joining voice chat {chat_id} after {max_retries} attempts: {error_msg}")
            if recovery or (await db.get_call(chat_id) and await db.playing(chat_id)):
                self.controller.mark_network_interruption(chat_id, reason=f"TimeoutError: {error_msg}")
                logger.warning(f"⏳ Keeping active VC session alive for later recovery: {chat_id}")
                return
            await log_call_lifecycle_snapshot(
                "JOIN_TIMEOUT_FINAL_BEFORE_STOP",
                client,
                chat_id,
            )
            await self.controller._controls._stop_impl(chat_id)
            await log_call_lifecycle_snapshot(
                "JOIN_TIMEOUT_FINAL_AFTER_STOP",
                client,
                chat_id,
            )
            if message:
                try:
                    await message.edit_text(
                        "⏱️ <b>ᴄᴏɴɴᴇᴄᴛɪᴏɴ ᴛɪᴍᴇᴅ ᴏᴜᴛ!</b>\n\n"
                        "<blockquote>ꜰᴀɪʟᴇᴅ ᴛᴏ ᴊᴏɪɴ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ. ᴘʟᴇᴀꜱᴇ ᴄʜᴇᴄᴋ ʏᴏᴜʀ ɴᴇᴛᴡᴏʀᴋ ᴀɴᴅ ᴛʀʏ ᴀɢᴀɪɴ.</blockquote>"
                    )
                except Exception:
                    pass
            await asyncio.sleep(2)
            await self.controller._queue._play_next_impl(chat_id)
        except Exception as e:
            error_msg = str(e).lower()
            logger.error(
                f"Unexpected error in play_media for {chat_id}: {e}", exc_info=True)
            active_session = recovery or (
                await db.get_call(chat_id) and await db.playing(chat_id)
            )
            network_error = any(token in error_msg for token in (
                "winerror 1231", "winerror 1232", "winerror 1236",
                "network is unreachable", "network location", "transport",
                "connection reset", "connection aborted", "connection timed out",
                "temporarily unavailable", "timed out",
                "cannot be initialized more than once",
            ))
            if recovery:
                return
            if active_session and network_error:
                self.controller.mark_network_interruption(chat_id, reason=f"network exception: {e}")
                logger.warning(
                    "Preserving active playback after transient network error for %s",
                    chat_id,
                )
                return
            await self.controller._controls._stop_impl(chat_id)
            if message:
                try:
                    await message.edit_text(f"❌ Playback error: {str(e)[:100]}")
                except Exception:
                    pass
