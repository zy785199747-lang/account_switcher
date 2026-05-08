# Riot Account Switcher

A Windows desktop app that stores multiple Riot Games accounts encrypted, shows each as a card with current League of Legends rank, and switches between them with one click — no more copy-pasting credentials every time.

![Phase status](https://img.shields.io/badge/version-0.4--switch-blue) ![Platform](https://img.shields.io/badge/platform-Windows-lightgrey) ![Python](https://img.shields.io/badge/python-3.10%2B-blue)

## Features

- **Encrypted vault** — accounts protected by a master password (AES via `cryptography`'s Fernet, PBKDF2-HMAC-SHA256 with 200k iterations)
- **One-click switch** — closes any running Riot Client, clears the cached session, relaunches, and auto-fills your username and password
- **Live rank cards** — auto-fetches each account's current solo-queue rank from the Riot API (`account-v1` + `league-v4 by-puuid`)
- **Stale tags** — ranks more than 24 hours old get a small "(updated 2h ago)" hint; older than 7 days, it turns amber
- **Auto-detected install path** — finds `RiotClientServices.exe` via the Windows registry, running processes, or a drive scan
- **Hidden API key** — end users never see or manage the Riot API key. Only the admin (you) configures it via `--admin`
- **Soft fallback banner** — if the API is down or the key is expired, the UI shows a non-actionable *"Rank info is temporarily unavailable"* message and keeps working with cached data

## Requirements

- Windows 10 / 11
- Python 3.10 or newer
- League of Legends + Riot Client installed (anywhere — auto-detected)

## Installation

Three options, easiest first.

### Option 0 — Pre-built .exe (no Python needed)

Grab the latest `RiotAccountSwitcher.exe` from the [Releases page](https://github.com/zy785199747-lang/account_switcher/releases). Double-click to run. ~46 MB single file, portable, no install required.

> Windows SmartScreen will warn the first time because the .exe isn't code-signed. Click *More info → Run anyway*.

The two source-install options below are for developers or anyone who wants to modify the code.

### Option 1 — uv (recommended for source install)

If you don't have [uv](https://github.com/astral-sh/uv) yet:

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then:

```powershell
git clone https://github.com/zy785199747-lang/account_switcher.git
cd account_switcher
uv venv --python 3.12 .venv
uv pip install -r requirements.txt --python .venv\Scripts\python.exe
.venv\Scripts\python.exe main.py
```

### Option 2 — pip with system Python

```powershell
git clone https://github.com/zy785199747-lang/account_switcher.git
cd account_switcher
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Building the .exe yourself

If you want to produce your own `RiotAccountSwitcher.exe` from source:

```powershell
# from a checked-out source tree with .venv set up:
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

This installs `pyinstaller` into the venv, runs the bundler with [`RiotAccountSwitcher.spec`](RiotAccountSwitcher.spec), and writes `dist\RiotAccountSwitcher.exe` (~46 MB).

GitHub Actions also rebuilds on every push to `master` and every tag — see [`.github/workflows/build.yml`](.github/workflows/build.yml). Tag pushes (`git push origin vX.Y`) auto-publish a GitHub Release with the exe attached.

## First-time setup

1. **Set a master password.** First launch shows a "Set Master Password" dialog. There is no recovery — pick something memorable.

2. **(Admin step) Configure the Riot API key** for live rank info:
   ```powershell
   .venv\Scripts\python.exe main.py --admin
   ```
   Get a free dev key at https://developer.riotgames.com/, paste it into the Admin window, click *Test key* (✓ confirms), Save.
   Dev keys expire every 24 hours; for a long-lived key, apply for a Personal API key at https://developer.riotgames.com/app-type.

   *The app works fine without an API key — you just won't see ranks on the cards.*

3. **Add your accounts.** From the main window: *Add Account* → fill in your Riot login + Riot ID → *Verify* (if API is up) → *Save*.

4. **Switch.** Click any card → in ~10 seconds you'll be logged into that account. Don't touch the keyboard while it types.

## File locations (all encrypted, never committed)

```
%APPDATA%\RiotAccountSwitcher\
├── vault.enc       # encrypted accounts + config
└── logs\
    └── app.log     # rotating log (1 MB × 5 files)
```

## Usage tips

- **Edit / Delete an account**: hover its card → ✏️ or 🗑️ in the top-right corner. Right-click also works.
- **Lock the vault** without exiting: toolbar → *Lock*. Re-prompts for the master password.
- **Verbose console logging**: launch with `--debug`.
- **Inspect/edit vault config** (until Phase 5 ships a Settings dialog):
  ```powershell
  .venv\Scripts\python.exe scripts\vault_admin.py show
  .venv\Scripts\python.exe scripts\vault_admin.py set <key> <value>
  .venv\Scripts\python.exe scripts\vault_admin.py clear <key>
  ```
- **Run the test suite**: `pytest tests/`

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Riot Client not found" | A file picker will appear; point at your real `RiotClientServices.exe` |
| Auto-fill types into the wrong field | Make sure no other window steals focus during the ~10s switch. Phase 5 will add a clipboard-only fallback |
| `app.log` shows the password length next to "typing password" | This is a length-only debug line, not the password itself. Use `--debug` only when needed |
| Forgot master password | Vault is unrecoverable by design. Delete `%APPDATA%\RiotAccountSwitcher\vault.enc` to start over |
| Rank cards show "Rank not loaded yet" | Either no API key (admin mode) or the key is expired. App still works for switching |

## Project layout

```
account_switcher/
├── main.py                       # entry point (CLI args + vault unlock + UI dispatch)
├── requirements.txt
├── docs/
│   └── TEST_CHECKLIST.md         # per-phase manual test items
├── scripts/
│   ├── seed_test_vault.py        # populate a throwaway vault for UI testing
│   └── vault_admin.py            # CLI to read/write vault config
├── src/
│   ├── models.py                 # Account dataclass
│   ├── logging_setup.py          # rotating-file logger + --debug
│   ├── storage/
│   │   ├── crypto.py             # PBKDF2 + Fernet
│   │   └── vault.py              # encrypted JSON CRUD
│   ├── riot/
│   │   ├── api.py                # Riot Web API client + caching
│   │   └── launcher.py           # process kill, session clear, launch, auto-fill
│   └── ui/
│       ├── master_password.py    # set + unlock dialogs
│       ├── main_window.py        # toolbar + card grid + banner
│       ├── account_card.py       # per-account card (hover edit/delete icons)
│       ├── add_account_dialog.py # add/edit form with Verify
│       ├── admin_window.py       # API key management (--admin only)
│       └── switch_worker.py      # QThread worker for the switch flow
└── tests/                         # 59 unit + smoke tests
```

## Development status

This is a personal-use tool that grew up in phases. Each phase has a git tag and is independently runnable:

| Tag | Phase |
|---|---|
| `v0.1-vault` | Encrypted vault + master password |
| `v0.2-crud` | Account cards UI |
| `v0.3-api` | Riot API integration + admin window + banner |
| `v0.4-switch` | **Current** — Riot Client launch + credential auto-fill |
| (planned) | Phase 5: Settings dialog, clipboard fallback, polish |
| (planned) | Phase 6: Documentation |

If you ever break `master`, the previous tag is your rollback point: `git checkout v0.3-api`.

## Security notes

- Account credentials and the Riot API key live only in the encrypted vault under `%APPDATA%`. Nothing is written to disk in plaintext, ever.
- The `.gitignore` excludes `*.enc`, `*.key`, `*.token`, and the logs directory so secrets can't accidentally be committed.
- The master password derives the encryption key with PBKDF2-HMAC-SHA256 at 200,000 iterations — slow on purpose to make brute force expensive.
- Auto-fill works by sending Win32 keystrokes to whichever window has focus once the Riot Client login window appears. Don't move focus during the ~10-second switch or your password may end up in the wrong app.

## License

MIT — see [LICENSE](LICENSE).
