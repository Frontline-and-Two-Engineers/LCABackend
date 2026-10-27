"""Telegram Bot handlers for skin and cape management."""

import io
import logging
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from sqlalchemy.ext.asyncio import AsyncSession
from bot.config import config
from bot.services.auth_service import AuthService
from bot.services.skin_service import SkinService, TextureValidationError
from bot.keyboards.main_kb import (
    get_skin_management_keyboard,
    get_cape_management_keyboard,
    get_skin_model_choice_keyboard,
    get_cancel_keyboard,
    get_start_keyboard,
)

logger = logging.getLogger(__name__)
router = Router(name="skin_router")


class UploadSkinStates(StatesGroup):
    waiting_for_file = State()
    waiting_for_model = State()


class UploadCapeStates(StatesGroup):
    waiting_for_file = State()


# ---------------- Skin Management ---------------- #


@router.message(Command("skin"))
@router.callback_query(F.data == "manage_skin")
async def show_skin_management(event: Message | CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    """Display skin management screen."""
    await state.clear()
    user_id = event.from_user.id if event.from_user else 0
    user = await AuthService.get_user_by_telegram_id(session, user_id)

    if not user:
        text = "Сначала зарегистрируйтесь в боте с помощью /start."
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text, reply_markup=get_start_keyboard(False))
        return

    has_skin = user.skin_hash is not None
    model_name = "Classic (Steve, 4px)" if user.skin_model == "classic" else "Slim (Alex, 3px)"
    status_text = "Кастомный скин установлен" if has_skin else "Стандартный скин (Steve/Alex)"

    text = (
        "🎨 <b>Управление скином Minecraft</b>\n\n"
        f"👤 Игрок: <b>{user.username}</b>\n"
        f"🎭 Статус: <b>{status_text}</b>\n"
        f"✂️ Модель рук: <b>{model_name}</b>\n\n"
        "Вы можете загрузить собственный файл скина в формате <code>.png</code> (разрешение <b>64x64</b> или <b>64x32</b>)."
    )

    kb = get_skin_management_keyboard(has_skin, user.skin_model)

    if isinstance(event, CallbackQuery):
        await event.answer()
        if event.message:
            await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "toggle_skin_model")
async def cb_toggle_skin_model(callback: CallbackQuery, session: AsyncSession) -> None:
    """Switch skin model between classic and slim."""
    user_id = callback.from_user.id if callback.from_user else 0
    user = await AuthService.get_user_by_telegram_id(session, user_id)
    if not user:
        await callback.answer()
        return

    new_model = "slim" if user.skin_model == "classic" else "classic"
    await SkinService.set_user_skin_model(session, user, new_model)
    model_title = "Classic (4px)" if new_model == "classic" else "Slim (3px)"
    await callback.answer(f"Модель изменена на {model_title}!")

    # Refresh screen
    has_skin = user.skin_hash is not None
    status_text = "Кастомный скин установлен" if has_skin else "Стандартный скин (Steve/Alex)"
    text = (
        "🎨 <b>Управление скином Minecraft</b>\n\n"
        f"👤 Игрок: <b>{user.username}</b>\n"
        f"🎭 Статус: <b>{status_text}</b>\n"
        f"✂️ Модель рук: <b>{model_title}</b>\n\n"
        "Вы можете загрузить собственный файл скина в формате <code>.png</code> (разрешение <b>64x64</b> или <b>64x32</b>)."
    )
    if callback.message:
        await callback.message.edit_text(
            text,
            reply_markup=get_skin_management_keyboard(has_skin, new_model),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "clear_skin")
