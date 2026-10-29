"""Integration tests for aiohttp launcher API endpoints."""

import io
import pytest
import pytest_asyncio
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer
from bot.api.launcher_api import create_api_app
from bot.config import config
from bot.db.engine import init_db, close_db, get_db_session, configure_engine
from bot.services.auth_service import AuthService
from bot.services.code_service import CodeService


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db(tmp_path, monkeypatch):
    """Set up temporary SQLite database and modpack storage for testing."""
    test_db_file = tmp_path / "test_lca.db"
    test_modpack = tmp_path / "modpack"
    test_modpack.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "database_url", f"sqlite+aiosqlite:///{test_db_file}")
    monkeypatch.setattr(config, "modpack_dir", test_modpack)
    monkeypatch.setattr(config, "admin_api_token", "test-secret-token")
    monkeypatch.setattr(config, "api_base_url", "http://testserver")

    configure_engine(config.database_url)

    await init_db()
    yield
    await close_db()


@pytest.mark.asyncio
async def test_auth_request_and_poll_flow():
    """Verify end-to-end request-code, bot confirmation, and polling response."""
    app = create_api_app()
    client = TestClient(TestServer(app))
    await client.start_server()

    try:
        # 1. Request code from launcher
        resp = await client.post("/api/auth/request-code", json={})
        assert resp.status == 200
        data = await resp.json()
        assert "code" in data
        assert "expires_in" in data
        code = data["code"]
        assert len(code) == 6

        # 2. Poll code before confirmation -> pending
        poll_resp1 = await client.get(f"/api/auth/poll/{code}")
        assert poll_resp1.status == 200
        poll_data1 = await poll_resp1.json()
        assert poll_data1["status"] == "pending"

        # 3. Simulate user registration and bot confirming code
        async with get_db_session() as session:
            ok, _, user = await AuthService.register_user(session, 1234567, "GamerPro")
            assert ok
            ok_conf, _, _, _ = await AuthService.confirm_auth_code(session, code, 1234567)
            assert ok_conf

        # 4. Poll code after confirmation -> confirmed with credentials
        poll_resp2 = await client.get(f"/api/auth/poll/{code}")
        assert poll_resp2.status == 200
        poll_data2 = await poll_resp2.json()
        assert poll_data2["status"] == "confirmed"
        assert poll_data2["username"] == "GamerPro"
        assert "uuid" in poll_data2
        assert "accessToken" in poll_data2
        access_token = poll_data2["accessToken"]

        # 5. Validate token
        val_resp = await client.get(
            "/api/auth/validate",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert val_resp.status == 200
        val_data = await val_resp.json()
        assert val_data["valid"] is True
        assert val_data["username"] == "GamerPro"

        # 6. Logout
        logout_resp = await client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert logout_resp.status == 200
        logout_data = await logout_resp.json()
        assert logout_data["success"] is True

        # 7. Validate after logout -> unauthorized
        val_resp2 = await client.get(
            "/api/auth/validate",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert val_resp2.status == 401
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_modpack_upload_manifest_download_flow():
    """Verify modpack upload, manifest generation, download, and deletion."""
    app = create_api_app()
    client = TestClient(TestServer(app))
    await client.start_server()

    try:
        # 1. Set modpack version
        ver_resp = await client.post(
            "/api/modpack/version",
            headers={"X-Admin-Token": "test-secret-token"},
            json={"version": "1.2.0", "changelog": "Initial release"},
        )
        assert ver_resp.status == 200
        assert (await ver_resp.json())["version"] == "1.2.0"

        # 2. Upload mod file
        fake_jar_content = b"PK\x03\x04TestModJarContent123456"
        form = FormData()
        form.add_field("path", "mods/TestMod-1.0.jar")
        form.add_field(
            "file",
            io.BytesIO(fake_jar_content),
            filename="TestMod-1.0.jar",
            content_type="application/java-archive",
        )

        upload_resp = await client.post(
            "/api/modpack/upload",
            headers={"X-Admin-Token": "test-secret-token"},
            data=form,
        )
        assert upload_resp.status == 200
        upload_data = await upload_resp.json()
        assert upload_data["success"] is True
        assert upload_data["path"] == "mods/TestMod-1.0.jar"
        assert upload_data["size"] == len(fake_jar_content)

        # 3. Check manifest
        man_resp = await client.get("/api/modpack/manifest.json")
        assert man_resp.status == 200
        manifest = await man_resp.json()
        assert manifest["version"] == "1.2.0"
        assert len(manifest["files"]) == 1
        entry = manifest["files"][0]
        assert entry["path"] == "mods/TestMod-1.0.jar"
        assert entry["size"] == len(fake_jar_content)
        assert entry["sha256"] == upload_data["sha256"]
        assert "url" in entry

        # 4. Download file
        dl_resp = await client.get("/api/modpack/files/mods/TestMod-1.0.jar")
        assert dl_resp.status == 200
        body = await dl_resp.read()
        assert body == fake_jar_content

        # 5. Delete file
        del_resp = await client.delete(
            "/api/modpack/files/mods/TestMod-1.0.jar",
            headers={"X-Admin-Token": "test-secret-token"},
        )
        assert del_resp.status == 200
        del_data = await del_resp.json()
        assert del_data["success"] is True

        # 6. Verify 404 after delete
        dl_resp404 = await client.get("/api/modpack/files/mods/TestMod-1.0.jar")
        assert dl_resp404.status == 404
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_modpack_disk_sync_and_download_with_spaces():
    """Verify that manually dropping files with spaces into data/modpack/ syncs and downloads."""
    from bot.services.modpack_service import ModpackService

    app = create_api_app()
    client = TestClient(TestServer(app))
    await client.start_server()

    try:
        # 1. Place file manually on disk
        mods_dir = config.modpack_dir / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)
        custom_mod = mods_dir / "Just Enough Items 1.20.jar"
        mod_content = b"PK\x03\x04JEIModData987654321"
        custom_mod.write_bytes(mod_content)

        # 2. Run disk sync
        async with get_db_session() as session:
            res = await ModpackService.sync_disk_files(session)
            assert res["added"] >= 1

        # 3. Request manifest
        man_resp = await client.get("/api/modpack/manifest.json")
        assert man_resp.status == 200
        man_data = await man_resp.json()
        assert len(man_data["files"]) >= 1

        found_file = next(f for f in man_data["files"] if "Just Enough Items" in f["path"])
        assert found_file["path"] == "mods/Just Enough Items 1.20.jar"
        assert "%20" in found_file["url"]

        # 4. Download file using the URL from manifest
        download_url = found_file["url"].replace("http://testserver", "")
        dl_resp = await client.get(download_url)
        assert dl_resp.status == 200
        dl_bytes = await dl_resp.read()
        assert dl_bytes == mod_content
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_client_base_zip_download_and_manifest_exclusion():
    """Verify client-base.zip is downloadable via API but excluded from modpack manifest."""
    from bot.services.modpack_service import ModpackService

    app = create_api_app()
    client = TestClient(TestServer(app))
    await client.start_server()

    try:
        # Create client-base.zip in modpack dir
        client_base_file = config.modpack_dir / "client-base.zip"
        fake_zip_bytes = b"PK\x03\x04FakeClientBaseArchiveContent123"
        client_base_file.write_bytes(fake_zip_bytes)

        # Run sync
        async with get_db_session() as session:
            await ModpackService.sync_disk_files(session)

        # Check manifest -> client-base.zip MUST NOT be listed in files
        man_resp = await client.get("/api/modpack/manifest.json")
        assert man_resp.status == 200
        manifest = await man_resp.json()
        manifest_paths = [f["path"] for f in manifest["files"]]
        assert "client-base.zip" not in manifest_paths

        # Download via /api/modpack/files/client-base.zip
        dl_resp1 = await client.get("/api/modpack/files/client-base.zip")
        assert dl_resp1.status == 200
        assert (await dl_resp1.read()) == fake_zip_bytes

        # Download via alias /api/client-base.zip
        dl_resp2 = await client.get("/api/client-base.zip")
        assert dl_resp2.status == 200
        assert (await dl_resp2.read()) == fake_zip_bytes
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_api_rate_limiting():
    """Verify rate limit of 10 requests per minute on request-code."""
    app = create_api_app()
    client = TestClient(TestServer(app))
    await client.start_server()

    try:
        # Reset limiter for clean test
        from bot.api.launcher_api import ip_limiter
        ip_limiter.requests.clear()

        # Send 10 allowed requests
        for i in range(10):
            resp = await client.post("/api/auth/request-code", json={})
            assert resp.status == 200

        # 11th request should be rate limited (429)
        resp_blocked = await client.post("/api/auth/request-code", json={})
        assert resp_blocked.status == 429
        data = await resp_blocked.json()
        assert data["error"] == "rate_limited"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_admin_auth_security():
    """Verify protected modpack endpoints reject requests without valid X-Admin-Token."""
    app = create_api_app()
    client = TestClient(TestServer(app))
    await client.start_server()

    try:
        # Set version without token -> 403
        resp1 = await client.post("/api/modpack/version", json={"version": "2.0.0"})
        assert resp1.status == 403

        # Set version with wrong token -> 403
        resp2 = await client.post(
            "/api/modpack/version",
            headers={"X-Admin-Token": "wrong-token"},
            json={"version": "2.0.0"},
        )
        assert resp2.status == 403

        # Delete file without token -> 403
        resp3 = await client.delete("/api/modpack/files/mods/NonExistent.jar")
        assert resp3.status == 403
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_channel_posts_and_media_endpoint(tmp_path, monkeypatch):
    """Verify /api/channel/posts returns posts and /api/channel/media/{filename} returns cached file."""
    test_media_dir = tmp_path / "channel_media"
    test_media_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "channel_media_dir", test_media_dir)

    # Put a mock image file
    mock_file = test_media_dir / "test.jpg"
    mock_file.write_bytes(b"\xFF\xD8\xFF\xE0\x00\x10JFIF")

    app = create_api_app()
    client = TestClient(TestServer(app))
    await client.start_server()

    try:
        # 1. Fetch channel posts
        resp = await client.get("/api/channel/posts?limit=3")
        assert resp.status == 200
        data = await resp.json()
        assert "posts" in data
        assert isinstance(data["posts"], list)
        assert len(data["posts"]) <= 3

        # 2. Fetch existing media
        resp_media = await client.get("/api/channel/media/test.jpg")
        assert resp_media.status == 200
        assert resp_media.headers["Content-Type"] == "image/jpeg"
        content = await resp_media.read()
        assert content == b"\xFF\xD8\xFF\xE0\x00\x10JFIF"

        # 3. Non-existent media -> 404
        resp_404 = await client.get("/api/channel/media/nonexistent.jpg")
        assert resp_404.status == 404

        # 4. Verify cleanup_old_media deletes obsolete files
        extra_file = test_media_dir / "999.jpg"
        extra_file.write_bytes(b"old_data")
        assert extra_file.exists()

        from bot.services.channel_service import channel_service
        deleted = channel_service.cleanup_old_media(keep_ids={123})
        assert not extra_file.exists()
        assert deleted >= 1
    finally:
        await client.close()
