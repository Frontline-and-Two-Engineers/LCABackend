"""Keyboards for Telegram Bot using native Telethon Button components."""

from telethon import Button
from bot.db.models import ActiveSession, ModpackFile


def get_start_keyboard(is_registered: bool) -> list[list[Button]]:
    """Keyboard shown on /start."""
    if not is_registered:
        return [
            [Button.inline("🎮 Зарегистрировать никнейм", b"start_registration")],
            [Button.inline("❓ Как войти в лаунчер?", b"open_help")],
        ]

    return [
        [
            Button.inline("👤 Мой профиль", b"open_profile"),
            Button.inline("🔑 Активные сессии", b"open_sessions"),
        ],
        [
            Button.inline("🎨 Мой скин", b"manage_skin"),
            Button.inline("🧥 Мой плащ", b"manage_cape"),
        ],
        [Button.inline("❓ Инструкция", b"open_help")],
    ]


def get_profile_keyboard() -> list[list[Button]]:
    """Keyboard for /profile view."""
    return [
        [
            Button.inline("🎨 Мой скин", b"manage_skin"),
            Button.inline("🧥 Мой плащ", b"manage_cape"),
        ],
        [
            Button.inline("✏️ Сменить ник", b"change_nickname"),
            Button.inline("🔑 Сессии", b"open_sessions"),
        ],
        [
            Button.inline("🚪 Отозвать ВСЕ сессии", b"confirm_revoke_all")
        ],
    ]


def get_skin_management_keyboard(has_skin: bool, current_model: str) -> list[list[Button]]:
    """Keyboard for skin management screen."""
    model_label = "Classic (Steve)" if current_model == "classic" else "Slim (Alex)"
    buttons = [
        [Button.inline("📥 Загрузить новый скин", b"upload_skin_start")],
        [Button.inline(f"🔄 Модель: {model_label}", b"toggle_skin_model")],
    ]
    if has_skin:
        buttons.append([Button.inline("🗑️ Сбросить на стандартный", b"clear_skin")])
    buttons.append([Button.inline("⬅️ Назад в профиль", b"open_profile")])
    return buttons


def get_cape_management_keyboard(has_cape: bool) -> list[list[Button]]:
    """Keyboard for cape management screen."""
    buttons = [
        [Button.inline("📥 Загрузить новый плащ", b"upload_cape_start")]
    ]
    if has_cape:
        buttons.append([Button.inline("🗑️ Удалить плащ", b"clear_cape")])
    buttons.append([Button.inline("⬅️ Назад в профиль", b"open_profile")])
    return buttons


def get_skin_model_choice_keyboard() -> list[list[Button]]:
    """Keyboard for selecting skin model during upload."""
    return [
        [Button.inline("Классическая (Steve, 4px)", b"choose_model:classic")],
        [Button.inline("Тонкая (Alex, 3px)", b"choose_model:slim")],
        [Button.inline("❌ Отмена", b"manage_skin")],
    ]


def get_sessions_keyboard(sessions: list[ActiveSession]) -> list[list[Button]]:
    """Keyboard listing active sessions with individual revoke buttons."""
    buttons = []
    for s in sessions:
        created_str = s.created_at.strftime("%d.%m %H:%M")
        buttons.append([
            Button.inline(
                f"❌ Отозвать сессию #{s.id} ({created_str})",
                f"revoke_session:{s.id}".encode("utf-8"),
            )
        ])

    if sessions:
        buttons.append([
            Button.inline("🚪 Отозвать ВСЕ сессии", b"confirm_revoke_all")
        ])

    buttons.append([
        Button.inline("⬅️ Назад в профиль", b"open_profile")
    ])

    return buttons


def get_confirm_revoke_all_keyboard() -> list[list[Button]]:
    """Confirmation keyboard for revoking all sessions."""
    return [
        [Button.inline("⚠️ Да, завершить все сессии", b"do_revoke_all")],
        [Button.inline("❌ Отмена", b"open_profile")],
    ]


def get_upload_path_keyboard(filename: str) -> list[list[Button]]:
    """Keyboard suggesting paths for an uploaded file."""
    buttons = [
        [Button.inline(f"📁 mods/{filename}", f"set_upload_path:mods/{filename}".encode("utf-8"))]
    ]
    if filename.endswith(".toml") or filename.endswith(".json") or filename.endswith(".txt"):
        buttons.append([
            Button.inline(f"📁 config/{filename}", f"set_upload_path:config/{filename}".encode("utf-8"))
        ])
    buttons.append([Button.inline("❌ Отмена", b"cancel_action")])
    return buttons


def get_delete_mod_keyboard(files: list[ModpackFile]) -> list[list[Button]]:
    """Keyboard listing files that can be deleted."""
    buttons = []
    for f in files[:20]:
        buttons.append([
            Button.inline(
                f"🗑️ {f.relative_path}",
                f"delete_file_select:{f.id}".encode("utf-8"),
            )
        ])

    buttons.append([Button.inline("❌ Отмена", b"cancel_action")])
    return buttons


def get_confirm_delete_keyboard(file_id: int, relative_path: str) -> list[list[Button]]:
    """Confirmation keyboard for file deletion."""
    return [
        [Button.inline("✅ Да, удалить навсегда", f"confirm_delete_file:{file_id}".encode("utf-8"))],
        [Button.inline("❌ Отмена", b"cancel_action")],
    ]


def get_cancel_keyboard() -> list[list[Button]]:
    """General cancel action keyboard."""
    return [
        [Button.inline("❌ Отмена", b"cancel_action")]
    ]
