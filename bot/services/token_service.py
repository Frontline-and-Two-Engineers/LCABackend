"""JWT Token service for generating and validating player access tokens."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import jwt
from bot.config import config

logger = logging.getLogger(__name__)


class TokenService:
    @staticmethod
    def create_token(
        user_id: int,
        username: str,
        uuid_str: str,
        telegram_id: int,
        ttl_days: Optional[int] = None,
    ) -> tuple[str, datetime]:
        """
        Generate a signed JWT access token for a Minecraft launcher session.

        Payload:
            sub: user_id
            username: Minecraft player name
            uuid: Minecraft offline UUID v3
            tid: Telegram user ID
            iat: Issued at timestamp
            exp: Expiration timestamp

        Returns:
            Tuple of (token_string, expires_at_datetime)
        """
        days = ttl_days if ttl_days is not None else config.token_ttl_days
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=days)

        payload = {
            "sub": str(user_id),
            "username": username,
            "uuid": uuid_str,
            "tid": telegram_id,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        }

        token = jwt.encode(payload, config.jwt_secret, algorithm="HS256")
        # Return naive UTC datetime for database compatibility
        return token, expires_at.replace(tzinfo=None)

    @staticmethod
    def decode_token(token: str) -> Optional[dict[str, Any]]:
        """
        Decode and verify JWT token.

        Returns payload dict if valid, or None if expired/invalid signature.
        """
        try:
            payload = jwt.decode(
                token,
                config.jwt_secret,
                algorithms=["HS256"],
                options={"require": ["sub", "username", "uuid", "exp"]},
            )
            return payload
        except jwt.ExpiredSignatureError:
            logger.warning("Token verification failed: signature expired")
            return None
        except jwt.PyJWTError as e:
            logger.warning("Token verification failed: %s", e)
            return None
