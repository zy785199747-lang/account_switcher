# Phase 5 Settings dialog — manage Riot API key, install path, default region.
#
# End-user-facing version of the admin window: anything the user might need to
# tweak after first launch lives here so they never have to touch the CLI.
#
# Saved keys (all written into the encrypted vault under `config`):
#   - riot_api_key            : the Riot Web API key
#   - riot_install_path       : path to RiotClientServices.exe
#   - default_region          : platform code prefilled in Add Account dialog
#   - confirm_switch_on_click : show "are you sure?" before launching Riot

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
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
from src.ui.add_account_dialog import REGIONS

DEV_PORTAL_URL = "https://developer.riotgames.com/"
PERSONAL_KEY_URL = "https://developer.riotgames.com/app-type"

# Vault config keys (keep in sync with admin_window.py and main_window.py).
CFG_API_KEY = "riot_api_key"
CFG_LAST_API_SUCCESS = "riot_api_last_success"
CFG_RIOT_INSTALL_PATH = "riot_install_path"
CFG_DEFAULT_REGION = "default_region"
CFG_CONFIRM_SWITCH = "confirm_switch_on_click"

# Default region fallback when the vault has nothing saved yet. Matches the
# fallback used by Account.region and MainWindow's get_config call.
DEFAULT_REGION_FALLBACK = "na1"

# Default for the confirm-on-switch checkbox. ON so accidental card clicks
# don't tear down a running Riot session unprompted.
DEFAULT_CONFIRM_SWITCH = True

log = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    def __init__(self, vault: Vault, parent=None):
        super().__init__(parent)
        self.vault = vault
        self.setWindowTitle("Settings")
        self.resize(640, 420)

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

        # Visual separator between API-key block and install-path / region.
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #444;")
        form.addRow(sep)

        # Riot Client install path with a Browse button. main.py resolves a
        # path at startup (registry -> running process -> drive scan -> picker)
        # so this field is normally already filled in. The user can override
        # if they keep Riot on a non-standard drive.
        self.install_path_field = QLineEdit()
        self.install_path_field.setPlaceholderText(
            r"C:\Riot Games\Riot Client\RiotClientServices.exe"
        )

        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.setFixedWidth(80)
        self.browse_btn.clicked.connect(self._on_browse_install_path)

        install_row = QHBoxLayout()
        install_row.setContentsMargins(0, 0, 0, 0)
        install_row.addWidget(self.install_path_field)
        install_row.addWidget(self.browse_btn)
        install_row_w = QWidget()
        install_row_w.setLayout(install_row)
        form.addRow("Riot Client path:", install_row_w)

        # Default region for new accounts (the Add Account dialog reads this).
        self.region_combo = QComboBox()
        for label, code in REGIONS:
            # User-visible label is "NA"; data role carries the platform code.
            self.region_combo.addItem(label, code)
        form.addRow("Default region:", self.region_combo)

        # Safety net: confirm before card-click triggers a switch. ON by
        # default so accidental clicks don't kill a running Riot session.
        self.confirm_switch_box = QCheckBox(
            "Confirm before switching accounts (kills running Riot Client)"
        )
        form.addRow("", self.confirm_switch_box)

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

        # Install path — empty until main.py has run a successful resolve.
        install_path = self.vault.get_config(CFG_RIOT_INSTALL_PATH, "") or ""
        self.install_path_field.setText(install_path)

        # Default region. Find the index in REGIONS whose platform code matches.
        saved_code = self.vault.get_config(CFG_DEFAULT_REGION, DEFAULT_REGION_FALLBACK)
        idx = self._region_index_for_code(saved_code)
        self.region_combo.setCurrentIndex(idx)

        # Confirm-before-switch. Falls back to ON if the key was never written.
        confirm = bool(self.vault.get_config(CFG_CONFIRM_SWITCH, DEFAULT_CONFIRM_SWITCH))
        self.confirm_switch_box.setChecked(confirm)

    def _region_index_for_code(self, code: str) -> int:
        # Linear search — REGIONS only has ~16 entries.
        for i, (_label, c) in enumerate(REGIONS):
            if c == code:
                return i
        return 0  # fall back to the first entry rather than -1

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
            # Only reload the timestamp label, not the key field
            self._refresh_last_success_label()
        else:
            log.info("settings: API key test failed")
            msg = "✗ key rejected"
            if err_text:
                msg += f" ({err_text})"
            self.test_result.setText(msg)
            self.test_result.setStyleSheet("color: #b00020;")

    def _on_browse_install_path(self) -> None:
        # Mirror the picker main.py uses for the first-launch fallback so the
        # user gets a consistent dialog. Start the picker at whatever's already
        # in the field (if any) so re-browse is not annoying.
        current = self.install_path_field.text().strip()
        start_dir = current if current and Path(current).exists() else "C:/"
        log.info("settings: opening install-path picker (start=%s)", start_dir)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Locate RiotClientServices.exe",
            start_dir,
            "RiotClientServices.exe (RiotClientServices.exe)",
        )
        if not path:
            return
        self.install_path_field.setText(path)

    def _refresh_last_success_label(self) -> None:
        # Only reload the last success timestamp, not the key field
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

    def _on_ok(self) -> None:
        # ---- API key ----
        key = self.api_key_field.text().strip()
        if key:
            log.info("settings: saving API key (length=%d)", len(key))
        else:
            log.info("settings: clearing API key")
        try:
            self.vault.set_config(CFG_API_KEY, key)
        except Exception as exc:
            log.exception("vault save failed (api key)")
            QMessageBox.critical(self, "Save failed", str(exc))
            return

        # ---- install path ----
        install_path = self.install_path_field.text().strip()
        if install_path and not Path(install_path).exists():
            # Don't block save — file may be on a USB drive that's not in
            # right now, or the user may be pre-configuring. Just warn so they
            # know it won't work until the file actually exists.
            reply = QMessageBox.warning(
                self,
                "Install path not found",
                f"The path does not exist right now:\n\n{install_path}\n\n"
                "Save anyway?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Save:
                return
        try:
            self.vault.set_config(CFG_RIOT_INSTALL_PATH, install_path)
            log.info("settings: install path saved (%s)",
                     install_path or "(cleared)")
        except Exception as exc:
            log.exception("vault save failed (install path)")
            QMessageBox.critical(self, "Save failed", str(exc))
            return

        # ---- default region ----
        region_code = self.region_combo.currentData() or DEFAULT_REGION_FALLBACK
        try:
            self.vault.set_config(CFG_DEFAULT_REGION, region_code)
            log.info("settings: default region saved (%s)", region_code)
        except Exception as exc:
            log.exception("vault save failed (region)")
            QMessageBox.critical(self, "Save failed", str(exc))
            return

        # ---- confirm-before-switch ----
        confirm = self.confirm_switch_box.isChecked()
        try:
            self.vault.set_config(CFG_CONFIRM_SWITCH, confirm)
            log.info("settings: confirm-switch saved (%s)", confirm)
        except Exception as exc:
            log.exception("vault save failed (confirm-switch)")
            QMessageBox.critical(self, "Save failed", str(exc))
            return

        self.accept()
