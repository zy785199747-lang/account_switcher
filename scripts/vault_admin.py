# Read / write the vault's config dict from the command line.
#
# Until Phase 5 ships the Settings dialog, this is the easiest way to poke
# at config values for debugging. Useful for testing the install-path
# detection paths without rebuilding the vault.
#
# Usage:
#   .venv\Scripts\python.exe scripts\vault_admin.py show
#   .venv\Scripts\python.exe scripts\vault_admin.py set <key> <value>
#   .venv\Scripts\python.exe scripts\vault_admin.py clear <key>
#
# You'll be prompted for the master password. Secret-looking values
# (anything with "key" or "token" in the key name) are masked in `show`.

import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.storage.vault import Vault, default_vault_path


def _looks_secret(key: str) -> bool:
    return any(s in key.lower() for s in ("key", "token", "password"))


def cmd_show(v: Vault) -> int:
    print("Vault config:")
    if not v.config:
        print("  (empty)")
        return 0
    for k, val in sorted(v.config.items()):
        if _looks_secret(k):
            shown = "*" * 8 if val else "(empty)"
        else:
            shown = val
        print(f"  {k} = {shown}")
    return 0


def cmd_set(v: Vault, args: list[str]) -> int:
    if len(args) < 2:
        print("usage: set <key> <value>")
        return 1
    key, value = args[0], args[1]
    v.set_config(key, value)
    masked = "********" if _looks_secret(key) else value
    print(f"set {key} = {masked}")
    return 0


def cmd_clear(v: Vault, args: list[str]) -> int:
    if len(args) < 1:
        print("usage: clear <key>")
        return 1
    key = args[0]
    if key not in v.config:
        print(f"(key {key} was not set; nothing to clear)")
        return 0
    del v.config[key]
    v.save()
    print(f"cleared {key}")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    cmd = sys.argv[1]
    rest = sys.argv[2:]

    path = default_vault_path()
    if not path.exists():
        print(f"No vault at {path}. Run `python main.py` first to create one.")
        return 1

    pwd = getpass.getpass("Master password: ")
    try:
        v = Vault.unlock(path, pwd)
    except Exception as exc:
        print(f"Could not unlock vault: {exc}")
        return 1

    if cmd == "show":
        return cmd_show(v)
    if cmd == "set":
        return cmd_set(v, rest)
    if cmd == "clear":
        return cmd_clear(v, rest)

    print(f"unknown command: {cmd}\n")
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
