"""Database session middleware for aiogram 3.x."""

from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from bot.db.engine import get_db_session


class DbSessionMiddleware(BaseMiddleware):
    """Injects an asynchronous SQLAlchemy session into handler's data context."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        async with get_db_session() as session:
            data["session"] = session
            return await handler(event, data)
