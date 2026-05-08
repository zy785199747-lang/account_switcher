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

from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from src.logging_setup import setup as setup_logging
from src.storage.crypto import InvalidPassword
from src.storage.vault import (
    CorruptVault,
    Vault,
    VaultNotFound,
    default_vault_path,
)
from src.ui.master_password import prompt_set_password, prompt_unlock

log = logging.getLogger(__name__)


# ---------- placeholder window for Phase 1 ----------

class Phase1PlaceholderWindow(QMainWindow):
    # Throwaway window so we can see the unlock worked. Phase 2 replaces this
    # with the real MainWindow that holds the account cards.

    def __init__(self, vault: Vault):
        super().__init__()
        self.setWindowTitle("Riot Account Switcher (Phase 1)")
        self.resize(480, 240)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(QLabel("Vault unlocked."))
        layout.addWidget(QLabel(f"Path: {vault.path}"))
        layout.addWidget(QLabel(f"Accounts: {len(vault.accounts)}"))
        layout.addWidget(QLabel(
            "Phase 2 will add the account grid and Add/Edit dialogs here."
        ))
        self.setCentralWidget(central)


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
    log.info("starting Riot Account Switcher (admin=%s, debug=%s)",
             args.admin, args.debug)
    log.info("logs: %s", log_file)

    app = QApplication(sys.argv)
    app.setApplicationName("Riot Account Switcher")

    vault = acquire_vault(app)
    if vault is None:
        log.info("no vault — exiting")
        return 0

    if args.admin:
        # Stub for Phase 3. AdminWindow comes later.
        QMessageBox.information(
            None,
            "Admin mode",
            "Admin mode (--admin) will be implemented in Phase 3.\n\n"
            "It will let you set the Riot API key.",
        )
        return 0

    window = Phase1PlaceholderWindow(vault)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
