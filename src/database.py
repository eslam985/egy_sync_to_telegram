"""
Database layer - all Supabase interactions in one place.
"""

import random
import asyncio
from typing import Optional

from supabase import create_client, Client

from src.config import settings
from src.logger import setup_logger

logger = setup_logger(__name__)


class Database:
    def __init__(self):
        self._client: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

    # ── Episode queries ───────────────────────────────────────────────────────

    def fetch_episode_page(self, offset: int) -> list:
        res = (
            self._client.table("episodes")
            .select("id, episode_number, media_id, seasons(season_number), medias(title), links(server_name)")
            .range(offset, offset + settings.DB_PAGE_SIZE - 1)
            .execute()
        )
        return res.data or []

    def fetch_episode_sources(self, episode_id: int) -> list:
        res = (
            self._client.table("links")
            .select("*")
            .eq("episode_id", episode_id)
            .execute()
        )
        return res.data or []

    def has_pending_tasks(self) -> bool:
        """Quick check: are there any episodes without telegram_direct links?"""
        for offset in range(0, settings.DB_MAX_ROWS, settings.DB_PAGE_SIZE):
            episodes = self.fetch_episode_page(offset)
            if not episodes:
                break
            for ep in episodes:
                existing = ep.get("links", [])
                locked = any(
                    "telegram_direct" in str(l.get("server_name", "")).lower()
                    for l in existing
                )
                if not locked:
                    return True
        return False

    # ── Link mutations ────────────────────────────────────────────────────────

    def insert_lock(self, episode_id: int, fake_url: str) -> bool:
        """Try to acquire a distributed lock via unique constraint. Returns True on success."""
        try:
            self._client.table("links").insert(
                {
                    "episode_id": episode_id,
                    "url": fake_url,
                    "server_name": "telegram_direct",
                    "quality": "720p",
                    "last_check_status": "processing",
                }
            ).execute()
            return True
        except Exception:
            return False

    def release_lock(self, fake_url: str) -> None:
        """Remove the lock record (used when download fails)."""
        try:
            self._client.table("links").delete().eq("url", fake_url).execute()
        except Exception as e:
            logger.warning(f"Failed to release lock for {fake_url}: {e}")

    def confirm_link(
        self,
        fake_url: str,
        real_url: str,
        server_name: str,
        quality: str,
    ) -> None:
        self._client.table("links").update(
            {
                "url": real_url,
                "server_name": server_name,
                "quality": quality,
                "last_check_status": "valid",
            }
        ).eq("url", fake_url).execute()

    def insert_extra_part(
        self,
        episode_id: int,
        url: str,
        server_name: str,
        quality: str,
    ) -> None:
        self._client.table("links").insert(
            {
                "episode_id": episode_id,
                "url": url,
                "server_name": server_name,
                "quality": quality,
                "last_check_status": "valid",
            }
        ).execute()

    # ── Task discovery ────────────────────────────────────────────────────────

    async def get_next_task(self, failed_ids: set) -> Optional[dict]:
        """حجز حلقة واحدة ذرياً لمنع الـ Race Condition وتقليل طلبات الشبكة."""
        try:
            response = self._client.rpc(
                "claim_telegram_sync_task",
                {
                    "p_failed_ids": list(failed_ids),
                    "p_source_servers": settings.SOURCE_SERVERS,
                }
            ).execute()

            if not response.data:
                return None

            task = response.data
            logger.info(f"🔒 Locked episode {task['episode_id']} via RPC")
            return task
        except Exception as e:
            logger.error(f"❌ خطأ أثناء حجز المهمة عبر RPC: {e}")
            return None