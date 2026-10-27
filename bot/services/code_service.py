"""One-time authorization code service and Minecraft offline UUID generator."""

import hashlib
import logging
import secrets
import uuid
from datetime import timedelta
from typing import Optional
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from bot.config import config
from bot.db.models import AuthCode, utc_now

logger = logging.getLogger(__name__)


def generate_offline_uuid(username: str) -> str:
    """
    Generate standard Minecraft offline UUID v3 for a player username.
    Matches Mojang's UUID.nameUUIDFromBytes(("OfflinePlayer:" + username).getBytes(StandardCharsets.UTF_8)).
    """
    data = f"OfflinePlayer:{username}".encode("utf-8")
    md5 = hashlib.md5(data).digest()
    md5_array = bytearray(md5)
    md5_array[6] = (md5_array[6] & 0x0F) | 0x30  # version 3
    md5_array[8] = (md5_array[8] & 0x3F) | 0x80  # IETF variant
    return str(uuid.UUID(bytes=bytes(md5_array)))


class CodeService:
    @staticmethod
    async def create_auth_code(
        session: AsyncSession,
        ttl_seconds: Optional[int] = None,
    ) -> AuthCode:
        """
        Generate a cryptographically random 6-digit code, ensure uniqueness among active codes,
        and persist it to the database.
        """
        ttl = ttl_seconds if ttl_seconds is not None else config.code_ttl_seconds
        now = utc_now()
        expires_at = now + timedelta(seconds=ttl)

        # Try generating unique unused code
        for _ in range(10):
            candidate = f"{secrets.randbelow(1_000_000):06d}"

            # Check if active code with same number already exists
            stmt = select(AuthCode).where(
                AuthCode.code == candidate,
                AuthCode.expires_at > now,
                AuthCode.is_used.is_(False),
            )
            result = await session.execute(stmt)
            if result.scalar_one_or_none() is None:
                # If an expired code exists with the same number, delete it to avoid unique constraint clash
                await session.execute(
                    delete(AuthCode).where(AuthCode.code == candidate)
                )

                auth_code = AuthCode(
                    code=candidate,
                    created_at=now,
                    expires_at=expires_at,
                    is_used=False,
                )
                session.add(auth_code)
                await session.commit()
                await session.refresh(auth_code)
                logger.info("Generated new auth code: %s (expires in %ds)", candidate, ttl)
                return auth_code

        raise RuntimeError("Failed to generate unique 6-digit auth code after multiple attempts")

    @staticmethod
    async def get_code(
        session: AsyncSession,
        code_str: str,
    ) -> Optional[AuthCode]:
        """Fetch auth code by 6-digit code string."""
        stmt = select(AuthCode).where(AuthCode.code == code_str)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def cleanup_expired_codes(session: AsyncSession) -> int:
        """
        Remove expired codes and old used codes to prevent database bloat.
        Returns the number of deleted records.
        """
        now = utc_now()
        # Delete unused codes that have expired, or used codes older than 24 hours
        threshold = now - timedelta(hours=24)
        stmt = delete(AuthCode).where(
            (AuthCode.expires_at < now) | (AuthCode.created_at < threshold)
        )
        result = await session.execute(stmt)
        await session.commit()
        deleted_count = result.rowcount
        if deleted_count > 0:
            logger.info("Cleaned up %d expired/stale auth codes", deleted_count)
        return deleted_count
