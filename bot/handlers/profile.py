"""Profile and session management handlers."""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from bot.services.auth_service import AuthService
from bot.keyboards.main_kb import (
    get_profile_keyboard,
    get_sessions_keyboard,
    get_confirm_revoke_all_keyboard,
    get_cancel_keyboard,
    get_start_keyboard,
)

router = Router(name="profile_router")


class ChangeNickStates(StatesGroup):
    waiting_for_new_username = State()


def format_profile_text(user, active_sessions_count: int) -> str:
    """Format user profile message."""
    created_str = user.created_at.strftime("%d.%m.%Y %H:%M")
    ban_status = "⛔ Заблокирован" if user.is_banned else "✅ Активен"
    return (
        "👤 <b>Профиль игрока</b>\n\n"
        f"🎮 Никнейм: <b>{user.username}</b>\n"
        f"🆔 Minecraft UUID: <code>{user.uuid}</code>\n"
        f"📅 Дата регистрации: <i>{created_str}</i>\n"
        f"🛡️ Статус аккаунта: {ban_status}\n"
        f"🔑 Активных сессий в лаунчере: <b>{active_sessions_count}</b>"
    )


@router.message(Command("profile"))
@router.callback_query(F.data == "open_profile")
async def show_profile(event: Message | CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    """Show player profile."""
    await state.clear()
    user_id = event.from_user.id if event.from_user else 0
    user = await AuthService.get_user_by_telegram_id(session, user_id)

    if not user:
        text = "Вы еще не зарегистрированы. Нажмите кнопку ниже для регистрации:"
        if isinstance(event, CallbackQuery):
            await event.answer()
            if event.message:
                await event.message.answer(text, reply_markup=get_start_keyboard(False))
        else:
            await event.answer(text, reply_markup=get_start_keyboard(False))
        return

    sessions = await AuthService.get_active_sessions(session, user.id)
    text = format_profile_text(user, len(sessions))

    if isinstance(event, CallbackQuery):
        await event.answer()
        if event.message:
            await event.message.edit_text(text, reply_markup=get_profile_keyboard(), parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=get_profile_keyboard(), parse_mode="HTML")


@router.message(Command("sessions"))
@router.callback_query(F.data == "open_sessions")
async def show_sessions(event: Message | CallbackQuery, session: AsyncSession) -> None:
    """Show list of active launcher sessions."""
    user_id = event.from_user.id if event.from_user else 0
    user = await AuthService.get_user_by_telegram_id(session, user_id)

    if not user:
        if isinstance(event, CallbackQuery):
            await event.answer("Сначала зарегистрируйтесь", show_alert=True)
        return

    sessions = await AuthService.get_active_sessions(session, user.id)

    if not sessions:
        text = "🔑 <b>Активные сессии</b>\n\nУ вас сейчас нет активных сессий в лаунчере."
    else:
        text = f"🔑 <b>Активные сессии ({len(sessions)})</b>\n\nНиже отображен список подключенных устройств. Вы можете завершить любую из сессий:"

    kb = get_sessions_keyboard(sessions)

    if isinstance(event, CallbackQuery):
        await event.answer()
        if event.message:
            await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("revoke_session:"))
async def revoke_single_session(callback: CallbackQuery, session: AsyncSession) -> None:
    """Revoke a single selected session."""
    user_id = callback.from_user.id if callback.from_user else 0
    user = await AuthService.get_user_by_telegram_id(session, user_id)
    if not user or not callback.data:
        await callback.answer()
        return

    session_id_str = callback.data.split(":")[-1]
    if session_id_str.isdigit():
        session_id = int(session_id_str)
        revoked = await AuthService.revoke_session_by_id(session, user.id, session_id)
        if revoked:
            await callback.answer(f"Сессия #{session_id} завершена!", show_alert=True)
        else:
            await callback.answer("Сессия не найдена или уже завершена", show_alert=True)

    # Refresh sessions view
    sessions = await AuthService.get_active_sessions(session, user.id)
    text = (
        f"🔑 <b>Активные сессии ({len(sessions)})</b>\n\n"
        "Список обновлен:"
    ) if sessions else "🔑 <b>Активные сессии</b>\n\nВсе сессии завершены."

    if callback.message:
        await callback.message.edit_text(text, reply_markup=get_sessions_keyboard(sessions), parse_mode="HTML")


@router.callback_query(F.data == "confirm_revoke_all")
async def confirm_revoke_all_sessions(callback: CallbackQuery) -> None:
    """Ask for confirmation before revoking all sessions."""
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "⚠️ <b>Вы уверены, что хотите завершить ВСЕ активные сессии в лаунчере?</b>\n"
            "Вам потребуется повторно ввести 6-значный код на всех устройствах.",
            reply_markup=get_confirm_revoke_all_keyboard(),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "do_revoke_all")
async def do_revoke_all_sessions(callback: CallbackQuery, session: AsyncSession) -> None:
    """Revoke all sessions for player."""
    user_id = callback.from_user.id if callback.from_user else 0
    user = await AuthService.get_user_by_telegram_id(session, user_id)
    if not user:
        await callback.answer()
        return

    count = await AuthService.revoke_all_user_sessions(session, user.id)
    await callback.answer(f"Завершено сессий: {count}", show_alert=True)

    if callback.message:
        await callback.message.edit_text(
            f"✅ <b>Все активные сессии ({count}) были успешно отозваны!</b>",
            reply_markup=get_profile_keyboard(),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "change_nickname")
async def start_change_nickname(callback: CallbackQuery, state: FSMContext) -> None:
    """Initiate change nickname flow."""
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "✏️ <b>Смена никнейма</b>\n\n"
            "Введите новый игровой никнейм (3-16 символов, только латиница, цифры и <code>_</code>):\n"
            "<i>Обратите внимание: UUID будет пересчитан в соответствии с новым ником!</i>",
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML",
        )
    await state.set_state(ChangeNickStates.waiting_for_new_username)


@router.message(ChangeNickStates.waiting_for_new_username, F.text)
async def process_new_nickname(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Handle new nickname input."""
    if not message.from_user or not message.text:
        return

    user = await AuthService.get_user_by_telegram_id(session, message.from_user.id)
    if not user:
        await state.clear()
        return

    new_nick = message.text.strip()
    success, msg = await AuthService.change_username(session, user, new_nick)

    if not success:
        await message.answer(
            f"❌ <b>Ошибка:</b> {msg}\n\nПопробуйте другой никнейм или отмените:",
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    await state.clear()
    await message.answer(
        f"✅ <b>Никнейм успешно изменен!</b>\n\n"
        f"👤 Новый ник: <b>{user.username}</b>\n"
        f"🆔 Новый UUID: <code>{user.uuid}</code>",
        reply_markup=get_profile_keyboard(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "cancel_action")
@router.message(Command("cancel"))
async def cancel_any_action(event: Message | CallbackQuery, state: FSMContext) -> None:
    """Cancel current active FSM state."""
    await state.clear()
    msg_text = "Действие отменено."
    if isinstance(event, CallbackQuery):
        await event.answer("Отменено")
        if event.message:
            await event.message.answer(msg_text, reply_markup=get_start_keyboard(True))
    else:
        await event.answer(msg_text, reply_markup=get_start_keyboard(True))
