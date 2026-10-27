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
    """Initialize database tables and create parent directories if needed."""
    if "sqlite" in config.database_url:
        db_path_str = config.database_url.split("///")[-1]
        db_path = Path(db_path_str).resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)

    async with engine.begin() as conn:
        logger.info("Initializing database tables...")
        await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables initialized successfully.")


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
