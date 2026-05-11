# Riot Account Switcher

A simple Windows app for managing multiple Riot accounts, checking basic League account info, and switching accounts without copy-pasting credentials by hand.

![Version](https://img.shields.io/badge/version-1.1.1-blue) ![Platform](https://img.shields.io/badge/platform-Windows-lightgrey) ![Python](https://img.shields.io/badge/python-3.10%2B-blue)

## What You Can Do

- Store Riot accounts in an encrypted vault protected by a master password.
- Switch accounts from a card with one click/double-click.
- Choose auto-fill mode: faster clipboard paste or slower typing fallback.
- View League rank info, including solo/flex rank, LP, profile icon, and stale update hints.
- Reorder account cards by dragging them, or use the right-click "Move account" fallback.
- Open an account's op.gg page from the card menu.
- Configure Riot API key, Riot Client path, default region, and switch confirmation in Settings.
- Check for app updates from the toolbar; packaged releases can download and install updates in-app.

## Requirements

- Windows 10 or 11
- League of Legends and Riot Client installed
- No Python needed if you use the release exe

## Installation

### Pre-built exe

Download `RiotAccountSwitcher.exe` from the [Releases page](https://github.com/zy785199747-lang/account_switcher/releases), then double-click it.

Windows SmartScreen may warn the first time because the exe is not code-signed. Click **More info -> Run anyway** if you trust this build.

## Updates

The app checks GitHub Releases after it starts.

You can also click **Check Updates** in the toolbar.

If you use the packaged `RiotAccountSwitcher.exe`, the app can download the new exe, replace the old one, and restart.

If you run from source, the app opens the latest release page instead.

### Source install

```powershell
git clone https://github.com/zy785199747-lang/account_switcher.git
cd account_switcher
uv venv --python 3.12 .venv
uv pip install -r requirements.txt --python .venv\Scripts\python.exe
.venv\Scripts\python.exe main.py
```

If you do not use `uv`, a normal Python virtual environment also works:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## First-Time Setup

1. Run the app and create a master password. Pick one you will remember.
2. The app will try to find Riot Client automatically.
3. Add your Riot accounts with username, password, Riot ID, region, and optional note.
4. Optional: add a Riot API key in Settings to enable live rank/profile info.

## How Riot Client Is Found

The app looks for `RiotClientServices.exe` in this order:

1. Saved Riot Client path from your encrypted vault.
2. Windows registry install records.
3. Any currently running Riot Client process.
4. Common install paths on your drives.

If the app cannot find Riot Client, it will ask you to choose `RiotClientServices.exe` manually. You can also change it later in **Settings -> Riot Client path -> Browse**.

## Switching Accounts

Double-click an account card, or right-click and choose **Switch to this account**.

The app will close Riot Client, clear the saved Riot session, relaunch Riot Client, and fill your login. Keep Riot Client focused while auto-fill runs.

## Auto-Fill Modes

In Settings, you can choose:

- **Clipboard paste**: faster and less visibly noisy.
- **Slow typing fallback**: slower, but sometimes more reliable if Riot Client misses focus.

Clipboard mode restores your previous clipboard after it finishes. If paste mode fails, the app falls back to slow typing.

## If You Forget Your Master Password

There is no password recovery. The vault is encrypted, and the app cannot decrypt it without the correct master password.

To start over:

1. Close the app.
2. Delete this file:

   `%APPDATA%\RiotAccountSwitcher\vault.enc`

3. Reopen the app.
4. Create a new master password and add your accounts again.

This deletes all saved accounts, settings, and the Riot API key stored in the vault.

## File Locations

```text
%APPDATA%\RiotAccountSwitcher\
  vault.enc       encrypted accounts and settings
  logs\app.log    rotating app log
  cache\          profile icon and Data Dragon cache
```

## Building The Exe

From a source checkout with `.venv` set up:

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

The build output is:

```text
dist\RiotAccountSwitcher.exe
```

## Run Tests

```powershell
.venv\Scripts\python.exe -m pytest tests/
```

If your Windows temp directory has permission issues, use a repo-local temp folder:

```powershell
.venv\Scripts\python.exe -m pytest tests/ --basetemp=.pytest-tmp -p no:cacheprovider
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| Riot Client not found | Choose `RiotClientServices.exe` manually, or set it in Settings. |
| Auto-fill goes to the wrong field | Keep Riot Client focused while auto-fill runs. Try slow typing mode if clipboard paste is flaky. |
| Rank info is blank | Add or refresh your Riot API key in Settings. The app still switches accounts without an API key. |
| Forgot master password | Delete `%APPDATA%\RiotAccountSwitcher\vault.enc` and start over. |
| Windows warns about the exe | The app is not code-signed; use **More info -> Run anyway** if you trust the release. |

## Security Notes

- Account credentials and the Riot API key are stored in the encrypted vault under `%APPDATA%`.
- The master password derives the encryption key with PBKDF2-HMAC-SHA256.
- There is no recovery path for a forgotten master password.
- Auto-fill works by focusing Riot Client and sending paste/keystroke input. Do not change focus while it runs.

## License

MIT - see [LICENSE](LICENSE).
