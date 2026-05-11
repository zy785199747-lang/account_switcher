# Encrypted vault.
#
# On disk format (vault.enc):
#   {
#     "version": 1,
#     "salt": "<base64>",
#     "ciphertext": "<base64>"
#   }
#
# After Fernet decrypts the ciphertext, we get a JSON blob that looks like:
#   {
#     "accounts": [ {Account dict}, ... ],
#     "config":   { "riot_api_key": "...", "default_region": "...", ... }
#   }
#
# `accounts` is the user's account list. `config` is admin-only stuff (API key,
# Riot install path, auto-fill mode) and is read by AdminWindow / SettingsDialog
# in later phases. Phase 1 only needs the wrapper to load + save.

import base64
import json
import logging
import os
from pathlib import Path
from typing import List, Optional

from src.models import Account
from src.storage.crypto import (
    InvalidPassword,
    SALT_LENGTH_BYTES,
    decrypt,
    derive_key,
    encrypt,
    generate_salt,
)

VAULT_VERSION = 1

log = logging.getLogger(__name__)


def default_vault_path() -> Path:
    # Windows: %APPDATA%\RiotAccountSwitcher\vault.enc
    # Falls back to home dir if APPDATA is missing (e.g. running on linux for tests).
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home()
    return base / "RiotAccountSwitcher" / "vault.enc"


class VaultNotFound(Exception):
    # Raised when caller asks to unlock a vault file that doesn't exist yet.
    # main.py uses this to decide between "set master password" and "unlock".
    pass


class CorruptVault(Exception):
    # Raised when the file exists but isn't a valid vault (bad JSON, missing
    # fields, wrong version). Different from InvalidPassword on purpose so the
    # UI can explain what happened.
    pass


