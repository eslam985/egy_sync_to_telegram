"""
Telegram uploader - handles sending files via Telethon.
"""

import asyncio
import re
import sys
from typing import Optional

from telethon import TelegramClient
from telethon.sessions import StringSession

from src.config import settings
from src.logger import setup_logger

logger = setup_logger(__name__)


def _progress_callback(current: int, total: int) -> None:
    percent = (current / total) * 100
    curr_mb = current // (1024 * 1024)
    tot_mb = total // (1024 * 1024)
    sys.stdout.write(f"\r📤 Uploading: {percent:.1f}% ({curr_mb} MB / {tot_mb} MB)")
    sys.stdout.flush()


class TelegramUploader:
    def __init__(self, client: TelegramClient):
        self._client = client

    async def upload_and_get_link(
        self,
        file_path: str,
        caption: str,
        episode_id: int,
    ) -> Optional[str]:
        """
        Upload file to Saved Messages, forward to target chat,
        then extract the HF stream URL from the bot's reply.
        Returns the URL string or None.
        """
        target = settings.TELEGRAM_TARGET_CHAT
        wait = settings.UPLOAD_WAIT_SECONDS

        logger.info(f"📤 Uploading '{file_path}' to Telegram...")

        async with self._client.action(target, "document"):
            sent = await self._client.send_file(
                "me",
                file_path,
                caption=caption,
                progress_callback=_progress_callback,
            )
            sys.stdout.write("\n")  # newline after progress bar

            await sent.forward_to(target)
            logger.info(f"⏳ Waiting {wait}s for bot to process...")
            await asyncio.sleep(wait)

        # Scan recent messages for the HF stream link
        return await self._extract_hf_link(target)

    async def _extract_hf_link(self, chat: str) -> Optional[str]:
        async for message in self._client.iter_messages(chat, limit=10):
            if message.text and "hf.space" in message.text:
                match = re.search(r"(https?://[^\s`]+hf\.space[^\s`]+)", message.text)
                if match:
                    url = match.group(1).strip().rstrip("`")
                    logger.info(f"🔗 HF link captured: {url[:70]}")
                    return url
        logger.warning("⚠️  No HF link found in recent messages.")
        return None
