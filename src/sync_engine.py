"""
SyncEngine - orchestrates the full pipeline:
  1. Fetch next pending episode from DB (with distributed lock)
  2. Download video (MixDrop / Archive / direct)
  3. Split if > 1.9 GB
  4. Upload to Telegram & capture HF stream URL
  5. Update DB with real URL
  6. If no tasks, sleep POLL_INTERVAL then retry indefinitely
"""

import asyncio
import os
from typing import Optional

from telethon import TelegramClient
from telethon.sessions import StringSession

from src.config import settings
from src.database import Database
from src.downloader import Downloader
from src.splitter import VideoSplitter
from src.uploader import TelegramUploader
from src.logger import setup_logger

logger = setup_logger(__name__)


class SyncEngine:
    def __init__(self):
        self._db = Database()
        self._downloader = Downloader()
        self._splitter = VideoSplitter()
        self._client = TelegramClient(
            StringSession(settings.TELEGRAM_SESSION),
            settings.TELEGRAM_API_ID,
            settings.TELEGRAM_API_HASH,
            sequential_updates=True,
        )
        self._uploader: Optional[TelegramUploader] = None
        self._failed_ids: set[int] = set()
        # طابور يتسع لملف واحد فقط كحد أقصى في الانتظار
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._producer_task: Optional[asyncio.Task] = None
        self._consumer_task: Optional[asyncio.Task] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        await self._client.start()
        self._uploader = TelegramUploader(self._client)
        logger.info("✅ Telegram client connected.")
        await self._run_loop()

    async def stop(self) -> None:
        if self._producer_task and not self._producer_task.done():
            self._producer_task.cancel()
        if self._consumer_task and not self._consumer_task.done():
            self._consumer_task.cancel()

        if self._client.is_connected():
            await self._client.disconnect()
        logger.info("🔌 Telegram client disconnected.")
        
    async def _ensure_connected(self) -> None:
        if not self._client.is_connected():
            logger.warning("🔌 Telegram client disconnected during long operation. Reconnecting...")
            await self._client.connect()
    # ── Main loop ─────────────────────────────────────────────────────────────

    # ── Main Loop (Pipelining Orchestrator) ───────────────────────────────────

    async def _run_loop(self) -> None:
        """
        Runs Producer (Downloader) and Consumer (Uploader) concurrently.
        """
        self._producer_task = asyncio.create_task(self._producer_loop())
        self._consumer_task = asyncio.create_task(self._consumer_loop())

        try:
            await asyncio.gather(self._producer_task, self._consumer_task)
        except asyncio.CancelledError:
            pass

    # ── Producer (Downloader) ─────────────────────────────────────────────────

    async def _producer_loop(self) -> None:
        """
        Fetches tasks from DB and downloads them into the queue.
        Blocks when queue reaches maxsize.
        """
        while True:
            task = await self._db.get_next_task(self._failed_ids)

            if task is None:
                interval = settings.POLL_INTERVAL_SECONDS
                logger.info(
                    f"✅ [Producer] No pending tasks found. "
                    f"Sleeping {interval // 60} min before next check..."
                )
                await asyncio.sleep(interval)
                continue

            ep_id = task["episode_id"]
            raw_title = task.get("title", "Unknown")
            ep_num = task.get("ep_num") or task.get("episode_number", 1)
            season_num = (
                task.get("season_number")
                if task.get("season_number") is not None
                else task.get("season_num")
            )
            fake_url = task["fake_url"]

            if season_num is not None:
                display_title = f"{raw_title} - الموسم {season_num} - الحلقة {ep_num}"
            else:
                display_title = raw_title

            temp_file = f"sync_{ep_id}.mp4"

            logger.info("=" * 60)
            logger.info(f"📥 [Producer] Downloading Task: {display_title} (ep_id={ep_id})")

            try:
                downloaded = await self._download_first_available(
                    task["sources"], temp_file
                )

                if not downloaded:
                    logger.warning(
                        f"⚠️ [Producer] All sources failed for episode {ep_id}. Releasing lock."
                    )
                    self._db.release_lock(fake_url)
                    self._failed_ids.add(ep_id)
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                    continue

                payload = {
                    "task": task,
                    "temp_file": temp_file,
                    "display_title": display_title,
                    "ep_id": ep_id,
                    "fake_url": fake_url,
                }

                # سيتوقف المنتج هنا تلقائياً إذا كان الطابور ممتلئاً برقم maxsize
                await self._queue.put(payload)
                logger.info(f"📦 [Producer] Pushed {display_title} to upload queue.")

            except Exception as e:
                logger.error(
                    f"❌ [Producer] Unexpected error on episode {ep_id}: {e}",
                    exc_info=True,
                )
                self._db.release_lock(fake_url)
                self._failed_ids.add(ep_id)
                if os.path.exists(temp_file):
                    os.remove(temp_file)

    # ── Consumer (Uploader) ───────────────────────────────────────────────────

    async def _consumer_loop(self) -> None:
        """
        Pulls downloaded tasks from the queue, splits if needed, uploads to Telegram,
        updates DB, and cleans up storage.
        """
        while True:
            item = await self._queue.get()

            task = item["task"]
            temp_file = item["temp_file"]
            display_title = item["display_title"]
            ep_id = item["ep_id"]
            fake_url = item["fake_url"]

            logger.info(f"📤 [Consumer] Processing Upload: {display_title} (ep_id={ep_id})")

            try:
                # 1. Split if needed
                parts = self._splitter.split(temp_file)

                # 2. Upload each part
                for idx, part_path in enumerate(parts):
                    await self._upload_part(
                        part_path=part_path,
                        episode_id=ep_id,
                        display_title=display_title,
                        part_index=idx,
                        total_parts=len(parts),
                        fake_url=fake_url,
                    )
                    if part_path != temp_file and os.path.exists(part_path):
                        os.remove(part_path)

            except Exception as e:
                logger.error(
                    f"❌ [Consumer] Upload error on episode {ep_id}: {e}",
                    exc_info=True,
                )
                self._db.release_lock(fake_url)
                self._failed_ids.add(ep_id)
            finally:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                self._queue.task_done()

    async def _download_first_available(self, sources: list, dest_path: str) -> bool:
        # إعادة ترتيب القائمة ديناميكياً لتقديم سيرفر streamtape أولاً إن وجد
        sorted_sources = sorted(
            sources,
            key=lambda x: 0 if x.get("server_name", "").lower() == "streamtape" else 1
        )
        
        for source in sorted_sources:
            server = source.get("server_name", "unknown")
            logger.info(f"📡 Trying source: {server}")

            direct_url = await self._downloader.get_direct_url(source)
            if not direct_url:
                logger.warning(f"   → Could not resolve direct URL for {server}")
                continue

            success = await self._downloader.download(direct_url, dest_path)
            if success:
                logger.info(f"   → Downloaded successfully from {server}")
                return True

            logger.warning(f"   → Download failed from {server}")

        return False

    async def _upload_part(
        self,
        part_path: str,
        episode_id: int,
        display_title: str,
        part_index: int,
        total_parts: int,
        fake_url: str,
    ) -> None:
        is_first = part_index == 0
        is_multi = total_parts > 1

        part_label = f" (الجزء {part_index + 1})" if is_multi else ""
        server_name = (
            f"telegram_direct_P{part_index + 1}" if is_multi else "telegram_direct"
        )
        quality = f"720p - Part {part_index + 1}" if is_multi else "720p"
        caption = f"🎬 {display_title}{part_label} | ID: {episode_id}"

        max_retries = 3
        hf_url = None
        for attempt in range(1, max_retries + 1):
            try:
                await self._ensure_connected()
                hf_url = await self._uploader.upload_and_get_link(
                    file_path=part_path,
                    caption=caption,
                    episode_id=episode_id,
                )
                break
            except Exception as e:
                logger.warning(f"⚠️ upload attempt {attempt}/{max_retries} failed for part {part_index + 1}: {e}")
                if attempt == max_retries:
                    raise e
                await asyncio.sleep(5)
                if self._client.is_connected():
                    await self._client.disconnect()
                await self._client.connect()

        if not hf_url:
            logger.warning(
                f"⚠️  No URL captured for part {part_index + 1}. Skipping DB update."
            )
            return

        if is_first:
            # Replace the placeholder lock with the real URL
            self._db.confirm_link(
                fake_url=fake_url,
                real_url=hf_url,
                server_name=server_name,
                quality=quality,
            )
            logger.info(f"✅ DB updated (part 1 confirmed).")
        else:
            self._db.insert_extra_part(
                episode_id=episode_id,
                url=hf_url,
                server_name=server_name,
                quality=quality,
            )
            logger.info(f"✅ DB updated (extra part {part_index + 1} inserted).")