class Vault:
    # In-memory state once unlocked.
    # Keep this object alive for the duration of the session. On lock() or
    # exit, drop the reference so the master_key gets garbage collected.

    def __init__(self, path: Path):
        self.path = path
        self._master_key: Optional[bytes] = None
        self._salt: Optional[bytes] = None
        self.accounts: List[Account] = []
        self.config: dict = {}

    # ---------- factory methods ----------

    @classmethod
    def create(cls, path: Path, master_password: str) -> "Vault":
        # Build a fresh empty vault and write it to disk. Used on first run.
        if path.exists():
            raise FileExistsError(f"vault already exists at {path}")
        log.info("creating new vault at %s", path)

        v = cls(path)
        v._salt = generate_salt()
        v._master_key = derive_key(master_password, v._salt)
        v.accounts = []
        v.config = {}
        v.save()
        return v

    @classmethod
    def unlock(cls, path: Path, master_password: str) -> "Vault":
        # Read + decrypt. Raises VaultNotFound, CorruptVault, or InvalidPassword.
        if not path.exists():
            raise VaultNotFound(f"no vault at {path}")
        log.info("unlocking vault at %s", path)

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CorruptVault(f"vault file is not valid JSON: {exc}") from exc

        if raw.get("version") != VAULT_VERSION:
            raise CorruptVault(
                f"vault version {raw.get('version')} not supported, expected {VAULT_VERSION}"
            )
        try:
            salt = base64.b64decode(raw["salt"])
            ciphertext = base64.b64decode(raw["ciphertext"])
        except (KeyError, ValueError) as exc:
            raise CorruptVault(f"vault file missing fields: {exc}") from exc

        if len(salt) != SALT_LENGTH_BYTES:
            raise CorruptVault(f"salt has wrong length: {len(salt)}")

        key = derive_key(master_password, salt)
        plaintext = decrypt(ciphertext, key)  # raises InvalidPassword on bad pwd

        try:
            payload = json.loads(plaintext.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            # Decryption succeeded but inner JSON is broken. This shouldn't
            # happen unless the vault was corrupted post-encrypt.
            raise CorruptVault(f"decrypted payload is not valid JSON: {exc}") from exc

        v = cls(path)
        v._salt = salt
        v._master_key = key
        v.accounts = [Account.from_dict(a) for a in payload.get("accounts", [])]
        v.config = payload.get("config", {})
        log.info("vault unlocked: %d accounts, config keys: %s",
                 len(v.accounts), list(v.config.keys()))
        return v

    # ---------- persistence ----------

    def save(self) -> None:
        # Re-encrypt and write atomically. Uses the existing salt/key so the
        # master password doesn't need to be re-typed on every save.
        if self._master_key is None or self._salt is None:
            raise RuntimeError("cannot save vault before unlock/create")

        payload = {
            "accounts": [a.to_dict() for a in self.accounts],
            "config": self.config,
        }
        plaintext = json.dumps(payload).encode("utf-8")
        ciphertext = encrypt(plaintext, self._master_key)

        on_disk = {
            "version": VAULT_VERSION,
            "salt": base64.b64encode(self._salt).decode("ascii"),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        }

        # Atomic-ish write: write to temp then rename. Avoids leaving a half-
        # written vault if the process crashes mid-save.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(on_disk), encoding="utf-8")
        os.replace(tmp, self.path)
        log.info("vault saved (%d accounts)", len(self.accounts))

    def lock(self) -> None:
        # Wipe in-memory secrets. Best-effort — python doesn't guarantee
        # zeroing memory, but we drop the references so GC can collect.
        log.info("locking vault")
        self._master_key = None
        self.accounts = []
        self.config = {}

    def reload(self) -> None:
        # Reload vault contents from disk without requiring master password.
        # Useful when another instance has written changes.
        if self._master_key is None or self._salt is None:
            raise RuntimeError("cannot reload vault before unlock/create")

        if not self.path.exists():
            raise VaultNotFound(f"vault file disappeared: {self.path}")

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CorruptVault(f"vault file is not valid JSON: {exc}") from exc

        try:
            ciphertext = base64.b64decode(raw["ciphertext"])
        except (KeyError, ValueError) as exc:
            raise CorruptVault(f"vault file missing ciphertext: {exc}") from exc

        plaintext = decrypt(ciphertext, self._master_key)
        try:
            payload = json.loads(plaintext.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CorruptVault(f"vault payload is not valid JSON: {exc}") from exc

        self.accounts = [Account.from_dict(d) for d in payload.get("accounts", [])]
        self.config = payload.get("config", {})
        log.info("vault reloaded: %d accounts, %d config keys",
                 len(self.accounts), len(self.config))

    # ---------- account CRUD ----------

    def add(self, account: Account) -> None:
        log.info("adding account id=%s", account.id)
        self.accounts.append(account)
        self.save()

    def update(self, account: Account) -> None:
        log.info("updating account id=%s", account.id)
        for i, existing in enumerate(self.accounts):
            if existing.id == account.id:
                self.accounts[i] = account
                self.save()
                return
        raise KeyError(f"no account with id={account.id}")

    def remove(self, account_id: str) -> None:
        log.info("removing account id=%s", account_id)
        before = len(self.accounts)
        self.accounts = [a for a in self.accounts if a.id != account_id]
        if len(self.accounts) == before:
            raise KeyError(f"no account with id={account_id}")
        self.save()

    def reorder(self, new_order_ids: List[str]) -> None:
        # Rearrange self.accounts according to a permutation of the current
        # account ids. Used by the drag-and-drop reorder feature in the UI.
        # Strict on input: the new id list must be a permutation of the
        # existing one — missing or extra ids raise. This is safer than
        # silently dropping rows on a UI bug.
        log.info("reordering vault (%d accounts)", len(new_order_ids))
        by_id = {a.id: a for a in self.accounts}
        existing_ids = set(by_id.keys())
        requested_ids = set(new_order_ids)
        if existing_ids != requested_ids:
            missing = existing_ids - requested_ids
            extra = requested_ids - existing_ids
            raise ValueError(
                f"reorder id set does not match current accounts "
                f"(missing={missing}, extra={extra})"
            )
        # Duplicate ids in the new list would silently shorten the result.
        if len(new_order_ids) != len(set(new_order_ids)):
            raise ValueError("reorder list contains duplicate ids")
        self.accounts = [by_id[i] for i in new_order_ids]
        self.save()

    def get(self, account_id: str) -> Account:
        for a in self.accounts:
            if a.id == account_id:
                return a
        raise KeyError(f"no account with id={account_id}")

    # ---------- config (used by later phases) ----------

    def set_config(self, key: str, value) -> None:
        self.config[key] = value
        self.save()

    def get_config(self, key: str, default=None):
        return self.config.get(key, default)
