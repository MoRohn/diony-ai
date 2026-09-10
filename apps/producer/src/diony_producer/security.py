"""Local-first authentication tokens and encrypted BYOK secret storage."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel

from diony_contracts.launch import MembershipRole
from diony_producer.control_plane import ControlPlane


class StoredSecret(BaseModel):
    workspace_id: str
    provider_id: str
    name: str
    ciphertext: str
    masked: str


class Actor(BaseModel):
    user_id: str
    workspace_id: str
    role: MembershipRole
    auth_mode: str


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _configured_key(path: Path) -> bytes:
    encoded = os.environ.get("DIONY_KEY_ENCRYPTION_KEY", "").strip()
    if encoded:
        try:
            key = base64.urlsafe_b64decode(encoded)
        except Exception as exc:
            raise RuntimeError("DIONY_KEY_ENCRYPTION_KEY must be URL-safe base64") from exc
        if len(key) != 32:
            raise RuntimeError("DIONY_KEY_ENCRYPTION_KEY must decode to 32 bytes")
        return key
    if os.environ.get("DIONY_AUTH_MODE", "local").strip().lower() == "token":
        raise RuntimeError("DIONY_KEY_ENCRYPTION_KEY is required on a shared token-auth controller")
    key_path = path.parent / "master.key"
    try:
        raw = key_path.read_bytes()
    except FileNotFoundError:
        key = secrets.token_bytes(32)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(key_path, flags, 0o600)
        except FileExistsError:
            raw = key_path.read_bytes()
        else:
            with os.fdopen(fd, "wb") as handle:
                handle.write(key)
            return key
    else:
        if len(raw) != 32:
            raise RuntimeError(f"Invalid Diony master key at {key_path}")
        return raw
    if len(raw) != 32:
        raise RuntimeError(f"Invalid Diony master key at {key_path}")
    return raw


class SecretVault:
    """AES-256-GCM envelope storage. Plaintext is never returned by list APIs."""

    def __init__(self, plane: ControlPlane) -> None:
        self.plane = plane
        self._key = _configured_key(plane.path)

    @staticmethod
    def _aad(workspace_id: str) -> bytes:
        return f"diony-byok-v1:{workspace_id}".encode()

    @staticmethod
    def _mask(value: str) -> str:
        if len(value) <= 8:
            return "•" * len(value)
        return f"{value[:4]}…{value[-4:]}"

    def encrypt_for_workspace(self, workspace_id: str, plaintext: str) -> str:
        nonce = secrets.token_bytes(12)
        encrypted = AESGCM(self._key).encrypt(nonce, plaintext.encode(), self._aad(workspace_id))
        envelope = {"v": 1, "n": _b64encode(nonce), "c": _b64encode(encrypted)}
        return _b64encode(json.dumps(envelope, separators=(",", ":")).encode())

    def decrypt_for_workspace(self, workspace_id: str, ciphertext: str) -> str:
        envelope = json.loads(_b64decode(ciphertext))
        if envelope.get("v") != 1:
            raise ValueError("Unsupported Diony secret envelope")
        plaintext = AESGCM(self._key).decrypt(
            _b64decode(envelope["n"]),
            _b64decode(envelope["c"]),
            self._aad(workspace_id),
        )
        return plaintext.decode()

    def put(self, workspace_id: str, provider_id: str, name: str, plaintext: str) -> StoredSecret:
        if not plaintext:
            raise ValueError("Provider secret cannot be empty")
        ciphertext = self.encrypt_for_workspace(workspace_id, plaintext)
        masked = self._mask(plaintext)
        self.plane.put_encrypted_secret(workspace_id, provider_id, name, ciphertext, masked)
        return StoredSecret(
            workspace_id=workspace_id,
            provider_id=provider_id,
            name=name,
            ciphertext=ciphertext,
            masked=masked,
        )

    def get(self, workspace_id: str, provider_id: str, name: str) -> str:
        row = self.plane.encrypted_secret(workspace_id, provider_id, name)
        if not row:
            raise KeyError(f"No {name} is stored for {provider_id}")
        return self.decrypt_for_workspace(workspace_id, row[0])


def issue_token(
    user_id: str,
    workspace_id: str,
    role: MembershipRole,
    *,
    ttl_seconds: int = 3600,
    secret: str | None = None,
) -> str:
    signing = (secret or os.environ.get("DIONY_AUTH_SECRET", "")).encode()
    if len(signing) < 24:
        raise RuntimeError("DIONY_AUTH_SECRET must be at least 24 bytes")
    payload = {
        "v": 1,
        "sub": user_id,
        "ws": workspace_id,
        "role": role.value,
        "iat": int(time.time()),
        "exp": int(time.time()) + max(60, ttl_seconds),
    }
    body = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = _b64encode(hmac.new(signing, body.encode(), hashlib.sha256).digest())
    return f"diony1.{body}.{signature}"


def verify_token(token: str, *, secret: str | None = None) -> Actor:
    signing = (secret or os.environ.get("DIONY_AUTH_SECRET", "")).encode()
    if len(signing) < 24:
        raise ValueError("Token authentication is not configured")
    try:
        prefix, body, signature = token.split(".", 2)
    except ValueError as exc:
        raise ValueError("Malformed Diony session token") from exc
    if prefix != "diony1":
        raise ValueError("Unsupported Diony session token")
    expected = hmac.new(signing, body.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64decode(signature)):
        raise ValueError("Invalid Diony session signature")
    payload = json.loads(_b64decode(body))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise ValueError("Diony session expired")
    return Actor(
        user_id=payload["sub"],
        workspace_id=payload["ws"],
        role=MembershipRole(payload["role"]),
        auth_mode="token",
    )
