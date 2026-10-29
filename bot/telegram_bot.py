"""Native MTProto Telegram Bot implementation using Telethon."""

import io
import logging
import re
import urllib.parse
from typing import Optional
from telethon import TelegramClient, events, Button
from telethon.network.connection import ConnectionTcpMTProxyRandomizedIntermediate

from bot.config import config
from bot.db.engine import get_db_session
from bot.middlewares.rate_limit import bot_code_limiter
from bot.services.auth_service import AuthService
from bot.services.code_service import CodeService
from bot.services.modpack_service import ModpackService, ModpackSecurityError
from bot.services.skin_service import SkinService, TextureValidationError
from bot.services.state_service import state_manager
from bot.keyboards.main_kb import (
    get_start_keyboard,
    get_profile_keyboard,
    get_sessions_keyboard,
    get_confirm_revoke_all_keyboard,
    get_skin_management_keyboard,
    get_cape_management_keyboard,
    get_skin_model_choice_keyboard,
    get_upload_path_keyboard,
    get_delete_mod_keyboard,
    get_confirm_delete_keyboard,
    get_cancel_keyboard,
)

logger = logging.getLogger(__name__)

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
    "/skin — Управление скином\n"
    "/cape — Управление плащом\n"
    "/sessions — Управление активными сессиями\n"
    "/help — Эта справка"
)


def create_telegram_client() -> TelegramClient:
    """Initialize Telethon MTProto client with optional MTProxy or SOCKS5 proxy."""
    proxy = None
    connection = None

    # 1. Native MTProto Proxy (tg://proxy?server=...&port=...&secret=...)
    if config.mtproto_proxy_host and config.mtproto_proxy_port and config.mtproto_proxy_secret:
        proxy = (
            config.mtproto_proxy_host,
            config.mtproto_proxy_port,
            config.mtproto_proxy_secret,
        )
        connection = ConnectionTcpMTProxyRandomizedIntermediate
        logger.info(
            "Configured native MTProto Proxy: %s:%d",
            config.mtproto_proxy_host,
            config.mtproto_proxy_port,
        )
    # 2. SOCKS5 / HTTP Proxy fallback
    elif config.telegram_proxy:
        parsed = urllib.parse.urlparse(config.telegram_proxy)
        scheme = parsed.scheme.lower()
        proxy_type = "socks5" if "socks5" in scheme else ("socks4" if "socks4" in scheme else "http")
        proxy = {
            "proxy_type": proxy_type,
            "addr": parsed.hostname,
            "port": parsed.port or 1080,
            "username": parsed.username,
            "password": parsed.password,
            "rdns": True,
        }
        logger.info("Configured %s proxy for MTProto: %s:%d", proxy_type, parsed.hostname, parsed.port)

    session_file = str(config.data_dir / "mtproto_bot.session")
    client = TelegramClient(
        session=session_file,
        api_id=config.api_id,
        api_hash=config.api_hash,
        proxy=proxy,
        connection=connection,
    )
    return client


