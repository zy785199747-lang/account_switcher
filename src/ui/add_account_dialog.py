# Add / Edit account dialog.
#
# Same dialog is used for both "add new" and "edit existing":
#   - Add  : dlg = AddAccountDialog(parent); if accepted -> dlg.get_account()
#   - Edit : dlg = AddAccountDialog(parent, account=existing) -> same flow,
#            id is preserved.
#
# Phase 2 has no Verify button. Phase 3 adds it (along with a rank preview)
# when the Riot API is wired up. Saving without verification is fine — rank
# fetching happens later.

import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.models import Account

log = logging.getLogger(__name__)

# Region list. Display label -> platform code used by Riot's summoner-v4 API.
# Order roughly matches Riot's developer portal so it's familiar.
REGIONS = [
    ("NA",   "na1"),
    ("EUW",  "euw1"),
    ("EUNE", "eun1"),
    ("KR",   "kr"),
    ("JP",   "jp1"),
    ("BR",   "br1"),
    ("LAN",  "la1"),
    ("LAS",  "la2"),
    ("OCE",  "oc1"),
    ("TR",   "tr1"),
    ("RU",   "ru"),
    ("PH",   "ph2"),
    ("SG",   "sg2"),
    ("TH",   "th2"),
    ("TW",   "tw2"),
    ("VN",   "vn2"),
]


class AddAccountDialog(QDialog):
    def __init__(self, parent=None, account: Optional[Account] = None,
                 default_region: str = "na1"):
        super().__init__(parent)
        self._editing = account
        self._result: Optional[Account] = None

        if account is None:
            self.setWindowTitle("Add Account")
        else:
            self.setWindowTitle("Edit Account")

        self.setModal(True)
        self.setMinimumWidth(400)
        self._build_ui()

        if account is not None:
            self._populate_from(account)
        else:
            self._set_region_code(default_region)

        self.username.setFocus()

    # ---------- layout ----------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        form = QFormLayout()

        self.username = QLineEdit()
        self.username.setPlaceholderText("Riot login username")
        form.addRow("Username:", self.username)

        # Password field with a "Show" toggle button next to it.
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("Riot login password")

        self.show_pwd_btn = QPushButton("Show")
        self.show_pwd_btn.setCheckable(True)
        self.show_pwd_btn.setFixedWidth(60)
        self.show_pwd_btn.toggled.connect(self._toggle_password_echo)

        pwd_row = QHBoxLayout()
        pwd_row.setContentsMargins(0, 0, 0, 0)
        pwd_row.addWidget(self.password)
        pwd_row.addWidget(self.show_pwd_btn)
        pwd_row_widget = QWidget()
        pwd_row_widget.setLayout(pwd_row)
        form.addRow("Password:", pwd_row_widget)

        # Riot ID is shown as two fields with a # in between for clarity.
        self.game_name = QLineEdit()
        self.game_name.setPlaceholderText("Faker")
        self.tag_line = QLineEdit()
        self.tag_line.setPlaceholderText("KR1")
        self.tag_line.setMaximumWidth(80)

        rid_row = QHBoxLayout()
        rid_row.setContentsMargins(0, 0, 0, 0)
        rid_row.addWidget(self.game_name)
        rid_row.addWidget(QLabel("#"))
        rid_row.addWidget(self.tag_line)
        rid_widget = QWidget()
        rid_widget.setLayout(rid_row)
        form.addRow("Riot ID:", rid_widget)

        self.region = QComboBox()
        for label, _code in REGIONS:
            self.region.addItem(label)
        form.addRow("Region:", self.region)

        outer.addLayout(form)

        # Inline error label, hidden until validation fails.
        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #b00020;")
        self.error_label.setVisible(False)
        outer.addWidget(self.error_label)

        # OK / Cancel.
        # Phase 3 will insert a "Verify" button between Cancel and OK.
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_ok)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _toggle_password_echo(self, checked: bool) -> None:
        if checked:
            self.password.setEchoMode(QLineEdit.EchoMode.Normal)
            self.show_pwd_btn.setText("Hide")
        else:
            self.password.setEchoMode(QLineEdit.EchoMode.Password)
            self.show_pwd_btn.setText("Show")

    # ---------- region helpers ----------

    def _set_region_code(self, code: str) -> None:
        # Find the index in REGIONS whose platform code matches `code`.
        for i, (_label, c) in enumerate(REGIONS):
            if c == code:
                self.region.setCurrentIndex(i)
                return
        # Fallback: first item.
        self.region.setCurrentIndex(0)

    def _selected_region_code(self) -> str:
        return REGIONS[self.region.currentIndex()][1]

    # ---------- populate (edit mode) ----------

    def _populate_from(self, a: Account) -> None:
        self.username.setText(a.username)
        self.password.setText(a.password)
        self.game_name.setText(a.game_name)
        self.tag_line.setText(a.tag_line)
        self._set_region_code(a.region)

    # ---------- accept ----------

    def _show_error(self, msg: str) -> None:
        self.error_label.setText(msg)
        self.error_label.setVisible(True)

    def _on_ok(self) -> None:
        # Validate. We're strict on emptiness but don't try to validate the
        # actual Riot ID or password — that's the user's problem (Phase 3
        # adds the Verify button which catches bad Riot IDs).
        username = self.username.text().strip()
        password = self.password.text()  # don't strip, passwords can have spaces
        game_name = self.game_name.text().strip()
        tag_line = self.tag_line.text().strip()

        if not username:
            self._show_error("Username is required.")
            return
        if not password:
            self._show_error("Password is required.")
            return
        if not game_name:
            self._show_error("Game Name is required.")
            return
        if not tag_line:
            self._show_error("Tag Line is required.")
            return

        # Build the Account. Preserve id and cached fields when editing so
        # we don't lose rank info on a simple credential change.
        if self._editing is None:
            self._result = Account(
                username=username,
                password=password,
                game_name=game_name,
                tag_line=tag_line,
                region=self._selected_region_code(),
            )
            log.info("dialog accepted: new account, riot_id=%s#%s, region=%s",
                     game_name, tag_line, self._selected_region_code())
        else:
            self._result = Account(
                id=self._editing.id,
                username=username,
                password=password,
                game_name=game_name,
                tag_line=tag_line,
                region=self._selected_region_code(),
                cached_tier=self._editing.cached_tier,
                cached_division=self._editing.cached_division,
                cached_lp=self._editing.cached_lp,
                cached_at=self._editing.cached_at,
            )
            log.info("dialog accepted: edit account id=%s", self._editing.id)

        self.accept()

    # ---------- public ----------

    def get_account(self) -> Optional[Account]:
        # Returns the new/edited Account, or None if user cancelled.
        return self._result
