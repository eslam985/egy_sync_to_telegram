---
title: EgySync
emoji: 🎬
colorFrom: blue
colorTo: purple
sdk: gradio
sdk_version: "5.15.0"
app_file: app.py
pinned: false
---

# EgySync — Telegram Video Sync Bot

Automatically syncs video episodes from streaming servers (MixDrop, StreamTape, etc.)
to Telegram, then saves the HuggingFace stream URLs in Supabase.

## ⚙️ Required Secrets (Space Settings → Secrets)

| Secret Name              | Description                                    |
|--------------------------|------------------------------------------------|
| `SUPABASE_URL`           | Your Supabase project URL                      |
| `SUPABASE_KEY`           | Supabase service_role key                      |
| `TELEGRAM_API_ID`        | Telegram app API ID (from my.telegram.org)     |
| `TELEGRAM_API_HASH`      | Telegram app API hash                          |
| `TELEGRAM_BOT_TOKEN`     | Bot token from @BotFather                      |
| `TELEGRAM_TARGET_CHAT`   | Target channel/bot username e.g. `@MyBot`      |

## 🎛️ Optional Environment Variables (with defaults)

| Variable                   | Default  | Description                                      |
|----------------------------|----------|--------------------------------------------------|
| `POLL_INTERVAL_SECONDS`    | `3600`   | Seconds to sleep when no tasks are found (1 hr)  |
| `MAX_DOWNLOAD_RETRIES`     | `3`      | How many times to retry a failed download         |
| `MAX_VIDEO_SIZE_GB`        | `1.9`    | Max file size before splitting for Telegram       |
| `SOURCE_SERVERS`           | `streamtape,mixdrop,vk,download` | Comma-separated server priority list |
| `MIXDROP_MAX_CLICK_ATTEMPTS` | `10`   | Max Playwright click attempts on MixDrop          |
| `UPLOAD_WAIT_SECONDS`      | `12`     | Seconds to wait after forwarding before reading bot reply |
| `SESSION_FILE`             | `egy_sync_session` | Telethon session file name              |

## 🏗️ Architecture

```
app.py              ← Entry point
src/
  config.py         ← Settings from env vars (no hardcoded secrets)
  logger.py         ← Centralized logging
  database.py       ← All Supabase queries
  downloader.py     ← Video download + MixDrop Playwright resolver
  splitter.py       ← ffmpeg-based video splitter
  uploader.py       ← Telethon upload + URL extraction
  sync_engine.py    ← Main orchestration loop
```

## 🔄 How It Works

1. Scans Supabase for episodes without `telegram_direct` links
2. Acquires a distributed lock (via unique DB constraint) to avoid duplicate work
3. Downloads the video from the best available source
4. Splits into parts if > 1.9 GB
5. Uploads to Telegram → forwards to target bot → captures the HF stream URL
6. Updates Supabase with the real URL
7. When no tasks remain → sleeps `POLL_INTERVAL_SECONDS` → retries forever
