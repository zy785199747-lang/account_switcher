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

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
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
    RiotApiClient,
    RiotApiError,
    RiotIdNotFound,
    refresh_rank,
)
from src.riot.launcher import (
    DEFAULT_RIOT_INSTALL_PATH,
    find_riot_install_path,
)
from src.storage.vault import Vault
from src.ui.account_card import AccountCard
from src.ui.add_account_dialog import AddAccountDialog, VerifyResult
from src.ui.switch_worker import SwitchWorker

# Vault config keys (kept consistent with admin_window.py).
CFG_API_KEY = "riot_api_key"
CFG_LAST_API_SUCCESS = "riot_api_last_success"
CFG_RIOT_INSTALL_PATH = "riot_install_path"

CARDS_PER_ROW = 3

log = logging.getLogger(__name__)


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

        # main.py guarantees a valid install path is in the vault at startup.
        # If the file disappeared mid-session (uninstall, drive ejected) the
        # picker fallback handles it.
        install_path = self._resolve_install_path()
        if install_path is None:
            log.info("switch aborted: no install path available")
            return
        riot_id = f"{existing.game_name}#{existing.tag_line}"

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

    def _on_switch_finished(self) -> None:
        log.info("switch finished successfully")
        if self._switch_dialog is not None:
            self._switch_dialog.close()
            self._switch_dialog = None
        self.statusBar().showMessage("Logged in.", 5000)

    def _on_switch_failed(self, msg: str) -> None:
        log.info("switch failed: %s", msg)
        if self._switch_dialog is not None:
            self._switch_dialog.close()
            self._switch_dialog = None
        QMessageBox.critical(self, "Switch failed", msg)

    def _on_switch_thread_finished(self) -> None:
        # Final cleanup. Runs after the QThread's event loop has stopped.
        if self._switch_worker is not None:
            self._switch_worker.deleteLater()
            self._switch_worker = None
        if self._switch_thread is not None:
            self._switch_thread.deleteLater()
            self._switch_thread = None

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

    def _on_refresh_clicked(self) -> None:
        # Refresh every account's rank. Silent on per-account failures (we
        # just leave the cached data alone). The banner shows up if any
        # call fails with ApiUnavailable.
        log.info("refresh ranks clicked (%d accounts)", len(self.vault.accounts))
        if not self.vault.accounts:
            return

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
            self.statusBar().showMessage(
                f"Ranks refreshed at {time.strftime('%H:%M:%S')}", 5000
            )

    def _on_refresh_one(self, account_id: str) -> None:
        log.info("refresh-one for account_id=%s", account_id)
        existing = self._find_account(account_id)
        if existing is None:
            return
        self._reload_api_client()
        self._try_refresh_one(existing, force=True)
        self._refresh_grid()

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
                    info = self.api_client.fetch_rank(candidate)
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
                if info.tier is None:
                    return VerifyResult(True, "Riot ID found. Account is unranked.")
                bits = [info.tier.title()]
                if info.division:
                    bits.append(info.division)
                if info.lp is not None:
                    bits.append(f"{info.lp} LP")
                return VerifyResult(True, "Found: " + " ".join(bits))
            except Exception as exc:
                # Catch-all so a bug in the verify path can't crash PyQt6.
                # Logged with traceback so we can debug after the fact.
                log.exception("verify callback crashed: %s", exc)
                return VerifyResult(
                    False,
                    f"Internal error during verify ({exc}). See logs.",
                )

        return verify
