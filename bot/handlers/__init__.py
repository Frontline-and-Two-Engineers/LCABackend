from aiogram import Router
from bot.handlers.start import router as start_router
from bot.handlers.register import router as register_router
from bot.handlers.auth_code import router as auth_code_router
from bot.handlers.profile import router as profile_router
from bot.handlers.skin import router as skin_router
from bot.handlers.admin import router as admin_router


def get_main_router() -> Router:
    """Create root router and include all feature routers in priority order."""
    root_router = Router(name="root_router")

    # Order matters: commands and specific flows first, then auth code matcher
    root_router.include_router(start_router)
    root_router.include_router(register_router)
    root_router.include_router(profile_router)
    root_router.include_router(skin_router)
    root_router.include_router(admin_router)
    root_router.include_router(auth_code_router)

    return root_router
