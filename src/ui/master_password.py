# Master password dialogs.
#
# Two modes:
#   - "set"    : first-time launch. Prompt for password, confirm twice. Warn
#                if shorter than 8 characters but allow it (personal-use tool,
#                not a bank).
#   - "unlock" : every subsequent launch. Single password field. Track failed
#                attempts in memory; lock out for 30 seconds after 5 wrongs.
#
# Both dialogs return the password as a string on success, or None if the user
# cancels. The caller (main.py) is responsible for actually creating/unlocking
# the vault — these dialogs only collect input.

import logging
import time
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

# Tweakable constants.
MIN_PASSWORD_LENGTH_WARNING = 8
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 30

log = logging.getLogger(__name__)


class SetMasterPasswordDialog(QDialog):
    # Shown on first launch when no vault file exists yet.
    # User picks a password and confirms it. The password is then used to
    # create the vault back in main.py.

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set Master Password")
        self.setModal(True)
        self._password: Optional[str] = None

        layout = QVBoxLayout(self)

        info = QLabel(
            "Welcome — pick a master password.\n"
            "It encrypts every account on this computer.\n"
            "There is NO recovery if you forget it."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        self.pwd1 = QLineEdit()
        self.pwd1.setEchoMode(QLineEdit.EchoMode.Password)
        self.pwd2 = QLineEdit()
        self.pwd2.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Master password:", self.pwd1)
        form.addRow("Confirm:", self.pwd2)
        layout.addLayout(form)

        # Inline error label, hidden until we have something to say.
        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #b00020;")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.pwd1.setFocus()

    def _show_error(self, msg: str) -> None:
        self.error_label.setText(msg)
        self.error_label.setVisible(True)

    def _on_ok(self) -> None:
        # Validate, optionally warn about weak passwords, then accept.
        p1 = self.pwd1.text()
        p2 = self.pwd2.text()

        if not p1:
            self._show_error("Password cannot be empty.")
            return
        if p1 != p2:
            self._show_error("Passwords do not match.")
            return

        if len(p1) < MIN_PASSWORD_LENGTH_WARNING:
            # Warn but allow. The user is the only person whose data is at risk.
            reply = QMessageBox.warning(
                self,
                "Short password",
                f"That password is only {len(p1)} characters. "
                f"At least {MIN_PASSWORD_LENGTH_WARNING} is recommended.\n\n"
                "Use it anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._password = p1
        log.info("master password set (length=%d)", len(p1))
        self.accept()

    def password(self) -> Optional[str]:
        return self._password


class UnlockDialog(QDialog):
    # Shown on every launch after the vault exists.
    # On wrong password we show an error, increment a counter, and after
    # MAX_FAILED_ATTEMPTS lock out for LOCKOUT_SECONDS. The counter is in
    # memory only — restarting the app resets it. That's intentional; this
    # is a personal-use tool, not a hardened security product.

    def __init__(self, parent=None, on_attempt=None):
        # on_attempt: callable(password: str) -> bool
        # Returns True if the password unlocks the vault, False otherwise.
        # The dialog calls this each time the user clicks OK.
        super().__init__(parent)
        self.setWindowTitle("Unlock Vault")
        self.setModal(True)

        self._on_attempt = on_attempt
        self._password: Optional[str] = None
        self._failed_count = 0
        self._locked_until: float = 0.0

        layout = QVBoxLayout(self)

        info = QLabel("Enter your master password to unlock the vault.")
        layout.addWidget(info)

        form = QFormLayout()
        self.pwd = QLineEdit()
        self.pwd.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Master password:", self.pwd)
        layout.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #b00020;")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.pwd.setFocus()

    def _show_error(self, msg: str) -> None:
        self.error_label.setText(msg)
        self.error_label.setVisible(True)

    def _on_ok(self) -> None:
        # Check lockout window first, then try the password via the callback.
        now = time.monotonic()
        if now < self._locked_until:
            remaining = int(self._locked_until - now) + 1
            self._show_error(f"Too many wrong attempts. Try again in {remaining}s.")
            return

        candidate = self.pwd.text()
        if not candidate:
            self._show_error("Password cannot be empty.")
            return
        if self._on_attempt is None:
            # No callback supplied — treat anything as accepted.
            # Used in unit tests; main.py always supplies one.
            self._password = candidate
            self.accept()
            return

        ok = self._on_attempt(candidate)
        if ok:
            log.info("vault unlocked successfully")
            self._password = candidate
            self.accept()
            return

        # Wrong password.
        self._failed_count += 1
        log.info("wrong master password (attempt %d/%d)",
                 self._failed_count, MAX_FAILED_ATTEMPTS)
        self.pwd.clear()

        if self._failed_count >= MAX_FAILED_ATTEMPTS:
            self._locked_until = now + LOCKOUT_SECONDS
            self._failed_count = 0  # reset so next lockout-trigger needs another 5
            self._show_error(
                f"Too many wrong attempts. Locked out for {LOCKOUT_SECONDS}s."
            )
        else:
            remaining = MAX_FAILED_ATTEMPTS - self._failed_count
            self._show_error(
                f"Wrong password. {remaining} attempt{'s' if remaining != 1 else ''} left."
            )

    def password(self) -> Optional[str]:
        return self._password


# ---------- helpers used by main.py ----------

def prompt_set_password(parent=None) -> Optional[str]:
    # Convenience wrapper. Returns the password or None on cancel.
    dlg = SetMasterPasswordDialog(parent)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return dlg.password()
    return None


def prompt_unlock(on_attempt, parent=None) -> Optional[str]:
    # Convenience wrapper. on_attempt(password) -> bool.
    dlg = UnlockDialog(parent, on_attempt=on_attempt)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return dlg.password()
    return None