async def cb_clear_skin(callback: CallbackQuery, session: AsyncSession) -> None:
    """Reset user skin to default."""
    user_id = callback.from_user.id if callback.from_user else 0
    user = await AuthService.get_user_by_telegram_id(session, user_id)
    if not user:
        await callback.answer()
        return

    await SkinService.clear_user_skin(session, user)
    await callback.answer("Скин сброшен на стандартный!", show_alert=True)

    # Refresh view
    model_name = "Classic (Steve, 4px)" if user.skin_model == "classic" else "Slim (Alex, 3px)"
    text = (
        "🎨 <b>Управление скином Minecraft</b>\n\n"
        f"👤 Игрок: <b>{user.username}</b>\n"
        "🎭 Статус: <b>Стандартный скин (Steve/Alex)</b>\n"
        f"✂️ Модель рук: <b>{model_name}</b>\n\n"
        "Скин сброшен. Чтобы установить новый, нажмите кнопку ниже:"
    )
    if callback.message:
        await callback.message.edit_text(
            text,
            reply_markup=get_skin_management_keyboard(False, user.skin_model),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "upload_skin_start")
async def cb_upload_skin_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to upload a PNG skin file."""
    await callback.answer()
    await state.set_state(UploadSkinStates.waiting_for_file)
    text = (
        "📥 <b>Загрузка скина</b>\n\n"
        "Отправьте файл скина в чат <b>документом (без сжатия фото)</b>.\n"
        "Требования:\n"
        "• Формат: <code>.png</code>\n"
        "• Разрешение: <b>64x64</b> или <b>64x32</b> пикселей"
    )
    if callback.message:
        await callback.message.answer(text, reply_markup=get_cancel_keyboard(), parse_mode="HTML")


@router.message(UploadSkinStates.waiting_for_file, F.document)
async def process_skin_document(message: Message, state: FSMContext, bot: Bot) -> None:
    """Receive PNG skin file, validate dimensions, and ask for model."""
    if not message.document:
        return

    filename = (message.document.file_name or "").lower()
    if not filename.endswith(".png"):
        await message.answer(
            "❌ Файл должен иметь расширение <code>.png</code>. Попробуйте еще раз:",
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    # Download document
    stream: io.BytesIO = await bot.download(message.document)  # type: ignore
    skin_bytes = stream.getvalue()

    try:
        w, h = SkinService.inspect_png_dimensions(skin_bytes)
    except TextureValidationError as e:
        await message.answer(f"❌ Ошибка валидации файла:\n{e}", reply_markup=get_cancel_keyboard())
        return

    await state.update_data(skin_bytes=skin_bytes)
    await state.set_state(UploadSkinStates.waiting_for_model)

    await message.answer(
        f"✅ Файл скина корректен ({w}x{h}px)!\n\n"
        "Выберите тип модели рук вашего скина:",
        reply_markup=get_skin_model_choice_keyboard(),
    )


@router.callback_query(UploadSkinStates.waiting_for_model, F.data.startswith("choose_model:"))
async def process_skin_model_choice(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Save skin with chosen model."""
    user_id = callback.from_user.id if callback.from_user else 0
    user = await AuthService.get_user_by_telegram_id(session, user_id)
    if not user or not callback.data:
        await callback.answer()
        return

    chosen_model = callback.data.split("choose_model:")[-1]
    data = await state.get_data()
    skin_bytes = data.get("skin_bytes")
    await state.clear()

    if not skin_bytes:
        await callback.answer("Ошибка: файл не найден в памяти", show_alert=True)
        return

    try:
        skin_hash = await SkinService.set_user_skin(session, user, skin_bytes, model=chosen_model)
        await callback.answer("Скин успешно установлен!", show_alert=True)
        model_name = "Classic (4px)" if chosen_model == "classic" else "Slim (3px)"

        success_text = (
            "🎉 <b>Скин успешно загружен и применен!</b>\n\n"
            f"👤 Игрок: <b>{user.username}</b>\n"
            f"✂️ Модель: <b>{model_name}</b>\n"
            f"🔑 SHA-256: <code>{skin_hash[:16]}...</code>\n\n"
            "При следующем входе на сервер или перезапуске лаунчера скин обновится."
        )
        if callback.message:
            await callback.message.edit_text(
                success_text,
                reply_markup=get_skin_management_keyboard(True, user.skin_model),
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error("Error setting user skin: %s", e)
        if callback.message:
            await callback.message.answer(f"❌ Ошибка при сохранении скина: {e}")


# ---------------- Cape Management ---------------- #


@router.message(Command("cape"))
@router.callback_query(F.data == "manage_cape")
async def show_cape_management(event: Message | CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    """Display cape management screen."""
    await state.clear()
    user_id = event.from_user.id if event.from_user else 0
    user = await AuthService.get_user_by_telegram_id(session, user_id)

    if not user:
        text = "Сначала зарегистрируйтесь в боте с помощью /start."
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text, reply_markup=get_start_keyboard(False))
        return

    has_cape = user.cape_hash is not None
    status_text = "Плащ установлен" if has_cape else "Плащ отсутствует"

    text = (
        "🧥 <b>Управление плащом Minecraft</b>\n\n"
        f"👤 Игрок: <b>{user.username}</b>\n"
        f"✨ Статус: <b>{status_text}</b>\n\n"
        "Вы можете загрузить собственный плащ в формате <code>.png</code> (разрешение <b>64x32</b> или <b>22x17</b> пикселей)."
    )

    kb = get_cape_management_keyboard(has_cape)

    if isinstance(event, CallbackQuery):
        await event.answer()
        if event.message:
            await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    else:
        await event.answer(text, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "clear_cape")
async def cb_clear_cape(callback: CallbackQuery, session: AsyncSession) -> None:
    """Remove user custom cape."""
    user_id = callback.from_user.id if callback.from_user else 0
    user = await AuthService.get_user_by_telegram_id(session, user_id)
    if not user:
        await callback.answer()
        return

    await SkinService.clear_user_cape(session, user)
    await callback.answer("Плащ удален!", show_alert=True)

    text = (
        "🧥 <b>Управление плащом Minecraft</b>\n\n"
        f"👤 Игрок: <b>{user.username}</b>\n"
        "✨ Статус: <b>Плащ отсутствует</b>\n\n"
        "Плащ удален. Чтобы загрузить новый, нажмите кнопку ниже:"
    )
    if callback.message:
        await callback.message.edit_text(
            text,
            reply_markup=get_cape_management_keyboard(False),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "upload_cape_start")
async def cb_upload_cape_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to upload a PNG cape file."""
    await callback.answer()
    await state.set_state(UploadCapeStates.waiting_for_file)
    text = (
        "📥 <b>Загрузка плаща</b>\n\n"
        "Отправьте файл плаща в чат <b>документом (без сжатия фото)</b>.\n"
        "Требования:\n"
        "• Формат: <code>.png</code>\n"
        "• Разрешение: <b>64x32</b> или <b>22x17</b> пикселей"
    )
    if callback.message:
        await callback.message.answer(text, reply_markup=get_cancel_keyboard(), parse_mode="HTML")


@router.message(UploadCapeStates.waiting_for_file, F.document)
async def process_cape_document(
    message: Message, state: FSMContext, bot: Bot, session: AsyncSession
) -> None:
    """Receive PNG cape file, validate, and save."""
    if not message.document or not message.from_user:
        return

    filename = (message.document.file_name or "").lower()
    if not filename.endswith(".png"):
        await message.answer(
            "❌ Файл должен иметь расширение <code>.png</code>. Попробуйте еще раз:",
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML",
        )
        return

    user = await AuthService.get_user_by_telegram_id(session, message.from_user.id)
    if not user:
        await state.clear()
        return

    stream: io.BytesIO = await bot.download(message.document)  # type: ignore
    cape_bytes = stream.getvalue()
    await state.clear()

    try:
        cape_hash = await SkinService.set_user_cape(session, user, cape_bytes)
        success_text = (
            "🎉 <b>Плащ успешно загружен и применен!</b>\n\n"
            f"👤 Игрок: <b>{user.username}</b>\n"
            f"🔑 SHA-256: <code>{cape_hash[:16]}...</code>\n\n"
            "Плащ будет отображаться на персонаже в игре!"
        )
        await message.answer(
            success_text,
            reply_markup=get_cape_management_keyboard(True),
            parse_mode="HTML",
        )
    except TextureValidationError as e:
        await message.answer(f"❌ Ошибка валидации файла плаща:\n{e}", reply_markup=get_cancel_keyboard())
    except Exception as e:
        logger.error("Error setting user cape: %s", e)
        await message.answer(f"❌ Ошибка при сохранении плаща: {e}")
