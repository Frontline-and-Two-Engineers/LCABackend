"""Service for fetching and caching news posts from the Telegram channel."""

import io
import logging
import re
import time
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any, Optional

import aiohttp
from bot.config import config

logger = logging.getLogger(__name__)

MONTHS_RU = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def format_ru_date(dt_str: str) -> str:
    """Format an ISO datetime string into Russian readable date."""
    if not dt_str:
        return ""
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        month_name = MONTHS_RU[dt.month - 1]
        return f"{dt.day} {month_name} {dt.year}, {dt.strftime('%H:%M')}"
    except Exception:
        return dt_str[:16].replace("T", " ")


class ChannelService:
    """Manages channel post fetching, media caching and retrieval."""

    def __init__(self) -> None:
        self._telethon_client: Any = None
        self._cached_posts: list[dict[str, Any]] = []
        self._cache_time: float = 0.0
        self._cache_ttl: float = 60.0  # 60 seconds

    def set_telethon_client(self, client: Any) -> None:
        """Assign active Telethon client for MTProto and real-time event listening."""
        self._telethon_client = client
        try:
            from telethon import events

            @client.on(events.NewMessage(chats=[config.channel_username]))
            async def _on_channel_post(event: Any) -> None:
                logger.info("Real-time new message received in channel @%s, updating posts cache...", config.channel_username)
                self._cache_time = 0.0
                await self.get_latest_posts(limit=3, force_refresh=True)
        except Exception as e:
            logger.debug("Could not attach channel event listener: %s", e)

    def get_media_path(self, filename: str) -> Optional[Path]:
        """Return path to cached media file if safe and exists."""
        # Sanitize filename (only allow alphanumeric, underscore, dot, hyphen)
        if not re.match(r"^[\w\-]+\.(jpg|jpeg|png|webp)$", filename, re.IGNORECASE):
            return None
        media_path = (config.channel_media_dir / filename).resolve()
        if not media_path.is_relative_to(config.channel_media_dir) or not media_path.exists():
            return None
        return media_path

    def cleanup_old_media(self, keep_ids: Optional[set[int]] = None) -> int:
        """
        Delete cached media files that are no longer associated with current active posts.
        Returns the number of deleted files.
        """
        if keep_ids is None:
            keep_ids = {p["id"] for p in self._cached_posts if p.get("id")}

        deleted_count = 0
        if not config.channel_media_dir.exists():
            return 0

        try:
            for file_path in config.channel_media_dir.iterdir():
                if not file_path.is_file() or file_path.name.startswith("."):
                    continue

                stem = file_path.stem
                try:
                    post_id = int(stem)
                    if post_id not in keep_ids:
                        file_path.unlink(missing_ok=True)
                        deleted_count += 1
                        logger.info("Removed old cached media: %s", file_path.name)
                except ValueError:
                    # Non-numeric media file older than 24h
                    age_seconds = time.time() - file_path.stat().st_mtime
                    if age_seconds > 86400:
                        file_path.unlink(missing_ok=True)
                        deleted_count += 1
                        logger.info("Removed stale media: %s", file_path.name)
        except Exception as e:
            logger.error("Error during media cache cleanup: %s", e)

        if deleted_count > 0:
            logger.info("Media cache cleanup: removed %d old file(s)", deleted_count)
        return deleted_count

    async def get_latest_posts(self, limit: int = 3, force_refresh: bool = False) -> list[dict[str, Any]]:
        """
        Fetch latest channel posts (newest first).
        Uses cache if within TTL, otherwise attempts MTProto or web scraping fallback.
        """
        now = time.time()
        if not force_refresh and self._cached_posts and (now - self._cache_time < self._cache_ttl):
            return self._cached_posts[:limit]

        posts: list[dict[str, Any]] = []

        # 1. Try MTProto via Telethon client only if connected AND NOT a bot account
        # (Telegram MTProto strictly restricts GetHistoryRequest for bot tokens)
        can_use_telethon_history = False
        if self._telethon_client:
            try:
                if self._telethon_client.is_connected() and not (await self._telethon_client.is_bot()):
                    can_use_telethon_history = True
            except Exception:
                can_use_telethon_history = False

        if can_use_telethon_history:
            try:
                posts = await self._fetch_via_telethon(limit * 3)
            except Exception as e:
                logger.debug("Telethon history fetch skipped or failed: %s", e)

        # 2. Web scraping of t.me/s/{channel} (primary and reliable for bot accounts)
        if not posts:
            try:
                posts = await self._fetch_via_web(limit * 3)
            except Exception as e:
                logger.error("Failed to fetch channel posts via web: %s", e)

        if posts:
            # Sort newest first by post id
            posts.sort(key=lambda p: p["id"], reverse=True)
            self._cached_posts = posts
            self._cache_time = now
            logger.info("Successfully refreshed %d channel posts for @%s", len(posts), config.channel_username)

            # Automatically delete old media files that are no longer part of active posts
            active_ids = {p["id"] for p in posts[:limit]}
            self.cleanup_old_media(active_ids)

            return self._cached_posts[:limit]

        # If fetch completely failed, return existing cache even if expired
        return self._cached_posts[:limit]

    async def _fetch_via_telethon(self, max_fetch: int = 10) -> list[dict[str, Any]]:
        """Fetch posts directly from Telegram MTProto."""
        results: list[dict[str, Any]] = []
        channel = await self._telethon_client.get_entity(config.channel_username)

        async for msg in self._telethon_client.iter_messages(channel, limit=max_fetch):
            # Skip service messages
            if getattr(msg, "action", None) is not None:
                continue

            raw_text = msg.text or ""
            # Filter service changes
            if raw_text.startswith("Channel photo updated") or raw_text.startswith("Channel name was changed"):
                continue

            if not raw_text and not msg.photo:
                continue

            image_url: Optional[str] = None
            if msg.photo:
                filename = f"{msg.id}.jpg"
                dest_path = config.channel_media_dir / filename
                if not dest_path.exists():
                    try:
                        await self._telethon_client.download_media(msg.photo, file=str(dest_path))
                    except Exception as err:
                        logger.warning("Could not download Telethon media for post %d: %s", msg.id, err)
                if dest_path.exists():
                    image_url = f"{config.api_base_url}/api/channel/media/{filename}"

            date_str = msg.date.isoformat() if msg.date else ""
            results.append({
                "id": msg.id,
                "date": date_str,
                "formatted_date": format_ru_date(date_str),
                "text": raw_text.strip(),
                "image_url": image_url,
                "views": getattr(msg, "views", None) or 0,
                "link": f"https://t.me/{config.channel_username}/{msg.id}",
            })

        return results

    async def _fetch_via_web(self, max_fetch: int = 15) -> list[dict[str, Any]]:
        """Fetch posts via Telegram public channel web preview (https://t.me/s/{channel})."""
        url = f"https://t.me/s/{config.channel_username}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        }

        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.warning("Telegram web returned status %d for %s", resp.status, url)
                    return []
                html = await resp.text()

        msg_blocks = re.findall(
            r"(<div class=\"tgme_widget_message[^\"]*\"[^>]*data-post=\"([^\"]+)\"[\s\S]*?)(?=<div class=\"tgme_widget_message[^\"]*\"[^>]*data-post=|$)",
            html,
        )

        results: list[dict[str, Any]] = []

        # Iterate in reverse (newest are at the bottom of the page)
        for block, post_id_str in reversed(msg_blocks):
            if "tgme_widget_message_service" in block:
                continue

            try:
                post_num = int(post_id_str.split("/")[-1])
            except ValueError:
                continue

            # Extract text
            text_match = re.search(r"<div class=\"tgme_widget_message_text[^\"]*\"[^>]*>([\s\S]*?)</div>", block)
            text = ""
            if text_match:
                raw_text = text_match.group(1)
                raw_text = re.sub(r"<br\s*/?>", "\n", raw_text)
                raw_text = re.sub(r"<[^>]+>", "", raw_text)
                text = unescape(raw_text).strip()

            if text.startswith("Channel photo updated") or text.startswith("Channel name was changed"):
                continue

            # Extract photo url
            photo_wrap = re.search(
                r"class=\"[^\"]*tgme_widget_message_photo_wrap[^\"]*\"[^>]*style=\"[^\"]*background-image:url\(\x27([^\x27]+)\x27\)",
                block,
            )
            photo_cdn_url = photo_wrap.group(1) if photo_wrap else None

            if not text and not photo_cdn_url:
                continue

            # Extract date
            date_match = re.search(r"<time datetime=\"([^\"]+)\"", block)
            date_str = date_match.group(1) if date_match else ""

            # Extract views
            views_match = re.search(r"<span class=\"tgme_widget_message_views\">([^<]+)</span>", block)
            views_str = views_match.group(1).strip() if views_match else ""

            # Cache image locally
            image_url: Optional[str] = None
            if photo_cdn_url:
                filename = f"{post_num}.jpg"
                dest_path = config.channel_media_dir / filename
                if not dest_path.exists():
                    try:
                        await self._download_file(photo_cdn_url, dest_path)
                    except Exception as err:
                        logger.warning("Could not cache image from %s: %s", photo_cdn_url, err)

                if dest_path.exists():
                    image_url = f"{config.api_base_url}/api/channel/media/{filename}"
                else:
                    image_url = photo_cdn_url

            results.append({
                "id": post_num,
                "date": date_str,
                "formatted_date": format_ru_date(date_str),
                "text": text,
                "image_url": image_url,
                "views": views_str,
                "link": f"https://t.me/{config.channel_username}/{post_num}",
            })

            if len(results) >= max_fetch:
                break

        return results

    async def _download_file(self, url: str, dest: Path) -> None:
        """Download remote image file into destination path."""
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    dest.write_bytes(content)


channel_service = ChannelService()
