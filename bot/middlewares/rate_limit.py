"""Rate limiting middleware for aiogram 3.x."""

import time
from collections import defaultdict
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject


class CodeRateLimiter:
    """In-memory rate limiter to restrict attempts per user per time window."""

    def __init__(self, max_attempts: int = 5, window_seconds: int = 60):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.attempts: dict[int, list[float]] = defaultdict(list)

    def is_allowed(self, user_id: int) -> bool:
        now = time.time()
        timestamps = self.attempts[user_id]
        self.attempts[user_id] = [t for t in timestamps if now - t < self.window_seconds]

        if len(self.attempts[user_id]) >= self.max_attempts:
            return False

        self.attempts[user_id].append(now)
        return True


bot_code_limiter = CodeRateLimiter(max_attempts=5, window_seconds=60)
