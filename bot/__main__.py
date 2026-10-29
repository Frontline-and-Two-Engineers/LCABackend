"""Main entry point: launches Telegram Bot polling and aiohttp API server concurrently."""

import asyncio
import logging
import signal
import sys
from pathlib import Path

# Ensure project root is in sys.path when executed directly as `python bot/__main__.py`
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from aiohttp import web

from bot.config import config
from bot.db.engine import init_db, close_db, get_db_session
from bot.api.launcher_api import create_api_app
from bot.services.code_service import CodeService
from bot.services.modpack_service import ModpackService
from bot.services.channel_service import channel_service
from bot.telegram_bot import create_telegram_client, setup_bot_handlers

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("lca_auth_backend")


async def periodic_code_cleanup() -> None:
    """Background task: periodically purges expired codes from database every 10 minutes."""
    logger.info("Started background code cleanup task (every 10 minutes).")
    while True:
        try:
            await asyncio.sleep(600)
            async with get_db_session() as session:
                deleted = await CodeService.cleanup_expired_codes(session)
                if deleted > 0:
                    logger.info("Background cleaner removed %d expired codes.", deleted)
        except asyncio.CancelledError:
            logger.info("Background cleanup task cancelled.")
            break
        except Exception as e:
            logger.error("Error during background cleanup: %s", e, exc_info=True)


async def main() -> None:
    logger.info("Starting LCALauncher Auth Server...")

    # 1. Initialize database tables
    await init_db()

    # Sync modpack directory files with database
    async with get_db_session() as session:
        sync_res = await ModpackService.sync_disk_files(session)
        logger.info(
            "Modpack synced on startup: %d files registered (added: %d, updated: %d, deleted: %d)",
            sync_res["total"],
            sync_res["added"],
            sync_res["updated"],
            sync_res["deleted"],
        )

    # 2. Start HTTP REST API server (aiohttp)
    api_app = create_api_app()
    runner = web.AppRunner(api_app)
    await runner.setup()
    site = web.TCPSite(runner, config.api_host, config.api_port)
    await site.start()
    logger.info("HTTP API server listening on http://%s:%d", config.api_host, config.api_port)
    logger.info("Public Base URL: %s", config.api_base_url)

    # 3. Start background cleanup task
    cleanup_task = asyncio.create_task(periodic_code_cleanup())

    # 4. Check Telegram Bot Credentials
    if not config.bot_token:
        logger.warning(
            "⚠️ BOT_TOKEN is not set in environment or .env! "
            "Telegram Bot is disabled. API server is running."
        )
        try:
            stop_event = asyncio.Event()
            await stop_event.wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            cleanup_task.cancel()
            await runner.cleanup()
            await close_db()
            logger.info("Shutdown completed.")
        return

    if not config.api_id or not config.api_hash:
        logger.warning(
            "⚠️ API_ID or API_HASH is missing in .env! "
            "To connect via native MTProto, get API_ID and API_HASH from https://my.telegram.org. "
            "API server is running."
        )
        try:
            stop_event = asyncio.Event()
            await stop_event.wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            cleanup_task.cancel()
            await runner.cleanup()
            await close_db()
            logger.info("Shutdown completed.")
        return

    # 5. Initialize native MTProto Telegram Bot
    telethon_client = create_telegram_client()
    setup_bot_handlers(telethon_client)
    channel_service.set_telethon_client(telethon_client)

    logger.info("Connecting MTProto bot client to Telegram Data Centers...")
    try:
        await telethon_client.start(bot_token=config.bot_token)
        me = await telethon_client.get_me()
        logger.info("✅ MTProto Bot successfully connected as @%s (ID: %d)", me.username, me.id)
        await telethon_client.run_until_disconnected()
    except Exception as e:
        logger.error("❌ Failed to connect or run MTProto Telegram Bot: %s", e)
        logger.info("HTTP API server remains active. Press Ctrl+C to terminate.")
        try:
            stop_event = asyncio.Event()
            await stop_event.wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
    finally:
        logger.info("Initiating graceful shutdown...")
        cleanup_task.cancel()
        if telethon_client.is_connected():
            await telethon_client.disconnect()
        await runner.cleanup()
        await close_db()
        logger.info("LCALauncher Auth Server stopped successfully.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Process terminated by user.")
