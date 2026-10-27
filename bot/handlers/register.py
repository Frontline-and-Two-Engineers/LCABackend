"""Registration flow for new players."""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from bot.services.auth_service import AuthService
from bot.keyboards.main_kb import get_cancel_keyboard, get_start_keyboard

router = Router(name="register_router")


class RegisterStates(StatesGroup):
    waiting_for_username = State()


@router.message(Command("register"))
@router.callback_query(F.data == "start_registration")
async def start_registration(event: Message | CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Initiate registration flow."""
    user_id = event.from_user.id if event.from_user else 0
    existing = await AuthService.get_user_by_telegram_id(session, user_id)
    if existing:
        msg = f"Вы уже зарегистрированы под ником <b>{existing.username}</b>!"
        if isinstance(event, CallbackQuery):
            await event.answer()
            if event.message:
                await event.message.answer(msg, reply_markup=get_start_keyboard(True), parse_mode="HTML")
        else:
            await event.answer(msg, reply_markup=get_start_keyboard(True), parse_mode="HTML")
        return

    text = (
        "🎮 <b>Регистрация игрового профиля</b>\n\n"
        "Введите желаемый никнейм для Minecraft:\n"
        "• От 3 до 16 символов\n"
        "• Только латинские буквы, цифры и символ подчеркивания (<code>_</code>)\n\n"
        "<i>Пример: Steve, Alex_99, CoolMiner</i>"
    )

    if isinstance(event, CallbackQuery):
        await event.answer()
        if event.message:
            await event.message.answer(text, reply_markup=get_cancel_keyboard(), parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=get_cancel_keyboard(), parse_mode="HTML")

    await state.set_state(RegisterStates.waiting_for_username)


@router.message(RegisterStates.waiting_for_username, F.text)
async def process_username(message: Message, state: FSMContext, session: AsyncSession) -> None:
    """Handle entered username during registration."""
    if not message.from_user or not message.text:
        return

    username = message.text.strip()
    success, msg, user = await AuthService.register_user(
        session=session,
        telegram_id=message.from_user.id,
        username=username,
    )

    if not success or not user:
        await message.answer(
            f"❌ <b>Ошибка:</b> {msg}\n\nПопробуйте ввести другой никнейм или нажмите /cancel:",
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    await state.clear()
    congrats = (
        "🎉 <b>Регистрация успешно завершена!</b>\n\n"
        f"👤 Игровой ник: <code>{user.username}</code>\n"
        f"🆔 Minecraft UUID: <code>{user.uuid}</code>\n\n"
        "🚀 Теперь вы можете войти в игру:\n"
        "1. Откройте <b>LCALauncher</b> на компьютере.\n"
        "2. Введите отображенный там 6-значный код прямо сюда в чат."
    )
    await message.answer(congrats, reply_markup=get_start_keyboard(True), parse_mode="HTML")
