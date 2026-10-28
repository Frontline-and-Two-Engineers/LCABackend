"""Admin management handlers for modpack and user moderation."""

import io
import logging
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from bot.config import config
from bot.db.models import ModpackFile
from bot.services.auth_service import AuthService
from bot.services.modpack_service import ModpackService, ModpackSecurityError
from bot.keyboards.main_kb import (
    get_upload_path_keyboard,
    get_delete_mod_keyboard,
    get_confirm_delete_keyboard,
    get_cancel_keyboard,
)

logger = logging.getLogger(__name__)
router = Router(name="admin_router")


# Admin authorization filter
def is_admin_user(user_id: int) -> bool:
    return config.is_admin(user_id)


class UploadModStates(StatesGroup):
    waiting_for_file = State()
    waiting_for_path = State()


class SetVersionStates(StatesGroup):
    waiting_for_version = State()
    waiting_for_changelog = State()


# ---------------- Modpack Info ---------------- #


@router.message(Command("modpack_info"))
async def cmd_modpack_info(message: Message, session: AsyncSession) -> None:
    """Show modpack statistics."""
    if not message.from_user or not is_admin_user(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return

    stats = await ModpackService.get_stats(session)
    updated_str = (
        stats["last_updated"].strftime("%d.%m.%Y %H:%M")
        if stats["last_updated"]
        else "Никогда"
    )

    text = (
        "📦 <b>Информация о сборке LCALauncher</b>\n\n"
        f"🏷️ Текущая версия: <b>{stats['version']}</b>\n"
        f"📁 Всего файлов: <b>{stats['total_files']}</b>\n"
        f"💾 Общий вес: <b>{stats['total_size_mb']} MB</b> ({stats['total_size']} байт)\n"
        f"🕒 Последнее обновление: <i>{updated_str}</i>\n"
        f"📂 Папка хранения: <code>{config.modpack_dir}</code>"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("sync_mods"))
async def cmd_sync_mods(message: Message, session: AsyncSession) -> None:
    """Scan data/modpack/ folder on disk and synchronize database records."""
    if not message.from_user or not is_admin_user(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return

    await message.answer("🔄 <i>Сканирование папки data/modpack/ и синхронизация файлов...</i>", parse_mode="HTML")
    res = await ModpackService.sync_disk_files(session, message.from_user.id)
    text = (
        "✅ <b>Синхронизация файлов сборки завершена!</b>\n\n"
        f"➕ Добавлено новых файлов: <b>{res['added']}</b>\n"
        f"🔄 Обновлено измененных: <b>{res['updated']}</b>\n"
        f"🗑️ Удалено отсутствующих: <b>{res['deleted']}</b>\n"
        f"📦 Всего файлов в сборке: <b>{res['total']}</b>"
    )
    await message.answer(text, parse_mode="HTML")


# ---------------- Upload Mod / File ---------------- #


@router.message(Command("upload"))
async def cmd_upload_start(message: Message, state: FSMContext) -> None:
    """Start mod file upload wizard."""
    if not message.from_user or not is_admin_user(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return

    await state.clear()
    await state.set_state(UploadModStates.waiting_for_file)
    await message.answer(
        "📎 <b>Загрузка файла в сборку</b>\n\n"
        "Отправьте файл боту (как документ, без сжатия).\n"
        "Поддерживаются файлы <code>.jar</code>, <code>.toml</code>, <code>.json</code>, <code>.zip</code> и др.",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML",
    )


@router.message(UploadModStates.waiting_for_file, F.document)
async def process_uploaded_document(message: Message, state: FSMContext, bot: Bot) -> None:
    """Receive document from admin and ask for relative path."""
    if not message.document:
        return

    doc = message.document
    filename = doc.file_name or "file.bin"

    # Download file content into memory
    downloaded_stream: io.BytesIO = await bot.download(doc)  # type: ignore
    file_bytes = downloaded_stream.getvalue()

    await state.update_data(
        file_bytes=file_bytes,
        original_filename=filename,
    )
    await state.set_state(UploadModStates.waiting_for_path)

    default_path = f"mods/{filename}"
    await message.answer(
        f"📥 Файл <b>{filename}</b> получен ({len(file_bytes)} байт).\n\n"
        f"Укажите относительный путь назначения в сборке (например: <code>{default_path}</code>) "
        "или выберите вариант кнопкой ниже:",
        reply_markup=get_upload_path_keyboard(filename),
        parse_mode="HTML",
    )


@router.callback_query(UploadModStates.waiting_for_path, F.data.startswith("set_upload_path:"))
async def process_callback_path(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Handle path chosen via inline button."""
    if not callback.data:
        return
    chosen_path = callback.data.split("set_upload_path:", 1)[1]
    await callback.answer()
    await _finalize_upload(callback.message, state, session, chosen_path, callback.from_user.id)


@router.message(UploadModStates.waiting_for_path, F.text)
async def process_text_path(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Handle custom relative path entered by admin."""
    if not message.text or not message.from_user:
        return
    chosen_path = message.text.strip()
    await _finalize_upload(message, state, session, chosen_path, message.from_user.id)


async def _finalize_upload(
    message: Message | None,
    state: FSMContext,
    session: AsyncSession,
    relative_path: str,
    user_id: int,
) -> None:
    """Save the uploaded file and notify admin."""
    data = await state.get_data()
    file_bytes: bytes = data.get("file_bytes", b"")
    await state.clear()

    if not file_bytes:
        if message:
            await message.answer("❌ Ошибка: файл не найден в памяти. Начните заново с /upload.")
        return

    try:
        mod_file = await ModpackService.save_file(
            session=session,
            relative_path=relative_path,
            content=file_bytes,
            uploaded_by_telegram_id=user_id,
        )
        size_mb = round(mod_file.size / (1024 * 1024), 2)
        if message:
            await message.answer(
                "✅ <b>Файл успешно добавлен в сборку!</b>\n\n"
                f"📁 Путь: <code>{mod_file.relative_path}</code>\n"
                f"💾 Размер: <b>{size_mb} MB</b> ({mod_file.size} байт)\n"
                f"🔑 SHA-256: <code>{mod_file.sha256}</code>",
                parse_mode="HTML",
            )
    except ModpackSecurityError as e:
        if message:
            await message.answer(f"❌ Ошибка безопасности пути: {e}")
    except Exception as e:
        logger.error("Error saving modpack file: %s", e)
        if message:
            await message.answer(f"❌ Произошла ошибка при сохранении: {e}")


# ---------------- Delete Mod ---------------- #


@router.message(Command("delete_mod"))
async def cmd_delete_mod(message: Message, session: AsyncSession) -> None:
    """Display list of files to select for deletion."""
    if not message.from_user or not is_admin_user(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return

    files = await ModpackService.list_files(session)
    if not files:
        await message.answer("В сборке пока нет файлов для удаления.")
        return

    await message.answer(
        "🗑️ <b>Удаление файла из сборки</b>\n\nВыберите файл для удаления:",
        reply_markup=get_delete_mod_keyboard(files),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("delete_file_select:"))
async def cb_select_file_for_delete(callback: CallbackQuery, session: AsyncSession) -> None:
    """Prompt for confirmation to delete chosen file."""
    if not callback.from_user or not is_admin_user(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    file_id_str = callback.data.split(":")[-1] if callback.data else ""
    if not file_id_str.isdigit():
        return

    file_id = int(file_id_str)
    stmt = select(ModpackFile).where(ModpackFile.id == file_id)
    res = await session.execute(stmt)
    mod_file = res.scalar_one_or_none()

    if not mod_file:
        await callback.answer("Файл не найден в базе данных.", show_alert=True)
        return

    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            f"⚠️ <b>Подтверждение удаления</b>\n\n"
            f"Вы точно хотите удалить файл <code>{mod_file.relative_path}</code> из сборки и с диска?",
            reply_markup=get_confirm_delete_keyboard(mod_file.id, mod_file.relative_path),
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("confirm_delete_file:"))
async def cb_confirm_delete_file(callback: CallbackQuery, session: AsyncSession) -> None:
    """Execute deletion of chosen file."""
    if not callback.from_user or not is_admin_user(callback.from_user.id):
        await callback.answer("⛔ Нет доступа", show_alert=True)
        return

    file_id_str = callback.data.split(":")[-1] if callback.data else ""
    if not file_id_str.isdigit():
        return

    file_id = int(file_id_str)
    stmt = select(ModpackFile).where(ModpackFile.id == file_id)
    res = await session.execute(stmt)
    mod_file = res.scalar_one_or_none()

    if not mod_file:
        await callback.answer("Файл уже удален", show_alert=True)
        return

    rel_path = mod_file.relative_path
    await ModpackService.delete_file(session, rel_path)
    await callback.answer(f"Файл {rel_path} удален!", show_alert=True)

    if callback.message:
        await callback.message.edit_text(
            f"✅ Файл <code>{rel_path}</code> успешно удален из сборки.",
            parse_mode="HTML",
        )


# ---------------- Set Version ---------------- #


@router.message(Command("set_version"))
async def cmd_set_version(message: Message, state: FSMContext) -> None:
    """Prompt for new modpack version string."""
    if not message.from_user or not is_admin_user(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return

    await state.clear()
    await state.set_state(SetVersionStates.waiting_for_version)
    await message.answer(
        "🏷️ <b>Обновление версии сборки</b>\n\n"
        "Введите новую семантическую версию сборки (например: <code>1.2.0</code>):",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML",
    )


@router.message(SetVersionStates.waiting_for_version, F.text)
async def process_version_input(message: Message, state: FSMContext) -> None:
    """Receive version string and ask for changelog."""
    if not message.text:
        return

    ver_str = message.text.strip()
    await state.update_data(version=ver_str)
    await state.set_state(SetVersionStates.waiting_for_changelog)

    await message.answer(
        f"Версия: <b>{ver_str}</b>\n\n"
        "Теперь введите описание изменений (changelog), либо отправьте <code>-</code> если изменений нет:",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML",
    )


@router.message(SetVersionStates.waiting_for_changelog, F.text)
async def process_changelog_input(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    """Save new version and changelog."""
    if not message.text or not message.from_user:
        return

    changelog = message.text.strip()
    if changelog == "-":
        changelog = None

    data = await state.get_data()
    version = data.get("version", "1.0.0")
    await state.clear()

    ver_obj = await ModpackService.create_version(
        session=session,
        version=version,
        changelog=changelog,
        created_by_telegram_id=message.from_user.id,
    )

    await message.answer(
        f"✅ <b>Версия сборки успешно обновлена на {ver_obj.version}!</b>\n\n"
        f"📝 Changelog:\n{ver_obj.changelog or '<i>(без описания)</i>'}",
        parse_mode="HTML",
    )


# ---------------- User Moderation (/ban, /unban, /userinfo) ---------------- #


@router.message(Command("ban"))
async def cmd_ban(message: Message, session: AsyncSession) -> None:
    """Ban a player by nickname or Telegram ID."""
    if not message.from_user or not is_admin_user(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return

    args = message.text.split()[1:] if message.text else []
    if not args:
        await message.answer("Использование: <code>/ban &lt;никнейм_или_telegram_id&gt;</code>", parse_mode="HTML")
        return

    target = args[0]
    success, msg, user = await AuthService.set_user_ban_status(session, target, is_banned=True)
    if not success:
        await message.answer(f"❌ {msg}")
    else:
        await message.answer(f"⛔ <b>{msg}</b>\nВсе активные сессии в лаунчере отозваны.", parse_mode="HTML")


@router.message(Command("unban"))
async def cmd_unban(message: Message, session: AsyncSession) -> None:
    """Unban a player by nickname or Telegram ID."""
    if not message.from_user or not is_admin_user(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return

    args = message.text.split()[1:] if message.text else []
    if not args:
        await message.answer("Использование: <code>/unban &lt;никнейм_или_telegram_id&gt;</code>", parse_mode="HTML")
        return

    target = args[0]
    success, msg, user = await AuthService.set_user_ban_status(session, target, is_banned=False)
    if not success:
        await message.answer(f"❌ {msg}")
    else:
        await message.answer(f"✅ <b>{msg}</b>", parse_mode="HTML")


@router.message(Command("userinfo"))
async def cmd_userinfo(message: Message, session: AsyncSession) -> None:
    """Inspect user profile and sessions."""
    if not message.from_user or not is_admin_user(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return

    args = message.text.split()[1:] if message.text else []
    if not args:
        await message.answer("Использование: <code>/userinfo &lt;никнейм_или_telegram_id&gt;</code>", parse_mode="HTML")
        return

    target = args[0]
    user = await AuthService.get_user_by_identifier(session, target)
    if not user:
        await message.answer(f"❌ Пользователь '{target}' не найден.")
        return

    sessions = await AuthService.get_active_sessions(session, user.id)
    ban_badge = "⛔ Заблокирован" if user.is_banned else "✅ Не заблокирован"

    text = (
        "🔍 <b>Информация об игроке</b>\n\n"
        f"🎮 Никнейм: <b>{user.username}</b>\n"
        f"📱 Telegram ID: <code>{user.telegram_id}</code>\n"
        f"🆔 Minecraft UUID: <code>{user.uuid}</code>\n"
        f"📅 Регистрация: <i>{user.created_at.strftime('%d.%m.%Y %H:%M')}</i>\n"
        f"🛡️ Статус: {ban_badge}\n"
        f"🔑 Активных сессий: <b>{len(sessions)}</b>"
    )
    await message.answer(text, parse_mode="HTML")
