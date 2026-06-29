"""
Video splitter - splits files exceeding Telegram's upload limit.
Uses ffmpeg stream-copy (no re-encoding, lossless split).
"""

import math
import os
import subprocess
from typing import List

from src.config import settings
from src.logger import setup_logger

logger = setup_logger(__name__)


class VideoSplitter:

    def split(self, input_path: str) -> List[str]:
        """
        If the file fits within MAX_VIDEO_SIZE_GB, return [input_path].
        Otherwise split into equal-duration parts and return their paths.
        """
        max_bytes = settings.MAX_VIDEO_SIZE_GB * 1024 ** 3
        file_size = os.path.getsize(input_path)

        if file_size <= max_bytes:
            size_mb = file_size / 1024 ** 2
            logger.info(f"✅ File size OK ({size_mb:.1f} MB), no split needed.")
            return [input_path]

        num_parts = math.ceil(file_size / max_bytes)
        logger.info(f"📦 File too large — splitting into {num_parts} parts...")

        duration = self._get_duration(input_path)
        if duration is None:
            logger.error("❌ Cannot determine video duration; skipping split.")
            return [input_path]

        part_duration = duration / num_parts
        base = os.path.splitext(input_path)[0]
        output_files: List[str] = []

        for i in range(num_parts):
            start = i * part_duration
            out = f"{base}_part{i + 1}.mp4"

            cmd = [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-t", str(part_duration),
                "-i", input_path,
                "-c", "copy",
                "-map", "0",
                out,
            ]

            logger.info(f"🎬 Extracting part {i + 1}/{num_parts}...")
            result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

            if result.returncode != 0:
                logger.error(f"ffmpeg error on part {i + 1}: {result.stderr.decode()[:200]}")
            else:
                output_files.append(out)

        return output_files if output_files else [input_path]

    @staticmethod
    def _get_duration(path: str) -> float | None:
        try:
            out = subprocess.check_output(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    path,
                ],
                stderr=subprocess.DEVNULL,
            )
            return float(out.decode().strip())
        except Exception as e:
            logger.warning(f"ffprobe failed: {e}")
            return None
