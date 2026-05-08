# Main window — toolbar, scrollable card grid, status bar.
#
# Phase 2 wires the structure but only the CRUD actions are functional:
#   - Add Account     -> opens AddAccountDialog -> vault.add -> rebuild grid
#   - Edit account    -> AddAccountDialog prefilled -> vault.update -> rebuild
#   - Delete account  -> confirm -> vault.remove -> rebuild
#   - Refresh Ranks   -> stub message ("Phase 3 will fetch from Riot API")
#   - Settings        -> stub dialog ("Settings panel comes in Phase 5")
#   - Lock            -> sets self.was_locked = True and closes the window
#                        (main.py loops and re-prompts unlock)
#   - Card click      -> stub message ("Phase 4 will launch Riot Client")
#
# The "Rank info is temporarily unavailable" banner exists but is hidden in
# Phase 2 — there is no API yet for it to react to. Phase 3 wires it.

import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from src.models import Account
from src.storage.vault import Vault
from src.ui.account_card import AccountCard
from src.ui.add_account_dialog import AddAccountDialog

CARDS_PER_ROW = 3

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self, vault: Vault):
        super().__init__()
        self.vault = vault
        self.was_locked = False  # main.py reads this to decide whether to re-prompt unlock

        self._cards: list[AccountCard] = []  # keep refs so signals stay connected

        self.setWindowTitle("Riot Account Switcher")
        self.resize(820, 600)

        self._build_toolbar()
        self._build_central()
        self._build_status_bar()

        self._refresh_grid()

    # ---------- layout ----------

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main toolbar")
        tb.setMovable(False)
        self.addToolBar(tb)

        add_act = QAction("Add Account", self)
        add_act.triggered.connect(self._on_add_clicked)
        tb.addAction(add_act)

        refresh_act = QAction("Refresh Ranks", self)
        refresh_act.triggered.connect(self._on_refresh_clicked)
        tb.addAction(refresh_act)

        settings_act = QAction("Settings", self)
        settings_act.triggered.connect(self._on_settings_clicked)
        tb.addAction(settings_act)

        tb.addSeparator()

        lock_act = QAction("Lock", self)
        lock_act.triggered.connect(self._on_lock_clicked)
        tb.addAction(lock_act)

    def _build_central(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(8, 8, 8, 8)

        # Banner. Hidden in Phase 2; Phase 3 shows it on apiUnavailable.
        self.banner = QFrame()
        self.banner.setFrameShape(QFrame.Shape.StyledPanel)
        self.banner.setStyleSheet(
            "QFrame { background: #443a1a; border: 1px solid #8a7228; "
            "border-radius: 4px; padding: 6px; }"
        )
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(8, 4, 8, 4)
        banner_label = QLabel("Rank info is temporarily unavailable")
        banner_label.setStyleSheet("color: #f0d674;")
        banner_layout.addWidget(banner_label)
        banner_layout.addStretch(1)
        self.banner.hide()
        outer.addWidget(self.banner)

        # Scrollable area that holds the grid of cards.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(8, 8, 8, 8)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(12)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        self.scroll.setWidget(self.grid_host)
        outer.addWidget(self.scroll, 1)

        # Empty-state label, shown when the vault has no accounts. Lives in
        # outer (not the grid) so it can be centred on its own.
        self.empty_label = QLabel(
            "No accounts yet.\nClick \"Add Account\" in the toolbar to get started."
        )
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("color: #888; font-size: 14px;")
        self.empty_label.hide()
        outer.addWidget(self.empty_label, 0)

        self.setCentralWidget(central)

    def _build_status_bar(self) -> None:
        sb = self.statusBar()
        sb.showMessage(f"Vault: {self.vault.path}")

    # ---------- grid management ----------

    def _refresh_grid(self) -> None:
        # Tear down existing cards and rebuild. Cheap because there will only
        # ever be a handful of accounts in practice.
        log.debug("rebuilding card grid (%d accounts)", len(self.vault.accounts))

        for card in self._cards:
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()

        if not self.vault.accounts:
            self.scroll.hide()
            self.empty_label.show()
            return

        self.empty_label.hide()
        self.scroll.show()

        for i, account in enumerate(self.vault.accounts):
            card = AccountCard(account)
            card.switch_requested.connect(self._on_switch)
            card.edit_requested.connect(self._on_edit)
            card.delete_requested.connect(self._on_delete)
            card.refresh_requested.connect(self._on_refresh_one)
            row = i // CARDS_PER_ROW
            col = i % CARDS_PER_ROW
            self.grid.addWidget(card, row, col)
            self._cards.append(card)

    # ---------- actions: add / edit / delete ----------

    def _on_add_clicked(self) -> None:
        log.info("add account clicked")
        default_region = self.vault.get_config("default_region", "na1")
        dlg = AddAccountDialog(self, default_region=default_region)
        if dlg.exec() != AddAccountDialog.DialogCode.Accepted:
            return
        new_account = dlg.get_account()
        if new_account is None:
            return
        try:
            self.vault.add(new_account)
        except Exception as exc:
            log.exception("vault.add failed")
            QMessageBox.critical(self, "Could not save account", str(exc))
            return
        self._refresh_grid()

    def _find_account(self, account_id: str) -> Optional[Account]:
        for a in self.vault.accounts:
            if a.id == account_id:
                return a
        return None

    def _on_edit(self, account_id: str) -> None:
        log.info("edit clicked for account_id=%s", account_id)
        existing = self._find_account(account_id)
        if existing is None:
            log.warning("edit: account_id=%s not found", account_id)
            return
        dlg = AddAccountDialog(self, account=existing)
        if dlg.exec() != AddAccountDialog.DialogCode.Accepted:
            return
        updated = dlg.get_account()
        if updated is None:
            return
        try:
            self.vault.update(updated)
        except Exception as exc:
            log.exception("vault.update failed")
            QMessageBox.critical(self, "Could not save changes", str(exc))
            return
        self._refresh_grid()

    def _on_delete(self, account_id: str) -> None:
        log.info("delete clicked for account_id=%s", account_id)
        existing = self._find_account(account_id)
        if existing is None:
            return
        riot_id = f"{existing.game_name}#{existing.tag_line}"
        reply = QMessageBox.question(
            self,
            "Delete account?",
            f"Permanently remove {riot_id} from the vault?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self.vault.remove(account_id)
        except Exception as exc:
            log.exception("vault.remove failed")
            QMessageBox.critical(self, "Could not delete", str(exc))
            return
        self._refresh_grid()

    # ---------- actions: stubs filled in by later phases ----------

    def _on_switch(self, account_id: str) -> None:
        # Phase 4 wires this to the launcher.
        log.info("switch requested for account_id=%s (Phase 4 not yet built)", account_id)
        existing = self._find_account(account_id)
        riot_id = (f"{existing.game_name}#{existing.tag_line}"
                   if existing else account_id)
        QMessageBox.information(
            self,
            "Switch (Phase 4)",
            f"Would launch Riot Client and log in as {riot_id}.\n\n"
            "Phase 4 will implement this. For now nothing happens.",
        )

    def _on_refresh_clicked(self) -> None:
        # Phase 3 wires this to the Riot API.
        log.info("refresh ranks clicked (Phase 3 not yet built)")
        QMessageBox.information(
            self,
            "Refresh Ranks (Phase 3)",
            "Phase 3 will fetch live ranks from the Riot API.\n"
            "Right now there is no API integration so this does nothing.",
        )

    def _on_refresh_one(self, account_id: str) -> None:
        log.info("refresh-one for account_id=%s (Phase 3 not yet built)", account_id)
        # Same stub as the toolbar refresh.
        self._on_refresh_clicked()

    def _on_settings_clicked(self) -> None:
        # Phase 5 fills this with region, install path, auto-fill mode.
        log.info("settings clicked (Phase 5 not yet built)")
        QMessageBox.information(
            self,
            "Settings (Phase 5)",
            "Settings panel (region, Riot Client install path, auto-fill mode) "
            "comes in Phase 5.",
        )

    def _on_lock_clicked(self) -> None:
        log.info("lock clicked")
        self.was_locked = True
        # Closing the window returns control to main.py, which checks
        # was_locked and re-prompts for the master password.
        self.close()
