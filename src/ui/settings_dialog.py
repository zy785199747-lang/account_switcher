# Phase 5 Settings dialog — manage Riot API key and install paths.

import logging
import time
from datetime import datetime
from typing import Optional

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.riot.api import RiotApiClient
from src.storage.vault import Vault

DEV_PORTAL_URL = "https://developer.riotgames.com/"
PERSONAL_KEY_URL = "https://developer.riotgames.com/app-type"

# Vault config keys (keep in sync with admin_window.py and main_window.py).
CFG_API_KEY = "riot_api_key"
CFG_LAST_API_SUCCESS = "riot_api_last_success"
CFG_RIOT_INSTALL_PATH = "riot_install_path"

log = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    def __init__(self, vault: Vault, parent=None):
        super().__init__(parent)
        self.vault = vault
        self.setWindowTitle("Settings")
        self.resize(600, 300)

        self._build_ui()
        self._load_from_vault()

    # ---------- layout ----------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        # Title
        title = QLabel("Settings")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        outer.addWidget(title)

        # Form
        form = QFormLayout()
        form.setSpacing(10)

        # Riot API key field with Show/Hide toggle.
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
        form.addRow("Riot API key:", key_row_w)

        # Test key button + result label.
        self.test_btn = QPushButton("Test key")
        self.test_btn.clicked.connect(self._on_test_key)
        self.test_result = QLabel("")
        self.test_result.setStyleSheet("color: #888;")

        test_row = QHBoxLayout()
        test_row.setContentsMargins(0, 0, 0, 0)
        test_row.addWidget(self.test_btn)
        test_row.addWidget(self.test_result, 1)
        test_row_w = QWidget()
        test_row_w.setLayout(test_row)
        form.addRow("", test_row_w)

        # Last successful call timestamp (informational).
        self.last_success_label = QLabel("never")
        self.last_success_label.setStyleSheet("color: #888;")
        form.addRow("Last successful call:", self.last_success_label)

        outer.addLayout(form)

        # Help links.
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

        # OK / Cancel
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).clicked.connect(self._on_ok)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).clicked.connect(self.reject)
        outer.addWidget(buttons)

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
        candidate = self.api_key_field.text().strip()
        if not candidate:
            self.test_result.setText("✗ no key entered")
            self.test_result.setStyleSheet("color: #b00020;")
            return

        log.info("settings: testing API key")
        self.test_btn.setEnabled(False)
        self.test_result.setText("testing...")
        self.test_result.setStyleSheet("color: #888;")
        self.repaint()

        client = RiotApiClient(api_key=candidate)
        try:
            ok = client.test_key()
        except Exception as exc:
            log.exception("test_key raised unexpectedly")
            ok = False
            err_text = str(exc)
        else:
            err_text = ""

        self.test_btn.setEnabled(True)

        if ok:
            log.info("settings: API key test succeeded")
            self.test_result.setText("✓ key is valid")
            self.test_result.setStyleSheet("color: #2a9d2a;")
            self.vault.set_config(CFG_LAST_API_SUCCESS, time.time())
            self._load_from_vault()
        else:
            log.info("settings: API key test failed")
            msg = "✗ key rejected"
            if err_text:
                msg += f" ({err_text})"
            self.test_result.setText(msg)
            self.test_result.setStyleSheet("color: #b00020;")

    def _on_ok(self) -> None:
        key = self.api_key_field.text().strip()
        if key:
            log.info("settings: saving API key (length=%d)", len(key))
            try:
                self.vault.set_config(CFG_API_KEY, key)
            except Exception as exc:
                log.exception("vault save failed")
                QMessageBox.critical(self, "Save failed", str(exc))
                return
        self.accept()
