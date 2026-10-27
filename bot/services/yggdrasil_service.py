"""Minecraft Yggdrasil Session Server implementation with RSA signature support."""

import base64
import json
import logging
import time
from typing import Any, Optional
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from bot.config import config
from bot.db.models import User

logger = logging.getLogger(__name__)


def to_unhyphenated_uuid(uuid_str: str) -> str:
    """Convert standard UUID with dashes to 32 hex characters lowercase."""
    return uuid_str.replace("-", "").lower()


class YggdrasilService:
    def __init__(self) -> None:
        self.private_key: rsa.RSAPrivateKey
        self.public_key: rsa.RSAPublicKey
        self.public_key_pem: str = ""
        # In-memory dictionary for pending server joins:
        # Key: (username.lower(), server_id) -> Value: (user_id, timestamp)
        self._pending_joins: dict[tuple[str, str], tuple[int, float]] = {}
        self._init_keys()

    def _init_keys(self) -> None:
        """Load RSA keypair from disk or generate a new 4096-bit keypair."""
        priv_path = config.keys_dir / "yggdrasil_private.pem"
        pub_path = config.keys_dir / "yggdrasil_public.pem"

        if priv_path.is_file() and pub_path.is_file():
            try:
                self.private_key = serialization.load_pem_private_key(
                    priv_path.read_bytes(),
                    password=None,
                )  # type: ignore
                self.public_key = serialization.load_pem_public_key(
                    pub_path.read_bytes()
                )  # type: ignore
                self.public_key_pem = pub_path.read_text("utf-8")
                logger.info("Loaded existing Yggdrasil RSA keypair.")
                return
            except Exception as e:
                logger.warning("Failed to load existing RSA keys (%s), generating new ones...", e)

        # Generate new 4096-bit RSA keypair
        logger.info("Generating new 4096-bit RSA keypair for Yggdrasil session signatures...")
        self.private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=4096,
        )
        self.public_key = self.private_key.public_key()

        priv_pem = self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        pub_pem = self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        priv_path.write_bytes(priv_pem)
        pub_path.write_bytes(pub_pem)
        self.public_key_pem = pub_pem.decode("utf-8")
        logger.info("Saved new Yggdrasil RSA keypair to %s", config.keys_dir)

    def register_server_join(
        self,
        username: str,
        server_id: str,
        user_id: int,
    ) -> None:
        """Record a player's pending join request with timestamp."""
        now = time.time()
        # Clean up stale joins older than 60 seconds
        self._pending_joins = {
            k: v for k, v in self._pending_joins.items() if now - v[1] < 60
        }
        self._pending_joins[(username.lower(), server_id)] = (user_id, now)
        logger.info("Recorded server join for player '%s' with serverId '%s'", username, server_id)

    def verify_server_join(
        self,
        username: str,
        server_id: str,
    ) -> Optional[int]:
        """
        Verify that player performed join request with matching serverId within 60s.
        Returns user_id if valid, or None.
        """
        now = time.time()
        key = (username.lower(), server_id)
        entry = self._pending_joins.pop(key, None)

        if not entry:
            return None

        user_id, join_time = entry
        if now - join_time > 60:
            return None

        return user_id

    def build_profile(
        self,
        user: User,
        base_url: str,
        sign: bool = True,
    ) -> dict[str, Any]:
        """
        Build Mojang Yggdrasil profile JSON with Base64 textures and SHA1withRSA signature.
        """
        uuid_hex = to_unhyphenated_uuid(user.uuid)
        clean_base = base_url.rstrip("/")

        # Construct textures payload
        textures_dict: dict[str, Any] = {}
        if user.skin_hash:
            skin_obj: dict[str, Any] = {
                "url": f"{clean_base}/textures/{user.skin_hash}",
            }
            if user.skin_model == "slim":
                skin_obj["metadata"] = {"model": "slim"}
            textures_dict["SKIN"] = skin_obj

        if user.cape_hash:
            textures_dict["CAPE"] = {
                "url": f"{clean_base}/textures/{user.cape_hash}",
            }

        textures_payload = {
            "timestamp": int(time.time() * 1000),
            "profileId": uuid_hex,
            "profileName": user.username,
            "isPublic": True,
            "textures": textures_dict,
        }

        # Encode textures to Base64
        textures_json_bytes = json.dumps(textures_payload, separators=(",", ":")).encode("utf-8")
        textures_b64 = base64.b64encode(textures_json_bytes).decode("utf-8")

        property_entry: dict[str, Any] = {
            "name": "textures",
            "value": textures_b64,
        }

        if sign:
            # Sign Base64 payload with SHA1withRSA (standard Mojang Yggdrasil format)
            raw_signature = self.private_key.sign(
                data=textures_b64.encode("utf-8"),
                padding=padding.PKCS1v15(),
                algorithm=hashes.SHA1(),
            )
            property_entry["signature"] = base64.b64encode(raw_signature).decode("utf-8")

        return {
            "id": uuid_hex,
            "name": user.username,
            "properties": [property_entry],
        }


yggdrasil_service = YggdrasilService()
