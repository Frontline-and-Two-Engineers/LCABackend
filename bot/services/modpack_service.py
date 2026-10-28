"""Modpack distribution and sync service."""

import hashlib
import logging
import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote
from sqlalchemy import select, func, desc, delete
from sqlalchemy.ext.asyncio import AsyncSession
from bot.config import config
from bot.db.models import ModpackFile, ModpackVersion, utc_now

logger = logging.getLogger(__name__)


class ModpackSecurityError(Exception):
    """Raised when an invalid or insecure path is provided."""
    pass


class ModpackService:
    @staticmethod
    def normalize_and_validate_path(relative_path: str) -> tuple[str, Path]:
        """
        Normalize relative path and verify it stays strictly inside MODPACK_DIR.
        Prevents directory traversal attacks (e.g. ../../etc/passwd) and absolute paths.
        """
        raw = relative_path.replace("\\", "/").strip()
        if raw.startswith("/") or ":" in raw:
            raise ModpackSecurityError("Абсолютные пути запрещены.")

        clean_rel = raw.lstrip("/")
        parts = clean_rel.split("/")
        if not clean_rel or ".." in parts or "." in parts:
            raise ModpackSecurityError("Недопустимый путь файла.")

        base_dir = config.modpack_dir.resolve()
        target_path = (base_dir / clean_rel).resolve()

        if not target_path.is_relative_to(base_dir):
            raise ModpackSecurityError("Попытка выхода за пределы директории сборки.")

        return clean_rel, target_path

    @staticmethod
    async def get_latest_version(session: AsyncSession) -> str:
        """Fetch current latest modpack version string."""
        stmt = select(ModpackVersion).order_by(desc(ModpackVersion.id)).limit(1)
        result = await session.execute(stmt)
        latest = result.scalar_one_or_none()
        return latest.version if latest else "1.0.0"

    @staticmethod
    async def get_manifest(session: AsyncSession, base_url: str) -> dict[str, Any]:
        """
        Generate manifest.json compatible with LCALauncher SyncEngine.

        Format:
        {
          "version": "1.2.0",
          "files": [
            {
              "path": "mods/SomeMod-1.0.jar",
              "url": "http://server/api/modpack/files/mods/SomeMod-1.0.jar",
              "sha256": "...",
              "size": 1048576
            }
          ]
        }
        """
        version = await ModpackService.get_latest_version(session)

        stmt = select(ModpackFile).order_by(ModpackFile.relative_path.asc())
        result = await session.execute(stmt)
        files = result.scalars().all()

        clean_base = base_url.rstrip("/")
        file_entries = []
        for f in files:
            file_entries.append({
                "path": f.relative_path,
                "url": f"{clean_base}/api/modpack/files/{quote(f.relative_path, safe='/')}",
                "sha256": f.sha256,
                "size": f.size,
            })

        return {
            "version": version,
            "files": file_entries,
        }

    @staticmethod
    def get_file_disk_path(relative_path: str) -> Optional[Path]:
        """Resolve and verify existence of file on disk."""
        try:
            _, target_path = ModpackService.normalize_and_validate_path(relative_path)
            if target_path.is_file():
                return target_path

            # Fallback for client-base.zip in data/ root
            clean_name = relative_path.strip().lstrip("/")
            if clean_name.lower() in {"client-base.zip", "client.zip"}:
                fallback = (config.modpack_dir.parent / clean_name).resolve()
                if fallback.is_file():
                    return fallback

            return None
        except ModpackSecurityError:
            return None

    @staticmethod
    async def save_file(
        session: AsyncSession,
        relative_path: str,
        content: bytes,
        uploaded_by_telegram_id: int,
    ) -> ModpackFile:
        """
        Save binary content to disk, compute SHA-256 and size, and upsert ModpackFile record.
        """
        clean_rel, target_path = ModpackService.normalize_and_validate_path(relative_path)

        # Ensure destination directory exists
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Write file to disk
        target_path.write_bytes(content)

        # Compute SHA-256 and size
        sha256 = hashlib.sha256(content).hexdigest()
        size = len(content)

        # Upsert in database
        stmt = select(ModpackFile).where(ModpackFile.relative_path == clean_rel)
        result = await session.execute(stmt)
        modpack_file = result.scalar_one_or_none()

        now = utc_now()
        if modpack_file:
            modpack_file.sha256 = sha256
            modpack_file.size = size
            modpack_file.uploaded_at = now
            modpack_file.uploaded_by_telegram_id = uploaded_by_telegram_id
        else:
            modpack_file = ModpackFile(
                relative_path=clean_rel,
                sha256=sha256,
                size=size,
                uploaded_at=now,
                uploaded_by_telegram_id=uploaded_by_telegram_id,
            )
            session.add(modpack_file)

        await session.commit()
        await session.refresh(modpack_file)

        logger.info(
            "Saved modpack file '%s' (%d bytes, sha256=%s)",
            clean_rel,
            size,
            sha256[:12],
        )
        return modpack_file

    @staticmethod
    async def delete_file(
        session: AsyncSession,
        relative_path: str,
    ) -> bool:
        """Delete file from disk and database."""
        try:
            clean_rel, target_path = ModpackService.normalize_and_validate_path(relative_path)
        except ModpackSecurityError:
            return False

        # Delete from disk
        if target_path.is_file():
            try:
                target_path.unlink()
            except OSError as e:
                logger.error("Error removing file %s: %s", target_path, e)

        # Delete from DB
        stmt = delete(ModpackFile).where(ModpackFile.relative_path == clean_rel)
        result = await session.execute(stmt)
        await session.commit()

        logger.info("Deleted modpack file '%s' (db rows: %d)", clean_rel, result.rowcount)
        return result.rowcount > 0

    @staticmethod
    async def create_version(
        session: AsyncSession,
        version: str,
        changelog: Optional[str],
        created_by_telegram_id: int,
    ) -> ModpackVersion:
        """Create new modpack version entry."""
        ver = ModpackVersion(
            version=version.strip(),
            changelog=changelog.strip() if changelog else None,
            created_at=utc_now(),
            created_by_telegram_id=created_by_telegram_id,
        )
        session.add(ver)
        await session.commit()
        await session.refresh(ver)
        logger.info("Created new modpack version '%s'", ver.version)
        return ver

    @staticmethod
    async def list_files(session: AsyncSession) -> list[ModpackFile]:
        """Fetch all files registered in modpack."""
        stmt = select(ModpackFile).order_by(ModpackFile.relative_path.asc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_stats(session: AsyncSession) -> dict[str, Any]:
        """Get summary statistics about current modpack."""
        version = await ModpackService.get_latest_version(session)

        count_stmt = select(func.count(ModpackFile.id), func.coalesce(func.sum(ModpackFile.size), 0))
        count_res = await session.execute(count_stmt)
        total_files, total_size = count_res.one()

        latest_stmt = select(ModpackFile.uploaded_at).order_by(desc(ModpackFile.uploaded_at)).limit(1)
        latest_res = await session.execute(latest_stmt)
        last_updated = latest_res.scalar_one_or_none()

        return {
            "version": version,
            "total_files": total_files or 0,
            "total_size": total_size or 0,
            "total_size_mb": round((total_size or 0) / (1024 * 1024), 2),
            "last_updated": last_updated,
        }

    @staticmethod
    async def sync_disk_files(session: AsyncSession, uploaded_by_telegram_id: int = 0) -> dict[str, int]:
        """
        Scan MODPACK_DIR on disk, register any newly added files, update changed files,
        and clean up database entries for files deleted from disk.
        """
        base_dir = config.modpack_dir.resolve()
        if not base_dir.exists():
            base_dir.mkdir(parents=True, exist_ok=True)

        disk_files: dict[str, Path] = {}
        for file_path in base_dir.rglob("*"):
            if file_path.is_file():
                if any(part.startswith(".") for part in file_path.parts):
                    continue
                rel_path = file_path.relative_to(base_dir).as_posix()
                # Exclude client base archive from modpack manifest (it's downloaded by ClientDownloader, not SyncEngine)
                if rel_path.lower() in {"client-base.zip", "client.zip"}:
                    continue
                disk_files[rel_path] = file_path

        stmt = select(ModpackFile)
        result = await session.execute(stmt)
        db_files = {f.relative_path: f for f in result.scalars().all()}

        added = 0
        updated = 0
        deleted = 0
        now = utc_now()

        for rel_path, path_obj in disk_files.items():
            content = path_obj.read_bytes()
            sha256 = hashlib.sha256(content).hexdigest()
            size = len(content)

            if rel_path in db_files:
                record = db_files[rel_path]
                if record.sha256 != sha256 or record.size != size:
                    record.sha256 = sha256
                    record.size = size
                    record.uploaded_at = now
                    updated += 1
            else:
                new_file = ModpackFile(
                    relative_path=rel_path,
                    sha256=sha256,
                    size=size,
                    uploaded_at=now,
                    uploaded_by_telegram_id=uploaded_by_telegram_id,
                )
                session.add(new_file)
                added += 1

        for rel_path, record in db_files.items():
            if rel_path not in disk_files:
                await session.delete(record)
                deleted += 1

        await session.commit()
        logger.info(
            "Modpack disk sync complete: %d added, %d updated, %d deleted, %d total",
            added,
            updated,
            deleted,
            len(disk_files),
        )
        return {
            "added": added,
            "updated": updated,
            "deleted": deleted,
            "total": len(disk_files),
        }
