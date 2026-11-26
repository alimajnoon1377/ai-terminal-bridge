"""Encryption at rest, password hashing and bearer-token handling."""

import base64
import hashlib
import os
import secrets
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_hasher = PasswordHasher()
# Verifying against this when a username is unknown keeps login timing uniform.
_DUMMY_HASH = _hasher.hash(secrets.token_hex(16))


class SecretBox:
    """AES-256-GCM with a per-record associated-data string.

    The associated data binds a ciphertext to its record, so copying an encrypted
    value onto another row makes it undecryptable.
    """

    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError("master key must be exactly 32 bytes")
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: str, aad: str) -> str:
        nonce = os.urandom(12)
        blob = nonce + self._aead.encrypt(nonce, plaintext.encode(), aad.encode())
        return base64.urlsafe_b64encode(blob).decode()

    def decrypt(self, token: str, aad: str) -> str:
        blob = base64.urlsafe_b64decode(token.encode())
        try:
            return self._aead.decrypt(blob[:12], blob[12:], aad.encode()).decode()
        except InvalidTag as exc:
            raise ValueError("cannot decrypt secret: wrong master key or corrupted data") from exc


def generate_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def load_master_key(configured: str, data_dir: Path) -> bytes:
    if not configured:
        path = data_dir / "master.key"
        if not path.exists():
            data_dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(generate_key())
        configured = path.read_text().strip()
    try:
        return base64.urlsafe_b64decode(configured.encode())
    except ValueError as exc:
        raise ValueError("ATB_MASTER_KEY is not valid base64") from exc


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str | None) -> bool:
    try:
        _hasher.verify(hashed or _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False
    return hashed is not None


def new_token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    # Tokens carry 256 bits of entropy, so a fast hash is sufficient.
    return hashlib.sha256(token.encode()).hexdigest()
