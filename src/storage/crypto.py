# Crypto helpers for the vault.
#
# How encryption works here:
#   1. User types a master password.
#   2. PBKDF2-HMAC-SHA256 stretches it into a 32-byte key (200k iterations,
#      slow on purpose so brute force is expensive).
#   3. Fernet wraps that key. Fernet = AES-128-CBC + HMAC-SHA256, authenticated.
#   4. We store the random per-vault salt next to the ciphertext so we can
#      re-derive the key on next unlock.
#
# Why these choices: cryptography library is the de-facto standard in python.
# Fernet is the "don't roll your own crypto" preset. PBKDF2 is fine for this
# threat model (offline attacker with the file). 200k iterations is the OWASP
# 2023 recommended minimum for SHA256-based PBKDF2.

import base64
import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Constants at top so they are easy to tweak when debugging.
PBKDF2_ITERATIONS = 200_000
KEY_LENGTH_BYTES = 32
SALT_LENGTH_BYTES = 16

log = logging.getLogger(__name__)


class InvalidPassword(Exception):
    # Raised when decryption fails because the master password is wrong.
    # Callers should show a friendly "wrong password" message in the UI.
    pass


def generate_salt() -> bytes:
    # Each vault gets its own random salt. Stored alongside the ciphertext.
    # Must NEVER be reused across vaults.
    return os.urandom(SALT_LENGTH_BYTES)


def derive_key(master_password: str, salt: bytes) -> bytes:
    # Slow on purpose. ~200ms on a modern laptop. That's fine for a one-time
    # unlock and painful for a brute-force attacker.
    if not isinstance(master_password, str) or master_password == "":
        raise ValueError("master_password must be a non-empty string")
    if len(salt) != SALT_LENGTH_BYTES:
        raise ValueError(f"salt must be {SALT_LENGTH_BYTES} bytes, got {len(salt)}")

    log.debug("deriving key with %d iterations", PBKDF2_ITERATIONS)
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_LENGTH_BYTES,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    raw_key = kdf.derive(master_password.encode("utf-8"))
    # Fernet expects a base64-url-safe-encoded 32-byte key.
    return base64.urlsafe_b64encode(raw_key)


def encrypt(plaintext: bytes, key: bytes) -> bytes:
    # `key` is what derive_key returned. Returns the Fernet token (base64).
    if not isinstance(plaintext, (bytes, bytearray)):
        raise TypeError("plaintext must be bytes")
    return Fernet(key).encrypt(bytes(plaintext))


def decrypt(ciphertext: bytes, key: bytes) -> bytes:
    # Wraps Fernet's InvalidToken into our InvalidPassword so callers don't
    # have to know about cryptography internals.
    try:
        return Fernet(key).decrypt(bytes(ciphertext))
    except InvalidToken as exc:
        log.info("decrypt failed: invalid token (likely wrong master password)")
        raise InvalidPassword("wrong master password") from exc
