# Account card widget.
#
# One card per account. The card is what the user clicks to switch accounts.
# Layout:
#   +------------------------+
#   |  [icon]    [✏️] [🗑️] | <- hover icons (hidden until mouse enters)
#   |                        |
#   |     Faker#KR1          |
#   |     KR                 |
#   |                        |
#   |     Diamond II 47 LP   |
#   +------------------------+
#
# Click anywhere on the card body  -> emit switch_requested
# Click the ✏️ icon                 -> emit edit_requested
# Click the 🗑️ icon                 -> emit delete_requested
# Right-click anywhere              -> popup menu with Switch/Edit/Delete/Refresh
#
# Phase 2 doesn't actually wire switching to the launcher (Phase 4 does); the
# MainWindow currently just shows a "TODO" message when switch_requested fires.
# Same for Refresh (Phase 3) — the menu item is there but inert until then.

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
)

from src.models import Account

# Tweakable card geometry.
CARD_WIDTH = 240
CARD_HEIGHT = 160
HOVER_ICON_SIZE = 26
HOVER_ICON_MARGIN = 6  # distance from the top-right corner

log = logging.getLogger(__name__)


class AccountCard(QFrame):
    # Signals carry the account id so MainWindow can find the right Account.
    switch_requested = pyqtSignal(str)
    edit_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    refresh_requested = pyqtSignal(str)

    def __init__(self, account: Account, parent=None):
        super().__init__(parent)
        self.account = account

        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # The card is its own paintable surface so the stylesheet bg sticks.
        self.setAutoFillBackground(True)
        self.setStyleSheet(
            "AccountCard { background: #2b2b2b; border: 1px solid #444; "
            "border-radius: 8px; }"
            "AccountCard:hover { border: 1px solid #6cf; }"
        )

        self._build_ui()
        self._build_hover_icons()

    # ---------- layout ----------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(4)

        # Rank icon placeholder. Real PNG comes in Phase 3 with the API.
        # For now we use a small text label so the card has visual structure.
        self.rank_icon_label = QLabel("[rank]")
        self.rank_icon_label.setStyleSheet("color: #888;")
        layout.addWidget(self.rank_icon_label)

        # Riot ID — bold, larger.
        self.riot_id_label = QLabel(self._format_riot_id())
        self.riot_id_label.setStyleSheet(
            "color: #eee; font-size: 14px; font-weight: bold;"
        )
        layout.addWidget(self.riot_id_label)

        # Region small line below the Riot ID.
        self.region_label = QLabel(self.account.region.upper())
        self.region_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.region_label)

        layout.addStretch(1)

        # Rank text. Phase 2: no API yet, so always show placeholder.
        self.rank_label = QLabel(self._format_rank())
        self.rank_label.setStyleSheet("color: #ccc;")
        layout.addWidget(self.rank_label)

    def _build_hover_icons(self) -> None:
        # Buttons are children of `self`. They sit on top of the card and
        # consume click events so the card's mousePressEvent doesn't fire
        # when the user clicks an icon. Hidden by default.
        self.edit_btn = QPushButton("✏️", self)
        self.edit_btn.setFixedSize(HOVER_ICON_SIZE, HOVER_ICON_SIZE)
        self.edit_btn.setCursor(Qt.CursorShape.ArrowCursor)
        self.edit_btn.setToolTip("Edit account")
        self.edit_btn.setStyleSheet(
            "QPushButton { background: rgba(0,0,0,120); color: white; "
            "border: 1px solid #555; border-radius: 4px; }"
            "QPushButton:hover { background: rgba(80,80,80,180); }"
        )
        self.edit_btn.clicked.connect(
            lambda: self.edit_requested.emit(self.account.id)
        )

        self.delete_btn = QPushButton("🗑️", self)
        self.delete_btn.setFixedSize(HOVER_ICON_SIZE, HOVER_ICON_SIZE)
        self.delete_btn.setCursor(Qt.CursorShape.ArrowCursor)
        self.delete_btn.setToolTip("Delete account")
        self.delete_btn.setStyleSheet(self.edit_btn.styleSheet())
        self.delete_btn.clicked.connect(
            lambda: self.delete_requested.emit(self.account.id)
        )

        # Place icons in the top-right corner. delete on the right, edit to its left.
        self.delete_btn.move(
            CARD_WIDTH - HOVER_ICON_SIZE - HOVER_ICON_MARGIN,
            HOVER_ICON_MARGIN,
        )
        self.edit_btn.move(
            CARD_WIDTH - 2 * HOVER_ICON_SIZE - 2 * HOVER_ICON_MARGIN,
            HOVER_ICON_MARGIN,
        )
        self.edit_btn.hide()
        self.delete_btn.hide()

    # ---------- formatters (kept simple so Phase 3 can swap them out) ----------

    def _format_riot_id(self) -> str:
        gn = self.account.game_name or "(no name)"
        tag = self.account.tag_line or "?"
        return f"{gn}#{tag}"

    def _format_rank(self) -> str:
        # Phase 2 has no Riot API yet, so cached_tier is always None.
        # Phase 3 fills these in. Keep this tiny so the swap is one line.
        if self.account.cached_tier is None:
            return "Rank not loaded yet"
        bits = [self.account.cached_tier]
        if self.account.cached_division:
            bits.append(self.account.cached_division)
        if self.account.cached_lp is not None:
            bits.append(f"{self.account.cached_lp} LP")
        return " ".join(bits)

    # ---------- public API for MainWindow ----------

    def update_account(self, account: Account) -> None:
        # Called when MainWindow re-renders after an edit or rank refresh.
        # Cheaper than tearing down and rebuilding the widget.
        self.account = account
        self.riot_id_label.setText(self._format_riot_id())
        self.region_label.setText(account.region.upper())
        self.rank_label.setText(self._format_rank())

    # ---------- event handlers ----------

    def enterEvent(self, event):  # type: ignore[override]
        self.edit_btn.show()
        self.delete_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event):  # type: ignore[override]
        self.edit_btn.hide()
        self.delete_btn.hide()
        super().leaveEvent(event)

    def mousePressEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            log.debug("card clicked: switch requested for %s", self.account.id)
            self.switch_requested.emit(self.account.id)
        elif event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())
        super().mousePressEvent(event)

    def _show_context_menu(self, global_pos) -> None:
        # Right-click backup. Same handlers as the icons.
        menu = QMenu(self)

        switch_act = QAction("Switch to this account", self)
        switch_act.triggered.connect(
            lambda: self.switch_requested.emit(self.account.id)
        )
        menu.addAction(switch_act)

        edit_act = QAction("Edit", self)
        edit_act.triggered.connect(
            lambda: self.edit_requested.emit(self.account.id)
        )
        menu.addAction(edit_act)

        delete_act = QAction("Delete", self)
        delete_act.triggered.connect(
            lambda: self.delete_requested.emit(self.account.id)
        )
        menu.addAction(delete_act)

        menu.addSeparator()

        refresh_act = QAction("Refresh rank", self)
        refresh_act.triggered.connect(
            lambda: self.refresh_requested.emit(self.account.id)
        )
        menu.addAction(refresh_act)

        menu.exec(global_pos)
