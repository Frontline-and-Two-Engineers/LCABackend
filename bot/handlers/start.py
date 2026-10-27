"""Start and Help handlers."""

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession
from bot.services.auth_service import AuthService
from bot.keyboards.main_kb import get_start_keyboard

router = Router(name="start_router")

HELP_TEXT = (
    "📖 <b>Инструкция по входу в LCALauncher</b>\n\n"
    "1. Запустите <b>LCALauncher</b> на вашем компьютере.\n"
    "2. На экране авторизации лаунчер сгенерирует и покажет <b>6-значный код</b>.\n"
    "3. Отправьте этот 6-значный код прямо в этот чат (например: <code>482917</code>).\n"
    "4. Бот подтвердит привязку, и лаунчер автоматически войдет в игру под вашим ником!\n\n"
    "📌 <i>Код действует 5 минут и является одноразовым.</i>\n\n"
    "Команды:\n"
    "/start — Главное меню\n"
    "/profile — Профиль игрока и сессии\n"
    "/sessions — Управление активными сессиями\n"
    "/help — Эта справка"
)


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession) -> None:
    """Handle /start command."""
    if not message.from_user:
        return

    user = await AuthService.get_user_by_telegram_id(session, message.from_user.id)

    if not user:
        text = (
            "👋 <b>Добро пожаловать в LCALauncher Auth!</b>\n\n"
            "Этот бот используется для безопасного входа в игру без логинов и паролей.\n\n"
            "Для начала вам необходимо зарегистрировать ваш игровой никнейм."
        )
        await message.answer(text, reply_markup=get_start_keyboard(is_registered=False), parse_mode="HTML")
    else:
        text = (
            f"👋 Привет, <b>{user.username}</b>!\n\n"
            f"🎮 Твой ник: <code>{user.username}</code>\n"
            f"🆔 UUID: <code>{user.uuid}</code>\n\n"
            "Для входа в игру просто отправь сюда <b>6-значный код</b>, который отображается в лаунчере."
        )
        await message.answer(text, reply_markup=get_start_keyboard(is_registered=True), parse_mode="HTML")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Handle /help command."""
    await message.answer(HELP_TEXT, parse_mode="HTML")


@router.callback_query(F.data == "open_help")
async def cb_help(callback: CallbackQuery) -> None:
    """Handle Help button callback."""
    await callback.answer()
    if callback.message:
        await callback.message.answer(HELP_TEXT, parse_mode="HTML")
