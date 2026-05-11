# Riot Account Switcher — entry point.
#
# Phase 1 scope:
#   - Initialise logging.
#   - If no vault exists at %APPDATA%\RiotAccountSwitcher\vault.enc:
#       show "Set Master Password" dialog -> create vault.
#   - Else:
#       show "Unlock Vault" dialog -> unlock vault.
#   - Show a placeholder QMainWindow so the user has visible proof the unlock
#     succeeded. This window will be replaced by MainWindow in Phase 2.
#
# Run with:
#   python main.py            # normal start
#   python main.py --debug    # verbose console logging
#   python main.py --admin    # Phase 3 only — currently exits with a stub message
#
# Sometimes you want a clean slate during development; delete:
#   %APPDATA%\RiotAccountSwitcher\vault.enc
# and you'll be back to "Set Master Password" on next launch.

import argparse
import logging
import sys

from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMessageBox,
)

from src.logging_setup import setup as setup_logging
from src.riot.launcher import find_riot_install_path
from src.storage.crypto import InvalidPassword
from src.storage.vault import (
    CorruptVault,
    Vault,
    VaultNotFound,
    default_vault_path,
)
from src.ui.admin_window import AdminWindow
from src.ui.main_window import MainWindow
from src.ui.master_password import prompt_set_password, prompt_unlock
from src.version import APP_VERSION

# Same vault key MainWindow uses. Kept in sync manually for now (a future
# config module could centralise these constants).
CFG_RIOT_INSTALL_PATH = "riot_install_path"

log = logging.getLogger(__name__)


def _install_excepthook() -> None:
    # PyQt6 aborts the process on uncaught Python exceptions raised from
    # inside Qt slots. Without a hook, we lose the traceback. Routing through
    # logging.exception writes the full stack to app.log so we can debug.
    def hook(exc_type, exc_value, exc_tb):
        log.error(
            "uncaught exception",
            exc_info=(exc_type, exc_value, exc_tb),
        )
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = hook


# ---------- main flow ----------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Riot Account Switcher")
    p.add_argument("--admin", action="store_true",
                   help="Open the admin window (API key management) — Phase 3+")
    p.add_argument("--debug", action="store_true",
                   help="Verbose console logging")
    return p.parse_args()


def show_error(parent, title: str, message: str) -> None:
    # Plain dialog used for fatal errors. Logging already captured the details.
    QMessageBox.critical(parent, title, message)


def ensure_install_path(vault: Vault) -> bool:
    # Make sure the vault has a working `riot_install_path`. Called at startup
    # (in normal mode, not --admin) so the user is never surprised by a file
    # picker the moment they click their first account card.
    #
    # Resolution order:
    #   1. Cached value in the vault — if the file still exists, use it.
    #   2. Auto-detect: registry → running process → drive scan.
    #      Cache the result on success.
    #   3. File picker dialog. Cache the result on success.
    #
    # Returns True if a path was resolved (and cached), False if the user
    # cancelled the picker. main.py treats False as "exit cleanly".
    cached = vault.get_config(CFG_RIOT_INSTALL_PATH)
    if cached and Path(cached).exists():
        log.info("using cached riot install path: %s", cached)
        return True
    if cached:
        log.info("cached install path %s no longer exists, redetecting", cached)

    detected = find_riot_install_path()
    if detected:
        vault.set_config(CFG_RIOT_INSTALL_PATH, detected)
        log.info("auto-detected and cached riot install path: %s", detected)
        return True

    # Last resort: ask the user.
    QMessageBox.information(
        None,
        "Locate Riot Client",
        "We couldn't auto-detect Riot Client on this computer.\n\n"
        "Please point us at RiotClientServices.exe.\n"
        "It's usually at C:\\Riot Games\\Riot Client\\RiotClientServices.exe",
    )
    path, _ = QFileDialog.getOpenFileName(
        None,
        "Locate RiotClientServices.exe",
        "C:/",
        "RiotClientServices.exe (RiotClientServices.exe)",
    )
    if not path:
        log.info("user cancelled install-path picker")
        return False

    vault.set_config(CFG_RIOT_INSTALL_PATH, path)
    log.info("user chose riot install path: %s", path)
    return True


def acquire_vault(app: QApplication) -> Vault | None:
    # Returns an unlocked Vault, or None if the user cancels.
    path = default_vault_path()
    log.info("vault path: %s", path)

    if not path.exists():
        # First run. Ask for a new password.
        log.info("no vault file found, prompting to set master password")
        password = prompt_set_password()
        if password is None:
            log.info("user cancelled set-password dialog")
            return None
        try:
            return Vault.create(path, password)
        except Exception as exc:
            log.exception("failed to create vault")
            show_error(None, "Vault creation failed",
                       f"Could not create the vault:\n\n{exc}")
            return None

    # Vault exists. Ask for the password.
    # The unlock dialog calls back into us each time the user clicks OK,
    # so we keep an outer reference to the unlocked vault.
    unlocked: dict = {"vault": None}

    def try_unlock(candidate: str) -> bool:
        try:
            unlocked["vault"] = Vault.unlock(path, candidate)
            return True
        except InvalidPassword:
            return False
        except CorruptVault as exc:
            # Don't keep prompting — this isn't a password issue.
            log.error("vault is corrupt: %s", exc)
            show_error(None, "Vault corrupt",
                       f"The vault file looks damaged:\n\n{exc}\n\n"
                       f"You may need to delete it and start over:\n{path}")
            return False
        except VaultNotFound:
            # Race where the file vanished between our exists() check and now.
            return False

    password = prompt_unlock(on_attempt=try_unlock)
    if password is None:
        log.info("user cancelled unlock dialog")
        return None
    return unlocked["vault"]


def main() -> int:
    args = parse_args()
    log_file = setup_logging(debug=args.debug)
    _install_excepthook()
    log.info("starting Riot Account Switcher (admin=%s, debug=%s)",
             args.admin, args.debug)
    log.info("logs: %s", log_file)

    app = QApplication(sys.argv)
    app.setApplicationName("Riot Account Switcher")
    app.setApplicationVersion(APP_VERSION)

    vault = acquire_vault(app)
    if vault is None:
        log.info("no vault — exiting")
        return 0

    if args.admin:
        # Admin window manages the Riot API key. Single-purpose UI: no
        # accounts, no cards, just the key field + Test + Save.
        # Skip install-path resolution: admin doesn't launch Riot.
        log.info("opening admin window")
        admin = AdminWindow(vault)
        admin.show()
        return app.exec()

    # Make sure we know where Riot Client lives BEFORE opening the main UI,
    # so the user never gets a surprise file picker mid-click.
    if not ensure_install_path(vault):
        log.info("install-path resolution cancelled, exiting")
        return 0

    # Lock loop: when the user clicks the toolbar Lock button, the window
    # closes with `was_locked = True`. We then re-prompt for the master
    # password and re-open the window. If the user cancels the unlock,
    # we exit cleanly.
    while True:
        window = MainWindow(vault)
        window.show()
        app.exec()

        if not window.was_locked:
            # Window closed via the X button -> normal exit.
            log.info("main window closed, exiting")
            return 0

        log.info("vault locked, reprompting unlock")
        vault.lock()
        vault = acquire_vault(app)
        if vault is None:
            log.info("user cancelled unlock after lock — exiting")
            return 0


if __name__ == "__main__":
    sys.exit(main())
