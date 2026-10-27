"""SQLAlchemy ORM models for LCALauncher auth server."""

from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    """Return current naive UTC datetime for database compatibility."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class User(Base):
    """Minecraft player account bound to a Telegram account."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, index=True
    )
    username: Mapped[str] = mapped_column(
        String(16), unique=True, nullable=False, index=True
    )
    uuid: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, nullable=False
    )
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Skin & Cape
    skin_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    skin_model: Mapped[str] = mapped_column(String(8), default="classic", nullable=False)
    cape_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Relationships
    sessions: Mapped[List["ActiveSession"]] = relationship(
        "ActiveSession",
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="desc(ActiveSession.created_at)",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} username='{self.username}' tg={self.telegram_id}>"


class AuthCode(Base):
    """One-time 6-digit authorization code generated for launcher login."""

    __tablename__ = "auth_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(
        String(6), unique=True, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, index=True
    )
    is_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    used_by_telegram_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True
    )
    session_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    @property
    def is_expired(self) -> bool:
        return utc_now() >= self.expires_at

    def __repr__(self) -> str:
        return f"<AuthCode code='{self.code}' used={self.is_used}>"


class ActiveSession(Base):
    """Active player launcher session with JWT token."""

    __tablename__ = "active_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token: Mapped[str] = mapped_column(Text, unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="sessions")

    @property
    def is_valid(self) -> bool:
        return not self.is_revoked and utc_now() < self.expires_at

    def __repr__(self) -> str:
        return f"<ActiveSession id={self.id} user_id={self.user_id} revoked={self.is_revoked}>"


class ModpackFile(Base):
    """Files belonging to the current modpack distribution (mods, configs, etc.)."""

    __tablename__ = "modpack_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    relative_path: Mapped[str] = mapped_column(
        String(512), unique=True, nullable=False, index=True
    )
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, nullable=False
    )
    uploaded_by_telegram_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )

    def __repr__(self) -> str:
        return f"<ModpackFile path='{self.relative_path}' size={self.size}>"


class ModpackVersion(Base):
    """Modpack version information and changelogs."""

    __tablename__ = "modpack_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    changelog: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, nullable=False
    )
    created_by_telegram_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )

    def __repr__(self) -> str:
        return f"<ModpackVersion version='{self.version}'>"
