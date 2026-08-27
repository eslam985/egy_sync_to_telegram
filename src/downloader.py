"""
Downloader - handles direct downloads and MixDrop via Playwright.
"""

import urllib.parse
import random
from typing import Optional

import httpx
from tqdm import tqdm
from playwright.async_api import async_playwright

from src.config import settings
from src.logger import setup_logger

logger = setup_logger(__name__)

_USER_AGENTS = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/149.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
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

        if "streamtape" in name or "streamtape" in url:
            return await self._resolve_streamtape(url)

        if "archive.org" in url:
            return await self._resolve_archive(url)
        
        if "mixdrop" in name or "mixdrop" in url:
            return await self._resolve_mixdrop(url)

        return url  # vk, etc.

    # ── MixDrop ───────────────────────────────────────────────────────────────

    async def _resolve_mixdrop(self, embed_url: str) -> Optional[str]:
        target = embed_url.replace("/e/", "/f/")
        if "?download" not in target:
            target += "?download"

        logger.info(f"🕵️  MixDrop Playwright: {target}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-infobars",
                    "--window-size=1920,1080"
                ]
            )
            ctx = await browser.new_context(user_agent=random.choice(_USER_AGENTS))
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
# ── Streamtape ────────────────────────────────────────────────────────────

    async def _resolve_streamtape(self, embed_url: str) -> Optional[str]:
        # تحويل الرابط إلى صيغة صفحة التحميل /v/ بدلاً من الـ Embed /e/
        target = embed_url.replace("/e/", "/v/").replace("/f/", "/v/")
        logger.info(f"🕵️  Streamtape Playwright: {target}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
            ctx = await browser.new_context(user_agent=random.choice(_USER_AGENTS))
            page = await ctx.new_page()

            try:
                await page.goto(target, wait_until="domcontentloaded")

                # 1. محاولة قنص الرابط مباشرة من العنصر المخفي لتفادي الكابتشا وتأخير الضغط
                try:
                    raw_link = await page.locator("#norobotlink").text_content(timeout=5000)
                    if raw_link and "get_video" in raw_link:
                        raw_link = raw_link.strip()
                        if raw_link.startswith("//"):
                            raw_link = f"https:{raw_link}"
                        final_url = raw_link if "dl=1" in raw_link else f"{raw_link}&dl=1"
                        logger.info(f"✅ Streamtape URL extracted directly from DOM: {final_url[:60]}...")
                        return final_url
                except Exception:
                    logger.debug("Streamtape: Direct DOM extraction failed, falling back to click method...")

                # 2. الطريقة الاحتياطية (في حال عدم وجود الرابط في الـ DOM مباشرة)
                btn = "#downloadvideo"
                await page.wait_for_selector(btn, state="visible", timeout=15_000)

                # 1. الضغط على الزر مرة واحدة لتشغيل العداد الزمني (Counter) الخاص بالموقع
                try:
                    async with ctx.expect_page(timeout=5000) as new_page_info:
                        await page.click(btn)
                    # إغلاق نافذة الإنبثاق (Popup) الناتجة عن الضغطة الأولى إذا ظهرت
                    ad_page = await new_page_info.value
                    await ad_page.close()
                except Exception:
                    pass

                await page.bring_to_front()

                # 2. الانتظار لمدة 6 ثوانٍ حتى ينتهي العداد (5 ثوانٍ) ويقوم السكربت بحقن الرابط
                logger.info("⏳ Streamtape: Waiting for 5s countdown to finish...")
                await page.wait_for_timeout(6000)

                # 3. استخراج الرابط النهائي من الخاصية href للزر
                href = await page.get_attribute(btn, "href")

                if href and "get_video" in href:
                    href = href.strip()
                    final_url = f"https:{href}" if href.startswith("//") else href
                    logger.info(f"✅ Streamtape direct URL resolved: {final_url[:60]}...")
                    return final_url

                # فحص محتوى الصفحة لمعرفة سبب الفشل بدقة
                page_text = await page.inner_text("body")
                is_dead = "video no longer available" in page_text.lower() or "not found" in page_text.lower()
                
                logger.warning(f"❌ Streamtape failed. Actual href: '{href}' | Is File Deleted: {is_dead}")
                return None

            except Exception as e:
                logger.warning(f"❌ Streamtape extraction failed: {e}")
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
                    headers={"User-Agent": random.choice(_USER_AGENTS)},
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
