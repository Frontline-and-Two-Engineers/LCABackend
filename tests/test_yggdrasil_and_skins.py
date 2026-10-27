"""Tests for Skin Service, Yggdrasil Session Server, and RSA signatures."""

import base64
import json
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from aiohttp.test_utils import TestClient, TestServer
from bot.api.launcher_api import create_api_app
from bot.config import config
from bot.db.engine import init_db, close_db, get_db_session, configure_engine
from bot.services.auth_service import AuthService
from bot.services.skin_service import SkinService, TextureValidationError
from bot.services.yggdrasil_service import yggdrasil_service, to_unhyphenated_uuid


def make_dummy_png(width: int, height: int) -> bytes:
    """Generate minimal valid PNG byte structure with IHDR chunk."""
    header = b"\x89PNG\r\n\x1a\n"
    # Chunk: length (4), type (4), data, crc (4)
    ihdr_data = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"  # 8 bit depth, RGBA
    )
    ihdr_chunk = (
        len(ihdr_data).to_bytes(4, "big")
        + b"IHDR"
        + ihdr_data
        + b"\x00\x00\x00\x00"  # dummy crc
    )
    return header + ihdr_chunk + b"\x00\x00\x00\x00IEND\xaeB`\x82"


@pytest_asyncio.fixture(autouse=True)
async def setup_test_env(tmp_path, monkeypatch):
    """Set up temporary database and storage paths."""
    test_db = tmp_path / "test_ygg.db"
    skins_dir = tmp_path / "skins"
    capes_dir = tmp_path / "capes"
    keys_dir = tmp_path / "keys"

    skins_dir.mkdir(parents=True, exist_ok=True)
    capes_dir.mkdir(parents=True, exist_ok=True)
    keys_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "database_url", f"sqlite+aiosqlite:///{test_db}")
    monkeypatch.setattr(config, "skins_dir", skins_dir)
    monkeypatch.setattr(config, "capes_dir", capes_dir)
    monkeypatch.setattr(config, "keys_dir", keys_dir)
    monkeypatch.setattr(config, "api_base_url", "http://testserver")

    # Re-init keys in test dir
    yggdrasil_service._init_keys()
    configure_engine(config.database_url)

    await init_db()
    yield
    await close_db()


@pytest.mark.asyncio
async def test_skin_and_cape_validation():
    """Verify PNG inspection and dimension validation."""
    # Invalid non-PNG data
    with pytest.raises(TextureValidationError):
        SkinService.inspect_png_dimensions(b"Not a png file")

    # Valid 64x64 skin
    png_64 = make_dummy_png(64, 64)
    w, h = SkinService.inspect_png_dimensions(png_64)
    assert (w, h) == (64, 64)

    # Valid 64x32 cape/skin
    png_32 = make_dummy_png(64, 32)
    w, h = SkinService.inspect_png_dimensions(png_32)
    assert (w, h) == (64, 32)

    # Invalid dimension 100x100
    png_bad = make_dummy_png(100, 100)
    async with get_db_session() as session:
        _, _, user = await AuthService.register_user(session, 5001, "SkinTester")
        assert user is not None

        with pytest.raises(TextureValidationError):
            await SkinService.set_user_skin(session, user, png_bad)

        # Successful skin setting
        skin_hash = await SkinService.set_user_skin(session, user, png_64, model="slim")
        assert len(skin_hash) == 64
        assert user.skin_hash == skin_hash
        assert user.skin_model == "slim"

        # Successful cape setting
        cape_hash = await SkinService.set_user_cape(session, user, png_32)
        assert len(cape_hash) == 64
        assert user.cape_hash == cape_hash

        # File exists on disk
        assert (config.skins_dir / f"{skin_hash}.png").exists()
        assert (config.capes_dir / f"{cape_hash}.png").exists()


