"""Unit tests for services: UUID generation, tokens, auth, and modpack management."""

import pytest
import pytest_asyncio
import uuid
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from bot.db.models import Base
from bot.services.code_service import generate_offline_uuid, CodeService
from bot.services.token_service import TokenService
from bot.services.auth_service import AuthService
from bot.services.modpack_service import ModpackService, ModpackSecurityError


@pytest_asyncio.fixture
async def test_session():
    """Create in-memory SQLite session for tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_maker() as session:
        yield session

    await engine.dispose()


def test_offline_uuid_generation():
    """Verify Minecraft offline UUID v3 generation algorithm."""
    player_uuid = generate_offline_uuid("Steve")
    parsed = uuid.UUID(player_uuid)
    # Check UUID version is 3
    assert parsed.version == 3
    # Check deterministic output
    assert generate_offline_uuid("Steve") == player_uuid
    # Case sensitivity check
    assert generate_offline_uuid("Steve") != generate_offline_uuid("steve")


def test_token_creation_and_decoding():
    """Verify JWT token encoding and decoding."""
    token, expires_at = TokenService.create_token(
        user_id=42,
        username="TestPlayer",
        uuid_str="12345678-1234-3123-8123-123456789abc",
        telegram_id=987654321,
        ttl_days=1,
    )
    assert isinstance(token, str)
    assert len(token) > 20

    payload = TokenService.decode_token(token)
    assert payload is not None
    assert payload["sub"] == "42"
    assert payload["username"] == "TestPlayer"
    assert payload["uuid"] == "12345678-1234-3123-8123-123456789abc"
    assert payload["tid"] == 987654321


@pytest.mark.asyncio
async def test_user_registration_and_validation(test_session: AsyncSession):
    """Verify user registration flow and validation rules."""
    # Invalid username format
    ok, err, user = await AuthService.register_user(test_session, 1001, "ab")
    assert not ok
    assert "от 3 до 16" in err

    ok, err, user = await AuthService.register_user(test_session, 1001, "Invalid-Name!")
    assert not ok

    # Successful registration
    ok, msg, user = await AuthService.register_user(test_session, 1001, "ValidPlayer_1")
    assert ok
    assert user is not None
    assert user.username == "ValidPlayer_1"
    assert user.telegram_id == 1001
    assert user.uuid == generate_offline_uuid("ValidPlayer_1")

    # Duplicate telegram_id
    ok2, err2, _ = await AuthService.register_user(test_session, 1001, "AnotherNick")
    assert not ok2
    assert "уже зарегистрированы" in err2

    # Duplicate username
    ok3, err3, _ = await AuthService.register_user(test_session, 1002, "ValidPlayer_1")
    assert not ok3
    assert "уже занят" in err3


@pytest.mark.asyncio
async def test_auth_code_flow(test_session: AsyncSession):
    """Verify code generation and confirmation binding."""
    # 1. Register a user
    _, _, user = await AuthService.register_user(test_session, 2001, "CodeTester")
    assert user is not None

    # 2. Generate auth code
    auth_code = await CodeService.create_auth_code(test_session, ttl_seconds=300)
    assert len(auth_code.code) == 6
    assert auth_code.code.isdigit()
    assert not auth_code.is_used

    # 3. Confirm auth code by user
    ok, msg, confirmed_code, confirmed_user = await AuthService.confirm_auth_code(
        test_session, auth_code.code, telegram_id=2001
    )
    assert ok
    assert confirmed_code is not None
    assert confirmed_code.is_used is True
    assert confirmed_code.used_by_telegram_id == 2001
    assert confirmed_code.session_token is not None

    # 4. Check active session created
    sessions = await AuthService.get_active_sessions(test_session, user.id)
    assert len(sessions) == 1
    assert sessions[0].token == confirmed_code.session_token

    # 5. Token validation
    val_ok, val_msg, val_user = await AuthService.validate_token(
        test_session, confirmed_code.session_token
    )
    assert val_ok
    assert val_user is not None
    assert val_user.username == "CodeTester"

    # 6. Cannot reuse confirmed code
    ok_reuse, reuse_err, _, _ = await AuthService.confirm_auth_code(
        test_session, auth_code.code, telegram_id=2001
    )
    assert not ok_reuse
    assert "уже был использован" in reuse_err


def test_modpack_path_traversal_protection():
    """Verify path traversal prevention."""
    with pytest.raises(ModpackSecurityError):
        ModpackService.normalize_and_validate_path("../../secret.txt")

    with pytest.raises(ModpackSecurityError):
        ModpackService.normalize_and_validate_path("/etc/passwd")

    rel, target = ModpackService.normalize_and_validate_path("mods/TestMod-1.0.jar")
    assert rel == "mods/TestMod-1.0.jar"


@pytest.mark.asyncio
async def test_ban_and_unban_flow(test_session: AsyncSession):
    """Verify player banning revokes sessions and prevents login."""
    # Register player
    ok, _, user = await AuthService.register_user(test_session, 3001, "BannedPlayer")
    assert ok and user is not None

    # Create active session
    code = await CodeService.create_auth_code(test_session, 300)
    ok_conf, _, confirmed_code, _ = await AuthService.confirm_auth_code(test_session, code.code, 3001)
    assert ok_conf
    token = confirmed_code.session_token

    # Ban player
    ban_ok, _, banned_user = await AuthService.set_user_ban_status(test_session, "BannedPlayer", is_banned=True)
    assert ban_ok
    assert banned_user.is_banned is True

    # Validate token -> should fail
    val_ok, val_err, _ = await AuthService.validate_token(test_session, token)
    assert not val_ok

    # Try auth code again while banned -> should fail
    code2 = await CodeService.create_auth_code(test_session, 300)
    ok_banned, err_banned, _, _ = await AuthService.confirm_auth_code(test_session, code2.code, 3001)
    assert not ok_banned
    assert "заблокирован" in err_banned

    # Unban player
    unban_ok, _, unbanned_user = await AuthService.set_user_ban_status(test_session, "3001", is_banned=False)
    assert unban_ok
    assert unbanned_user.is_banned is False


@pytest.mark.asyncio
async def test_nickname_change_flow(test_session: AsyncSession):
    """Verify player nickname change and UUID recalculation."""
    ok, _, user = await AuthService.register_user(test_session, 4001, "OldNick")
    assert ok and user is not None
    old_uuid = user.uuid

    # Change to valid new nickname
    ch_ok, ch_msg = await AuthService.change_username(test_session, user, "NewNick")
    assert ch_ok
    assert user.username == "NewNick"
    assert user.uuid != old_uuid
    assert user.uuid == generate_offline_uuid("NewNick")

    # Invalid nickname
    inv_ok, _ = await AuthService.change_username(test_session, user, "bad")
    # "bad" has 3 chars, so it's valid, let's test "x!"
    inv_ok, _ = await AuthService.change_username(test_session, user, "x!")
    assert not inv_ok


def test_bot_code_rate_limiter():
    """Verify bot rate limiting allows 5 attempts then blocks 6th."""
    from bot.middlewares.rate_limit import CodeRateLimiter
    limiter = CodeRateLimiter(max_attempts=5, window_seconds=60)
    user_id = 99999

    for _ in range(5):
        assert limiter.is_allowed(user_id) is True

    assert limiter.is_allowed(user_id) is False


@pytest.mark.asyncio
async def test_automatic_database_column_migration(tmp_path):
    """Verify init_db automatically adds missing columns to existing tables."""
    import sqlite3
    from bot.config import config
    from bot.db.engine import init_db, configure_engine, close_db

    mig_db = tmp_path / "legacy.db"
    # Create old table without skin_hash, skin_model, cape_hash
    con = sqlite3.connect(mig_db)
    con.execute(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id BIGINT UNIQUE NOT NULL,
            username VARCHAR(16) UNIQUE NOT NULL,
            uuid VARCHAR(36) UNIQUE NOT NULL,
            created_at DATETIME NOT NULL,
            is_banned BOOLEAN NOT NULL
        )
        """
    )
    con.commit()
    con.close()

    # Reconfigure engine to point to legacy db
    configure_engine(f"sqlite+aiosqlite:///{mig_db}")
    await init_db()

    # Verify columns were added automatically
    con2 = sqlite3.connect(mig_db)
    cols = [col[1] for col in con2.execute("PRAGMA table_info(users)").fetchall()]
    con2.close()
    await close_db()

    assert "skin_hash" in cols
    assert "skin_model" in cols
    assert "cape_hash" in cols
