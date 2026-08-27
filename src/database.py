"""
EgySync - Telegram Video Sync Bot
Entry point for HuggingFace Spaces deployment.
"""

import httpx

# إجبار المكتبة عالمياً على إغلاق HTTP/2 وتفعيل HTTP/1.1 المستقر لمنع سقوط اتصال سوبابيس
def _patch_httpx_client(client_class):
    orig_init = client_class.__init__
    def patched_init(self, *args, **kwargs):
        kwargs["http2"] = False
        orig_init(self, *args, **kwargs)
    client_class.__init__ = patched_init

_patch_httpx_client(httpx.Client)
_patch_httpx_client(httpx.AsyncClient)

import asyncio
import logging
import sys
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from src.sync_engine import SyncEngine
from src.config import settings
from src.logger import setup_logger

logger = setup_logger(__name__)


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK")
        
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        
    def log_message(self, format, *args):
        pass

def run_health_server():
    try:
        server = HTTPServer(('0.0.0.0', 7860), HealthCheckHandler)
        server.serve_forever()
    except Exception:
        pass


async def main():
    logger.info("🚀 EgySync starting on HuggingFace Space...")
    logger.info(f"📋 Config: poll_interval={settings.POLL_INTERVAL_SECONDS}s, max_retries={settings.MAX_DOWNLOAD_RETRIES}")

    
    logger.info("🌐 Installing Playwright Chromium browser...")
    subprocess.run(["python", "-m", "playwright", "install", "chromium"], check=True)

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
    threading.Thread(target=run_health_server, daemon=True).start()
    asyncio.run(main())
