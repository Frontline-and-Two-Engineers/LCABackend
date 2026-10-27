"""Authentication and user management business logic service."""

import logging
import re
from typing import Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from bot.db.models import User, AuthCode, ActiveSession, utc_now
from bot.services.code_service import CodeService, generate_offline_uuid
from bot.services.token_service import TokenService

logger = logging.getLogger(__name__)

USERNAME_REGEX = re.compile(r"^[a-zA-Z0-9_]{3,16}$")


class AuthService:
    @staticmethod
    def validate_username(username: str) -> tuple[bool, str]:
        """Validate Minecraft username format (3-16 characters, alphanumeric + underscore)."""
        if not username:
            return False, "Никнейм не может быть пустым."
        if len(username) < 3 or len(username) > 16:
            return False, "Никнейм должен быть от 3 до 16 символов."
        if not USERNAME_REGEX.match(username):
            return (
                False,
                "Никнейм может содержать только латинские буквы, цифры и символ подчеркивания.",
            )
        return True, ""

    @staticmethod
    async def get_user_by_telegram_id(
        session: AsyncSession,
        telegram_id: int,
    ) -> Optional[User]:
        """Get User entity by Telegram ID."""
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_user_by_username(
        session: AsyncSession,
        username: str,
    ) -> Optional[User]:
        """Get User entity by Minecraft username (case-insensitive search)."""
        stmt = select(User).where(User.username.ilike(username))
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_user_by_identifier(
        session: AsyncSession,
        identifier: str,
    ) -> Optional[User]:
        """Find user by username or numeric Telegram ID."""
        clean = identifier.strip().lstrip("@")
        if clean.isdigit():
            user = await AuthService.get_user_by_telegram_id(session, int(clean))
            if user:
                return user
        return await AuthService.get_user_by_username(session, clean)

    @staticmethod
    async def register_user(
        session: AsyncSession,
        telegram_id: int,
        username: str,
    ) -> tuple[bool, str, Optional[User]]:
        """
        Register a new user account with a Minecraft username and offline UUID.
        """
        valid, msg = AuthService.validate_username(username)
        if not valid:
            return False, msg, None

        # Check if telegram_id is already registered
        existing_tg = await AuthService.get_user_by_telegram_id(session, telegram_id)
        if existing_tg:
            return False, f"Вы уже зарегистрированы под ником {existing_tg.username}.", existing_tg

        # Check if username is already taken
        existing_nick = await AuthService.get_user_by_username(session, username)
        if existing_nick:
            return False, f"Никнейм '{username}' уже занят другим игроком.", None

        # Generate Minecraft offline UUID v3
        offline_uuid = generate_offline_uuid(username)

        user = User(
            telegram_id=telegram_id,
            username=username,
            uuid=offline_uuid,
            created_at=utc_now(),
            is_banned=False,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        logger.info(
            "Registered new user: %s (UUID: %s, TG: %d)",
            user.username,
            user.uuid,
            user.telegram_id,
        )
        return True, "Регистрация успешно завершена!", user

    @staticmethod
    async def change_username(
        session: AsyncSession,
        user: User,
        new_username: str,
    ) -> tuple[bool, str]:
        """Change player's Minecraft username and regenerate offline UUID."""
        if user.username.lower() == new_username.lower():
            return False, "Новый никнейм совпадает с текущим."

        valid, msg = AuthService.validate_username(new_username)
        if not valid:
            return False, msg

        existing = await AuthService.get_user_by_username(session, new_username)
        if existing and existing.id != user.id:
            return False, f"Никнейм '{new_username}' уже занят другим игроком."

        new_uuid = generate_offline_uuid(new_username)
        user.username = new_username
        user.uuid = new_uuid
        await session.commit()

        logger.info("User TG %d changed username to %s (UUID: %s)", user.telegram_id, new_username, new_uuid)
        return True, f"Никнейм успешно изменен на '{new_username}'!"

    @staticmethod
    async def confirm_auth_code(
        session: AsyncSession,
        code_str: str,
        telegram_id: int,
    ) -> tuple[bool, str, Optional[AuthCode], Optional[User]]:
        """
        Verify code from launcher, attach user, and generate session token.
        """
        auth_code = await CodeService.get_code(session, code_str)
        if not auth_code:
            return False, "Код не найден или введен неверно.", None, None

        if auth_code.is_expired:
            return False, "Срок действия кода истек (5 минут). Запросите новый в лаунчере.", None, None

        if auth_code.is_used:
            return False, "Этот код уже был использован.", None, None

        user = await AuthService.get_user_by_telegram_id(session, telegram_id)
        if not user:
            return False, "Сначала зарегистрируйтесь в боте с помощью команды /start.", None, None

        if user.is_banned:
            return False, "Ваш аккаунт заблокирован администратором.", None, user

        # Generate JWT session token
        token, expires_at = TokenService.create_token(
            user_id=user.id,
            username=user.username,
            uuid_str=user.uuid,
            telegram_id=user.telegram_id,
        )

        # Create active session
        active_session = ActiveSession(
            user_id=user.id,
            token=token,
            created_at=utc_now(),
            expires_at=expires_at,
            is_revoked=False,
        )
        session.add(active_session)

        # Mark code as confirmed
        auth_code.is_used = True
        auth_code.used_by_telegram_id = telegram_id
        auth_code.session_token = token

        await session.commit()
        await session.refresh(auth_code)

        logger.info(
            "Auth code %s confirmed by user %s (TG: %d)",
            code_str,
            user.username,
            telegram_id,
        )
        return True, "Авторизация подтверждена!", auth_code, user

    @staticmethod
    async def validate_token(
        session: AsyncSession,
        token: str,
    ) -> tuple[bool, str, Optional[User]]:
        """
        Validate JWT token from launcher against database sessions and user status.
        """
        payload = TokenService.decode_token(token)
        if not payload:
            return False, "token_expired", None

        sub_raw = payload.get("sub")
        try:
            user_id = int(sub_raw) if sub_raw is not None else None
        except (ValueError, TypeError):
            return False, "token_invalid", None
        stmt = (
            select(ActiveSession)
            .options(selectinload(ActiveSession.user))
            .where(
                ActiveSession.token == token,
                ActiveSession.user_id == user_id,
                ActiveSession.is_revoked.is_(False),
                ActiveSession.expires_at > utc_now(),
            )
        )
        result = await session.execute(stmt)
        active_session = result.scalar_one_or_none()

        if not active_session or not active_session.user:
            return False, "session_invalid_or_revoked", None

        if active_session.user.is_banned:
            return False, "user_banned", None

        return True, "valid", active_session.user

    @staticmethod
    async def revoke_token(
        session: AsyncSession,
        token: str,
    ) -> bool:
        """Revoke a specific session token."""
        stmt = (
            update(ActiveSession)
            .where(ActiveSession.token == token)
            .values(is_revoked=True)
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount > 0

    @staticmethod
    async def get_active_sessions(
        session: AsyncSession,
        user_id: int,
    ) -> list[ActiveSession]:
        """Fetch list of active unrevoked sessions for user."""
        stmt = (
            select(ActiveSession)
            .where(
                ActiveSession.user_id == user_id,
                ActiveSession.is_revoked.is_(False),
                ActiveSession.expires_at > utc_now(),
            )
            .order_by(ActiveSession.created_at.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def revoke_session_by_id(
        session: AsyncSession,
        user_id: int,
        session_id: int,
    ) -> bool:
        """Revoke a specific session by its primary key ID belonging to user."""
        stmt = (
            update(ActiveSession)
            .where(
                ActiveSession.id == session_id,
                ActiveSession.user_id == user_id,
            )
            .values(is_revoked=True)
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount > 0

    @staticmethod
    async def revoke_all_user_sessions(
        session: AsyncSession,
        user_id: int,
    ) -> int:
        """Revoke all active sessions for a given user."""
        stmt = (
            update(ActiveSession)
            .where(
                ActiveSession.user_id == user_id,
                ActiveSession.is_revoked.is_(False),
            )
            .values(is_revoked=True)
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount

    @staticmethod
    async def set_user_ban_status(
        session: AsyncSession,
        identifier: str,
        is_banned: bool,
    ) -> tuple[bool, str, Optional[User]]:
        """Ban or unban a player and revoke their sessions on ban."""
        user = await AuthService.get_user_by_identifier(session, identifier)
        if not user:
            return False, f"Пользователь '{identifier}' не найден.", None

        user.is_banned = is_banned
        if is_banned:
            # Revoke all active sessions
            await AuthService.revoke_all_user_sessions(session, user.id)

        await session.commit()
        status_text = "заблокирован" if is_banned else "разблокирован"
        logger.info("User %s (TG: %d) was %s", user.username, user.telegram_id, status_text)
        return True, f"Пользователь {user.username} успешно {status_text}.", user
