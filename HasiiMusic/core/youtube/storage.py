# ==============================================================================
# storage.py - YouTube Storage Manager
# ==============================================================================
# This file manages the filesystem cache for downloaded audio/video.
# Features:
# - Validates cached files against 0-byte stubs
# - Locates existing downloads by video ID
# - Cleans up corrupted stubs
# - Supports MP4/MKV/MOV as audio fallback containers
# ==============================================================================

import os
import glob
from pathlib import Path
from typing import Optional

from HasiiMusic import logger


class StorageManager:
    def __init__(self, min_valid_bytes: int = 4096):
        self.MIN_VALID_BYTES = min_valid_bytes

    def is_valid_file(self, path: str) -> bool:
        """
        Return True only if path exists, is a real file,
        and has enough content.

        Guards against 0-byte stubs and partial writes.
        """
        try:
            return (
                os.path.isfile(path)
                and os.path.getsize(path) >= self.MIN_VALID_BYTES
            )
        except OSError:
            return False

    def delete_stub(self, path: str) -> None:
        """
        Delete an invalid/corrupt file stub so a fresh download
        is triggered next time.
        """
        try:
            os.remove(path)

            logger.warning(
                f"🗑️ Deleted invalid cached file "
                f"(too small or corrupt): {path}"
            )

        except OSError:
            pass

    def locate_download_file(
        self,
        video_id: str,
        video: bool = False
    ) -> Optional[str]:

        pattern = f"downloads/{video_id}*"

        candidates = sorted(
            [
                path
                for path in glob.glob(pattern)
                if not path.endswith(
                    (
                        ".part",
                        ".ytdl",
                        ".info.json",
                        ".temp"
                    )
                )
            ]
        )

        # ======================================================================
        # Supported video containers
        # ======================================================================

        video_exts = {
            ".mp4",
            ".mkv",
            ".mov"
        }

        # ======================================================================
        # Supported audio containers
        # ======================================================================

        audio_exts = {
            ".m4a",
            ".webm",
            ".opus",
            ".mp3",
            ".ogg",
            ".wav",
            ".flac"
        }

        # ======================================================================
        # VIDEO MODE
        # ======================================================================

        if video:

            for path in candidates:

                if os.path.isdir(path):
                    continue

                extension = Path(path).suffix.lower()

                if extension in video_exts:

                    if self.is_valid_file(path):

                        logger.info(
                            f"🎬 Located video file: {path}"
                        )

                        return path

                    self.delete_stub(path)

        # ======================================================================
        # AUDIO MODE
        # ======================================================================

        else:

            # ------------------------------------------------------------------
            # First: look for native audio formats
            # ------------------------------------------------------------------

            for path in candidates:

                if os.path.isdir(path):
                    continue

                extension = Path(path).suffix.lower()

                if extension in audio_exts:

                    if self.is_valid_file(path):

                        logger.info(
                            f"🎵 Located audio file: {path}"
                        )

                        return path

                    self.delete_stub(path)

            # ------------------------------------------------------------------
            # IMPORTANT:
            #
            # yt-dlp can sometimes return an MP4 container even when the bot
            # requested audio mode.
            #
            # Therefore MP4/MKV/MOV must be accepted as an audio fallback.
            # ------------------------------------------------------------------

            for path in candidates:

                if os.path.isdir(path):
                    continue

                extension = Path(path).suffix.lower()

                if extension in video_exts:

                    if self.is_valid_file(path):

                        logger.info(
                            f"🎵 Located audio fallback container: {path}"
                        )

                        return path

                    self.delete_stub(path)

        # ======================================================================
        # Nothing found
        # ======================================================================

        return None