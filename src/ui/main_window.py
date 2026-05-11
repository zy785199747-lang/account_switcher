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
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QEvent, QPoint, Qt, QThread, QTimer, QUrl
from PyQt6.QtGui import QAction, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QScrollArea,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from src.models import Account
from src.riot.api import (
    ApiUnavailable,
    RankInfo,
    RiotApiClient,
    RiotApiError,
    RiotIdNotFound,
    cache_needs_refresh,
    refresh_rank,
)
from src.riot.ddragon import (
    ensure_profile_icon,
    profile_icon_local_path,
)
from src.ui.profile_icon import clear_cache as clear_profile_icon_pixmap_cache
from src.riot.launcher import (
    AUTO_FILL_CLIPBOARD,
    DEFAULT_RIOT_INSTALL_PATH,
    find_riot_install_path,
)
from src.storage.vault import Vault
from src.ui.account_card import CARD_WIDTH, AccountCard
from src.ui.add_account_dialog import AddAccountDialog, VerifyResult
from src.ui.reorder_dialog import ReorderDialog
from src.ui.settings_dialog import SettingsDialog
from src.ui.switch_worker import SwitchWorker
from src.ui.update_worker import UpdateWorker
from src.updater import (
    RELEASES_URL,
    format_update_summary,
    install_downloaded_update,
    running_from_frozen_exe,
)
from src.version import APP_VERSION

# Vault config keys (kept consistent with admin_window.py and settings_dialog.py).
CFG_API_KEY = "riot_api_key"
CFG_LAST_API_SUCCESS = "riot_api_last_success"
CFG_RIOT_INSTALL_PATH = "riot_install_path"
CFG_CONFIRM_SWITCH = "confirm_switch_on_click"
CFG_AUTO_FILL_MODE = "auto_fill_mode"

# Default for the confirm-on-switch setting when the vault has nothing saved.
# ON by default so a misclick never tears down a running Riot session.
DEFAULT_CONFIRM_SWITCH = True

CARD_GRID_MIN_COLUMNS = 1

log = logging.getLogger(__name__)


def _format_rank_one_line(info: RankInfo) -> str:
    # "DIAMOND" / "II" / 47   -> "Diamond II 47 LP"
    # None / None / None      -> "Unranked"
    # Mirrors AccountCard._format_one_rank but kept local so the verify
    # preview doesn't depend on the card widget.
    if info.tier is None:
        return "Unranked"
    bits = [info.tier.title()]
    if info.division:
        bits.append(info.division)
    if info.lp is not None:
        bits.append(f"{info.lp} LP")
    return " ".join(bits)


