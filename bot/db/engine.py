"""Database engine initialization and session management."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from pathlib import Path
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from bot.config import config
from bot.db.models import Base

logger = logging.getLogger(__name__)

engine: AsyncEngine = create_async_engine(
    config.database_url,
    echo=False,
    future=True,
)

async_session_maker = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


def configure_engine(database_url: str) -> None:
    """Reconfigure database engine and session maker with a new database URL."""
    global engine, async_session_maker
    engine = create_async_engine(
        database_url,
        echo=False,
        future=True,
    )
    async_session_maker = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def init_db() -> None:
    """Initialize database tables and run automatic lightweight column migrations."""
    if "sqlite" in config.database_url:
        db_path_str = config.database_url.split("///")[-1]
        db_path = Path(db_path_str).resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)

    async with engine.begin() as conn:
        logger.info("Initializing database tables...")
        await conn.run_sync(Base.metadata.create_all)

        # Run automatic migrations for new columns
        def _check_and_migrate_columns(sync_conn):
            from sqlalchemy import inspect, text
            inspector = inspect(sync_conn)
            if "users" in inspector.get_table_names():
                existing_cols = {c["name"] for c in inspector.get_columns("users")}
                if "skin_hash" not in existing_cols:
                    logger.info("Migrating database: adding 'skin_hash' column to users table...")
                    sync_conn.execute(text("ALTER TABLE users ADD COLUMN skin_hash VARCHAR(64)"))
                if "skin_model" not in existing_cols:
                    logger.info("Migrating database: adding 'skin_model' column to users table...")
                    sync_conn.execute(text("ALTER TABLE users ADD COLUMN skin_model VARCHAR(8) DEFAULT 'classic' NOT NULL"))
                if "cape_hash" not in existing_cols:
                    logger.info("Migrating database: adding 'cape_hash' column to users table...")
                    sync_conn.execute(text("ALTER TABLE users ADD COLUMN cape_hash VARCHAR(64)"))

        await conn.run_sync(_check_and_migrate_columns)
        logger.info("Database tables and columns initialized successfully.")


async def close_db() -> None:
    """Dispose of the database engine connection pool."""
    logger.info("Closing database engine...")
    await engine.dispose()
    logger.info("Database engine closed.")


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional asynchronous database session scope."""
    session = async_session_maker()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
