# Account card widget.
#
# One card per account. The card is what the user double-clicks to switch.
# Layout:
#   +------------------------------------------+
#   |              [✏️] [🗑️]                  | <- hover icons (hidden until mouse enters)
#   | [👤]  Faker#KR1                          | <- profile icon (40px) + Riot ID
#   |       KR                                 |
#   |       main ranked account                | <- optional note (italic; hidden if empty)
#   |                                          | (stretch)
#   |  Solo  [💎] Diamond II 47 LP             |
#   |  Flex  [🥇] Gold I 23 LP                 |
#   |  (updated 2h ago)                        |
#   +------------------------------------------+
#
# Double-click card body  -> emit switch_requested
# Right-click anywhere    -> popup menu (Switch/Move Up/Move Down/Edit/Delete/Refresh/op.gg)

import logging
import time

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QDesktopServices
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.external_links import opgg_summoner_url
from src.models import Account
from src.ui.profile_icon import profile_icon_pixmap
from src.ui.rank_icon import rank_pixmap

# Tweakable card geometry.
CARD_WIDTH = 240
CARD_HEIGHT = 180         # extra room for the two-row rank block
HOVER_ICON_SIZE = 26
HOVER_ICON_MARGIN = 6     # distance from the top-right corner
RANK_ICON_SIZE = 22       # smaller — sits inline with the rank text
RANK_LABEL_WIDTH = 32     # fixed width for "Solo" / "Flex" label so the
                          # text columns line up between the two rows
PROFILE_ICON_SIZE = 40    # circular avatar at the left of the Riot ID stack
PROFILE_ICON_GAP = 8      # space between the avatar and the text stack

# Rank "freshness" thresholds for the stale tag.
# Anything younger than the first threshold = no tag.
# Between first and second = grey "(updated Xh ago)".
# Older than second = amber "(updated Xd ago)".
STALE_GREY_AFTER_SECONDS = 24 * 3600       # 24h
STALE_AMBER_AFTER_SECONDS = 7 * 24 * 3600  # 7d

# Card stylesheets — kept here for the default style.
_STYLE_DEFAULT = (
    "AccountCard { background: #2b2b2b; border: 1px solid #444; "
    "border-radius: 8px; }"
    "AccountCard:hover { border: 1px solid #6cf; }"
)

log = logging.getLogger(__name__)


def _transparent_for_mouse(w) -> None:
    # Mark a child widget so it doesn't intercept mouse events — they fall
    # through to the parent AccountCard. Without this, the right-click
    # context menu and the double-click-to-switch wouldn't fire when the
    # cursor was over any label or row widget. Keep this OFF on real
    # interactive children (the hover ✏️ / 🗑️ buttons) — they still
    # need their own clicks.
    w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)


