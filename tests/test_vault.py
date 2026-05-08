# Unit tests for src/storage/vault.py.
#
# We use a temp directory (pytest's tmp_path fixture) so tests don't touch
# the real %APPDATA% vault. Each test gets its own isolated vault file.

import json

import pytest

from src.models import Account
from src.storage.crypto import InvalidPassword
from src.storage.vault import (
    CorruptVault,
    Vault,
    VaultNotFound,
)


# ---------- helpers ----------

def _make_account(username="alice", game_name="Alice", tag_line="NA1"):
    # Build a minimal Account we can shove in a vault for testing.
    return Account(
        username=username,
        password="secret-pwd",
        game_name=game_name,
        tag_line=tag_line,
        region="na1",
    )


# ---------- create + unlock ----------

def test_create_and_unlock_round_trip(tmp_path):
    path = tmp_path / "vault.enc"
    Vault.create(path, "hunter2")
    assert path.exists()

    v = Vault.unlock(path, "hunter2")
    assert v.accounts == []
    assert v.config == {}


def test_create_refuses_overwrite(tmp_path):
    path = tmp_path / "vault.enc"
    Vault.create(path, "hunter2")
    with pytest.raises(FileExistsError):
        Vault.create(path, "different")


def test_unlock_missing_file_raises_vault_not_found(tmp_path):
    with pytest.raises(VaultNotFound):
        Vault.unlock(tmp_path / "does_not_exist.enc", "hunter2")


def test_unlock_wrong_password_raises_invalid_password(tmp_path):
    path = tmp_path / "vault.enc"
    Vault.create(path, "right")
    with pytest.raises(InvalidPassword):
        Vault.unlock(path, "wrong")


def test_unlock_corrupted_json_raises_corrupt_vault(tmp_path):
    path = tmp_path / "vault.enc"
    path.write_text("this is not valid JSON {{{")
    with pytest.raises(CorruptVault):
        Vault.unlock(path, "hunter2")


def test_unlock_missing_fields_raises_corrupt_vault(tmp_path):
    path = tmp_path / "vault.enc"
    path.write_text(json.dumps({"version": 1, "salt": "AAAA"}))  # no ciphertext
    with pytest.raises(CorruptVault):
        Vault.unlock(path, "hunter2")


def test_unlock_wrong_version_raises_corrupt_vault(tmp_path):
    path = tmp_path / "vault.enc"
    path.write_text(json.dumps({
        "version": 999,
        "salt": "AAAAAAAAAAAAAAAAAAAAAA==",
        "ciphertext": "AAAA",
    }))
    with pytest.raises(CorruptVault):
        Vault.unlock(path, "hunter2")


# ---------- account CRUD ----------

def test_add_and_persist(tmp_path):
    path = tmp_path / "vault.enc"
    v = Vault.create(path, "hunter2")
    v.add(_make_account())

    # Re-unlock from disk to prove the change actually persisted.
    v2 = Vault.unlock(path, "hunter2")
    assert len(v2.accounts) == 1
    assert v2.accounts[0].username == "alice"
    assert v2.accounts[0].game_name == "Alice"


def test_update_account(tmp_path):
    path = tmp_path / "vault.enc"
    v = Vault.create(path, "hunter2")
    a = _make_account()
    v.add(a)

    a.password = "rotated"
    v.update(a)

    v2 = Vault.unlock(path, "hunter2")
    assert v2.accounts[0].password == "rotated"


def test_update_unknown_id_raises(tmp_path):
    path = tmp_path / "vault.enc"
    v = Vault.create(path, "hunter2")
    ghost = _make_account()
    with pytest.raises(KeyError):
        v.update(ghost)


def test_remove_account(tmp_path):
    path = tmp_path / "vault.enc"
    v = Vault.create(path, "hunter2")
    a = _make_account()
    v.add(a)
    v.remove(a.id)

    v2 = Vault.unlock(path, "hunter2")
    assert v2.accounts == []


def test_remove_unknown_id_raises(tmp_path):
    path = tmp_path / "vault.enc"
    v = Vault.create(path, "hunter2")
    with pytest.raises(KeyError):
        v.remove("nope")


def test_get_account(tmp_path):
    path = tmp_path / "vault.enc"
    v = Vault.create(path, "hunter2")
    a = _make_account()
    v.add(a)
    assert v.get(a.id).username == "alice"


def test_multiple_accounts_round_trip(tmp_path):
    path = tmp_path / "vault.enc"
    v = Vault.create(path, "hunter2")
    v.add(_make_account(username="alice", game_name="Alice"))
    v.add(_make_account(username="bob", game_name="Bob"))
    v.add(_make_account(username="carol", game_name="Carol"))

    v2 = Vault.unlock(path, "hunter2")
    names = sorted(a.username for a in v2.accounts)
    assert names == ["alice", "bob", "carol"]


# ---------- config ----------

def test_set_get_config(tmp_path):
    path = tmp_path / "vault.enc"
    v = Vault.create(path, "hunter2")
    v.set_config("riot_api_key", "RGAPI-test-key")
    v.set_config("default_region", "kr")

    v2 = Vault.unlock(path, "hunter2")
    assert v2.get_config("riot_api_key") == "RGAPI-test-key"
    assert v2.get_config("default_region") == "kr"
    assert v2.get_config("missing", "fallback") == "fallback"


def test_lock_clears_in_memory_state(tmp_path):
    path = tmp_path / "vault.enc"
    v = Vault.create(path, "hunter2")
    v.add(_make_account())
    v.lock()
    assert v.accounts == []
    assert v.config == {}


def test_save_after_lock_raises(tmp_path):
    path = tmp_path / "vault.enc"
    v = Vault.create(path, "hunter2")
    v.lock()
    with pytest.raises(RuntimeError):
        v.save()
