"""Main entry point: launches Telegram Bot polling and aiohttp API server concurrently."""

import asyncio
import logging
import signal
import sys
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import config
from bot.db.engine import init_db, close_db, get_db_session
from bot.middlewares.db_session import DbSessionMiddleware
from bot.handlers import get_main_router
from bot.api.launcher_api import create_api_app
from bot.services.code_service import CodeService

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

    # 4. Check Telegram Bot Token
    if not config.bot_token:
        logger.warning(
            "⚠️ BOT_TOKEN is not set in environment or .env! "
            "Telegram Bot polling is disabled. API server is running."
        )
        # Keep API server running indefinitely
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

    # 5. Initialize aiogram Bot & Dispatcher
    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Register Middlewares
    dp.update.middleware(DbSessionMiddleware())

    # Register Routers
    dp.include_router(get_main_router())

    logger.info("Telegram Bot polling starting...")

    try:
        # Start bot polling and wait
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        logger.info("Initiating graceful shutdown...")
        cleanup_task.cancel()
        await bot.session.close()
        await runner.cleanup()
        await close_db()
        logger.info("LCALauncher Auth Server stopped successfully.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Process terminated by user.")
