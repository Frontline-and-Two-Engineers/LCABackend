"""Authorization code processing handler for launcher login."""

import logging
from aiogram import Router, F
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession
from bot.middlewares.rate_limit import bot_code_limiter
from bot.services.auth_service import AuthService
from bot.keyboards.main_kb import get_start_keyboard

logger = logging.getLogger(__name__)
router = Router(name="auth_code_router")


@router.message(F.text.regexp(r"^\d{6}$"))
async def process_auth_code(message: Message, session: AsyncSession) -> None:
    """Handle 6-digit one-time code sent by the player."""
    if not message.from_user or not message.text:
        return

    telegram_id = message.from_user.id
    code_str = message.text.strip()

    # Rate limiting: max 5 attempts per minute per Telegram ID
    if not bot_code_limiter.is_allowed(telegram_id):
        logger.warning("Bot rate limit reached for user TG %d trying code %s", telegram_id, code_str)
        await message.answer(
            "⚠️ <b>Слишком много попыток ввода кода.</b>\n"
            "Пожалуйста, подождите одну минуту перед повторной попыткой.",
            parse_mode="HTML",
        )
        return

    success, msg, auth_code, user = await AuthService.confirm_auth_code(
        session=session,
        code_str=code_str,
        telegram_id=telegram_id,
    )

    if not success:
        logger.info("Failed auth code confirmation for TG %d: %s", telegram_id, msg)
        await message.answer(
            f"❌ <b>Ошибка авторизации:</b>\n{msg}",
            reply_markup=get_start_keyboard(is_registered=user is not None),
            parse_mode="HTML",
        )
        return

    logger.info("Successful launcher login for %s (TG: %d)", user.username, telegram_id)
    success_text = (
        "✅ <b>Авторизация успешна!</b>\n\n"
        f"🎮 Лаунчер подключен к аккаунту: <b>{user.username}</b>\n"
        "Сессия активирована. Лаунчер автоматически начнет загрузку игры!"
    )
    await message.answer(success_text, reply_markup=get_start_keyboard(True), parse_mode="HTML")
