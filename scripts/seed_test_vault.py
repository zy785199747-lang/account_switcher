# Throwaway-vault seeder.
#
# Usage:
#   .venv\Scripts\python.exe scripts\seed_test_vault.py
#
# Drops a fresh vault at %APPDATA%\RiotAccountSwitcher\vault.enc with three
# fake accounts so you can exercise the UI without typing real credentials.
# If a vault already exists, asks before overwriting.
#
# Master password for the seeded vault: "test1234"

import sys
from pathlib import Path

# Add repo root to sys.path so `from src.*` works when run as a script.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.models import Account
from src.storage.vault import Vault, default_vault_path

SEED_PASSWORD = "test1234"

FAKE_ACCOUNTS = [
    Account(
        username="alice_lol",
        password="alice-pwd",
        game_name="AliceMain",
        tag_line="NA1",
        region="na1",
    ),
    Account(
        username="bob_smurf",
        password="bob-pwd",
        game_name="BobSmurf",
        tag_line="EUW",
        region="euw1",
    ),
    Account(
        username="carol_kr",
        password="carol-pwd",
        game_name="CarolKR",
        tag_line="KR1",
        region="kr",
    ),
]


def main() -> int:
    path = default_vault_path()
    print(f"Vault path: {path}")

    if path.exists():
        ans = input("Vault already exists. Overwrite? [y/N] ").strip().lower()
        if ans != "y":
            print("aborted")
            return 1
        path.unlink()

    print(f"Creating vault with master password: {SEED_PASSWORD}")
    v = Vault.create(path, SEED_PASSWORD)
    for a in FAKE_ACCOUNTS:
        v.add(a)
        print(f"  + added {a.game_name}#{a.tag_line} (region={a.region})")

    print(f"\nDone. Run `.venv\\Scripts\\python.exe main.py` and unlock with "
          f"password '{SEED_PASSWORD}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
