"""
# ==============================================================================
# queue.py - Calls Queue and Transition Manager
# ==============================================================================
# This file handles playback transitions and queue management.
# Features:
# - Plays the next track when current ends
# - Handles loop mode logic
# - Lazily auto-fetches large Spotify playlists in the background
# ==============================================================================
"""

import asyncio
import re
import unicodedata
from difflib import SequenceMatcher
from pyrogram import errors
from HasiiMusic import app, config, db, lang, logger, preload, queue, yt

class CallQueue:
    def __init__(self, controller):
        self.controller = controller
        self._playlist_tasks = {}

    async def replay(self, chat_id: int) -> None:
        try:
            if not await db.get_call(chat_id):
                return

            media = queue.get_current(chat_id)
            _lang = await lang.get_lang(chat_id)
            msg = await app.send_message(chat_id=chat_id, text=_lang["play_again"])
            await self.controller.play_media(chat_id, msg, media)
        except Exception as e:
            logger.error(f"Error in replay for {chat_id}: {e}", exc_info=True)

    async def play_next(self, chat_id: int, expected_index: int = None) -> None:
        if chat_id in self.controller._stopping:
            self.controller._pending_transitions.discard(chat_id)
            return
        if not await db.get_call(chat_id):
            self.controller._pending_transitions.discard(chat_id)
            return

        lock = self.controller.get_lock(chat_id)
        async with lock:
            if chat_id in self.controller._stopping or not await db.get_call(chat_id):
                self.controller._pending_transitions.discard(chat_id)
                return
            self.controller._pending_transitions.discard(chat_id)
            if expected_index is not None and self.controller._track_index.get(chat_id, 0) != expected_index:
                logger.info(f"Skipping stale play_next for {chat_id}")
                return
            
            self.controller._track_index[chat_id] = self.controller._track_index.get(chat_id, 0) + 1
            await self._play_next_impl(chat_id)

    async def _play_next_impl(self, chat_id: int) -> None:
        try:
            if not await db.get_call(chat_id):
                return

            loop_mode = await db.get_loop(chat_id)

            if loop_mode == 1:
                media = queue.get_current(chat_id)
                if media:
                    _lang = await lang.get_lang(chat_id)
                    try:
                        msg = await app.send_message(chat_id=chat_id, text=_lang["play_again"])
                        await self.controller._player._play_media_impl(chat_id, msg, media)
                    except errors.ChannelPrivate:
                        logger.warning(
                            f"Bot removed from {chat_id}, cleaning up")
                        try:
                            await self.controller._controls._stop_impl(chat_id)
                        except (AttributeError, Exception) as leave_ex:
                            logger.debug(
                                f"Could not leave call for {chat_id}: {leave_ex}")
                        await db.rm_chat(chat_id)
                    return

            previous_media = queue.get_current(chat_id)
            media = queue.get_next(chat_id)

            if not media and loop_mode == 10:
                all_items = queue.get_all(chat_id)
                if all_items:
                    first_track = all_items[0]
                    _lang = await lang.get_lang(chat_id)
                    try:
                        msg = await app.send_message(chat_id=chat_id, text="🔁 Looping queue...")
                        if not first_track.file_path:
                            is_live = getattr(first_track, 'is_live', False)
                            lock = self.controller.get_lock(chat_id)
                            current_session = self.controller._session_gen.get(chat_id, 0)
                            lock.release()
                            try:
                                first_track.file_path = await yt.download(
                                    first_track.id,
                                    is_live=is_live,
                                    video=getattr(first_track, 'video', False),
                                )
                            finally:
                                await lock.acquire()
                            
                            if self.controller._session_gen.get(chat_id, 0) != current_session:
                                logger.info(f"Session invalidated during looping download for {chat_id}")
                                return
                            if queue.get_current(chat_id) != first_track:
                                logger.info(f"Queue altered during looping download for {chat_id}")
                                return
                        first_track.message_id = msg.id
                        await self.controller._player._play_media_impl(chat_id, msg, first_track)
                    except errors.ChannelPrivate:
                        logger.warning(
                            f"Bot removed from {chat_id}, cleaning up")
                        await self.controller._controls._stop_impl(chat_id)
                        await db.rm_chat(chat_id)
                    return

            try:
                if media and media.message_id:
                    await app.delete_messages(
                        chat_id=chat_id,
                        message_ids=media.message_id,
                        revoke=True,
                    )
                    media.message_id = 0
            except Exception as e:
                logger.debug(
                    f"Could not delete previous message in {chat_id}: {e}")

            if not media:
                # Autoplay: when the queue is exhausted, choose a genuinely
                # different related track and continue in the same voice chat.
                # Loop modes above intentionally take priority over autoplay.
                if await db.get_autoplay(chat_id) and previous_media:
                    try:
                        recent = self.controller._autoplay_recent.setdefault(
                            chat_id, __import__("collections").deque(maxlen=12)
                        )
                        failed = self.controller._autoplay_failed.setdefault(
                            chat_id, set()
                        )

                        def _norm(value: str) -> str:
                            value = unicodedata.normalize("NFKC", value or "").lower()
                            value = re.sub(
                                r"\b(official\s*(audio|video)|lyrics?|visualizer|hd|4k)\b",
                                " ",
                                value,
                            )
                            value = re.sub(r"[^\w\u3400-\u9fff\uac00-\ud7a3]+", " ", value)
                            return re.sub(r"\s+", " ", value).strip()

                        previous_title = _norm(getattr(previous_media, "title", ""))
                        previous_id = getattr(previous_media, "id", None)
                        queue_ids = {
                            getattr(item, "id", None)
                            for item in queue.get_queue(chat_id)
                        }
                        recent_ids = {item[0] for item in recent if item}
                        recent_titles = {item[1] for item in recent if len(item) > 1}

                        def _same_song(candidate) -> bool:
                            cid = getattr(candidate, "id", None)
                            if not cid or cid == previous_id or cid in queue_ids:
                                return True
                            if cid in failed or cid in recent_ids:
                                return True

                            ctitle = _norm(getattr(candidate, "title", ""))
                            if not ctitle or ctitle == previous_title:
                                return True
                            if (
                                min(len(ctitle), len(previous_title)) >= 8
                                and (
                                    ctitle in previous_title
                                    or previous_title in ctitle
                                )
                            ):
                                return True
                            if ctitle in recent_titles:
                                return True

                            # Search results frequently return the same recording
                            # with slightly different punctuation/channel suffixes.
                            # Reject near-identical titles but allow same-artist songs.
                            if SequenceMatcher(None, ctitle, previous_title).ratio() >= 0.78:
                                return True
                            return False

                        title = getattr(previous_media, "title", "") or ""
                        channel = getattr(previous_media, "channel_name", "") or ""

                        # Prefer artist/channel-level discovery instead of searching
                        # the exact finished title first. Exact-title searches are
                        # what caused the observed autoplay loop.
                        artist_hint = re.split(
                            r"\s[-–—|:/]\s", title, maxsplit=1
                        )[0].strip()
                        if not artist_hint:
                            artist_hint = channel

                        queries = []
                        if artist_hint:
                            queries.extend([
                                f"{artist_hint} similar songs",
                                f"{artist_hint} best songs",
                            ])
                        if title:
                            queries.append(f"{title} related songs")
                        if channel and channel.lower() != artist_hint.lower():
                            queries.append(f"{channel} songs")

                        recommendation = None
                        seen_candidates = set()

                        for query in queries:
                            if not query:
                                continue
                            candidates = await yt.search_candidates(
                                query, 0, music=True, limit=5
                            )
                            for candidate in candidates:
                                cid = getattr(candidate, "id", None)
                                if cid in seen_candidates:
                                    continue
                                seen_candidates.add(cid)
                                if _same_song(candidate):
                                    continue
                                if getattr(candidate, "is_live", False):
                                    continue
                                recommendation = candidate
                                break
                            if recommendation:
                                break

                        if recommendation:
                            recommendation.user = None
                            recommendation.message_id = 0
                            recommendation.file_path = None
                            queue.add(chat_id, recommendation)
                            media = queue.get_current(chat_id)
                            recent.append((
                                getattr(media, "id", None),
                                _norm(getattr(media, "title", "")),
                            ))
                            logger.info(
                                f"🤖 Autoplay selected '{getattr(media, 'title', 'unknown')}' "
                                f"for {chat_id}"
                            )
                        else:
                            logger.info(
                                f"🤖 Autoplay found no new recommendation for {chat_id}; "
                                "keeping the current session safe instead of repeating a track"
                            )
                    except Exception as e:
                        logger.warning(f"Autoplay search failed for {chat_id}: {e}")

                if not media:
                    if config.QUEUE_END_MESSAGE:
                        _lang = await lang.get_lang(chat_id)
                        try:
                            await app.send_message(
                                chat_id=chat_id,
                                text=_lang.get(
                                    "queue_end_message", "✅ Queue finished. Stream ended automatically.")
                            )
                        except Exception as e:
                            logger.debug(
                                f"Could not send queue_end message in {chat_id}: {e}")
                    return await self.controller._controls._stop_impl(chat_id)

                # Remove the previous track's status message before sending
                # the autoplay track message below.
                if previous_media and previous_media.message_id:
                    try:
                        await app.delete_messages(
                            chat_id=chat_id,
                            message_ids=previous_media.message_id,
                            revoke=True,
                        )
                        previous_media.message_id = 0
                    except Exception as e:
                        logger.debug(
                            f"Could not delete previous autoplay message in {chat_id}: {e}")

            _lang = await lang.get_lang(chat_id)
            msg = None

            if not re.fullmatch(r"[A-Za-z0-9_-]{11}", media.id) or not getattr(media, "thumbnail", None):
                try:
                    # use YouTube Music search for spotify tracks, regular YT for everything else
                    is_spotify_track = getattr(media, "playlist_type", None) in ("playlist", "album", "artist")
                    resolved = await yt.search(media.id, 0, music=is_spotify_track)
                    if resolved:
                        media.id = resolved.id
                        if resolved.thumbnail:
                            media.thumbnail = resolved.thumbnail
                        if resolved.duration_sec:
                            media.duration_sec = resolved.duration_sec
                            media.duration = resolved.duration
                except Exception:
                    pass

            if not media.file_path:
                is_live = getattr(media, 'is_live', False)
                lock = self.controller.get_lock(chat_id)
                current_session = self.controller._session_gen.get(chat_id, 0)
                lock.release()
                try:
                    media.file_path = await yt.download(
                        media.id,
                        is_live=is_live,
                        video=getattr(media, 'video', False),
                    )
                finally:
                    await lock.acquire()

                if self.controller._session_gen.get(chat_id, 0) != current_session:
                    logger.info(f"Session invalidated during play_next download for {chat_id}")
                    return
                if queue.get_current(chat_id) != media:
                    logger.info(f"Queue altered during play_next download for {chat_id}")
                    return
                if not media.file_path:
                    failed_id = getattr(media, "id", None)
                    if failed_id:
                        self.controller._autoplay_failed.setdefault(
                            chat_id, set()
                        ).add(failed_id)

                    logger.warning(
                        f"Skipping unplayable track '{getattr(media, 'title', 'unknown')}' "
                        f"in {chat_id}"
                    )

                    # Remove/advance through the failed item. If autoplay is
                    # enabled, let the normal exhausted-queue path search for
                    # another candidate instead of retrying the same bad ID.
                    if len(queue.get_queue(chat_id)) > 1 or await db.get_autoplay(chat_id):
                        return await self._play_next_impl(chat_id)

                    await self.controller._controls._stop_impl(chat_id)
                    if msg:
                        try:
                            await msg.edit_text(
                                _lang["error_no_file"].format(
                                    config.SUPPORT_CHAT)
                            )
                        except Exception:
                            pass
                    return

            try:
                msg = await app.send_message(chat_id=chat_id, text=_lang["play_next"])
            except errors.FloodWait as fw:
                # keep playback running even if the status message hits FloodWait
                logger.warning(
                    f"FloodWait in play_next for {chat_id}: skipping status message ({fw.value}s)")
                msg = None
            except errors.ChannelPrivate:
                logger.warning(f"Bot removed from {chat_id}, cleaning up")
                await self.controller._controls._stop_impl(chat_id)
                await db.rm_chat(chat_id)
                return
            except Exception as e:
                logger.error(
                    f"Failed to send play_next message for {chat_id}: {e}")
                msg = None

            media.message_id = msg.id if msg else 0
            if msg:
                await self.controller._player._play_media_impl(chat_id, msg, media)
            else:
                logger.info(
                    f"Playing next track for {chat_id} without message update")
                await self.controller._player._play_media_impl(chat_id, None, media)

            try:
                await preload.start_preload(chat_id, count=1)
            except Exception as e:
                logger.debug(
                    f"Error starting preload after play_next for {chat_id}: {e}")

            # Autoload next playlist batch in background when reaching track 28 (up to PLAYLIST_MAX)
            pl_url = getattr(media, "playlist_url", None)
            pl_idx = getattr(media, "playlist_index", 0)
            pl_limit = getattr(config, "PLAYLIST_LIMIT", 30)
            pl_max = getattr(config, "PLAYLIST_MAX", 100)
            trigger_mod = max(1, pl_limit - 2) if pl_limit > 2 else 0
            if pl_url and pl_idx > 0 and pl_idx % pl_limit == (trigger_mod % pl_limit):
                next_offset = (pl_idx // pl_limit + 1) * pl_limit
                if next_offset < pl_max:
                    batch_limit = min(pl_limit, pl_max - next_offset)
                    self._schedule_playlist_batch(
                        chat_id=chat_id,
                        playlist_url=pl_url,
                        user=media.user,
                        offset=next_offset,
                        limit=batch_limit,
                    )
        except Exception as e:
            logger.error(
                f"Error in play_next for {chat_id}: {e}", exc_info=True)
            try:
                await self.controller._controls._stop_impl(chat_id)
            except Exception:
                pass

    def _schedule_playlist_batch(
        self, chat_id: int, playlist_url: str, user: str, offset: int, limit: int
    ) -> None:
        existing = self._playlist_tasks.get(chat_id)
        if existing is not None and not existing.done():
            return
        generation = self.controller._session_gen.get(chat_id, 0)
        task = asyncio.create_task(
            self._fetch_next_playlist_batch(
                chat_id=chat_id, playlist_url=playlist_url, user=user,
                offset=offset, limit=limit, generation=generation,
            ),
            name=f"playlist_batch:{chat_id}",
        )
        self._playlist_tasks[chat_id] = task
        task.add_done_callback(
            lambda done, cid=chat_id: self._playlist_task_done(cid, done)
        )

    def _playlist_task_done(self, chat_id: int, task: asyncio.Task) -> None:
        if self._playlist_tasks.get(chat_id) is task:
            self._playlist_tasks.pop(chat_id, None)

    async def cancel_playlist_tasks(self, chat_id: int) -> None:
        task = self._playlist_tasks.pop(chat_id, None)
        if task is None:
            return
        if not task.done() and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _fetch_next_playlist_batch(
        self, chat_id: int, playlist_url: str, user: str, offset: int, limit: int = 30,
        generation: int | None = None,
    ) -> None:
        try:
            from HasiiMusic import spotify, queue
            if spotify.valid(playlist_url) and spotify.is_playlist(playlist_url):
                if chat_id in self.controller._stopping or not await db.get_call(chat_id):
                    return
                if generation is not None and self.controller._session_gen.get(chat_id, 0) != generation:
                    return
                next_tracks = await spotify.playlist(limit, user, playlist_url, offset=offset)
                if next_tracks:
                    if chat_id in self.controller._stopping or not await db.get_call(chat_id):
                        return
                    if generation is not None and self.controller._session_gen.get(chat_id, 0) != generation:
                        return
                    for track in next_tracks:
                        queue.add(chat_id, track)
                    logger.info(
                        f"📋 Autoloaded next {len(next_tracks)} playlist tracks (offset {offset}) for chat {chat_id}"
                    )
        except Exception as e:
            logger.debug(f"Could not autoload next playlist batch for {chat_id}: {e}")