class AccountCard(QFrame):
    # Signals carry the account id so MainWindow can find the right Account.
    switch_requested = pyqtSignal(str)
    edit_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    refresh_requested = pyqtSignal(str)
    # Emitted on menu: (account_id, direction) where direction is "up" or "down"
    move_requested = pyqtSignal(str, str)

    def __init__(self, account: Account, parent=None):
        super().__init__(parent)
        self.account = account

        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # The card is its own paintable surface so the stylesheet bg sticks.
        self.setAutoFillBackground(True)
        self.setStyleSheet(_STYLE_DEFAULT)

        self._build_ui()
        self._build_hover_icons()

    # ---------- layout ----------

    def _build_ui(self) -> None:
        # Why _transparent_for_mouse() is sprinkled below:
        # Every QLabel and intermediate QWidget on the card would otherwise
        # eat mouse events (Qt doesn't bubble unhandled mouse events up to
        # the parent widget). Without this, pressing on the Riot ID text,
        # region label, rank icons, etc. never reaches AccountCard.
        # mousePressEvent — so drag-to-reorder silently fails. Setting the
        # WA_TransparentForMouseEvents attribute lets presses pass through
        # to the card. Hover icons stay normal because they're real
        # interactive buttons that need their own click handling.

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(4)

        # ---- top row: profile icon + Riot ID / Region / Note stack ----
        # Profile icon is loaded from %APPDATA%\RiotAccountSwitcher\cache\
        # profile_icons\<id>.png if cached; otherwise profile_icon.py draws
        # a first-letter fallback so the card is never blank.
        self.profile_icon_label = QLabel()
        self.profile_icon_label.setFixedSize(PROFILE_ICON_SIZE, PROFILE_ICON_SIZE)
        self.profile_icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _transparent_for_mouse(self.profile_icon_label)
        self._apply_profile_icon()

        # Riot ID — bold, larger.
        self.riot_id_label = QLabel(self._format_riot_id())
        self.riot_id_label.setStyleSheet(
            "color: #eee; font-size: 14px; font-weight: bold;"
        )
        _transparent_for_mouse(self.riot_id_label)

        # Region small line below the Riot ID.
        self.region_label = QLabel(self.account.region.upper())
        self.region_label.setStyleSheet("color: #888; font-size: 11px;")
        _transparent_for_mouse(self.region_label)

        # Optional user note. Italic + slightly lighter than the region so
        # it reads as a personal annotation, not a system label. Hidden when
        # empty so cards without notes don't waste vertical space.
        self.note_label = QLabel("")
        self.note_label.setStyleSheet(
            "color: #9aa; font-size: 11px; font-style: italic;"
        )
        self.note_label.setWordWrap(False)
        _transparent_for_mouse(self.note_label)
        self._apply_note()

        text_stack = QVBoxLayout()
        text_stack.setContentsMargins(0, 0, 0, 0)
        text_stack.setSpacing(2)
        text_stack.addWidget(self.riot_id_label)
        text_stack.addWidget(self.region_label)
        text_stack.addWidget(self.note_label)
        text_stack.addStretch(1)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(PROFILE_ICON_GAP)
        # Profile icon aligned to the top so it lines up with the Riot ID
        # line, not the vertical centre of the (variable-height) text stack.
        top_row.addWidget(self.profile_icon_label, 0, Qt.AlignmentFlag.AlignTop)
        top_row.addLayout(text_stack, 1)
        layout.addLayout(top_row)

        layout.addStretch(1)

        # Rank block at the bottom: two rows (Solo / Flex), each row is
        # [queue label] [tier icon] [rank text].
        # Empty-cache and both-unranked cases collapse to a single line
        # rendered in self.rank_status_label (see _apply_rank_block).
        self.rank_status_label = QLabel("")
        self.rank_status_label.setStyleSheet("color: #ccc;")
        _transparent_for_mouse(self.rank_status_label)
        layout.addWidget(self.rank_status_label)

        self.solo_row, self.solo_icon_label, self.solo_text_label = \
            self._make_rank_row("Solo")
        self.flex_row, self.flex_icon_label, self.flex_text_label = \
            self._make_rank_row("Flex")
        layout.addWidget(self.solo_row)
        layout.addWidget(self.flex_row)
        self._apply_rank_block()

        self.stale_label = QLabel("")
        self.stale_label.setStyleSheet("color: #888; font-size: 10px;")
        self.stale_label.setVisible(False)
        _transparent_for_mouse(self.stale_label)
        layout.addWidget(self.stale_label)
        self._refresh_stale_label()

    def _make_rank_row(self, queue_label: str):
        # Builds one rank row: "Solo  [icon] Diamond II 47 LP". Returns the
        # row widget + the two child labels so callers can update text/pixmap
        # in place without re-creating the row.
        #
        # Every widget here is marked transparent-for-mouse so a press on
        # the rank text or icon falls through to AccountCard and we don't
        # break drag-to-reorder.
        row = QWidget()
        _transparent_for_mouse(row)
        hbox = QHBoxLayout(row)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(6)

        # Queue label ("Solo" / "Flex"). Fixed width so the icon column
        # aligns between the two rows even when one rank string is longer.
        ql = QLabel(queue_label)
        ql.setFixedWidth(RANK_LABEL_WIDTH)
        ql.setStyleSheet("color: #888; font-size: 11px;")
        _transparent_for_mouse(ql)
        hbox.addWidget(ql)

        icon = QLabel()
        icon.setFixedSize(RANK_ICON_SIZE, RANK_ICON_SIZE)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _transparent_for_mouse(icon)
        hbox.addWidget(icon)

        text = QLabel("")
        text.setStyleSheet("color: #ccc; font-size: 12px;")
        _transparent_for_mouse(text)
        hbox.addWidget(text, 1)

        return row, icon, text

    def _build_hover_icons(self) -> None:
        # Two children of `self` floating in the top-right. They consume
        # click/drag events so the card's body handlers don't fire when the
        # user clicks an icon. Hidden by default; enterEvent/leaveEvent
        # toggle visibility together.
        # Layout from left to right: edit, delete.
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

        # Place icons in the top-right corner.
        # Right-to-left: delete, edit — that puts the destructive
        # action in the corner where misclicks are least likely.
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

    def _apply_profile_icon(self) -> None:
        # profile_icon.py handles both the disk-cached PNG path and the
        # first-letter procedural fallback, so this is a one-liner.
        pix = profile_icon_pixmap(
            self.account.cached_profile_icon_id,
            self.account.game_name,
            PROFILE_ICON_SIZE,
        )
        self.profile_icon_label.setPixmap(pix)

    def _apply_note(self) -> None:
        # Truncate long notes so they don't blow out the card width. The full
        # text stays available as a tooltip on the WHOLE card (because the
        # note_label is transparent-for-mouse-events and can't catch hovers
        # itself — see _transparent_for_mouse comment above).
        text = (self.account.note or "").strip()
        if not text:
            self.note_label.setVisible(False)
            self.note_label.setText("")
            self.setToolTip("")
            return
        # Card width ~240; with a 12px left margin and 11px italic font,
        # ~30 chars fit before we'd start clipping. Trim with an ellipsis.
        display = text if len(text) <= 30 else (text[:29] + "…")
        self.note_label.setText(display)
        self.setToolTip(text)
        self.note_label.setVisible(True)

    @staticmethod
    def _format_one_rank(tier, division, lp) -> str:
        # "DIAMOND" / "II" / 47   -> "Diamond II 47 LP"
        # None / None / None      -> "Unranked"
        # "MASTER" / None / 312   -> "Master 312 LP"  (high tiers have no division)
        if tier is None:
            return "Unranked"
        bits = [tier.title()]
        if division:
            bits.append(division)
        if lp is not None:
            bits.append(f"{lp} LP")
        return " ".join(bits)

    def _apply_rank_block(self) -> None:
        # Two states:
        #   1. Never fetched (cached_at is None) -> single "Rank not loaded yet"
        #      line. This is a transient state — once the launch-time refresh
        #      lands we move to state 2 — so a one-liner is fine.
        #   2. Fetched (cached_at is set) -> always show the two-row block
        #      with one row per queue. The Unranked badge + "Unranked" text
        #      stand in for queues with no rank, so an unranked-in-both
        #      account still gets the same layout as a ranked one.
        a = self.account

        if a.cached_at is None:
            self.rank_status_label.setText("Rank not loaded yet")
            self.rank_status_label.setVisible(True)
            self.solo_row.setVisible(False)
            self.flex_row.setVisible(False)
            return

        # Full two-row layout. rank_pixmap returns the Unranked badge when
        # the tier is None (assuming assets/ranks/unranked.png exists; the
        # procedural fallback handles the no-asset case).
        self.rank_status_label.setVisible(False)
        self.solo_row.setVisible(True)
        self.flex_row.setVisible(True)

        self.solo_icon_label.setPixmap(
            rank_pixmap(a.cached_tier, RANK_ICON_SIZE)
        )
        self.solo_text_label.setText(
            self._format_one_rank(a.cached_tier, a.cached_division, a.cached_lp)
        )

        self.flex_icon_label.setPixmap(
            rank_pixmap(a.cached_flex_tier, RANK_ICON_SIZE)
        )
        self.flex_text_label.setText(
            self._format_one_rank(a.cached_flex_tier,
                                  a.cached_flex_division,
                                  a.cached_flex_lp)
        )

    def _refresh_stale_label(self) -> None:
        # Show "(updated Xh ago)" when the cache is older than 24h.
        # Hidden when there's no cache at all or when it's fresh.
        cached_at = self.account.cached_at
        if cached_at is None:
            self.stale_label.setVisible(False)
            return
        age = time.time() - cached_at
        if age < STALE_GREY_AFTER_SECONDS:
            self.stale_label.setVisible(False)
            return

        # Format a friendly age string.
        if age < 7 * 24 * 3600:
            hours = int(age // 3600)
            text = f"(updated {hours}h ago)"
        else:
            days = int(age // (24 * 3600))
            text = f"(updated {days}d ago)"

        # Amber once it crosses the second threshold.
        color = "#888" if age < STALE_AMBER_AFTER_SECONDS else "#d49a2c"
        self.stale_label.setStyleSheet(f"color: {color}; font-size: 10px;")
        self.stale_label.setText(text)
        self.stale_label.setVisible(True)

    # ---------- public API for MainWindow ----------

    def update_account(self, account: Account) -> None:
        # Called when MainWindow re-renders after an edit or rank refresh.
        # Cheaper than tearing down and rebuilding the widget.
        self.account = account
        self.riot_id_label.setText(self._format_riot_id())
        self.region_label.setText(account.region.upper())
        self._apply_profile_icon()
        self._apply_note()
        self._apply_rank_block()
        self._refresh_stale_label()

    # ---------- event handlers ----------

    def enterEvent(self, event):  # type: ignore[override]
        self.edit_btn.show()
        self.delete_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event):  # type: ignore[override]
        self.edit_btn.hide()
        self.delete_btn.hide()
        super().leaveEvent(event)

    # ---- mouse handlers ----
    # Right-click shows context menu; double-click switches to account.
    # Single left click is intentionally a no-op to prevent misclicks.

    def mousePressEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):  # type: ignore[override]
        # The intentional gesture for "switch to this account." Pairs with
        # the Confirm-before-switch setting as a second safety layer.
        if event.button() == Qt.MouseButton.LeftButton:
            log.debug("card double-clicked: switch requested for %s",
                      self.account.id)
            self.switch_requested.emit(self.account.id)
        super().mouseDoubleClickEvent(event)

    def _show_context_menu(self, global_pos) -> None:
        # Right-click menu with account actions including reorder option.
        menu = QMenu(self)

        switch_act = QAction("Switch to this account", self)
        switch_act.triggered.connect(
            lambda: self.switch_requested.emit(self.account.id)
        )
        menu.addAction(switch_act)

        menu.addSeparator()

        reorder_act = QAction("Reorder accounts", self)
        reorder_act.triggered.connect(
            lambda: self.move_requested.emit(self.account.id, "reorder")
        )
        menu.addAction(reorder_act)

        menu.addSeparator()

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

        menu.addSeparator()

        opgg_act = QAction("Open op.gg page", self)
        opgg_act.triggered.connect(self._open_opgg)
        menu.addAction(opgg_act)

        menu.exec(global_pos)

    def _open_opgg(self) -> None:
        # Builds the URL via src/external_links.py and hands it to the OS
        # default browser. QDesktopServices works on Windows + macOS + Linux,
        # so the same code path runs everywhere.
        url = opgg_summoner_url(self.account)
        log.info("opening op.gg for %s#%s -> %s",
                 self.account.game_name, self.account.tag_line, url)
        QDesktopServices.openUrl(QUrl(url))
