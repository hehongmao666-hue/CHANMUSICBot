# ==============================================================================
# download.py - YouTube Downloader Engine
# ==============================================================================
# This file orchestrates the downloading of audio and video from YouTube.
#
# Features:
# - Limits concurrency using semaphores
# - Prevents duplicate concurrent downloads using locks
# - Executes yt-dlp to download streams and files
# - Resolves Spotify fallbacks lazily
# - Uses cookies.txt for YouTube authentication
# - Uses Node.js JS runtime for YouTube challenge solving
# - Optimized for low-memory environments such as Render 512MB
# ==============================================================================

import re
import glob
import time
import os
import asyncio
from pathlib import Path
from typing import Optional

import yt_dlp

from HasiiMusic import config, logger


class Downloader:

    def __init__(self, cookies_manager, storage_manager, searcher):

        self._cookies = cookies_manager
        self._storage = storage_manager
        self._searcher = searcher

        # ==========================================================================
        # MEMORY OPTIMIZATION
        # ==========================================================================
        # Limit simultaneous yt-dlp downloads.
        #
        # Previous:
        #     Semaphore(5)
        #
        # New:
        #     Semaphore(2)
        #
        # This significantly reduces RAM usage when multiple users request
        # songs/videos at the same time.
        # ==========================================================================

        self._download_semaphore = asyncio.Semaphore(2)

        # Prevent the same YouTube video from being downloaded multiple times
        # simultaneously.
        self._download_locks: dict = {}

        self._max_video_height = getattr(
            config,
            "VIDEO_MAX_HEIGHT",
            1080
        )

    # ==========================================================================
    # PER VIDEO LOCK
    # ==========================================================================

    def _get_download_lock(self, video_id: str) -> asyncio.Lock:

        if video_id not in self._download_locks:
            self._download_locks[video_id] = asyncio.Lock()

        return self._download_locks[video_id]

    # ==========================================================================
    # DOWNLOAD
    # ==========================================================================

    async def download(
        self,
        video_id: str,
        is_live: bool = False,
        video: bool = False
    ) -> Optional[str]:

        # ==========================================================================
        # LAZY RESOLUTION
        # ==========================================================================
        # Resolve:
        # - Search query
        # - Spotify URL
        # - Spotify track
        #
        # into a YouTube video ID.
        # ==========================================================================

        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):

            try:

                from HasiiMusic import spotify

                if spotify.valid(video_id):

                    resolved = await spotify.search(
                        video_id,
                        0
                    )

                else:

                    resolved = await self._searcher.search(
                        video_id,
                        0
                    )

                if resolved and resolved.id:

                    video_id = resolved.id

                    is_live = getattr(
                        resolved,
                        "is_live",
                        is_live
                    )

                else:

                    logger.warning(
                        f"Could not resolve '{video_id}' for download"
                    )

                    return None

            except Exception as e:

                logger.warning(
                    f"Failed to lazily resolve '{video_id}': {e}"
                )

                return None

        # ==========================================================================
        # YOUTUBE URL
        # ==========================================================================

        url = (
            "https://www.youtube.com/watch?v="
            + video_id
        )

        # ==========================================================================
        # COOKIES
        # ==========================================================================
        # Use absolute path so Render can reliably locate cookies.txt.
        # ==========================================================================

        cookie_path = os.path.abspath(
            "cookies.txt"
        )

        logger.info(
            f"🍪 Using cookies from: {cookie_path}"
        )

        # ==========================================================================
        # LIVE STREAM
        # ==========================================================================

        if is_live:

            ydl_opts = {

                "quiet": True,

                "no_warnings": True,

                "cookiefile": cookie_path,

                "format": "bestaudio/best",

                "noplaylist": True,

                "socket_timeout": 20,

                "extractor_retries": 5,

                "sleep_interval_requests": 1,

                # IMPORTANT:
                # Node.js is required by current YouTube extraction.
                "js_runtimes": {
                    "node": {}
                },
            }

            def _extract_url():

                with yt_dlp.YoutubeDL(
                    ydl_opts
                ) as ydl:

                    try:

                        info = ydl.extract_info(
                            url,
                            download=False
                        )

                        if not info:
                            return None

                        # Direct URL
                        direct = info.get("url")

                        if direct:
                            return direct

                        # Search formats
                        for fmt in info.get(
                            "formats",
                            []
                        ):

                            if (
                                fmt.get("acodec") != "none"
                                and fmt.get("url")
                            ):

                                return fmt["url"]

                        return info.get(
                            "manifest_url"
                        )

                    except yt_dlp.utils.ExtractorError as ex:

                        error_msg = str(ex)

                        if "not available" in error_msg.lower():

                            logger.error(
                                "Video format not available "
                                "or region-blocked."
                            )

                        else:

                            logger.error(
                                "Live stream URL extraction failed: %s",
                                ex
                            )

                        return None

                    except Exception as ex:

                        logger.error(
                            "Unexpected error during "
                            "live stream extraction: %s",
                            ex
                        )

                        return None

            try:

                stream_url = await asyncio.wait_for(
                    asyncio.to_thread(
                        _extract_url
                    ),
                    timeout=35
                )

            except asyncio.TimeoutError:

                logger.error(
                    "Live stream URL extraction timed out "
                    "for %s",
                    video_id
                )

                return None

            return stream_url

        # ==========================================================================
        # DOWNLOAD FILE NAME
        # ==========================================================================

        filename_pattern = (
            f"downloads/{video_id}"
        )

        # ==========================================================================
        # CACHE CHECK
        # ==========================================================================

        def _check_cache() -> Optional[str]:

            """
            Check downloads/ for a valid existing file.

            Invalid small files are deleted automatically.
            """

            existing_files = [

                f

                for f in glob.glob(
                    f"{filename_pattern}.*"
                )

                if not f.endswith(
                    (
                        ".part",
                        ".ytdl",
                        ".temp"
                    )
                )
            ]

            # ==================================================================
            # VIDEO MODE
            # ==================================================================

            if video:

                video_candidates = [

                    f

                    for f in existing_files

                    if Path(f).suffix.lower()
                    in {
                        ".mp4",
                        ".mkv",
                        ".mov"
                    }
                ]

                for f in video_candidates:

                    if self._storage.is_valid_file(f):

                        return f

                    self._storage.delete_stub(f)

            # ==================================================================
            # AUDIO MODE
            # ==================================================================

            else:

                audio_candidates = [

                    f

                    for f in existing_files

                    if Path(f).suffix.lower()
                    in {
                        ".m4a",
                        ".webm",
                        ".opus",
                        ".mp3",
                        ".ogg",
                        ".wav",
                        ".flac"
                    }
                ]

                for f in audio_candidates:

                    if self._storage.is_valid_file(f):

                        return f

                    self._storage.delete_stub(f)

                # --------------------------------------------------------------
                # MP4/MKV/MOV FALLBACK
                # --------------------------------------------------------------
                # yt-dlp can sometimes return a container such as MP4 even when
                # audio mode was requested.
                #
                # StorageManager also supports this fallback.
                # --------------------------------------------------------------

                container_fallbacks = [

                    f

                    for f in existing_files

                    if Path(f).suffix.lower()
                    in {
                        ".mp4",
                        ".mkv",
                        ".mov"
                    }
                ]

                for f in container_fallbacks:

                    if self._storage.is_valid_file(f):

                        return f

                    self._storage.delete_stub(f)

            return None

        # ==========================================================================
        # FAST CACHE PATH
        # ==========================================================================

        cached = _check_cache()

        if cached:

            logger.info(
                f"📦 Using cached file: {cached}"
            )

            return cached

        # ==========================================================================
        # CREATE DOWNLOAD DIRECTORY
        # ==========================================================================

        downloads_dir = Path(
            "downloads"
        )

        if not downloads_dir.exists():

            try:

                downloads_dir.mkdir(
                    parents=True,
                    exist_ok=True
                )

                logger.info(
                    "📁 Created downloads directory"
                )

            except Exception as e:

                logger.error(
                    f"❌ Cannot create downloads directory: {e}"
                )

                return None

        # ==========================================================================
        # PER VIDEO LOCK
        # ==========================================================================

        async with self._get_download_lock(video_id):

            # Check cache again after acquiring lock.
            # Another request may have completed the same download.

            cached = _check_cache()

            if cached:

                logger.info(
                    f"📦 Using cached file after lock: {cached}"
                )

                return cached

            # ======================================================================
            # DOWNLOAD SEMAPHORE
            # ======================================================================
            # MEMORY OPTIMIZATION:
            #
            # Maximum simultaneous downloads = 2
            #
            # This is one of the most important changes for Render 512MB.
            # ======================================================================

            async with self._download_semaphore:

                # ==================================================================
                # BASE YT-DLP OPTIONS
                # ==================================================================

                base_opts = {

                    "outtmpl":
                        "downloads/%(id)s.%(ext)s",

                    "quiet":
                        True,

                    "noplaylist":
                        True,

                    "geo_bypass":
                        True,

                    "no_warnings":
                        True,

                    "overwrites":
                        False,

                    "nocheckcertificate":
                        True,

                    "continuedl":
                        True,

                    "noprogress":
                        True,

                    # ==================================================================
                    # MEMORY / NETWORK OPTIMIZATION
                    # ==================================================================
                    #
                    # Previous:
                    #     concurrent_fragment_downloads = 4
                    #
                    # New:
                    #     concurrent_fragment_downloads = 1
                    #
                    # This reduces simultaneous fragment processing and memory usage.
                    # ==================================================================

                    "concurrent_fragment_downloads":
                        1,

                    # Previous:
                    #     524288 (512KB)
                    #
                    # New:
                    #     262144 (256KB)
                    #
                    # Smaller chunks reduce peak memory pressure.
                    # ==================================================================

                    "http_chunk_size":
                        262144,

                    "socket_timeout":
                        30,

                    "retries":
                        2,

                    "fragment_retries":
                        2,

                    "extractor_retries":
                        5,

                    "sleep_interval_requests":
                        1,

                    # ==================================================================
                    # YOUTUBE JS CHALLENGE
                    # ==================================================================

                    "js_runtimes": {
                        "node": {}
                    },

                    # ==================================================================
                    # YOUTUBE COOKIES
                    # ==================================================================

                    "cookiefile":
                        cookie_path,
                }

                # ======================================================================
                # VIDEO OPTIONS
                # ======================================================================

                if video:

                    height_filter = ""

                    if (
                        self._max_video_height
                        and self._max_video_height > 0
                    ):

                        height_filter = (
                            f"[height<={self._max_video_height}]"
                        )

                    format_chain = (

                        f"bestvideo[ext=mp4]"
                        f"{height_filter}"
                        "+bestaudio[ext=m4a]/"

                        f"bestvideo"
                        f"{height_filter}"
                        "+bestaudio/"

                        "bestvideo+bestaudio/"
                        "best"
                    )

                    ydl_opts = {

                        **base_opts,

                        "format":
                            format_chain,

                        "merge_output_format":
                            "mp4",

                        "postprocessors": [

                            {
                                "key":
                                    "FFmpegVideoConvertor",

                                "preferedformat":
                                    "mp4",
                            }

                        ],
                    }

                # ======================================================================
                # AUDIO OPTIONS
                # ======================================================================

                else:

                    ydl_opts = {

                        **base_opts,

                        "format":
                            "bestaudio/best",

                        "postprocessors":
                            [],
                    }

                # ======================================================================
                # ACTUAL DOWNLOAD
                # ======================================================================

                def _download(
                    ydl_runtime_opts
                ):

                    ydl_instance = None

                    try:

                        ydl_instance = yt_dlp.YoutubeDL(
                            ydl_runtime_opts
                        )

                        info = ydl_instance.extract_info(
                            url,
                            download=True
                        )

                        if not info:

                            logger.error(
                                f"❌ Failed to extract info "
                                f"for {video_id}"
                            )

                            return None

                        # Small delay to make sure filesystem writes are complete.
                        time.sleep(0.5)

                        # Locate downloaded file.
                        located = (
                            self._storage.locate_download_file(
                                video_id,
                                video=video
                            )
                        )

                        if located:

                            logger.info(
                                f"✅ Download ready: {located}"
                            )

                            return located

                        # ------------------------------------------------------------------
                        # FALLBACK CHECK
                        # ------------------------------------------------------------------
                        # This protects against cases where StorageManager cannot
                        # immediately detect the output file.
                        # ------------------------------------------------------------------

                        fallback = _check_cache()

                        if fallback:

                            logger.info(
                                f"✅ Download ready via fallback: "
                                f"{fallback}"
                            )

                            return fallback

                        logger.error(
                            f"❌ Download completed but file not found "
                            f"for: {video_id}"
                        )

                        return None

                    # ==================================================================
                    # EXTRACTOR ERROR
                    # ==================================================================

                    except yt_dlp.utils.ExtractorError as ex:

                        error_msg = str(ex)

                        if "not available" in error_msg.lower():

                            logger.error(
                                "❌ Video not available: "
                                "May be region-blocked or private."
                            )

                        elif "age" in error_msg.lower():

                            logger.error(
                                "❌ Age-restricted video: "
                                "Cookies required."
                            )

                        else:

                            logger.error(
                                "❌ YouTube extraction failed: %s",
                                ex
                            )

                        return None

                    # ==================================================================
                    # DOWNLOAD ERROR
                    # ==================================================================

                    except yt_dlp.utils.DownloadError as ex:

                        error_msg = str(ex)

                        recovered = (
                            self._storage.locate_download_file(
                                video_id,
                                video=video
                            )
                        )

                        # ------------------------------------------------------------------
                        # RENAME ERROR
                        # ------------------------------------------------------------------

                        if (
                            "unable to rename file"
                            in error_msg.lower()
                            and recovered
                        ):

                            logger.warning(
                                f"⚠️ Renaming failed for {video_id}, "
                                f"using recovered file "
                                f"{Path(recovered).name}"
                            )

                            return recovered

                        # ------------------------------------------------------------------
                        # HTTP RANGE ERROR
                        # ------------------------------------------------------------------

                        if (
                            "416" in error_msg
                            or
                            "Requested range not satisfiable"
                            in error_msg
                        ):

                            logger.warning(
                                f"⚠️ Range error for {video_id}, skipping"
                            )

                        else:

                            logger.warning(
                                f"⚠️ Download error for {video_id}: "
                                f"{ex}"
                            )

                            if recovered:

                                logger.warning(
                                    f"⚠️ Using recovered file for "
                                    f"{video_id} despite download error"
                                )

                                return recovered

                        return None

                    # ==================================================================
                    # UNEXPECTED ERROR
                    # ==================================================================

                    except Exception as ex:

                        logger.warning(
                            f"⚠️ Unexpected download error for "
                            f"{video_id}: {ex}"
                        )

                        return None

                    # ==================================================================
                    # CLEANUP
                    # ==================================================================

                    finally:

                        if ydl_instance:

                            try:

                                ydl_instance.close()

                            except Exception:

                                pass

                # ======================================================================
                # RUN DOWNLOAD IN THREAD
                # ======================================================================

                return await asyncio.to_thread(
                    _download,
                    ydl_opts
                )