"""
EgySync - Telegram Video Sync Bot
Entry point for HuggingFace Spaces deployment.
"""

import asyncio
import logging
import sys

from src.sync_engine import SyncEngine
from src.config import settings
from src.logger import setup_logger

logger = setup_logger(__name__)


async def main():
    logger.info("🚀 EgySync starting on HuggingFace Space...")
    logger.info(f"📋 Config: poll_interval={settings.POLL_INTERVAL_SECONDS}s, max_retries={settings.MAX_DOWNLOAD_RETRIES}")

    engine = SyncEngine()

    try:
        await engine.start()
    except KeyboardInterrupt:
        logger.info("⛔ Shutdown signal received.")
    except Exception as e:
        logger.critical(f"💥 Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        await engine.stop()
        logger.info("🔌 Engine stopped gracefully.")


if __name__ == "__main__":
    asyncio.run(main())
