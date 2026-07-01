"""
Downloader - handles direct downloads and MixDrop via Playwright.
"""

import urllib.parse
from typing import Optional

import httpx
from tqdm import tqdm
from playwright.async_api import async_playwright

from src.config import settings
from src.logger import setup_logger

logger = setup_logger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 15; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Mobile Safari/537.36",
)
_MIN_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB


class Downloader:

    async def get_direct_url(self, source: dict) -> Optional[str]:
        """
        Resolve a source record to a direct downloadable URL.
        Returns None if the source is unavailable.
        """
        url = source["url"]
        name = source.get("server_name", "").lower()

        if "mixdrop" in name or "mixdrop" in url:
            return await self._resolve_mixdrop(url)

        if "archive.org" in url:
            return await self._resolve_archive(url)

        return url  # streamtape, vk, etc. — already direct

    # ── MixDrop ───────────────────────────────────────────────────────────────

    async def _resolve_mixdrop(self, embed_url: str) -> Optional[str]:
        target = embed_url.replace("/e/", "/f/")
        if "?download" not in target:
            target += "?download"

        logger.info(f"🕵️  MixDrop Playwright: {target}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context(user_agent=_USER_AGENT)
            page = await ctx.new_page()

            try:
                await page.goto(target, wait_until="domcontentloaded")

                content = await page.content()
                if "can't find the file you are looking for" in content:
                    logger.warning("🚫 MixDrop: file deleted (404)")
                    return None

                btn = "a.download-btn"
                max_attempts = settings.MIXDROP_MAX_CLICK_ATTEMPTS
                wait_ms = settings.MIXDROP_CLICK_WAIT_MS

                for i in range(1, max_attempts + 1):
                    try:
                        await page.wait_for_selector(
                            btn, state="visible", timeout=10_000
                        )

                        if i == max_attempts // 2:
                            logger.info("🔄 MixDrop: mid-session reload...")
                            await page.reload(wait_until="domcontentloaded")
                            await page.wait_for_timeout(3000)
                            continue

                        try:
                            async with ctx.expect_page(timeout=10_000) as new_page_info:
                                await page.click(btn)
                            ad_page = await new_page_info.value
                            await page.wait_for_timeout(5000)
                            await ad_page.close()
                        except Exception:
                            logger.debug(f"MixDrop click {i}: no ad page opened.")

                        await page.bring_to_front()
                        href = await page.get_attribute(btn, "href")

                        if href and href.startswith("http"):
                            is_valid = "mxcontent" in href or (
                                "?download" not in href and "mixdrop" not in href
                            )
                            if is_valid:
                                logger.info(
                                    f"✅ MixDrop direct URL resolved: {href[:60]}..."
                                )
                                return href

                        await page.wait_for_timeout(wait_ms)

                    except Exception as e:
                        logger.debug(f"MixDrop attempt {i} error: {e}")

                logger.warning(
                    "❌ MixDrop: could not resolve direct URL after all attempts."
                )
                return None

            finally:
                await browser.close()

    # ── Archive.org ───────────────────────────────────────────────────────────

    async def _resolve_archive(self, url: str) -> str:
        identifier = url.rstrip("/").split("/")[-1]
        api_url = f"https://archive.org/metadata/{identifier}"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(api_url)
                data = resp.json()
                files = data.get("files", [])
                mp4 = next(
                    (f["name"] for f in files if f["name"].lower().endswith(".mp4")),
                    None,
                )
                if mp4:
                    return (
                        f"https://archive.org/download/{identifier}/"
                        f"{urllib.parse.quote(mp4)}"
                    )
        except Exception as e:
            logger.warning(f"Archive.org metadata fetch failed: {e}")

        if not url.lower().endswith(".mp4"):
            return f"{url.rstrip('/')}/{identifier}.mp4"
        return url

    # ── Generic stream download ───────────────────────────────────────────────

    async def download(
        self,
        url: str,
        dest_path: str,
        max_retries: int = None,
    ) -> bool:
        retries = max_retries or settings.MAX_DOWNLOAD_RETRIES

        for attempt in range(1, retries + 1):
            try:
                logger.info(f"📥 Download attempt {attempt}/{retries}: {url[:80]}")

                async with httpx.AsyncClient(
                    timeout=None,
                    follow_redirects=True,
                    headers={"User-Agent": _USER_AGENT},
                ) as client:
                    async with client.stream("GET", url) as resp:
                        resp.raise_for_status()

                        content_type = resp.headers.get("Content-Type", "").lower()
                        if (
                            "video" not in content_type
                            and "octet-stream" not in content_type
                        ):
                            logger.warning(
                                f"❌ Unexpected content-type: {content_type}"
                            )
                            return False

                        total = int(resp.headers.get("Content-Length", 0))
                        if 0 < total < _MIN_FILE_SIZE_BYTES:
                            logger.warning(
                                f"❌ File too small: {total / 1024 / 1024:.2f} MB"
                            )
                            return False

                        with open(dest_path, "wb") as f, tqdm(
                            total=total or None,
                            unit="B",
                            unit_scale=True,
                            desc=f"📥 {dest_path}",
                            leave=False,
                        ) as bar:
                            async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                                f.write(chunk)
                                bar.update(len(chunk))

                return True

            except Exception as e:
                logger.warning(f"⚠️  Download attempt {attempt} failed: {e}")
                if attempt < retries:
                    import asyncio

                    await asyncio.sleep(5 * attempt)

        return False
