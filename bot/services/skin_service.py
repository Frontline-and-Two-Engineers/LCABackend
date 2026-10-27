"""Skin and cape validation and storage management service."""

import hashlib
import logging
from pathlib import Path
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from bot.config import config
from bot.db.models import User

logger = logging.getLogger(__name__)

# Valid dimensions for Minecraft textures
VALID_SKIN_DIMENSIONS = {(64, 64), (64, 32), (128, 128)}
VALID_CAPE_DIMENSIONS = {(64, 32), (22, 17), (128, 64)}


class TextureValidationError(Exception):
    """Raised when a texture image is invalid or has unsupported dimensions."""
    pass


class SkinService:
    @staticmethod
    def inspect_png_dimensions(data: bytes) -> tuple[int, int]:
        """
        Parse PNG header and extract width and height in pixels.
        Ensures valid PNG magic bytes and IHDR chunk.
        """
        if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
            raise TextureValidationError("Файл не является корректным изображением формата PNG.")

        if data[12:16] != b"IHDR":
            raise TextureValidationError("Неверный формат заголовка PNG (отсутствует IHDR chunk).")

        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")

        if width <= 0 or height <= 0:
            raise TextureValidationError("Недопустимый размер изображения.")

        return width, height

    @staticmethod
    async def set_user_skin(
        session: AsyncSession,
        user: User,
        data: bytes,
        model: str = "classic",
    ) -> str:
        """
        Validate PNG skin data, save to disk, and update user record.
        Returns the SHA-256 hash of the saved texture.
        """
        width, height = SkinService.inspect_png_dimensions(data)
        if (width, height) not in VALID_SKIN_DIMENSIONS:
            allowed_str = ", ".join(f"{w}x{h}" for w, h in sorted(VALID_SKIN_DIMENSIONS))
            raise TextureValidationError(
                f"Неверный размер скина: {width}x{height}px. Поддерживаемые размеры: {allowed_str}px."
            )

        skin_hash = hashlib.sha256(data).hexdigest()
        file_path = config.skins_dir / f"{skin_hash}.png"

        # Save to disk
        file_path.write_bytes(data)

        # Update database
        user.skin_hash = skin_hash
        user.skin_model = "slim" if model.lower() == "slim" else "classic"
        await session.commit()

        logger.info(
            "Updated skin for %s: %s (%dx%d, model=%s)",
            user.username,
            skin_hash[:12],
            width,
            height,
            user.skin_model,
        )
        return skin_hash

    @staticmethod
    async def set_user_skin_model(
        session: AsyncSession,
        user: User,
        model: str,
    ) -> str:
        """Update user skin model ('classic' Steve vs 'slim' Alex)."""
        clean_model = "slim" if model.lower() == "slim" else "classic"
        user.skin_model = clean_model
        await session.commit()
        return clean_model

    @staticmethod
    async def clear_user_skin(
        session: AsyncSession,
        user: User,
    ) -> None:
        """Remove user custom skin (resets to default Steve/Alex)."""
        user.skin_hash = None
        await session.commit()
        logger.info("Cleared custom skin for %s", user.username)

    @staticmethod
    async def set_user_cape(
        session: AsyncSession,
        user: User,
        data: bytes,
    ) -> str:
        """
        Validate PNG cape data, save to disk, and update user record.
        Returns the SHA-256 hash of the saved cape.
        """
        width, height = SkinService.inspect_png_dimensions(data)
        if (width, height) not in VALID_CAPE_DIMENSIONS:
            allowed_str = ", ".join(f"{w}x{h}" for w, h in sorted(VALID_CAPE_DIMENSIONS))
            raise TextureValidationError(
                f"Неверный размер плаща: {width}x{height}px. Поддерживаемые размеры: {allowed_str}px."
            )

        cape_hash = hashlib.sha256(data).hexdigest()
        file_path = config.capes_dir / f"{cape_hash}.png"

        # Save to disk
        file_path.write_bytes(data)

        # Update database
        user.cape_hash = cape_hash
        await session.commit()

        logger.info("Updated cape for %s: %s (%dx%d)", user.username, cape_hash[:12], width, height)
        return cape_hash

    @staticmethod
    async def clear_user_cape(
        session: AsyncSession,
        user: User,
    ) -> None:
        """Remove user custom cape."""
        user.cape_hash = None
        await session.commit()
        logger.info("Cleared custom cape for %s", user.username)

    @staticmethod
    def get_texture_path(texture_hash: str) -> Optional[Path]:
        """Find skin or cape PNG file on disk by its hash."""
        # Sanitize hash
        clean_hash = "".join(c for c in texture_hash if c.isalnum())
        if not clean_hash:
            return None

        skin_path = config.skins_dir / f"{clean_hash}.png"
        if skin_path.is_file():
            return skin_path

        cape_path = config.capes_dir / f"{clean_hash}.png"
        if cape_path.is_file():
            return cape_path

        return None
