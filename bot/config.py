"""Configuration settings loaded from environment variables."""

import os
from pathlib import Path
from dataclasses import dataclass, field
from dotenv import load_dotenv

# Load .env file from project root
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


def _parse_admin_ids(raw_ids: str) -> set[int]:
    """Parse comma-separated Telegram IDs into a set of integers."""
    result: set[int] = set()
    for item in raw_ids.split(","):
        cleaned = item.strip()
        if cleaned.isdigit() or (cleaned.startswith("-") and cleaned[1:].isdigit()):
            result.add(int(cleaned))
    return result


@dataclass
class Config:
    # Telegram Bot & MTProto
    bot_token: str = field(default_factory=lambda: os.getenv("BOT_TOKEN", "").strip())
    api_id: int = field(
        default_factory=lambda: int(os.getenv("API_ID", os.getenv("TG_API_ID", "0")) or "0")
    )
    api_hash: str = field(
        default_factory=lambda: os.getenv("API_HASH", os.getenv("TG_API_HASH", "")).strip()
    )

    # MTProto Proxy settings (tg://proxy?server=...&port=...&secret=...)
    mtproto_proxy_host: str = field(
        default_factory=lambda: os.getenv("MTPROTO_PROXY_HOST", os.getenv("MTPROXY_HOST", "")).strip()
    )
    mtproto_proxy_port: int = field(
        default_factory=lambda: int(os.getenv("MTPROTO_PROXY_PORT", os.getenv("MTPROXY_PORT", "0")) or "0")
    )
    mtproto_proxy_secret: str = field(
        default_factory=lambda: os.getenv("MTPROTO_PROXY_SECRET", os.getenv("MTPROXY_SECRET", "")).strip()
    )

    # General SOCKS5 / HTTP Proxy (fallback)
    telegram_proxy: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_PROXY", os.getenv("PROXY_URL", "")).strip()
    )
    telegram_api_server: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_API_SERVER", "").strip().rstrip("/")
    )

    # Storage Directories
    data_dir: Path = field(default_factory=lambda: (BASE_DIR / "data").resolve())

    # JWT & Security
    jwt_secret: str = field(
        default_factory=lambda: os.getenv("JWT_SECRET", "default-dev-secret-change-me")
    )
    admin_api_token: str = field(
        default_factory=lambda: os.getenv("ADMIN_API_TOKEN", "default-admin-token")
    )

    # Database
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", "sqlite+aiosqlite:///./data/lca_auth.db"
        )
    )

    # API Server
    api_host: str = field(default_factory=lambda: os.getenv("API_HOST", "0.0.0.0"))
    api_port: int = field(
        default_factory=lambda: int(os.getenv("API_PORT", "8080"))
    )
    api_base_url: str = field(
        default_factory=lambda: os.getenv("API_BASE_URL", "http://localhost:8080").rstrip("/")
    )

    # TTL settings
    code_ttl_seconds: int = field(
        default_factory=lambda: int(os.getenv("CODE_TTL_SECONDS", "300"))
    )
    token_ttl_days: int = field(
        default_factory=lambda: int(os.getenv("TOKEN_TTL_DAYS", "30"))
    )

    # Admins
    admin_telegram_ids: set[int] = field(
        default_factory=lambda: _parse_admin_ids(os.getenv("ADMIN_TELEGRAM_IDS", ""))
    )

    # Modpack Storage Directory
    modpack_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("MODPACK_DIR", str(BASE_DIR / "data" / "modpack"))
        ).resolve()
    )

    # Skins, Capes and Crypto Keys Directories
    skins_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("SKINS_DIR", str(BASE_DIR / "data" / "skins"))
        ).resolve()
    )
    capes_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("CAPES_DIR", str(BASE_DIR / "data" / "capes"))
        ).resolve()
    )
    keys_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("KEYS_DIR", str(BASE_DIR / "data" / "keys"))
        ).resolve()
    )

    def is_admin(self, telegram_id: int) -> bool:
        """Check if the given Telegram ID has admin privileges."""
        return telegram_id in self.admin_telegram_ids


config = Config()

# Ensure directories exist
config.modpack_dir.mkdir(parents=True, exist_ok=True)
config.skins_dir.mkdir(parents=True, exist_ok=True)
config.capes_dir.mkdir(parents=True, exist_ok=True)
config.keys_dir.mkdir(parents=True, exist_ok=True)
