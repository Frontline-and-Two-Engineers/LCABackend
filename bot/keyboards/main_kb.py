"""Keyboards for Telegram Bot."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from bot.db.models import ActiveSession, ModpackFile


def get_start_keyboard(is_registered: bool) -> InlineKeyboardMarkup:
    """Keyboard shown on /start."""
    if not is_registered:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎮 Зарегистрировать никнейм",
                        callback_data="start_registration",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❓ Как войти в лаунчер?",
                        callback_data="open_help",
                    )
                ],
            ]
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👤 Мой профиль",
                    callback_data="open_profile",
                ),
                InlineKeyboardButton(
                    text="🔑 Активные сессии",
                    callback_data="open_sessions",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❓ Инструкция",
                    callback_data="open_help",
                )
            ],
        ]
    )


def get_profile_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for /profile view."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎨 Мой скин",
                    callback_data="manage_skin",
                ),
                InlineKeyboardButton(
                    text="🧥 Мой плащ",
                    callback_data="manage_cape",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Сменить ник",
                    callback_data="change_nickname",
                ),
                InlineKeyboardButton(
                    text="🔑 Сессии",
                    callback_data="open_sessions",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🚪 Отозвать ВСЕ сессии",
                    callback_data="confirm_revoke_all",
                )
            ],
        ]
    )


def get_skin_management_keyboard(has_skin: bool, current_model: str) -> InlineKeyboardMarkup:
    """Keyboard for skin management screen."""
    model_label = "Classic (Steve)" if current_model == "classic" else "Slim (Alex)"
    buttons = [
        [
            InlineKeyboardButton(
                text="📥 Загрузить новый скин",
                callback_data="upload_skin_start",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"🔄 Модель: {model_label}",
                callback_data="toggle_skin_model",
            )
        ],
    ]
    if has_skin:
        buttons.append([
            InlineKeyboardButton(
                text="🗑️ Сбросить на стандартный",
                callback_data="clear_skin",
            )
        ])
    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад в профиль",
            callback_data="open_profile",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_cape_management_keyboard(has_cape: bool) -> InlineKeyboardMarkup:
    """Keyboard for cape management screen."""
    buttons = [
        [
            InlineKeyboardButton(
                text="📥 Загрузить новый плащ",
                callback_data="upload_cape_start",
            )
        ]
    ]
    if has_cape:
        buttons.append([
            InlineKeyboardButton(
                text="🗑️ Удалить плащ",
                callback_data="clear_cape",
            )
        ])
    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад в профиль",
            callback_data="open_profile",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_skin_model_choice_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for selecting skin model during upload."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Классическая (Steve, 4px)",
                    callback_data="choose_model:classic",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Тонкая (Alex, 3px)",
                    callback_data="choose_model:slim",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="manage_skin",
                )
            ],
        ]
    )


def get_sessions_keyboard(sessions: list[ActiveSession]) -> InlineKeyboardMarkup:
    """Keyboard listing active sessions with individual revoke buttons."""
    buttons = []
    for s in sessions:
        created_str = s.created_at.strftime("%d.%m %H:%M")
        buttons.append([
            InlineKeyboardButton(
                text=f"❌ Отозвать сессию #{s.id} ({created_str})",
                callback_data=f"revoke_session:{s.id}",
            )
        ])

    if sessions:
        buttons.append([
            InlineKeyboardButton(
                text="🚪 Отозвать ВСЕ сессии",
                callback_data="confirm_revoke_all",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="⬅️ Назад в профиль",
            callback_data="open_profile",
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_confirm_revoke_all_keyboard() -> InlineKeyboardMarkup:
    """Confirmation keyboard for revoking all sessions."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚠️ Да, завершить все сессии",
                    callback_data="do_revoke_all",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="open_profile",
                )
            ],
        ]
    )


def get_upload_path_keyboard(filename: str) -> InlineKeyboardMarkup:
    """Keyboard suggesting paths for an uploaded file."""
    buttons = [
        [
            InlineKeyboardButton(
                text=f"📁 mods/{filename}",
                callback_data=f"set_upload_path:mods/{filename}",
            )
        ]
    ]
    if filename.endswith(".toml") or filename.endswith(".json") or filename.endswith(".txt"):
        buttons.append([
            InlineKeyboardButton(
                text=f"📁 config/{filename}",
                callback_data=f"set_upload_path:config/{filename}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data="cancel_action",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_delete_mod_keyboard(files: list[ModpackFile]) -> InlineKeyboardMarkup:
    """Keyboard listing files that can be deleted."""
    buttons = []
    for f in files[:20]:  # limit to 20 for inline keyboard ergonomics
        buttons.append([
            InlineKeyboardButton(
                text=f"🗑️ {f.relative_path}",
                callback_data=f"delete_file_select:{f.id}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="❌ Отмена",
            callback_data="cancel_action",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_confirm_delete_keyboard(file_id: int, relative_path: str) -> InlineKeyboardMarkup:
    """Confirmation keyboard for file deletion."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, удалить навсегда",
                    callback_data=f"confirm_delete_file:{file_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="cancel_action",
                )
            ],
        ]
    )


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    """General cancel action keyboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="cancel_action",
                )
            ]
        ]
    )