class MainWindow(QMainWindow):
    def __init__(self, vault: Vault):
        super().__init__()
        self.vault = vault
        self.was_locked = False  # main.py reads this to decide whether to re-prompt unlock

        self._cards: list[AccountCard] = []  # keep refs so signals stay connected

        # Per-window API client. Reads the key from the vault config.
        self.api_client = RiotApiClient(
            api_key=self.vault.get_config(CFG_API_KEY, "") or ""
        )
        # Whether the most recent API attempt succeeded. We don't proactively
        # ping on startup — banner appears the first time something fails.
        self._api_available: Optional[bool] = None

        # Switch-flow plumbing. Held as attributes so Python doesn't garbage-
        # collect the QThread/worker mid-flight.
        self._switch_thread: Optional[QThread] = None
        self._switch_worker: Optional[SwitchWorker] = None
        self._switch_dialog: Optional[QProgressDialog] = None
        self._update_thread: Optional[QThread] = None
        self._update_worker: Optional[UpdateWorker] = None
        self._update_manual = False
        self._version_status_label: Optional[QLabel] = None
        self._status_restore_timer = QTimer(self)
        self._status_restore_timer.setSingleShot(True)
        self._status_restore_timer.timeout.connect(self._restore_default_status)

        # Manual card-drag visuals. The layout order only changes on release;
        # during drag, a pixmap ghost follows the cursor and the source card
        # fades in place so the user has spatial feedback.
        self._drag_ghost: Optional[QLabel] = None
        self._drag_source_card: Optional[AccountCard] = None
        self._drag_hot_spot = None

        self.setWindowTitle("Riot Account Switcher")
        self.resize(820, 600)

        self._build_toolbar()
        self._build_central()
        self._build_status_bar()

        self._refresh_grid()

        # Kick off a launch-time rank refresh once the window has had a chance
        # to paint. refresh_rank(force=False) skips anything still inside its
        # 1h cache TTL, so subsequent launches don't hammer the API and the
        # user gets an instant window. Only stale rows actually hit the network.
        QTimer.singleShot(150, self._refresh_on_launch)
        QTimer.singleShot(1500, self._check_for_updates_silent)

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

        update_act = QAction("Check Updates", self)
        update_act.triggered.connect(self._on_check_updates_clicked)
        tb.addAction(update_act)

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
        self.scroll.viewport().installEventFilter(self)
        self._card_columns = CARD_GRID_MIN_COLUMNS

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(8, 8, 8, 8)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(12)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

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
        sb.showMessage(self._default_status_message())

        self._version_status_label = QLabel(f"Version {APP_VERSION}")
        self._version_status_label.setStyleSheet("color: #666;")

        sb.addPermanentWidget(self._version_status_label, 0)

    def _default_status_message(self) -> str:
        return f"Vault: {self.vault.path}"

    def _restore_default_status(self) -> None:
        self.statusBar().showMessage(self._default_status_message())

    def _show_status(self, message: str, timeout_ms: int = 0) -> None:
        self._status_restore_timer.stop()
        self.statusBar().showMessage(message)
        if timeout_ms > 0:
            self._status_restore_timer.start(timeout_ms)

    # ---------- grid management ----------

    def _card_columns_for_width(self, width: int) -> int:
        margins = self.grid.contentsMargins()
        usable = max(0, width - margins.left() - margins.right())
        spacing = self.grid.horizontalSpacing()

        # n cards need n * CARD_WIDTH plus (n - 1) gaps. Add one gap before
        # integer division so exact-fit widths do not lose a column.
        return max(
            CARD_GRID_MIN_COLUMNS,
            (usable + spacing) // (CARD_WIDTH + spacing),
        )

    def _relayout_cards(self) -> None:
        # Reposition the existing card widgets for the current window width.
        # Qt will move an existing widget when addWidget() is called again,
        # but taking the old layout items first avoids stale cell metadata.
        while self.grid.count():
            self.grid.takeAt(0)

        columns = self._card_columns_for_width(self.scroll.viewport().width())
        self._card_columns = columns
        for i, card in enumerate(self._cards):
            row = i // columns
            col = i % columns
            self.grid.addWidget(card, row, col)

    def _refresh_grid(self) -> None:
        # Tear down existing cards and rebuild. Cheap because there will only
        # ever be a handful of accounts in practice.
        log.debug("rebuilding card grid (%d accounts)", len(self.vault.accounts))

        while self.grid.count():
            self.grid.takeAt(0)
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
            card.move_requested.connect(self._on_move)
            card.drag_reorder_started.connect(self._on_card_drag_started)
            card.drag_reorder_moved.connect(self._on_card_drag_moved)
            card.drag_reorder_requested.connect(self._on_card_drag_reorder)
            self._cards.append(card)
        self._relayout_cards()

    def eventFilter(self, watched, event):  # type: ignore[override]
        if watched is self.scroll.viewport() and event.type() == QEvent.Type.Resize:
            columns = self._card_columns_for_width(event.size().width())
            if columns != self._card_columns:
                self._relayout_cards()
        return super().eventFilter(watched, event)

    def _drop_index_for_position(self, pos, dragged_id: str) -> int:
        # Convert a drop point into a row-major insertion slot. The dragged
        # card is ignored so dropping near its old position can still produce
        # a clean "before/after the neighbours" answer.
        cards = [c for c in self._cards if c.account.id != dragged_id]
        if not cards:
            return 0

        rows: list[list[AccountCard]] = []
        for card in cards:
            geom = card.geometry()
            for row in rows:
                if abs(row[0].geometry().center().y() - geom.center().y()) < geom.height() // 2:
                    row.append(card)
                    break
            else:
                rows.append([card])

        rows.sort(key=lambda row: row[0].geometry().top())
        for row in rows:
            row.sort(key=lambda card: card.geometry().left())

        target_row = min(
            rows,
            key=lambda row: abs(row[0].geometry().center().y() - pos.y()),
        )
        before_rows = sum(len(row) for row in rows[:rows.index(target_row)])

        for i, card in enumerate(target_row):
            geom = card.geometry()
            if pos.x() < geom.center().x():
                return before_rows + i
        return before_rows + len(target_row)

    def _on_card_drag_reorder(self, dragged_id: str, global_pos) -> None:
        self._clear_card_drag_visual()
        drop_pos = self.grid_host.mapFromGlobal(global_pos)
        target_index = self._drop_index_for_position(
            drop_pos,
            dragged_id,
        )
        self._reorder_card_to_index(dragged_id, target_index)

    def _find_card_widget(self, account_id: str) -> Optional[AccountCard]:
        for card in self._cards:
            if card.account.id == account_id:
                return card
        return None

    def _on_card_drag_started(self, account_id: str, global_pos, hot_spot) -> None:
        card = self._find_card_widget(account_id)
        if card is None:
            return

        self._clear_card_drag_visual()
        self._drag_source_card = card
        self._drag_hot_spot = hot_spot

        effect = QGraphicsOpacityEffect(card)
        effect.setOpacity(0.35)
        card.setGraphicsEffect(effect)

        ghost = QLabel(self)
        ghost.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        ghost.setPixmap(card.grab())
        ghost.setFixedSize(card.size())
        ghost.setStyleSheet(
            "QLabel { border: 1px solid #6cf; border-radius: 8px; }"
        )
        self._drag_ghost = ghost
        self._move_card_drag_ghost(global_pos)
        ghost.show()
        ghost.raise_()

    def _on_card_drag_moved(self, account_id: str, global_pos) -> None:
        if self._drag_source_card is None:
            self._on_card_drag_started(account_id, global_pos, QPoint())
        self._move_card_drag_ghost(global_pos)

    def _move_card_drag_ghost(self, global_pos) -> None:
        if self._drag_ghost is None or self._drag_hot_spot is None:
            return
        local = self.mapFromGlobal(global_pos)
        self._drag_ghost.move(local - self._drag_hot_spot)
        self._drag_ghost.raise_()

    def _clear_card_drag_visual(self) -> None:
        if self._drag_source_card is not None:
            self._drag_source_card.setGraphicsEffect(None)
            self._drag_source_card = None
        if self._drag_ghost is not None:
            self._drag_ghost.hide()
            self._drag_ghost.deleteLater()
            self._drag_ghost = None
        self._drag_hot_spot = None

    def _reorder_card_to_index(self, account_id: str, target_index: int) -> bool:
        old_order = [a.id for a in self.vault.accounts]
        if account_id not in old_order:
            return False

        new_order = [i for i in old_order if i != account_id]
        target_index = max(0, min(target_index, len(new_order)))
        new_order.insert(target_index, account_id)

        if new_order == old_order:
            log.info("drag reorder: no change for account_id=%s", account_id)
            return True

        try:
            self.vault.reorder(new_order)
        except Exception as exc:
            log.exception("drag reorder failed")
            QMessageBox.critical(self, "Reorder failed", str(exc))
            return False

        self._refresh_grid()
        self._show_status("Account order updated.", 3000)
        return True

    # ---------- actions: add / edit / delete ----------

    def _on_add_clicked(self) -> None:
        log.info("add account clicked")
        default_region = self.vault.get_config("default_region", "na1")
        # Only pass a verify callback when the API looks usable. If we've
        # already seen it fail this session we hide the Verify button so the
        # user isn't tempted to wait for a slow timeout.
        verify_cb = self._build_verify_callback() if self._api_seems_usable() else None
        dlg = AddAccountDialog(self, default_region=default_region,
                               verify_callback=verify_cb)
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
        # Best-effort: try to fetch the rank straight away so the new card
        # isn't blank. Silent failure — the card just stays blank if API down.
        if self._api_seems_usable():
            self._try_refresh_one(new_account)
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
        verify_cb = self._build_verify_callback() if self._api_seems_usable() else None
        dlg = AddAccountDialog(self, account=existing, verify_callback=verify_cb)
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

    # ---------- actions: drag-and-drop reorder ----------

    def _on_move(self, account_id: str, action: str) -> None:
        # Move action from context menu. For now only "reorder" is implemented.
        if action != "reorder":
            log.warning("unknown move action: %s", action)
            return

        log.info("reorder dialog requested")
        dlg = ReorderDialog(self.vault.accounts, parent=self)
        if dlg.exec() != ReorderDialog.DialogCode.Accepted:
            return

        new_order = dlg.get_new_order()
        log.info("reorder: applying new order with %d accounts", len(new_order))

        # No-op check
        old_order = [a.id for a in self.vault.accounts]
        if new_order == old_order:
            log.info("reorder: no change, skipping save")
            return

        try:
            self.vault.reorder(new_order)
        except Exception as exc:
            log.exception("vault reorder failed")
            QMessageBox.critical(self, "Reorder failed", str(exc))
            return
        self._refresh_grid()

    # ---------- actions: stubs filled in by later phases ----------

    def _on_switch(self, account_id: str) -> None:
        # Kick off the kill -> launch -> autofill pipeline on a worker thread.
        # Shows a progress dialog while it runs.
        log.info("switch requested for account_id=%s", account_id)

        if self._switch_thread is not None:
            # A previous switch is still running. Don't start a second.
            QMessageBox.information(
                self, "Switch in progress",
                "Another switch is already running. Please wait for it to finish.",
            )
            return

        existing = self._find_account(account_id)
        if existing is None:
            log.warning("switch: account_id=%s not found", account_id)
            return

        riot_id = f"{existing.game_name}#{existing.tag_line}"

        # Confirmation gate (Settings -> "Confirm before switching"). Default
        # ON. The setting is read fresh each click so toggling it in Settings
        # takes effect immediately without restarting the window. The note,
        # when set, is shown in the prompt so the user can sanity-check that
        # they're switching to the right account before Riot Client dies.
        if bool(self.vault.get_config(CFG_CONFIRM_SWITCH, DEFAULT_CONFIRM_SWITCH)):
            body = f"Switch to {riot_id}?"
            note = (existing.note or "").strip()
            if note:
                body += f"\n\nNote: {note}"
            body += ("\n\nThis will close any running Riot Client and log "
                     "you in as this account.")
            reply = QMessageBox.question(
                self,
                "Confirm switch",
                body,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                log.info("switch cancelled by user for account_id=%s", account_id)
                return

        # main.py guarantees a valid install path is in the vault at startup.
        # If the file disappeared mid-session (uninstall, drive ejected) the
        # picker fallback handles it.
        install_path = self._resolve_install_path()
        if install_path is None:
            log.info("switch aborted: no install path available")
            return

        # Progress dialog — modal, no cancel button (cancelling mid-launch is
        # messy; user can close the Riot Client manually if needed).
        self._switch_dialog = QProgressDialog(
            f"Switching to {riot_id}...", "", 0, 0, self
        )
        self._switch_dialog.setWindowTitle("Switching account")
        self._switch_dialog.setCancelButton(None)  # type: ignore[arg-type]
        self._switch_dialog.setMinimumDuration(0)
        self._switch_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self._switch_dialog.show()

        # Build worker + thread.
        self._switch_thread = QThread(self)
        self._switch_worker = SwitchWorker(
            username=existing.username,
            password=existing.password,
            install_path=install_path,
            riot_id=riot_id,
            auto_fill_mode=self.vault.get_config(
                CFG_AUTO_FILL_MODE,
                AUTO_FILL_CLIPBOARD,
            ),
        )
        self._switch_worker.moveToThread(self._switch_thread)
        self._switch_thread.started.connect(self._switch_worker.run)
        self._switch_worker.progress.connect(self._on_switch_progress)
        self._switch_worker.finished.connect(self._on_switch_finished)
        self._switch_worker.failed.connect(self._on_switch_failed)
        # Both terminal signals stop the thread.
        self._switch_worker.finished.connect(self._switch_thread.quit)
        self._switch_worker.failed.connect(self._switch_thread.quit)
        # When the thread is fully stopped, drop our refs so the next switch can run.
        self._switch_thread.finished.connect(self._on_switch_thread_finished)
        self._switch_thread.start()

    def _on_switch_progress(self, msg: str) -> None:
        log.debug("switch dialog message: %s", msg)
        if self._switch_dialog is not None:
            self._switch_dialog.setLabelText(msg)
        self._show_status(msg)

    def _on_switch_finished(self, riot_id: str) -> None:
        log.info("switch finished successfully for %s", riot_id)
        if self._switch_dialog is not None:
            self._switch_dialog.close()
            self._switch_dialog = None
        if riot_id:
            self._show_status(f"Logged in as {riot_id}.", 5000)
        else:
            self._show_status("Logged in.", 5000)

    def _on_switch_failed(self, msg: str) -> None:
        log.info("switch failed: %s", msg)
        if self._switch_dialog is not None:
            self._switch_dialog.close()
            self._switch_dialog = None
        self._show_status("Switch failed.", 5000)
        QMessageBox.critical(self, "Switch failed", msg)

    def _on_switch_thread_finished(self) -> None:
        # Final cleanup. Runs after the QThread's event loop has stopped.
        if self._switch_worker is not None:
            self._switch_worker.deleteLater()
            self._switch_worker = None
        if self._switch_thread is not None:
            self._switch_thread.deleteLater()
            self._switch_thread = None

    def closeEvent(self, event) -> None:
        if self._switch_thread is not None and self._switch_thread.isRunning():
            QMessageBox.information(
                self,
                "Switch in progress",
                "Account switching is still running. Please wait for it to finish.",
            )
            event.ignore()
            return
        if self._update_thread is not None and self._update_thread.isRunning():
            QMessageBox.information(
                self,
                "Update in progress",
                "An update check or download is still running. Please wait for it to finish.",
            )
            event.ignore()
            return
        super().closeEvent(event)

    # ---------- install path (mid-session fallback only) ----------
    #
    # main.py runs the full resolve-and-cache flow at startup, so by the time
    # the main window is open the vault SHOULD have a valid path. These
    # methods exist for the rare case where the file disappears mid-session
    # (Riot uninstalled, USB drive ejected, etc.).

    def _resolve_install_path(self) -> Optional[str]:
        cached = self.vault.get_config(CFG_RIOT_INSTALL_PATH)
        if cached and Path(cached).exists():
            return cached
        if cached:
            log.info("cached install path %s no longer exists, asking user", cached)
        return self._prompt_for_install_path()

    def _prompt_for_install_path(self) -> Optional[str]:
        QMessageBox.information(
            self,
            "Riot Client not found",
            "The Riot Client was not where we expected.\n\n"
            "Please point us at RiotClientServices.exe.",
        )
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Locate RiotClientServices.exe",
            "C:/",
            "RiotClientServices.exe (RiotClientServices.exe)",
        )
        if not path:
            return None
        self.vault.set_config(CFG_RIOT_INSTALL_PATH, path)
        return path

    def _refresh_on_launch(self) -> None:
        # Best-effort refresh fired once after MainWindow opens. Behaviour rules:
        #   - No API key configured -> skip entirely (quiet, no banner).
        #   - For each account, refresh_rank(force=False) skips fresh caches
        #     (<1h old) so a fast subsequent launch does zero network work.
        #   - ApiUnavailable flips the banner and aborts the loop (no point
        #     trying the rest with the same broken key).
        #   - Per-account RiotApiError is logged and skipped — one bad Riot ID
        #     shouldn't poison the launch path.
        #   - processEvents() between accounts keeps the UI responsive while
        #     the synchronous requests calls block this thread.
        if not self.vault.accounts:
            return
        if not self.api_client.api_key:
            log.info("refresh-on-launch: no API key configured, skipping")
            return

        # cache_needs_refresh covers both "TTL expired" and "schema upgrade
        # since last fetch" — the second is what makes the first launch after
        # a feature bump (like adding flex rank) backfill all rows.
        stale = [a for a in self.vault.accounts if cache_needs_refresh(a)]
        if not stale:
            log.info("refresh-on-launch: all %d accounts cache-fresh + schema-current, skipping",
                     len(self.vault.accounts))
            return

        log.info("refresh-on-launch: %d/%d accounts need a fetch",
                 len(stale), len(self.vault.accounts))
        self._show_status("Refreshing ranks...")
        had_success = False
        had_unavailable = False
        for account in stale:
            try:
                changed = refresh_rank(self.api_client, account, force=False)
                if changed:
                    had_success = True
                    self.vault.update(account)
            except ApiUnavailable as exc:
                log.info("refresh-on-launch: api unavailable (%s)", exc)
                had_unavailable = True
                break
            except RiotApiError as exc:
                log.warning("refresh-on-launch: api error for %s#%s: %s",
                            account.game_name, account.tag_line, exc)
            # Yield to the Qt event loop so the window stays interactive.
            QApplication.processEvents()

        if had_unavailable:
            self._set_api_available(False)
        elif had_success:
            self._set_api_available(True)

        if had_success:
            self._refresh_grid()
            self._show_status(
                f"Ranks refreshed at {time.strftime('%H:%M:%S')}",
                5000,
            )
        elif had_unavailable:
            self._show_status("Rank refresh unavailable.", 5000)
        else:
            self._restore_default_status()

        # Now that icon IDs are populated (refresh_rank wrote them), download
        # any profile icon PNGs that aren't on disk yet. Tiny files, but go
        # one at a time with processEvents so the window stays interactive.
        self._download_missing_profile_icons()

    def _download_missing_profile_icons(self) -> None:
        # Eager fetch for the avatar block on each card. Skips icons that are
        # already on disk. Silent on per-icon failure — the card just renders
        # the first-letter fallback until next launch. DDragon is unauthed
        # so this works even if the Riot Web API key is missing/expired.
        new_pngs = False
        for account in list(self.vault.accounts):
            icon_id = account.cached_profile_icon_id
            if icon_id is None:
                continue
            local = profile_icon_local_path(icon_id)
            if local.exists() and local.stat().st_size > 0:
                continue
            log.info("downloading profile icon %d for %s#%s",
                     icon_id, account.game_name, account.tag_line)
            path = ensure_profile_icon(icon_id)
            if path is not None:
                new_pngs = True
            QApplication.processEvents()
        if new_pngs:
            # Drop any pre-existing fallback pixmaps so the next grid render
            # picks up the freshly-downloaded PNGs instead of stale "?" disks.
            clear_profile_icon_pixmap_cache()
            self._refresh_grid()

    def _on_refresh_clicked(self) -> None:
        # Refresh every account's rank. Silent on per-account failures (we
        # just leave the cached data alone). The banner shows up if any
        # call fails with ApiUnavailable.
        log.info("refresh ranks clicked (%d accounts)", len(self.vault.accounts))
        if not self.vault.accounts:
            return
        self._show_status("Refreshing ranks...")

        # Pull the API key fresh in case admin changed it since startup.
        self._reload_api_client()

        had_success = False
        had_unavailable = False
        for account in list(self.vault.accounts):
            try:
                changed = refresh_rank(self.api_client, account, force=True)
                if changed:
                    had_success = True
                    self.vault.update(account)
            except ApiUnavailable as exc:
                log.info("refresh: api unavailable (%s)", exc)
                had_unavailable = True
                break  # no point trying the rest with the same broken key
            except RiotApiError as exc:
                log.warning("refresh: api error for %s#%s: %s",
                            account.game_name, account.tag_line, exc)
                # Keep going — one bad Riot ID shouldn't stop the whole batch.

        # Update banner state based on what happened.
        if had_unavailable:
            self._set_api_available(False)
        elif had_success or self.vault.accounts:
            # If we got even one success (or there was nothing to fail), API
            # looks fine. Hide the banner if it was up.
            self._set_api_available(True)

        self._refresh_grid()
        if had_success:
            self._show_status(
                f"Ranks refreshed at {time.strftime('%H:%M:%S')}",
                5000,
            )
            # Catch any icon IDs that changed since last launch (user picked a
            # new profile icon in-game). No-op when nothing new is missing.
            self._download_missing_profile_icons()
        elif had_unavailable:
            self._show_status("Rank refresh unavailable.", 5000)
        else:
            self._show_status("Ranks are up to date.", 5000)

    def _on_refresh_one(self, account_id: str) -> None:
        log.info("refresh-one for account_id=%s", account_id)
        existing = self._find_account(account_id)
        if existing is None:
            return
        self._reload_api_client()
        self._show_status("Refreshing rank...")
        self._try_refresh_one(existing, force=True)
        self._refresh_grid()
        self._show_status("Rank refresh finished.", 5000)

    def _on_settings_clicked(self) -> None:
        log.info("settings clicked")
        # Refresh vault to ensure we have latest config from disk
        try:
            self.vault.reload()
        except Exception as exc:
            log.warning("could not reload vault: %s", exc)
        dlg = SettingsDialog(self.vault, parent=self)
        if dlg.exec() != SettingsDialog.DialogCode.Accepted:
            return
        # Reload API client in case the key changed
        self._reload_api_client()

    def _check_for_updates_silent(self) -> None:
        if self._update_thread is not None:
            return
        self._start_update_check(manual=False)

    def _on_check_updates_clicked(self) -> None:
        self._start_update_check(manual=True)

    def _start_update_check(self, manual: bool) -> None:
        if self._update_thread is not None:
            if manual:
                QMessageBox.information(
                    self,
                    "Update check",
                    "An update check is already running.",
                )
            return

        log.info("update check started (manual=%s)", manual)
        self._update_manual = manual
        if manual:
            self._show_status("Checking for updates...")

        self._update_thread = QThread(self)
        self._update_worker = UpdateWorker(download=False)
        self._update_worker.moveToThread(self._update_thread)
        self._update_thread.started.connect(self._update_worker.run)
        self._update_worker.update_available.connect(self._on_update_available)
        self._update_worker.no_update.connect(self._on_no_update_available)
        self._update_worker.failed.connect(self._on_update_failed)
        self._update_worker.finished.connect(self._update_thread.quit)
        self._update_thread.finished.connect(self._on_update_thread_finished)
        self._update_thread.start()

    def _on_update_available(self, info) -> None:
        log.info("update available: %s", info.latest_version)
        self._show_status(
            f"Update available: {info.latest_version}",
            5000,
        )
        body = format_update_summary(info)
        if running_from_frozen_exe():
            body += "\n\nDownload and install it now? The app will restart."
            reply = QMessageBox.question(
                self,
                "Update available",
                body,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._start_update_download(info)
            return

        body += "\n\nAuto-install is only available in the packaged exe."
        body += "\nOpen the release page?"
        reply = QMessageBox.question(
            self,
            "Update available",
            body,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl(info.release_url or RELEASES_URL))

    def _on_no_update_available(self) -> None:
        log.info("no update available")
        self._show_status("No update available.", 5000)
        if self._update_manual:
            QMessageBox.information(
                self,
                "No update available",
                f"You are already on the latest version.\n\n"
                f"Current version: {APP_VERSION}",
            )

    def _on_update_failed(self, msg: str) -> None:
        log.info("update check failed: %s", msg)
        self._show_status("Update check failed.", 5000)
        if self._update_manual:
            QMessageBox.warning(
                self,
                "Update check failed",
                msg,
            )

    def _start_update_download(self, info) -> None:
        if self._update_thread is not None:
            self._update_thread.quit()
            self._update_thread.wait(1000)
            self._on_update_thread_finished()

        log.info("update download started: %s", info.latest_version)
        self._show_status("Downloading update...")
        self._update_manual = True
        self._update_thread = QThread(self)
        self._update_worker = UpdateWorker(download=True, info=info)
        self._update_worker.moveToThread(self._update_thread)
        self._update_thread.started.connect(self._update_worker.run)
        self._update_worker.downloaded.connect(self._on_update_downloaded)
        self._update_worker.failed.connect(self._on_update_failed)
        self._update_worker.finished.connect(self._update_thread.quit)
        self._update_thread.finished.connect(self._on_update_thread_finished)
        self._update_thread.start()

    def _on_update_downloaded(self, info, path) -> None:
        log.info("update downloaded: %s", path)
        self._show_status("Update downloaded.", 5000)
        try:
            install_downloaded_update(path)
        except Exception as exc:
            log.warning("auto-install failed: %s", exc)
            QMessageBox.information(
                self,
                "Update downloaded",
                f"Downloaded {info.asset_name}.\n\n"
                f"Auto-install failed:\n{exc}\n\n"
                "Opening the release page instead.",
            )
            QDesktopServices.openUrl(QUrl(info.release_url or RELEASES_URL))
            return

        if self._update_thread is not None:
            self._update_thread.quit()
            self._update_thread.wait(1000)
            self._on_update_thread_finished()

        QMessageBox.information(
            self,
            "Update ready",
            "The update will install after the app closes. The app will restart.",
        )
        QApplication.quit()

    def _on_update_thread_finished(self) -> None:
        if self._update_worker is not None:
            self._update_worker.deleteLater()
            self._update_worker = None
        if self._update_thread is not None:
            self._update_thread.deleteLater()
            self._update_thread = None

    def _on_lock_clicked(self) -> None:
        log.info("lock clicked")
        self.was_locked = True
        # Closing the window returns control to main.py, which checks
        # was_locked and re-prompts for the master password.
        self.close()

    # ---------- API plumbing ----------

    def _reload_api_client(self) -> None:
        # If the admin changed the key (via `--admin`) while this window was
        # open, picking it up is as cheap as re-reading from the vault config.
        # Vault.unlock loaded the latest config when the window was opened;
        # if Lock + unlock happened in between, we already have the latest.
        latest = self.vault.get_config(CFG_API_KEY, "") or ""
        if latest != self.api_client.api_key:
            log.info("api key changed in vault, refreshing client")
            self.api_client = RiotApiClient(api_key=latest)

    def _api_seems_usable(self) -> bool:
        # Rule: assume usable until proven otherwise. Only flip to "not
        # usable" after we've had a real ApiUnavailable.
        # No key configured -> definitely not usable.
        if not self.api_client.api_key:
            return False
        return self._api_available is not False

    def _set_api_available(self, available: bool) -> None:
        if self._api_available == available:
            return  # no change
        self._api_available = available
        if available:
            log.info("API marked available, hiding banner")
            self.banner.hide()
        else:
            log.info("API marked unavailable, showing banner")
            self.banner.show()

    def _try_refresh_one(self, account: Account, force: bool = False) -> None:
        # Fetch one account's rank. Updates the vault if it succeeded. Updates
        # the banner state on ApiUnavailable. Other errors are logged and
        # silenced — UI stays clean.
        try:
            changed = refresh_rank(self.api_client, account, force=force)
            if changed:
                self.vault.update(account)
            self._set_api_available(True)
        except ApiUnavailable as exc:
            log.info("api unavailable while refreshing %s#%s: %s",
                     account.game_name, account.tag_line, exc)
            self._set_api_available(False)
        except RiotApiError as exc:
            log.warning("api error for %s#%s: %s",
                        account.game_name, account.tag_line, exc)

    def _build_verify_callback(self):
        # Returns a callable suitable for AddAccountDialog. Captures self.
        def verify(candidate: Account) -> VerifyResult:
            # Pull the latest key in case admin updated it.
            try:
                self._reload_api_client()
                try:
                    ranks = self.api_client.fetch_rank(candidate)
                except RiotIdNotFound:
                    # Bad Riot ID is a user error, NOT an API outage. Don't flip
                    # the banner — just tell the user the ID is wrong.
                    return VerifyResult(
                        False,
                        "Player not found — check Game Name and Tag Line.",
                    )
                except ApiUnavailable as exc:
                    self._set_api_available(False)
                    return VerifyResult(False, f"Riot API unavailable ({exc}).")
                except RiotApiError as exc:
                    return VerifyResult(False, f"Riot API error: {exc}")

                self._set_api_available(True)
                # Both queues unranked -> single-line message.
                if ranks.solo.tier is None and ranks.flex.tier is None:
                    return VerifyResult(True,
                                        "Riot ID found. Unranked in both queues.")
                solo_str = _format_rank_one_line(ranks.solo)
                flex_str = _format_rank_one_line(ranks.flex)
                return VerifyResult(
                    True,
                    f"Found. Solo: {solo_str} / Flex: {flex_str}",
                )
            except Exception as exc:
                # Catch-all so a bug in the verify path can't crash PyQt6.
                # Logged with traceback so we can debug after the fact.
                log.exception("verify callback crashed: %s", exc)
                return VerifyResult(
                    False,
                    f"Internal error during verify ({exc}). See logs.",
                )

        return verify
