# ==============================================================================
# download.py - YouTube Downloader Engine
# ==============================================================================
# Features:
# - YouTube audio/video downloading
# - Spotify/search lazy resolution
# - Per-video download locks
# - Download concurrency control
# - Low-memory optimization for Render
# - Render Secret File cookie support
# - Local cookies.txt fallback
# - Automatic YouTube cookie failure detection
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

        # Maximum 2 simultaneous yt-dlp downloads.
        self._download_semaphore = asyncio.Semaphore(2)

        # Prevent duplicate downloads of the same video.
        self._download_locks: dict = {}

        self._max_video_height = getattr(
            config,
            "VIDEO_MAX_HEIGHT",
            1080
        )

    # ==========================================================================
    # COOKIE PATH
    # ==========================================================================

    def _get_cookie_path(self) -> Optional[str]:
        """
        Get YouTube cookies path.

        Priority:
        1. Render Secret File:
           /etc/secrets/cookies.txt

        2. Local/project file:
           ./cookies.txt

        This allows:
        - Production -> Render Secret File
        - Local development -> cookies.txt
        """

        # ----------------------------------------------------------------------
        # Render Secret File
        # ----------------------------------------------------------------------

        secret_cookie = "/etc/secrets/cookies.txt"

        if os.path.isfile(secret_cookie):

            logger.info(
                "🍪 Using YouTube cookies from Render Secret File"
            )

            return secret_cookie

        # ----------------------------------------------------------------------
        # Local fallback
        # ----------------------------------------------------------------------

        local_cookie = os.path.abspath(
            "cookies.txt"
        )

        if os.path.isfile(local_cookie):

            logger.info(
                f"🍪 Using local YouTube cookies: {local_cookie}"
            )

            return local_cookie

        # ----------------------------------------------------------------------
        # No cookie found
        # ----------------------------------------------------------------------

        logger.warning(
            "⚠️ YouTube cookies.txt was not found. "
            "YouTube playback may fail if authentication is required."
        )

        return None

    # ==========================================================================
    # COOKIE OPTIONS
    # ==========================================================================

    def _get_cookie_options(self) -> dict:
        """
        Return yt-dlp cookie options only when a valid cookie file exists.
        """

        cookie_path = self._get_cookie_path()

        if not cookie_path:
            return {}

        return {
            "cookiefile": cookie_path
        }

    # ==========================================================================
    # YOUTUBE COOKIE FAILURE DETECTION
    # ==========================================================================

    def _is_cookie_error(self, error_message: str) -> bool:
        """
        Detect common YouTube authentication / anti-bot errors.

        This does NOT automatically refresh cookies.
        It only identifies when the current cookies are likely invalid
        or no longer sufficient.
        """

        if not error_message:
            return False

        error_lower = error_message.lower()

        cookie_error_keywords = (

            # Authentication
            "login required",
            "sign in to confirm",
            "sign in to verify",

            # YouTube anti-bot
            "not a bot",
            "confirm you're not a bot",
            "confirm you are not a bot",

            # Cookie invalidation
            "cookies are no longer valid",
            "cookie has expired",
            "cookies have expired",
            "authentication required",

            # Account-related
            "account cookies",
            "logged in",

            # Some YouTube extraction failures
            "requested video is not available"
        )

        return any(
            keyword in error_lower
            for keyword in cookie_error_keywords
        )

    # ==========================================================================
    # COOKIE FAILURE LOG
    # ==========================================================================

    def _log_cookie_failure(self, error_message: str) -> None:
        """
        Print a clear message to Render logs when cookies appear invalid.
        """

        logger.error(
            "🍪 =================================================="
        )

        logger.error(
            "🍪 YouTube Cookie may be expired or invalid."
        )

        logger.error(
            "🍪 Please update the Render Secret File:"
        )

        logger.error(
            "🍪 /etc/secrets/cookies.txt"
        )

        logger.error(
            "🍪 Do NOT put cookies.txt into Git."
        )

        logger.error(
            "🍪 =================================================="
        )

    # ==========================================================================
    # PER VIDEO LOCK
    # ==========================================================================

    def _get_download_lock(
        self,
        video_id: str
    ) -> asyncio.Lock:

        if video_id not in self._download_locks:

            self._download_locks[video_id] = (
                asyncio.Lock()
            )

        return self._download_locks[video_id]

    # ==========================================================================
    # MAIN DOWNLOAD
    # ==========================================================================

    async def download(
        self,
        video_id: str,
        is_live: bool = False,
        video: bool = False
    ) -> Optional[str]:

        # ==========================================================================
        # LAZY RESOLVE
        # ==========================================================================

        if not re.fullmatch(
            r"[A-Za-z0-9_-]{11}",
            video_id
        ):

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
        # COOKIE OPTIONS
        # ==========================================================================

        cookie_opts = self._get_cookie_options()

        # ==========================================================================
        # LIVE STREAM
        # ==========================================================================

        if is_live:

            ydl_opts = {

                "quiet": True,

                "no_warnings": True,

                "format": "bestaudio/best",

                "noplaylist": True,

                "socket_timeout": 20,

                "extractor_retries": 5,

                "sleep_interval_requests": 1,

                # Current YouTube JS challenge support.
                "js_runtimes": {
                    "node": {}
                },

                **cookie_opts,
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

                        direct = info.get(
                            "url"
                        )

                        if direct:
                            return direct

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

                        if self._is_cookie_error(
                            error_msg
                        ):

                            self._log_cookie_failure(
                                error_msg
                            )

                        elif "not available" in error_msg.lower():

                            logger.error(
                                "❌ Video format not available "
                                "or region-blocked."
                            )

                        else:

                            logger.error(
                                "❌ Live stream URL extraction failed: %s",
                                ex
                            )

                        return None

                    except Exception as ex:

                        logger.error(
                            "❌ Unexpected error during "
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
                    "❌ Live stream URL extraction timed out "
                    "for %s",
                    video_id
                )

                return None

            return stream_url

        # ==========================================================================
        # FILE CACHE
        # ==========================================================================

        filename_pattern = (
            f"downloads/{video_id}"
        )

        def _check_cache() -> Optional[str]:

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
            # VIDEO
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
            # AUDIO
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
                # AUDIO FALLBACK
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
        # FAST CACHE CHECK
        # ==========================================================================

        cached = _check_cache()

        if cached:

            logger.info(
                f"📦 Using cached file: {cached}"
            )

            return cached

        # ==========================================================================
        # DOWNLOAD DIRECTORY
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

        async with self._get_download_lock(
            video_id
        ):

            cached = _check_cache()

            if cached:

                logger.info(
                    f"📦 Using cached file after lock: {cached}"
                )

                return cached

            # ==================================================================
            # MEMORY-LIMITED DOWNLOAD
            # ==================================================================

            async with self._download_semaphore:

                # ==============================================================
                # BASE YT-DLP OPTIONS
                # ==============================================================

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

                    # ==========================================================
                    # MEMORY OPTIMIZATION
                    # ==========================================================

                    "concurrent_fragment_downloads":
                        1,

                    "http_chunk_size":
                        262144,

                    # ==========================================================
                    # NETWORK
                    # ==========================================================

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

                    # ==========================================================
                    # YOUTUBE JS CHALLENGE
                    # ==========================================================

                    "js_runtimes": {
                        "node": {}
                    },

                    # ==========================================================
                    # COOKIES
                    # ==========================================================

                    **cookie_opts,
                }

                # ==================================================================
                # VIDEO MODE
                # ==================================================================

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

                # ==================================================================
                # AUDIO MODE
                # ==================================================================

                else:

                    ydl_opts = {

                        **base_opts,

                        "format":
                            "bestaudio/best",

                        "postprocessors":
                            [],
                    }

                # ==================================================================
                # DOWNLOAD FUNCTION
                # ==================================================================

                def _download(
                    ydl_runtime_opts
                ):

                    ydl_instance = None

                    try:

                        ydl_instance = (
                            yt_dlp.YoutubeDL(
                                ydl_runtime_opts
                            )
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

                        # Allow filesystem writes to finish.
                        time.sleep(0.5)

                        # ======================================================
                        # LOCATE FILE
                        # ======================================================

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

                        # ======================================================
                        # FALLBACK CACHE CHECK
                        # ======================================================

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

                    # ==========================================================
                    # EXTRACTOR ERROR
                    # ==========================================================

                    except yt_dlp.utils.ExtractorError as ex:

                        error_msg = str(ex)

                        if self._is_cookie_error(
                            error_msg
                        ):

                            self._log_cookie_failure(
                                error_msg
                            )

                        elif "age" in error_msg.lower():

                            logger.error(
                                "❌ Age-restricted video: "
                                "Cookies may be required."
                            )

                        elif "not available" in error_msg.lower():

                            logger.error(
                                "❌ Video not available: "
                                "May be region-blocked or private."
                            )

                        else:

                            logger.error(
                                "❌ YouTube extraction failed: %s",
                                ex
                            )

                        return None

                    # ==========================================================
                    # DOWNLOAD ERROR
                    # ==========================================================

                    except yt_dlp.utils.DownloadError as ex:

                        error_msg = str(ex)

                        # ------------------------------------------------------
                        # Detect cookie failure
                        # ------------------------------------------------------

                        if self._is_cookie_error(
                            error_msg
                        ):

                            self._log_cookie_failure(
                                error_msg
                            )

                        # ------------------------------------------------------
                        # Try to recover downloaded file
                        # ------------------------------------------------------

                        recovered = (
                            self._storage.locate_download_file(
                                video_id,
                                video=video
                            )
                        )

                        # ------------------------------------------------------
                        # Rename error
                        # ------------------------------------------------------

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

                        # ------------------------------------------------------
                        # HTTP 416
                        # ------------------------------------------------------

                        if (
                            "416" in error_msg
                            or
                            "requested range not satisfiable"
                            in error_msg.lower()
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

                    # ==========================================================
                    # UNEXPECTED ERROR
                    # ==========================================================

                    except Exception as ex:

                        logger.warning(
                            f"⚠️ Unexpected download error for "
                            f"{video_id}: {ex}"
                        )

                        return None

                    # ==========================================================
                    # CLEANUP
                    # ==========================================================

                    finally:

                        if ydl_instance:

                            try:

                                ydl_instance.close()

                            except Exception:

                                pass

                # ==================================================================
                # RUN IN THREAD
                # ==================================================================

                return await asyncio.to_thread(
                    _download,
                    ydl_opts
                )