@pytest.mark.asyncio
async def test_rsa_signature_verification():
    """Verify Yggdrasil profile Base64 textures and RSA PKCS#1 v1.5 SHA1 signature."""
    async with get_db_session() as session:
        _, _, user = await AuthService.register_user(session, 6001, "SignTester")
        assert user is not None

        png_skin = make_dummy_png(64, 64)
        skin_hash = await SkinService.set_user_skin(session, user, png_skin, model="slim")

        profile = yggdrasil_service.build_profile(user, "http://auth.server.com", sign=True)
        assert profile["name"] == "SignTester"
        assert profile["id"] == to_unhyphenated_uuid(user.uuid)
        assert len(profile["properties"]) == 1

        prop = profile["properties"][0]
        assert prop["name"] == "textures"
        textures_b64 = prop["value"]
        signature_b64 = prop["signature"]

        # Decode textures json
        textures_json = json.loads(base64.b64decode(textures_b64).decode("utf-8"))
        assert textures_json["profileName"] == "SignTester"
        assert "SKIN" in textures_json["textures"]
        assert textures_json["textures"]["SKIN"]["url"] == f"http://auth.server.com/textures/{skin_hash}"
        assert textures_json["textures"]["SKIN"]["metadata"]["model"] == "slim"

        # Verify cryptographic signature using public key
        sig_bytes = base64.b64decode(signature_b64)
        # Should NOT raise InvalidSignature
        yggdrasil_service.public_key.verify(
            signature=sig_bytes,
            data=textures_b64.encode("utf-8"),
            padding=padding.PKCS1v15(),
            algorithm=hashes.SHA1(),
        )


@pytest.mark.asyncio
async def test_yggdrasil_join_and_has_joined_flow():
    """Verify full end-to-end join and hasJoined flow for Minecraft server."""
    app = create_api_app()
    client = TestClient(TestServer(app))
    await client.start_server()

    try:
        # 1. Register user and obtain token
        async with get_db_session() as session:
            ok, _, user = await AuthService.register_user(session, 7001, "McServerPlayer")
            assert ok and user is not None
            png_skin = make_dummy_png(64, 64)
            await SkinService.set_user_skin(session, user, png_skin)

            # Confirm auth code to get access token
            from bot.services.code_service import CodeService
            code_obj = await CodeService.create_auth_code(session, 300)
            ok_conf, _, conf_code, _ = await AuthService.confirm_auth_code(
                session, code_obj.code, 7001
            )
            assert ok_conf and conf_code is not None
            access_token = conf_code.session_token
            uuid_hex = to_unhyphenated_uuid(user.uuid)

        server_id_hash = "1a2b3c4d5e6f7a8b9c0d"

        # 2. Game Client calls join
        join_payload = {
            "accessToken": access_token,
            "selectedProfile": uuid_hex,
            "serverId": server_id_hash,
        }
        join_resp = await client.post(
            "/sessionserver/session/minecraft/join",
            json=join_payload,
        )
        assert join_resp.status == 204

        # 3. Dedicated Server calls hasJoined
        has_joined_resp = await client.get(
            f"/sessionserver/session/minecraft/hasJoined?username=McServerPlayer&serverId={server_id_hash}"
        )
        assert has_joined_resp.status == 200
        server_profile = await has_joined_resp.json()
        assert server_profile["name"] == "McServerPlayer"
        assert server_profile["id"] == uuid_hex
        assert len(server_profile["properties"]) == 1

        # 4. Try hasJoined again -> should be 204 (already consumed)
        second_resp = await client.get(
            f"/sessionserver/session/minecraft/hasJoined?username=McServerPlayer&serverId={server_id_hash}"
        )
        assert second_resp.status == 204

        # 5. Check profile endpoint
        prof_resp = await client.get(
            f"/sessionserver/session/minecraft/profile/{uuid_hex}"
        )
        assert prof_resp.status == 200
        prof_data = await prof_resp.json()
        assert prof_data["name"] == "McServerPlayer"

        # 6. Check authlib-injector metadata endpoint
        meta_resp = await client.get("/api/yggdrasil")
        assert meta_resp.status == 200
        meta_data = await meta_resp.json()
        assert "signaturePublickey" in meta_data
        assert "BEGIN PUBLIC KEY" in meta_data["signaturePublickey"]
    finally:
        await client.close()
