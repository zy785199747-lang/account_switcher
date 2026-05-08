# Admin window — only reachable via `python main.py --admin`.
#
# This is the ONLY place in the entire app that exposes the Riot API key.
# The normal UI never shows it, never asks for it, and can't reach this
# window. Design decision per the plan: end users don't deal with the API key.
#
# Workflow (admin runs this once a day or whenever they need to rotate keys):
#   1. Run `python main.py --admin`.
#   2. Master password unlock dialog appears (same as normal app).
#   3. AdminWindow opens with:
#        - the current API key (masked, with a Show toggle)
#        - "Test key" button -> pings Riot, shows ✓ or ✗
#        - last successful call timestamp (informational)
#        - "Get a key" link -> opens https://developer.riotgames.com/
#        - "Apply for Personal API key" link -> opens app-type page
#        - Save / Close buttons
#   4. Save writes the key into the same encrypted vault as accounts.
#   5. Close exits.

import logging
import time
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.riot.api import RiotApiClient
from src.storage.vault import Vault

DEV_PORTAL_URL = "https://developer.riotgames.com/"
PERSONAL_KEY_URL = "https://developer.riotgames.com/app-type"

# Vault config keys (kept in one place so other modules read the same names).
CFG_API_KEY = "riot_api_key"
CFG_LAST_API_SUCCESS = "riot_api_last_success"

log = logging.getLogger(__name__)


class AdminWindow(QMainWindow):
    def __init__(self, vault: Vault):
        super().__init__()
        self.vault = vault

        self.setWindowTitle("Riot Account Switcher — Admin")
        self.resize(560, 280)

        self._build_ui()
        self._load_from_vault()

    # ---------- layout ----------

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(10)

        title = QLabel("Riot API key (admin only)")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        outer.addWidget(title)

        intro = QLabel(
            "This window manages the Riot API key. End users of the app never "
            "see this screen. Paste your key, click Test key, then Save."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #888;")
        outer.addWidget(intro)

        form = QFormLayout()
        form.setSpacing(8)

        # API key field with Show / Hide toggle.
        self.api_key_field = QLineEdit()
        self.api_key_field.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_field.setPlaceholderText("RGAPI-...")

        self.show_btn = QPushButton("Show")
        self.show_btn.setCheckable(True)
        self.show_btn.setFixedWidth(60)
        self.show_btn.toggled.connect(self._toggle_echo)

        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.addWidget(self.api_key_field)
        key_row.addWidget(self.show_btn)
        key_row_w = QWidget()
        key_row_w.setLayout(key_row)
        form.addRow("API key:", key_row_w)

        # Test key button + result label.
        self.test_btn = QPushButton("Test key")
        self.test_btn.clicked.connect(self._on_test_key)
        self.test_result = QLabel("")  # populated after testing
        self.test_result.setStyleSheet("color: #888;")

        test_row = QHBoxLayout()
        test_row.setContentsMargins(0, 0, 0, 0)
        test_row.addWidget(self.test_btn)
        test_row.addWidget(self.test_result, 1)
        test_row_w = QWidget()
        test_row_w.setLayout(test_row)
        form.addRow("", test_row_w)

        # Informational: last successful call timestamp.
        self.last_success_label = QLabel("never")
        self.last_success_label.setStyleSheet("color: #888;")
        form.addRow("Last successful call:", self.last_success_label)

        outer.addLayout(form)

        # Helpful links to the developer portal.
        link_row = QHBoxLayout()
        get_key_btn = QPushButton("Get a dev key (24h)")
        get_key_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(DEV_PORTAL_URL))
        )
        long_key_btn = QPushButton("Apply for Personal API key")
        long_key_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(PERSONAL_KEY_URL))
        )
        link_row.addWidget(get_key_btn)
        link_row.addWidget(long_key_btn)
        link_row.addStretch(1)
        outer.addLayout(link_row)

        # Save / Close at the bottom.
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Close
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).clicked.connect(self._on_save)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.close)
        outer.addWidget(buttons)

        self.setCentralWidget(central)

    # ---------- vault interop ----------

    def _load_from_vault(self) -> None:
        existing = self.vault.get_config(CFG_API_KEY, "") or ""
        self.api_key_field.setText(existing)

        last = self.vault.get_config(CFG_LAST_API_SUCCESS)
        if last:
            try:
                ts = datetime.fromtimestamp(float(last))
                self.last_success_label.setText(
                    ts.strftime("%Y-%m-%d %H:%M:%S")
                )
            except (TypeError, ValueError):
                self.last_success_label.setText("(invalid timestamp)")
        else:
            self.last_success_label.setText("never")

    # ---------- event handlers ----------

    def _toggle_echo(self, checked: bool) -> None:
        if checked:
            self.api_key_field.setEchoMode(QLineEdit.EchoMode.Normal)
            self.show_btn.setText("Hide")
        else:
            self.api_key_field.setEchoMode(QLineEdit.EchoMode.Password)
            self.show_btn.setText("Show")

    def _on_test_key(self) -> None:
        # Use the value currently in the field, NOT what's saved. Lets the
        # user paste a key and test it before committing to Save.
        candidate = self.api_key_field.text().strip()
        if not candidate:
            self.test_result.setText("✗ no key entered")
            self.test_result.setStyleSheet("color: #b00020;")
            return

        log.info("admin: testing API key")
        self.test_btn.setEnabled(False)
        self.test_result.setText("testing...")
        self.test_result.setStyleSheet("color: #888;")

        # We use a fresh client so we don't disturb anything else.
        client = RiotApiClient(api_key=candidate)
        # Repaint the UI so "testing..." is visible while the call runs.
        self.repaint()

        try:
            ok = client.test_key()
        except Exception as exc:  # belt-and-braces, test_key already swallows errors
            log.exception("test_key raised unexpectedly")
            ok = False
            err_text = str(exc)
        else:
            err_text = ""

        self.test_btn.setEnabled(True)

        if ok:
            log.info("admin: API key test succeeded")
            self.test_result.setText("✓ key is valid")
            self.test_result.setStyleSheet("color: #2a9d2a;")
            # Update last-success even before Save so the field reflects the
            # most recent successful auth.
            self.vault.set_config(CFG_LAST_API_SUCCESS, time.time())
            self._load_from_vault()
        else:
            log.info("admin: API key test failed (%s)", err_text or "auth rejected")
            msg = "✗ key rejected"
            if err_text:
                msg += f" ({err_text})"
            self.test_result.setText(msg)
            self.test_result.setStyleSheet("color: #b00020;")

    def _on_save(self) -> None:
        key = self.api_key_field.text().strip()
        log.info("admin: saving API key (length=%d)", len(key))
        try:
            self.vault.set_config(CFG_API_KEY, key)
        except Exception as exc:
            log.exception("vault save failed")
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        QMessageBox.information(
            self,
            "Saved",
            "API key saved to the encrypted vault.\n\n"
            "Close this window and run `python main.py` to use it.",
        )
