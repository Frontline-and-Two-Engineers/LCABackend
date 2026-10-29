"""HTTP REST API server for LCALauncher (aiohttp)."""

import logging
import time
from collections import defaultdict
from typing import Any
from aiohttp import web
from bot.config import config
from bot.db.engine import get_db_session
from bot.db.models import User
from bot.services.auth_service import AuthService
from bot.services.code_service import CodeService
from bot.services.modpack_service import ModpackService, ModpackSecurityError
from bot.services.skin_service import SkinService
from bot.services.channel_service import channel_service
from bot.services.yggdrasil_service import yggdrasil_service, to_unhyphenated_uuid
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class IPRateLimiter:
    """Simple in-memory sliding-window rate limiter for IP addresses."""

    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, ip: str) -> bool:
        now = time.time()
        timestamps = self.requests[ip]

        # Purge timestamps outside the sliding window
        self.requests[ip] = [t for t in timestamps if now - t < self.window_seconds]

        if len(self.requests[ip]) >= self.max_requests:
            return False

        self.requests[ip].append(now)
        return True


ip_limiter = IPRateLimiter(max_requests=10, window_seconds=60)


def extract_bearer_token(request: web.Request) -> str | None:
    """Extract Bearer token from Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return None


def verify_admin_token(request: web.Request) -> bool:
    """Verify X-Admin-Token header matches configured secret."""
    token = request.headers.get("X-Admin-Token", "").strip()
    return bool(token and token == config.admin_api_token)


# ---------------- Auth Endpoints ---------------- #


async def request_code_handler(request: web.Request) -> web.Response:
    """
    POST /api/auth/request-code
    Request a new 6-digit one-time code to be displayed in the launcher.
    Rate limited: max 10 requests per minute per IP.
    """
    client_ip = request.remote or "unknown"
    if not ip_limiter.is_allowed(client_ip):
        logger.warning("Rate limit exceeded for IP %s on /api/auth/request-code", client_ip)
        return web.json_response(
            {"error": "rate_limited", "errorMessage": "Слишком много запросов. Попробуйте позже."},
            status=429,
        )

    async with get_db_session() as session:
        auth_code = await CodeService.create_auth_code(session, config.code_ttl_seconds)
        return web.json_response({
            "code": auth_code.code,
            "expires_in": config.code_ttl_seconds,
        })


async def poll_code_handler(request: web.Request) -> web.Response:
    """
    GET /api/auth/poll/{code}
    Check if the user entered the code in Telegram bot.
    """
    code_str = request.match_info.get("code", "").strip()
    if not code_str:
        return web.json_response(
            {"error": "invalid_code", "errorMessage": "Код не указан"},
            status=400,
        )

    async with get_db_session() as session:
        auth_code = await CodeService.get_code(session, code_str)
        if not auth_code:
            return web.json_response(
                {"error": "invalid_code", "errorMessage": "Код не найден"},
                status=404,
            )

        if auth_code.is_used and auth_code.session_token:
            # Confirmed: fetch user details
            user = None
            if auth_code.used_by_telegram_id:
                user = await AuthService.get_user_by_telegram_id(
                    session, auth_code.used_by_telegram_id
                )

            if not user:
                return web.json_response(
                    {"error": "user_not_found", "errorMessage": "Пользователь не найден"},
                    status=500,
                )

            return web.json_response({
                "status": "confirmed",
                "username": user.username,
                "uuid": user.uuid,
                "accessToken": auth_code.session_token,
            })

        if auth_code.is_expired:
            return web.json_response({"status": "expired"})

        return web.json_response({"status": "pending"})


async def validate_token_handler(request: web.Request) -> web.Response:
    """
    GET /api/auth/validate
    Validate launcher session token on startup.
    Header: Authorization: Bearer <token>
    """
    token = extract_bearer_token(request)
    if not token:
        return web.json_response(
            {"valid": False, "error": "missing_token"},
            status=401,
        )

    async with get_db_session() as session:
        valid, msg, user = await AuthService.validate_token(session, token)
        if not valid or not user:
            return web.json_response(
                {"valid": False, "error": msg or "token_expired"},
                status=401,
            )

        return web.json_response({
            "valid": True,
            "username": user.username,
            "uuid": user.uuid,
        })


async def logout_handler(request: web.Request) -> web.Response:
    """
    POST /api/auth/logout
    Revoke current launcher session token.
    Header: Authorization: Bearer <token>
    """
    token = extract_bearer_token(request)
    if not token:
        return web.json_response(
            {"error": "missing_token", "errorMessage": "Токен не предоставлен"},
            status=400,
        )

    async with get_db_session() as session:
        revoked = await AuthService.revoke_token(session, token)
        return web.json_response({"success": revoked})


# ---------------- Modpack Endpoints ---------------- #


async def manifest_handler(request: web.Request) -> web.Response:
    """
    GET /api/modpack/manifest.json
    Returns modpack manifest compatible with LCALauncher SyncEngine.
    """
    base_url = _get_effective_base_url(request)
    async with get_db_session() as session:
        manifest = await ModpackService.get_manifest(session, base_url)
        return web.json_response(manifest)


async def download_file_handler(request: web.Request) -> web.StreamResponse:
    """
    GET /api/modpack/files/{path:.*}
    Serves modpack binary file or client-base.zip directly for downloading.
    """
    relative_path = request.match_info.get("path", "")
    if not relative_path:
        return web.json_response(
            {"error": "file_not_found", "errorMessage": "Путь не указан"},
            status=404,
        )

    disk_path = ModpackService.get_file_disk_path(relative_path)
    if not disk_path:
        return web.json_response(
            {"error": "file_not_found", "errorMessage": "Файл не найден"},
            status=404,
        )

    content_type = "application/zip" if disk_path.name.endswith(".zip") else "application/octet-stream"
    return web.FileResponse(
        path=disk_path,
        chunk_size=65536,
        headers={
            "Content-Type": content_type,
            "Content-Disposition": f'attachment; filename="{disk_path.name}"',
        },
    )


async def download_client_base_handler(request: web.Request) -> web.StreamResponse:
    """
    GET /api/client-base.zip or /client-base.zip alias.
    """
    disk_path = ModpackService.get_file_disk_path("client-base.zip")
    if not disk_path:
        return web.json_response(
            {"error": "client_base_not_found", "errorMessage": "Архив client-base.zip не найден на сервере"},
            status=404,
        )

    return web.FileResponse(
        path=disk_path,
        chunk_size=65536,
        headers={
            "Content-Type": "application/zip",
            "Content-Disposition": 'attachment; filename="client-base.zip"',
        },
    )


async def upload_file_handler(request: web.Request) -> web.Response:
    """
    POST /api/modpack/upload
    Protected endpoint to upload or replace a file in the modpack.
    Header: X-Admin-Token: <admin_secret>
    Multipart form-data: file, path
    """
    if not verify_admin_token(request):
        return web.json_response(
            {"error": "unauthorized", "errorMessage": "Неверный или отсутствующий токен администратора"},
            status=403,
        )

    reader = await request.multipart()
    file_bytes: bytes | None = None
    target_path: str | None = None

    while True:
        part = await reader.next()
        if part is None:
            break

        if part.name == "path":
            target_path = (await part.text()).strip()
        elif part.name == "file":
            file_bytes = await part.read()

    if not file_bytes or not target_path:
        return web.json_response(
            {"error": "bad_request", "errorMessage": "Поля 'file' и 'path' обязательны"},
            status=400,
        )

    try:
        async with get_db_session() as session:
            mod_file = await ModpackService.save_file(
                session=session,
                relative_path=target_path,
                content=file_bytes,
                uploaded_by_telegram_id=0,
            )
            return web.json_response({
                "success": True,
                "path": mod_file.relative_path,
                "sha256": mod_file.sha256,
                "size": mod_file.size,
            })
    except ModpackSecurityError as e:
        return web.json_response(
            {"error": "invalid_path", "errorMessage": str(e)},
            status=400,
        )
    except Exception as e:
        logger.error("Failed to upload file via HTTP: %s", e)
        return web.json_response(
            {"error": "server_error", "errorMessage": "Ошибка при сохранении файла"},
            status=500,
        )


async def delete_file_handler(request: web.Request) -> web.Response:
    """
    DELETE /api/modpack/files/{path:.*}
    Protected endpoint to delete a file from modpack.
    Header: X-Admin-Token: <admin_secret>
    """
    if not verify_admin_token(request):
        return web.json_response(
            {"error": "unauthorized", "errorMessage": "Неверный токен администратора"},
            status=403,
        )

    relative_path = request.match_info.get("path", "")
    if not relative_path:
        return web.json_response(
            {"error": "bad_request", "errorMessage": "Путь не указан"},
            status=400,
        )

    async with get_db_session() as session:
        deleted = await ModpackService.delete_file(session, relative_path)
        if not deleted:
            return web.json_response(
                {"error": "file_not_found", "errorMessage": "Файл не найден"},
                status=404,
            )

        return web.json_response({
            "success": True,
            "deleted": relative_path,
        })


async def set_version_handler(request: web.Request) -> web.Response:
    """
    POST /api/modpack/version
    Protected endpoint to set a new modpack version.
    Header: X-Admin-Token: <admin_secret>
    JSON Body: {"version": "1.3.0", "changelog": "..."}
    """
    if not verify_admin_token(request):
        return web.json_response(
            {"error": "unauthorized", "errorMessage": "Неверный токен администратора"},
            status=403,
        )

    try:
        data = await request.json()
    except Exception:
        return web.json_response(
            {"error": "bad_request", "errorMessage": "Невалидный JSON"},
            status=400,
        )

    version_str = data.get("version", "").strip()
    changelog = data.get("changelog")

    if not version_str:
        return web.json_response(
            {"error": "bad_request", "errorMessage": "Поле 'version' обязательно"},
            status=400,
        )

    async with get_db_session() as session:
        ver = await ModpackService.create_version(
            session=session,
            version=version_str,
            changelog=changelog,
            created_by_telegram_id=0,
        )
        return web.json_response({
            "success": True,
            "version": ver.version,
        })


# ---------------- Yggdrasil Session & Skin Endpoints ---------------- #


def _get_effective_base_url(request: web.Request) -> str:
    """Determine base URL from config or incoming request."""
    base_url = config.api_base_url
    if not base_url or "localhost" in base_url or "0.0.0.0" in base_url:
        scheme = request.scheme
        host = request.host
        base_url = f"{scheme}://{host}"
    return base_url


async def yggdrasil_metadata_handler(request: web.Request) -> web.Response:
    """
    GET /api/yggdrasil
    Authlib-injector server metadata endpoint.
    Allows clients and servers to authenticate via -javaagent:authlib-injector.jar=<url>
    """
    base_url = _get_effective_base_url(request)
    parsed = urlparse(base_url)
    domain = parsed.hostname or "localhost"

    return web.json_response({
        "meta": {
            "serverName": "LCALauncher Auth Server",
            "implementationName": "LCAAuth",
            "implementationVersion": "1.0.0",
        },
        "skinDomains": [domain, f".{domain}"],
        "signaturePublickey": yggdrasil_service.public_key_pem,
    })


async def yggdrasil_join_handler(request: web.Request) -> web.Response:
    """
    POST /sessionserver/session/minecraft/join
    Called by Minecraft game client to authenticate connection to a dedicated server.
    Body: {"accessToken": "...", "selectedProfile": "<32_hex_uuid>", "serverId": "..."}
    """
    try:
        data = await request.json()
    except Exception:
        return web.json_response(
            {"error": "IllegalArgumentException", "errorMessage": "Invalid JSON body"},
            status=400,
        )

    access_token = data.get("accessToken", "").strip()
    selected_profile = data.get("selectedProfile", "").strip().lower()
    server_id = data.get("serverId", "").strip()

    if not access_token or not selected_profile or not server_id:
        return web.json_response(
            {"error": "IllegalArgumentException", "errorMessage": "Missing required fields"},
            status=400,
        )

    async with get_db_session() as session:
        valid, msg, user = await AuthService.validate_token(session, access_token)
        if not valid or not user:
            return web.json_response(
                {"error": "ForbiddenOperationException", "errorMessage": "Invalid credentials or session expired"},
                status=403,
            )

        if to_unhyphenated_uuid(user.uuid) != selected_profile:
            logger.warning(
                "Profile mismatch in join: user %s has %s, got %s",
                user.username,
                to_unhyphenated_uuid(user.uuid),
                selected_profile,
            )
            return web.json_response(
                {"error": "ForbiddenOperationException", "errorMessage": "Profile mismatch"},
                status=403,
            )

        # Register join session
        yggdrasil_service.register_server_join(user.username, server_id, user.id)
        # Mojang protocol specifies 204 No Content on success
        return web.Response(status=204)


async def yggdrasil_has_joined_handler(request: web.Request) -> web.Response:
    """
    GET /sessionserver/session/minecraft/hasJoined?username=...&serverId=...
    Called by Minecraft dedicated server to verify player connection.
    Returns 200 OK with signed player profile & textures if valid, or 204 No Content.
    """
    username = request.query.get("username", "").strip()
    server_id = request.query.get("serverId", "").strip()

    if not username or not server_id:
        return web.Response(status=204)

    user_id = yggdrasil_service.verify_server_join(username, server_id)
    if not user_id:
        logger.warning("hasJoined verification failed for player '%s'", username)
        return web.Response(status=204)

    async with get_db_session() as session:
        user = await AuthService.get_user_by_username(session, username)
        if not user or user.is_banned:
            return web.Response(status=204)

        base_url = _get_effective_base_url(request)
        profile_data = yggdrasil_service.build_profile(user, base_url, sign=True)
        return web.json_response(profile_data)


async def yggdrasil_profile_handler(request: web.Request) -> web.Response:
    """
    GET /sessionserver/session/minecraft/profile/{uuid}
    Query profile by UUID with signed textures property.
    """
    raw_uuid = request.match_info.get("uuid", "").strip().replace("-", "").lower()
    unsigned = request.query.get("unsigned", "false").lower() == "true"

    if len(raw_uuid) != 32:
        return web.Response(status=204)

    async with get_db_session() as session:
        # Search by hex without dashes or formatted uuid
        from sqlalchemy import select
        stmt = select(User)
        result = await session.execute(stmt)
        users = result.scalars().all()
        target_user = None
        for u in users:
            if to_unhyphenated_uuid(u.uuid) == raw_uuid:
                target_user = u
                break

        if not target_user:
            return web.Response(status=204)

        base_url = _get_effective_base_url(request)
        profile_data = yggdrasil_service.build_profile(target_user, base_url, sign=not unsigned)
        return web.json_response(profile_data)


async def texture_download_handler(request: web.Request) -> web.StreamResponse:
    """
    GET /textures/{hash}
    Serve raw PNG skin or cape texture.
    """
    texture_hash = request.match_info.get("hash", "").strip()
    path = SkinService.get_texture_path(texture_hash)
    if not path:
        return web.json_response(
            {"error": "texture_not_found", "errorMessage": "Текстура не найдена"},
            status=404,
        )

    return web.FileResponse(
        path=path,
        chunk_size=65536,
        headers={
            "Content-Type": "image/png",
            "Cache-Control": "public, max-age=86400",
        },
    )


async def user_skin_direct_handler(request: web.Request) -> web.StreamResponse:
    """
    GET /api/skins/{username}.png
    Direct link to download user's current skin.
    """
    username = request.match_info.get("username", "").strip()
    if username.endswith(".png"):
        username = username[:-4]

    async with get_db_session() as session:
        user = await AuthService.get_user_by_username(session, username)
        if not user or not user.skin_hash:
            return web.json_response({"error": "skin_not_found"}, status=404)

        path = SkinService.get_texture_path(user.skin_hash)
        if not path:
            return web.json_response({"error": "skin_file_missing"}, status=404)

        return web.FileResponse(
            path=path,
            headers={"Content-Type": "image/png"},
        )


async def user_cape_direct_handler(request: web.Request) -> web.StreamResponse:
    """
    GET /api/capes/{username}.png
    Direct link to download user's current cape.
    """
    username = request.match_info.get("username", "").strip()
    if username.endswith(".png"):
        username = username[:-4]

    async with get_db_session() as session:
        user = await AuthService.get_user_by_username(session, username)
        if not user or not user.cape_hash:
            return web.json_response({"error": "cape_not_found"}, status=404)

        path = SkinService.get_texture_path(user.cape_hash)
        if not path:
            return web.json_response({"error": "cape_file_missing"}, status=404)

        return web.FileResponse(
            path=path,
            headers={"Content-Type": "image/png"},
        )


# ---------------- Channel Posts Endpoints ---------------- #


async def channel_posts_handler(request: web.Request) -> web.Response:
    """
    GET /api/channel/posts
    Returns recent channel news posts with photos, links and view count.
    """
    limit = 3
    try:
        limit = int(request.query.get("limit", "3"))
    except ValueError:
        pass
    refresh = request.query.get("refresh", "0") in ("1", "true", "yes")

    posts = await channel_service.get_latest_posts(limit=limit, force_refresh=refresh)
    return web.json_response({
        "channel": config.channel_username,
        "posts": posts,
    })


async def channel_media_handler(request: web.Request) -> web.StreamResponse:
    """
    GET /api/channel/media/{filename}
    Streams cached channel post media file (JPEG/PNG/WebP).
    """
    filename = request.match_info.get("filename", "").strip()
    path = channel_service.get_media_path(filename)
    if not path:
        return web.json_response({"error": "media_not_found"}, status=404)

    ext = path.suffix.lower()
    content_type = "image/jpeg"
    if ext == ".png":
        content_type = "image/png"
    elif ext == ".webp":
        content_type = "image/webp"

    return web.FileResponse(
        path=path,
        headers={
            "Content-Type": content_type,
            "Cache-Control": "public, max-age=86400",
        },
    )


def create_api_app() -> web.Application:
    """Create and configure the aiohttp Application for the launcher REST API."""
    app = web.Application()

    # Auth routes
    app.router.add_post("/api/auth/request-code", request_code_handler)
    app.router.add_get("/api/auth/poll/{code}", poll_code_handler)
    app.router.add_get("/api/auth/validate", validate_token_handler)
    app.router.add_post("/api/auth/logout", logout_handler)

    # Modpack & Base Client routes
    app.router.add_get("/api/modpack/manifest.json", manifest_handler)
    app.router.add_get("/api/modpack/files/{path:.*}", download_file_handler)
    app.router.add_get("/api/client-base.zip", download_client_base_handler)
    app.router.add_get("/client-base.zip", download_client_base_handler)
    app.router.add_post("/api/modpack/upload", upload_file_handler)
    app.router.add_delete("/api/modpack/files/{path:.*}", delete_file_handler)
    app.router.add_post("/api/modpack/version", set_version_handler)

    # Yggdrasil Session Server routes (Minecraft dedicated server & client auth)
    app.router.add_get("/", yggdrasil_metadata_handler)
    app.router.add_get("/api/yggdrasil", yggdrasil_metadata_handler)
    app.router.add_post("/sessionserver/session/minecraft/join", yggdrasil_join_handler)
    app.router.add_post("/api/yggdrasil/sessionserver/session/minecraft/join", yggdrasil_join_handler)
    app.router.add_get("/sessionserver/session/minecraft/hasJoined", yggdrasil_has_joined_handler)
    app.router.add_get("/api/yggdrasil/sessionserver/session/minecraft/hasJoined", yggdrasil_has_joined_handler)
    app.router.add_get("/sessionserver/session/minecraft/profile/{uuid}", yggdrasil_profile_handler)
    app.router.add_get("/api/yggdrasil/sessionserver/session/minecraft/profile/{uuid}", yggdrasil_profile_handler)

    # Textures (Skins and Capes)
    app.router.add_get("/textures/{hash}", texture_download_handler)
    app.router.add_get("/api/skins/{username}", user_skin_direct_handler)
    app.router.add_get("/api/capes/{username}", user_cape_direct_handler)

    # Channel News Posts & Media
    app.router.add_get("/api/channel/posts", channel_posts_handler)
    app.router.add_get("/api/channel/media/{filename}", channel_media_handler)

    return app
