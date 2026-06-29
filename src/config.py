"""
Configuration - All settings loaded from environment variables (Secrets).
Never hardcode credentials in source code.
"""

import os
from dataclasses import dataclass, field
from typing import List


def _require(key: str) -> str:
    """Fetch a required env variable or raise a clear error."""
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"❌ Required environment variable '{key}' is not set. "
            f"Add it to HuggingFace Space Secrets."
        )
    # إزالة أي مسافات أو أسطر جديدة نهائياً من داخل النص ومن أطرافه، ثم حذف علامات التنصيص
    cleaned_value = "".join(value.split()).strip("'\"")
    print(f"⚙️ [DEBUG] {key} length: {len(cleaned_value)}")
    return cleaned_value


@dataclass(frozen=True)
class Settings:
    # ── Supabase ──────────────────────────────────────────────────────────────
    SUPABASE_URL: str = field(default_factory=lambda: _require("SUPABASE_URL"))
    SUPABASE_KEY: str = field(default_factory=lambda: _require("SUPABASE_KEY"))

    # ── Telegram ──────────────────────────────────────────────────────────────
    TELEGRAM_API_ID: int = field(default_factory=lambda: int(_require("TELEGRAM_API_ID")))
    TELEGRAM_API_HASH: str = field(default_factory=lambda: _require("TELEGRAM_API_HASH"))
    TELEGRAM_SESSION: str = field(default_factory=lambda: _require("TELEGRAM_SESSION"))
    TELEGRAM_TARGET_CHAT: str = field(default_factory=lambda: _require("TELEGRAM_TARGET_CHAT"))

    # ── Sync behaviour ────────────────────────────────────────────────────────
    POLL_INTERVAL_SECONDS: int = field(
        default_factory=lambda: int(os.getenv("POLL_INTERVAL_SECONDS", "3600"))
    )
    MAX_DOWNLOAD_RETRIES: int = field(
        default_factory=lambda: int(os.getenv("MAX_DOWNLOAD_RETRIES", "3"))
    )
    MAX_VIDEO_SIZE_GB: float = field(
        default_factory=lambda: float(os.getenv("MAX_VIDEO_SIZE_GB", "1.9"))
    )
    DB_PAGE_SIZE: int = field(
        default_factory=lambda: int(os.getenv("DB_PAGE_SIZE", "1000"))
    )
    DB_MAX_ROWS: int = field(
        default_factory=lambda: int(os.getenv("DB_MAX_ROWS", "15000"))
    )

    # ── Source servers priority (comma-separated in env) ──────────────────────
    SOURCE_SERVERS: List[str] = field(
        default_factory=lambda: os.getenv(
            "SOURCE_SERVERS", "streamtape,mixdrop,vk,download"
        ).split(",")
    )

    # ── Playwright ────────────────────────────────────────────────────────────
    MIXDROP_MAX_CLICK_ATTEMPTS: int = field(
        default_factory=lambda: int(os.getenv("MIXDROP_MAX_CLICK_ATTEMPTS", "10"))
    )
    MIXDROP_CLICK_WAIT_MS: int = field(
        default_factory=lambda: int(os.getenv("MIXDROP_CLICK_WAIT_MS", "5000"))
    )

    # ── Telegram upload ───────────────────────────────────────────────────────
    UPLOAD_WAIT_SECONDS: int = field(
        default_factory=lambda: int(os.getenv("UPLOAD_WAIT_SECONDS", "12"))
    )
    SESSION_FILE: str = field(
        default_factory=lambda: os.getenv("SESSION_FILE", "egy_sync_session")
    )


# Singleton - imported everywhere
settings = Settings()
