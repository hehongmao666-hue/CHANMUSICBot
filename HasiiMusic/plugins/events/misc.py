# ==============================================================================
# misc.py - Background Tasks & Events
# ==============================================================================
# Background jobs like auto-leaving empty calls, tracking playback time,
# updating progress bars, and handling voice chat state changes.
# ==============================================================================

import asyncio
import time

import pyrogram
from pyrogram import enums, filters, types

from HasiiMusic import tune, app, config, db, lang, logger, queue, tasks, userbot, yt, preload
from HasiiMusic.helpers import buttons


@app.on_message(filters.video_chat_started, group=19)
@app.on_message(filters.video_chat_ended, group=20)
async def _watcher_vc(_, m: types.Message):
    await tune.stop(m.chat.id)


async def track_time():
    while True:
        try:
            await asyncio.sleep(1)
            for chat_id in list(db.active_calls):
                try:
                    if not await db.playing(chat_id):
                        continue
                    media = queue.get_current(chat_id)
                    if not media:
                        continue
                    media.time += 1
                except Exception as e:
                    logger.debug(f"track_time error for chat {chat_id}: {e}")
                    continue
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Critical error in track_time task: {e}")
            await asyncio.sleep(1)


async def update_timer(length=10):
    # One timer task per active chat.
    chat_tasks = {}

    async def update_chat_timer(chat_id):
        try:
            while True:
                await asyncio.sleep(20)

                # Stop immediately when playback is no longer active.
                if chat_id not in db.active_calls:
                    break

                if not await db.playing(chat_id):
                    break

                media = queue.get_current(chat_id)
                if not media:
                    break

                if not hasattr(media, "time") or media.time is None:
                    media.time = 0

                duration = media.duration_sec
                message_id = media.message_id

                if not duration or not message_id:
                    continue

                played = media.time
                remaining = duration - played

                bar_length = 12
                percentage = min((played / duration) * 100, 100) if duration else 0
                filled = int(round(bar_length * percentage / 100))
                timer_bar = "—" * filled + "●" + "—" * (bar_length - filled)

                if remaining <= 30:
                    await preload.start_preload(chat_id, count=1)

                if remaining < 10:
                    remove = True
                    timer_text = timer_bar
                else:
                    remove = False
                    if duration >= 3600:
                        played_time = time.strftime("%H:%M:%S", time.gmtime(played))
                        total_time = time.strftime("%H:%M:%S", time.gmtime(duration))
                    else:
                        played_time = time.strftime("%M:%S", time.gmtime(played))
                        total_time = time.strftime("%M:%S", time.gmtime(duration))
                    timer_text = f"{played_time} {timer_bar} {total_time}"

                await app.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=message_id,
                    reply_markup=buttons.controls(
                        chat_id=chat_id,
                        timer=timer_text,
                        remove=remove,
                    ),
                )

        except asyncio.CancelledError:
            raise

        except Exception as e:
            error_str = str(e)

            # IMPORTANT:
            # Never retry Telegram FloodWait in a tight loop.
            # Stop this chat's timer and let the next valid playback
            # create a fresh timer.
            if "FLOOD_WAIT" in error_str:
                logger.warning(
                    f"update_timer stopped for chat {chat_id}: Telegram Flood Wait: {e}"
                )
                return

            # These errors mean this timer should no longer continue.
            stop_errors = (
                "MESSAGE_ID_INVALID",
                "MESSAGE_DELETE",
                "MESSAGE_AUTHOR_REQUIRED",
                "CHANNEL_PRIVATE",
                "haven't joined this channel",
            )
            if any(err in error_str for err in stop_errors):
                logger.debug(
                    f"update_timer stopped for chat {chat_id}: {e}"
                )
                return

            if "MESSAGE_NOT_MODIFIED" in error_str:
                # Harmless: the markup is already identical.
                return

            if "CHAT_ADMIN_REQUIRED" in error_str:
                logger.warning(
                    f"update_timer stopped for chat {chat_id}: CHAT_ADMIN_REQUIRED"
                )
                return

            logger.warning(f"update_timer stopped for chat {chat_id}: {e}")

    try:
        while True:
            await asyncio.sleep(2)

            # Spawn timers only for currently active + playing chats.
            for chat_id in list(db.active_calls):
                try:
                    if chat_id in chat_tasks:
                        continue

                    if not await db.playing(chat_id):
                        continue

                    task = asyncio.create_task(
                        update_chat_timer(chat_id),
                        name=f"update_timer:{chat_id}",
                    )
                    chat_tasks[chat_id] = task

                except Exception as e:
                    logger.debug(
                        f"update_timer setup error for chat {chat_id}: {e}"
                    )

            # Clean up finished / inactive timers.
            finished_chats = []

            for chat_id, task in list(chat_tasks.items()):
                if task.done() or chat_id not in db.active_calls:
                    finished_chats.append(chat_id)

            for chat_id in finished_chats:
                task = chat_tasks.pop(chat_id, None)
                if task is not None and not task.done():
                    task.cancel()

                # Consume task exceptions so they never become
                # "Task exception was never retrieved".
                if task is not None and task.done():
                    try:
                        task.result()
                    except asyncio.CancelledError:
                        pass
                    except Exception as e:
                        logger.debug(
                            f"update_timer task cleanup for chat {chat_id}: {e}"
                        )

    except asyncio.CancelledError:
        # Shutdown: cancel every child timer.
        for task in chat_tasks.values():
            if not task.done():
                task.cancel()

        if chat_tasks:
            await asyncio.gather(
                *chat_tasks.values(),
                return_exceptions=True,
            )

        chat_tasks.clear()
        raise


async def vc_watcher(sleep=15):
    alone_times = {}
    LEAVE_TIMEOUT = 1200

    while True:
        try:
            await asyncio.sleep(sleep)
            current_time = time.time()

            for chat_id in list(db.active_calls):
                try:
                    if not config.AUTO_LEAVE:
                        alone_times.pop(chat_id, None)
                        continue

                    client = await db.get_assistant(chat_id)

                    try:
                        participants = await client.get_participants(chat_id)
                    except Exception:
                        alone_times.pop(chat_id, None)
                        continue

                    if len(participants) < 2:
                        if chat_id not in alone_times:
                            alone_times[chat_id] = current_time
                        else:
                            alone_duration = current_time - alone_times[chat_id]
                            if alone_duration >= LEAVE_TIMEOUT:
                                _lang = await lang.get_lang(chat_id)
                                try:
                                    current_media = queue.get_current(chat_id)
                                    if current_media and current_media.message_id:
                                        sent = await app.edit_message_reply_markup(
                                            chat_id=chat_id,
                                            message_id=current_media.message_id,
                                            reply_markup=buttons.controls(
                                                chat_id=chat_id,
                                                status=_lang["stopped"],
                                                remove=True,
                                            ),
                                        )
                                        await sent.reply_text(_lang["auto_left"])
                                except Exception:
                                    pass

                                await tune.stop(chat_id)
                                alone_times.pop(chat_id, None)
                    else:
                        alone_times.pop(chat_id, None)

                except Exception as e:
                    print(f"vc_watcher error for chat {chat_id}: {e}")
                    alone_times.pop(chat_id, None)
                    continue

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Critical error in vc_watcher task: {e}")
            await asyncio.sleep(1)


# Always run VC watcher to check for empty voice chats
tasks.append(asyncio.create_task(vc_watcher()))
tasks.append(asyncio.create_task(track_time()))
tasks.append(asyncio.create_task(update_timer()))
