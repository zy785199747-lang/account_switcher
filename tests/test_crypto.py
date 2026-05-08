# Unit tests for src/storage/crypto.py.
#
# We care about three things:
#   1. encrypt -> decrypt round-trips with the right password.
#   2. decrypting with the wrong password raises InvalidPassword (not some
#      cryptic Fernet error).
#   3. derive_key is deterministic for a given (password, salt) pair.

import pytest

from src.storage.crypto import (
    InvalidPassword,
    SALT_LENGTH_BYTES,
    decrypt,
    derive_key,
    encrypt,
    generate_salt,
)


def test_generate_salt_correct_length():
    salt = generate_salt()
    assert len(salt) == SALT_LENGTH_BYTES


def test_generate_salt_is_random():
    # Two salts in a row must differ. If this ever fails, something is very wrong.
    assert generate_salt() != generate_salt()


def test_derive_key_deterministic():
    salt = b"\x00" * SALT_LENGTH_BYTES
    k1 = derive_key("hunter2", salt)
    k2 = derive_key("hunter2", salt)
    assert k1 == k2


def test_derive_key_different_salt_different_key():
    k1 = derive_key("hunter2", b"\x01" * SALT_LENGTH_BYTES)
    k2 = derive_key("hunter2", b"\x02" * SALT_LENGTH_BYTES)
    assert k1 != k2


def test_derive_key_rejects_empty_password():
    with pytest.raises(ValueError):
        derive_key("", generate_salt())


def test_derive_key_rejects_bad_salt_length():
    with pytest.raises(ValueError):
        derive_key("hunter2", b"too short")


def test_round_trip():
    salt = generate_salt()
    key = derive_key("correct horse battery staple", salt)
    plaintext = b'{"hello": "world"}'
    cipher = encrypt(plaintext, key)
    assert decrypt(cipher, key) == plaintext


def test_wrong_password_raises_invalid_password():
    salt = generate_salt()
    good_key = derive_key("right", salt)
    bad_key = derive_key("wrong", salt)
    cipher = encrypt(b"secret", good_key)
    with pytest.raises(InvalidPassword):
        decrypt(cipher, bad_key)


def test_corrupted_ciphertext_raises_invalid_password():
    # Tampered ciphertext also surfaces as InvalidPassword. The UI doesn't
    # distinguish between "wrong password" and "tampered file" because either
    # way the user can't proceed.
    salt = generate_salt()
    key = derive_key("right", salt)
    cipher = bytearray(encrypt(b"secret", key))
    cipher[-1] ^= 0xFF  # flip the last byte to break the HMAC
    with pytest.raises(InvalidPassword):
        decrypt(bytes(cipher), key)


def test_encrypt_rejects_non_bytes_plaintext():
    salt = generate_salt()
    key = derive_key("hunter2", salt)
    with pytest.raises(TypeError):
        encrypt("not bytes", key)  # type: ignore[arg-type]