def setup_bot_handlers(client: TelegramClient) -> None:
    """Register all command, message and callback query handlers."""

    def is_admin(user_id: int) -> bool:
        return config.is_admin(user_id)

    # ---------------- Commands ---------------- #

    @client.on(events.NewMessage(pattern=r"^/start$"))
    async def handle_start(event: events.NewMessage.Event) -> None:
        user_id = event.sender_id
        state_manager.clear(user_id)

        async with get_db_session() as session:
            user = await AuthService.get_user_by_telegram_id(session, user_id)
            if not user:
                text = (
                    "👋 <b>Добро пожаловать в LCALauncher Auth (MTProto)!</b>\n\n"
                    "Этот бот используется для безопасного входа в игру без логинов и паролей.\n\n"
                    "Для начала вам необходимо зарегистрировать ваш игровой никнейм."
                )
                await event.respond(text, parse_mode="html", buttons=get_start_keyboard(False))
            else:
                text = (
                    f"👋 Привет, <b>{user.username}</b>!\n\n"
                    f"🎮 Твой ник: <code>{user.username}</code>\n"
                    f"🆔 UUID: <code>{user.uuid}</code>\n\n"
                    "Для входа в игру просто отправь сюда <b>6-значный код</b>, который отображается в лаунчере."
                )
                await event.respond(text, parse_mode="html", buttons=get_start_keyboard(True))

    @client.on(events.NewMessage(pattern=r"^/help$"))
    async def handle_help(event: events.NewMessage.Event) -> None:
        await event.respond(HELP_TEXT, parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^/profile$"))
    async def handle_profile(event: events.NewMessage.Event) -> None:
        user_id = event.sender_id
        state_manager.clear(user_id)
        async with get_db_session() as session:
            user = await AuthService.get_user_by_telegram_id(session, user_id)
            if not user:
                await event.respond(
                    "Вы еще не зарегистрированы. Нажмите кнопку ниже для регистрации:",
                    buttons=get_start_keyboard(False),
                )
                return

            sessions = await AuthService.get_active_sessions(session, user.id)
            ban_status = "⛔ Заблокирован" if user.is_banned else "✅ Активен"
            text = (
                "👤 <b>Профиль игрока</b>\n\n"
                f"🎮 Никнейм: <b>{user.username}</b>\n"
                f"🆔 Minecraft UUID: <code>{user.uuid}</code>\n"
                f"📅 Дата регистрации: <i>{user.created_at.strftime('%d.%m.%Y %H:%M')}</i>\n"
                f"🛡️ Статус аккаунта: {ban_status}\n"
                f"🔑 Активных сессий в лаунчере: <b>{len(sessions)}</b>"
            )
            await event.respond(text, parse_mode="html", buttons=get_profile_keyboard())

    @client.on(events.NewMessage(pattern=r"^/sessions$"))
    async def handle_sessions(event: events.NewMessage.Event) -> None:
        user_id = event.sender_id
        async with get_db_session() as session:
            user = await AuthService.get_user_by_telegram_id(session, user_id)
            if not user:
                return

            sessions = await AuthService.get_active_sessions(session, user.id)
            if not sessions:
                text = "🔑 <b>Активные сессии</b>\n\nУ вас сейчас нет активных сессий в лаунчере."
            else:
                text = f"🔑 <b>Активные сессии ({len(sessions)})</b>\n\nНиже отображен список подключенных устройств. Вы можете завершить любую из сессий:"
            await event.respond(text, parse_mode="html", buttons=get_sessions_keyboard(sessions))

    @client.on(events.NewMessage(pattern=r"^/skin$"))
    async def handle_skin(event: events.NewMessage.Event) -> None:
        user_id = event.sender_id
        state_manager.clear(user_id)
        async with get_db_session() as session:
            user = await AuthService.get_user_by_telegram_id(session, user_id)
            if not user:
                await event.respond("Сначала зарегистрируйтесь с помощью /start.", buttons=get_start_keyboard(False))
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
            await event.respond(text, parse_mode="html", buttons=get_skin_management_keyboard(has_skin, user.skin_model))

    @client.on(events.NewMessage(pattern=r"^/cape$"))
    async def handle_cape(event: events.NewMessage.Event) -> None:
        user_id = event.sender_id
        state_manager.clear(user_id)
        async with get_db_session() as session:
            user = await AuthService.get_user_by_telegram_id(session, user_id)
            if not user:
                await event.respond("Сначала зарегистрируйтесь с помощью /start.", buttons=get_start_keyboard(False))
                return

            has_cape = user.cape_hash is not None
            status_text = "Плащ установлен" if has_cape else "Плащ отсутствует"
            text = (
                "🧥 <b>Управление плащом Minecraft</b>\n\n"
                f"👤 Игрок: <b>{user.username}</b>\n"
                f"✨ Статус: <b>{status_text}</b>\n\n"
                "Вы можете загрузить собственный плащ в формате <code>.png</code> (разрешение <b>64x32</b> или <b>22x17</b> пикселей)."
            )
            await event.respond(text, parse_mode="html", buttons=get_cape_management_keyboard(has_cape))

    @client.on(events.NewMessage(pattern=r"^/cancel$"))
    async def handle_cancel(event: events.NewMessage.Event) -> None:
        state_manager.clear(event.sender_id)
        await event.respond("Действие отменено.", buttons=get_start_keyboard(True))

    # ---------------- Admin Commands ---------------- #

    @client.on(events.NewMessage(pattern=r"^/modpack_info$"))
    async def handle_modpack_info(event: events.NewMessage.Event) -> None:
        if not is_admin(event.sender_id):
            await event.respond("⛔ У вас нет доступа к этой команде.")
            return

        async with get_db_session() as session:
            stats = await ModpackService.get_stats(session)
            updated_str = stats["last_updated"].strftime("%d.%m.%Y %H:%M") if stats["last_updated"] else "Никогда"
            text = (
                "📦 <b>Информация о сборке LCALauncher</b>\n\n"
                f"🏷️ Текущая версия: <b>{stats['version']}</b>\n"
                f"📁 Всего файлов: <b>{stats['total_files']}</b>\n"
                f"💾 Общий вес: <b>{stats['total_size_mb']} MB</b> ({stats['total_size']} байт)\n"
                f"🕒 Последнее обновление: <i>{updated_str}</i>\n"
                f"📂 Папка хранения: <code>{config.modpack_dir}</code>"
            )
            await event.respond(text, parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^/sync_mods$"))
    async def handle_sync_mods(event: events.NewMessage.Event) -> None:
        if not is_admin(event.sender_id):
            await event.respond("⛔ У вас нет доступа к этой команде.")
            return

        await event.respond("🔄 <i>Сканирование папки data/modpack/ и синхронизация файлов...</i>", parse_mode="html")
        async with get_db_session() as session:
            res = await ModpackService.sync_disk_files(session, event.sender_id)
            text = (
                "✅ <b>Синхронизация файлов сборки завершена!</b>\n\n"
                f"➕ Добавлено новых файлов: <b>{res['added']}</b>\n"
                f"🔄 Обновлено измененных: <b>{res['updated']}</b>\n"
                f"🗑️ Удалено отсутствующих: <b>{res['deleted']}</b>\n"
                f"📦 Всего файлов в сборке: <b>{res['total']}</b>"
            )
            await event.respond(text, parse_mode="html")

    @client.on(events.NewMessage(pattern=r"^/upload$"))
    async def handle_upload_cmd(event: events.NewMessage.Event) -> None:
        if not is_admin(event.sender_id):
            await event.respond("⛔ У вас нет доступа к этой команде.")
            return

        state_manager.set_state(event.sender_id, "waiting_for_mod_file")
        await event.respond(
            "📎 <b>Загрузка файла в сборку (MTProto до 2 ГБ)</b>\n\n"
            "Отправьте файл боту (как документ).\n"
            "Поддерживаются файлы <code>.jar</code>, <code>.toml</code>, <code>.json</code>, <code>.zip</code> и др.",
            parse_mode="html",
            buttons=get_cancel_keyboard(),
        )

    @client.on(events.NewMessage(pattern=r"^/delete_mod$"))
    async def handle_delete_mod_cmd(event: events.NewMessage.Event) -> None:
        if not is_admin(event.sender_id):
            await event.respond("⛔ У вас нет доступа к этой команде.")
            return

        async with get_db_session() as session:
            files = await ModpackService.list_files(session)
            if not files:
                await event.respond("В сборке пока нет файлов для удаления.")
                return

            await event.respond(
                "🗑️ <b>Удаление файла из сборки</b>\n\nВыберите файл для удаления:",
                parse_mode="html",
                buttons=get_delete_mod_keyboard(files),
            )

    @client.on(events.NewMessage(pattern=r"^/set_version$"))
    async def handle_set_version_cmd(event: events.NewMessage.Event) -> None:
        if not is_admin(event.sender_id):
            await event.respond("⛔ У вас нет доступа к этой команде.")
            return

        state_manager.set_state(event.sender_id, "waiting_for_version")
        await event.respond(
            "🏷️ <b>Обновление версии сборки</b>\n\nВведите новую версию (например <code>1.2.0</code>):",
            parse_mode="html",
            buttons=get_cancel_keyboard(),
        )

    @client.on(events.NewMessage(pattern=r"^/ban(\s+.+)?$"))
    async def handle_ban_cmd(event: events.NewMessage.Event) -> None:
        if not is_admin(event.sender_id):
            await event.respond("⛔ У вас нет доступа к этой команде.")
            return

        args = event.text.split()[1:]
        if not args:
            await event.respond("Использование: <code>/ban &lt;никнейм_или_telegram_id&gt;</code>", parse_mode="html")
            return

        async with get_db_session() as session:
            success, msg, _ = await AuthService.set_user_ban_status(session, args[0], is_banned=True)
            await event.respond(f"⛔ {msg}" if success else f"❌ {msg}")

    @client.on(events.NewMessage(pattern=r"^/unban(\s+.+)?$"))
    async def handle_unban_cmd(event: events.NewMessage.Event) -> None:
        if not is_admin(event.sender_id):
            await event.respond("⛔ У вас нет доступа к этой команде.")
            return

        args = event.text.split()[1:]
        if not args:
            await event.respond("Использование: <code>/unban &lt;никнейм_или_telegram_id&gt;</code>", parse_mode="html")
            return

        async with get_db_session() as session:
            success, msg, _ = await AuthService.set_user_ban_status(session, args[0], is_banned=False)
            await event.respond(f"✅ {msg}" if success else f"❌ {msg}")

    @client.on(events.NewMessage(pattern=r"^/userinfo(\s+.+)?$"))
    async def handle_userinfo_cmd(event: events.NewMessage.Event) -> None:
        if not is_admin(event.sender_id):
            await event.respond("⛔ У вас нет доступа к этой команде.")
            return

        args = event.text.split()[1:]
        if not args:
            await event.respond("Использование: <code>/userinfo &lt;никнейм_или_telegram_id&gt;</code>", parse_mode="html")
            return

        async with get_db_session() as session:
            user = await AuthService.get_user_by_identifier(session, args[0])
            if not user:
                await event.respond(f"❌ Пользователь '{args[0]}' не найден.")
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
            await event.respond(text, parse_mode="html")

    # ---------------- State and General Message Handler ---------------- #

    @client.on(events.NewMessage)
    async def handle_general_message(event: events.NewMessage.Event) -> None:
        text = (event.raw_text or "").strip()
        user_id = event.sender_id

        # Skip slash commands (handled above)
        if text.startswith("/"):
            return

        current_state = state_manager.get_state(user_id)

        # 1. State: Registration
        if current_state == "waiting_for_username":
            async with get_db_session() as session:
                success, msg, user = await AuthService.register_user(session, user_id, text)
                if not success or not user:
                    await event.respond(f"❌ {msg}\n\nПопробуйте другой ник или /cancel:", buttons=get_cancel_keyboard())
                    return

                state_manager.clear(user_id)
                congrats = (
                    "🎉 <b>Регистрация успешно завершена!</b>\n\n"
                    f"👤 Игровой ник: <code>{user.username}</code>\n"
                    f"🆔 Minecraft UUID: <code>{user.uuid}</code>\n\n"
                    "🚀 Теперь вы можете войти в игру:\n"
                    "1. Откройте <b>LCALauncher</b> на компьютере.\n"
                    "2. Введите отображенный там 6-значный код прямо сюда в чат."
                )
                await event.respond(congrats, parse_mode="html", buttons=get_start_keyboard(True))
            return

        # 2. State: Change nickname
        if current_state == "waiting_for_new_username":
            async with get_db_session() as session:
                user = await AuthService.get_user_by_telegram_id(session, user_id)
                if not user:
                    state_manager.clear(user_id)
                    return

                success, msg = await AuthService.change_username(session, user, text)
                if not success:
                    await event.respond(f"❌ {msg}\n\nПопробуйте другой ник или /cancel:", buttons=get_cancel_keyboard())
                    return

                state_manager.clear(user_id)
                await event.respond(
                    f"✅ <b>Никнейм успешно изменен!</b>\n\n👤 Новый ник: <b>{user.username}</b>\n🆔 Новый UUID: <code>{user.uuid}</code>",
                    parse_mode="html",
                    buttons=get_profile_keyboard(),
                )
            return

        # 3. State: Upload Skin File
        if current_state == "waiting_for_skin_file":
            if not event.message.document:
                await event.respond("❌ Пожалуйста, отправьте файл <b>документом</b> (без сжатия):", parse_mode="html")
                return

            # Download file into memory via native MTProto
            skin_bytes = await event.message.download_media(file=bytes)
            try:
                w, h = SkinService.inspect_png_dimensions(skin_bytes)
            except TextureValidationError as e:
                await event.respond(f"❌ Ошибка валидации файла:\n{e}", buttons=get_cancel_keyboard())
                return

            state_manager.update_data(user_id, skin_bytes=skin_bytes)
            state_manager.set_state(user_id, "waiting_for_skin_model")
            await event.respond(
                f"✅ Файл скина корректен ({w}x{h}px)!\n\nВыберите тип модели рук вашего скина:",
                buttons=get_skin_model_choice_keyboard(),
            )
            return

        # 4. State: Upload Cape File
        if current_state == "waiting_for_cape_file":
            if not event.message.document:
                await event.respond("❌ Пожалуйста, отправьте файл <b>документом</b> (без сжатия):", parse_mode="html")
                return

            cape_bytes = await event.message.download_media(file=bytes)
            state_manager.clear(user_id)
            async with get_db_session() as session:
                user = await AuthService.get_user_by_telegram_id(session, user_id)
                if not user:
                    return

                try:
                    cape_hash = await SkinService.set_user_cape(session, user, cape_bytes)
                    await event.respond(
                        f"🎉 <b>Плащ успешно применен!</b>\n\n🔑 SHA-256: <code>{cape_hash[:16]}...</code>",
                        parse_mode="html",
                        buttons=get_cape_management_keyboard(True),
                    )
                except TextureValidationError as e:
                    await event.respond(f"❌ {e}", buttons=get_cancel_keyboard())
            return

        # 5. State: Upload Admin Mod File
        if current_state == "waiting_for_mod_file":
            if not event.message.document:
                await event.respond("❌ Отправьте файл документом:", buttons=get_cancel_keyboard())
                return

            doc = event.message.document
            filename = "mod.jar"
            for attr in doc.attributes:
                if hasattr(attr, "file_name") and attr.file_name:
                    filename = attr.file_name
                    break

            # Native MTProto supports downloading large files (up to 2 GB)
            file_bytes = await event.message.download_media(file=bytes)
            state_manager.update_data(user_id, file_bytes=file_bytes, filename=filename)
            state_manager.set_state(user_id, "waiting_for_mod_path")

            default_path = f"mods/{filename}"
            await event.respond(
                f"📥 Файл <b>{filename}</b> получен ({len(file_bytes)} байт).\n\n"
                f"Укажите путь назначения в сборке (например: <code>{default_path}</code>) или выберите вариант кнопкой:",
                parse_mode="html",
                buttons=get_upload_path_keyboard(filename),
            )
            return

        # 6. State: Admin Mod Target Path
        if current_state == "waiting_for_mod_path":
            data = state_manager.get_data(user_id)
            file_bytes = data.get("file_bytes")
            state_manager.clear(user_id)

            if not file_bytes:
                await event.respond("Ошибка: файл не найден в памяти.")
                return

            async with get_db_session() as session:
                try:
                    mod_file = await ModpackService.save_file(
                        session=session,
                        relative_path=text,
                        content=file_bytes,
                        uploaded_by_telegram_id=user_id,
                    )
                    size_mb = round(mod_file.size / (1024 * 1024), 2)
                    await event.respond(
                        "✅ <b>Файл успешно добавлен в сборку!</b>\n\n"
                        f"📁 Путь: <code>{mod_file.relative_path}</code>\n"
                        f"💾 Размер: <b>{size_mb} MB</b> ({mod_file.size} байт)\n"
                        f"🔑 SHA-256: <code>{mod_file.sha256}</code>",
                        parse_mode="html",
                    )
                except ModpackSecurityError as e:
                    await event.respond(f"❌ Ошибка пути: {e}")
            return

        # 7. State: Admin Version
        if current_state == "waiting_for_version":
            state_manager.update_data(user_id, version=text)
            state_manager.set_state(user_id, "waiting_for_changelog")
            await event.respond(
                f"Версия: <b>{text}</b>\n\nТеперь введите changelog (или <code>-</code>):",
                parse_mode="html",
                buttons=get_cancel_keyboard(),
            )
            return

        # 8. State: Admin Changelog
        if current_state == "waiting_for_changelog":
            data = state_manager.get_data(user_id)
            version = data.get("version", "1.0.0")
            changelog = None if text == "-" else text
            state_manager.clear(user_id)

            async with get_db_session() as session:
                ver_obj = await ModpackService.create_version(session, version, changelog, user_id)
                await event.respond(
                    f"✅ <b>Версия сборки обновлена на {ver_obj.version}!</b>\n\n📝 Changelog:\n{ver_obj.changelog or '<i>(без описания)</i>'}",
                    parse_mode="html",
                )
            return

        # 9. 6-digit Launcher Auth Code
        if re.match(r"^\d{6}$", text):
            if not bot_code_limiter.is_allowed(user_id):
                await event.respond("⚠️ <b>Слишком много попыток ввода кода.</b> Подождите минуту.", parse_mode="html")
                return

            async with get_db_session() as session:
                success, msg, _, user = await AuthService.confirm_auth_code(session, text, user_id)
                if not success or not user:
                    await event.respond(f"❌ <b>Ошибка авторизации:</b>\n{msg}", parse_mode="html")
                    return

                await event.respond(
                    "✅ <b>Авторизация успешна!</b>\n\n"
                    f"🎮 Лаунчер подключен к аккаунту: <b>{user.username}</b>\n"
                    "Сессия активирована. Лаунчер автоматически продолжит вход в игру!",
                    parse_mode="html",
                    buttons=get_start_keyboard(True),
                )
            return

    # ---------------- Callback Query Handlers ---------------- #

    @client.on(events.CallbackQuery)
    async def handle_callback_query(event: events.CallbackQuery.Event) -> None:
        user_id = event.sender_id
        data = (event.data or b"").decode("utf-8")

        if data == "open_help":
            await event.answer()
            await event.respond(HELP_TEXT, parse_mode="html")
            return

        if data == "cancel_action":
            state_manager.clear(user_id)
            await event.answer("Отменено")
            await event.edit("Действие отменено.", buttons=get_start_keyboard(True))
            return

        if data == "start_registration":
            async with get_db_session() as session:
                existing = await AuthService.get_user_by_telegram_id(session, user_id)
                if existing:
                    await event.answer(f"Вы уже зарегистрированы как {existing.username}!", alert=True)
                    return

                state_manager.set_state(user_id, "waiting_for_username")
                await event.answer()
                await event.respond(
                    "🎮 <b>Регистрация игрового профиля</b>\n\n"
                    "Введите желаемый никнейм для Minecraft (3-16 символов, только латиница, цифры и <code>_</code>):",
                    parse_mode="html",
                    buttons=get_cancel_keyboard(),
                )
            return

        if data == "open_profile":
            await event.answer()
            async with get_db_session() as session:
                user = await AuthService.get_user_by_telegram_id(session, user_id)
                if not user:
                    await event.respond("Сначала зарегистрируйтесь:", buttons=get_start_keyboard(False))
                    return

                sessions = await AuthService.get_active_sessions(session, user.id)
                ban_status = "⛔ Заблокирован" if user.is_banned else "✅ Активен"
                text = (
                    "👤 <b>Профиль игрока</b>\n\n"
                    f"🎮 Никнейм: <b>{user.username}</b>\n"
                    f"🆔 Minecraft UUID: <code>{user.uuid}</code>\n"
                    f"📅 Дата регистрации: <i>{user.created_at.strftime('%d.%m.%Y %H:%M')}</i>\n"
                    f"🛡️ Статус аккаунта: {ban_status}\n"
                    f"🔑 Активных сессий в лаунчере: <b>{len(sessions)}</b>"
                )
                await event.edit(text, parse_mode="html", buttons=get_profile_keyboard())
            return

        if data == "open_sessions":
            await event.answer()
            async with get_db_session() as session:
                user = await AuthService.get_user_by_telegram_id(session, user_id)
                if not user:
                    return

                sessions = await AuthService.get_active_sessions(session, user.id)
                text = f"🔑 <b>Активные сессии ({len(sessions)})</b>\n\nСписок подключенных устройств:" if sessions else "🔑 Активных сессий нет."
                await event.edit(text, parse_mode="html", buttons=get_sessions_keyboard(sessions))
            return

        if data.startswith("revoke_session:"):
            session_id = int(data.split(":")[1])
            async with get_db_session() as session:
                user = await AuthService.get_user_by_telegram_id(session, user_id)
                if user:
                    revoked = await AuthService.revoke_session_by_id(session, user.id, session_id)
                    await event.answer(f"Сессия #{session_id} завершена!" if revoked else "Сессия уже завершена", alert=True)
                    sessions = await AuthService.get_active_sessions(session, user.id)
                    await event.edit("🔑 Список активных сессий обновлен:", buttons=get_sessions_keyboard(sessions))
            return

        if data == "confirm_revoke_all":
            await event.answer()
            await event.edit(
                "⚠️ <b>Вы уверены, что хотите завершить ВСЕ активные сессии в лаунчере?</b>",
                parse_mode="html",
                buttons=get_confirm_revoke_all_keyboard(),
            )
            return

        if data == "do_revoke_all":
            async with get_db_session() as session:
                user = await AuthService.get_user_by_telegram_id(session, user_id)
                if user:
                    count = await AuthService.revoke_all_user_sessions(session, user.id)
                    await event.answer(f"Завершено сессий: {count}", alert=True)
                    await event.edit("✅ Все сессии успешно завершены.", buttons=get_profile_keyboard())
            return

        if data == "change_nickname":
            await event.answer()
            state_manager.set_state(user_id, "waiting_for_new_username")
            await event.respond(
                "✏️ <b>Смена никнейма</b>\n\nВведите новый никнейм (3-16 символов, только латиница, цифры и <code>_</code>):",
                parse_mode="html",
                buttons=get_cancel_keyboard(),
            )
            return

        if data == "manage_skin":
            await event.answer()
            async with get_db_session() as session:
                user = await AuthService.get_user_by_telegram_id(session, user_id)
                if not user:
                    return

                has_skin = user.skin_hash is not None
                model_name = "Classic (Steve, 4px)" if user.skin_model == "classic" else "Slim (Alex, 3px)"
                status_text = "Кастомный скин установлен" if has_skin else "Стандартный скин (Steve/Alex)"
                text = (
                    "🎨 <b>Управление скином Minecraft</b>\n\n"
                    f"👤 Игрок: <b>{user.username}</b>\n"
                    f"🎭 Статус: <b>{status_text}</b>\n"
                    f"✂️ Модель рук: <b>{model_name}</b>\n\n"
                    "Разрешение: <b>64x64</b> или <b>64x32</b> PNG."
                )
                await event.edit(text, parse_mode="html", buttons=get_skin_management_keyboard(has_skin, user.skin_model))
            return

        if data == "upload_skin_start":
            await event.answer()
            state_manager.set_state(user_id, "waiting_for_skin_file")
            await event.respond(
                "📥 <b>Загрузка скина (MTProto)</b>\n\nОтправьте <code>.png</code> файл документа (64x64 или 64x32):",
                parse_mode="html",
                buttons=get_cancel_keyboard(),
            )
            return

        if data == "toggle_skin_model":
            async with get_db_session() as session:
                user = await AuthService.get_user_by_telegram_id(session, user_id)
                if user:
                    new_model = "slim" if user.skin_model == "classic" else "classic"
                    await SkinService.set_user_skin_model(session, user, new_model)
                    model_title = "Classic (4px)" if new_model == "classic" else "Slim (3px)"
                    await event.answer(f"Модель изменена на {model_title}!")
                    has_skin = user.skin_hash is not None
                    await event.edit(
                        f"🎨 <b>Управление скином</b>\n\nМодель переключена на: <b>{model_title}</b>",
                        parse_mode="html",
                        buttons=get_skin_management_keyboard(has_skin, new_model),
                    )
            return

        if data == "clear_skin":
            async with get_db_session() as session:
                user = await AuthService.get_user_by_telegram_id(session, user_id)
                if user:
                    await SkinService.clear_user_skin(session, user)
                    await event.answer("Скин сброшен!", alert=True)
                    await event.edit("🎨 Скин сброшен на стандартный.", buttons=get_skin_management_keyboard(False, user.skin_model))
            return

        if data.startswith("choose_model:"):
            chosen_model = data.split(":")[1]
            stored_bytes = state_manager.get_data(user_id).get("skin_bytes")
            state_manager.clear(user_id)

            if not stored_bytes:
                await event.answer("Файл не найден в памяти", alert=True)
                return

            async with get_db_session() as session:
                user = await AuthService.get_user_by_telegram_id(session, user_id)
                if user:
                    skin_hash = await SkinService.set_user_skin(session, user, stored_bytes, model=chosen_model)
                    await event.answer("Скин успешно установлен!", alert=True)
                    model_name = "Classic (4px)" if chosen_model == "classic" else "Slim (3px)"
                    await event.edit(
                        f"🎉 <b>Скин установлен!</b>\n\nМодель: <b>{model_name}</b>\nSHA-256: <code>{skin_hash[:16]}...</code>",
                        parse_mode="html",
                        buttons=get_skin_management_keyboard(True, user.skin_model),
                    )
            return

        if data == "manage_cape":
            await event.answer()
            async with get_db_session() as session:
                user = await AuthService.get_user_by_telegram_id(session, user_id)
                if user:
                    has_cape = user.cape_hash is not None
                    status_text = "Плащ установлен" if has_cape else "Плащ отсутствует"
                    text = f"🧥 <b>Управление плащом</b>\n\nСтатус: <b>{status_text}</b>\nРазрешение: 64x32 PNG."
                    await event.edit(text, parse_mode="html", buttons=get_cape_management_keyboard(has_cape))
            return

        if data == "upload_cape_start":
            await event.answer()
            state_manager.set_state(user_id, "waiting_for_cape_file")
            await event.respond("📥 Отправьте <code>.png</code> файл плаща документом (64x32):", parse_mode="html", buttons=get_cancel_keyboard())
            return

        if data == "clear_cape":
            async with get_db_session() as session:
                user = await AuthService.get_user_by_telegram_id(session, user_id)
                if user:
                    await SkinService.clear_user_cape(session, user)
                    await event.answer("Плащ удален!", alert=True)
                    await event.edit("🧥 Плащ удален.", buttons=get_cape_management_keyboard(False))
            return

        # Admin modpack callbacks
        if data.startswith("set_upload_path:"):
            target_path = data.split("set_upload_path:")[1]
            file_bytes = state_manager.get_data(user_id).get("file_bytes")
            state_manager.clear(user_id)

            if not file_bytes:
                await event.answer("Файл не найден в памяти", alert=True)
                return

            async with get_db_session() as session:
                try:
                    mod_file = await ModpackService.save_file(session, target_path, file_bytes, user_id)
                    size_mb = round(mod_file.size / (1024 * 1024), 2)
                    await event.answer("Файл сохранен!", alert=True)
                    await event.edit(
                        f"✅ <b>Файл добавлен:</b>\n<code>{mod_file.relative_path}</code> ({size_mb} MB)",
                        parse_mode="html",
                    )
                except ModpackSecurityError as e:
                    await event.answer(f"Ошибка: {e}", alert=True)
            return

        if data.startswith("delete_file_select:"):
            file_id = int(data.split(":")[1])
            async with get_db_session() as session:
                from sqlalchemy import select
                stmt = select(ModpackFile).where(ModpackFile.id == file_id)
                res = await session.execute(stmt)
                mod_file = res.scalar_one_or_none()
                if not mod_file:
                    await event.answer("Файл не найден", alert=True)
                    return

                await event.answer()
                await event.edit(
                    f"⚠️ <b>Удалить файл {mod_file.relative_path}?</b>",
                    parse_mode="html",
                    buttons=get_confirm_delete_keyboard(mod_file.id, mod_file.relative_path),
                )
            return

        if data.startswith("confirm_delete_file:"):
            file_id = int(data.split(":")[1])
            async with get_db_session() as session:
                from sqlalchemy import select
                stmt = select(ModpackFile).where(ModpackFile.id == file_id)
                res = await session.execute(stmt)
                mod_file = res.scalar_one_or_none()
                if not mod_file:
                    await event.answer("Файл уже удален", alert=True)
                    return

                rel = mod_file.relative_path
                await ModpackService.delete_file(session, rel)
                await event.answer(f"Файл {rel} удален!", alert=True)
                await event.edit(f"✅ Файл <code>{rel}</code> удален.", parse_mode="html")
            return
