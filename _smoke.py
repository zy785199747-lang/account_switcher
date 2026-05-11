import sys
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)

from src.models import Account
from src.ui.account_card import (
    ACCOUNT_CARD_MIME,
    AccountCard,
    DragHandle,
    HOVER_ICON_MARGIN,
    HOVER_ICON_SIZE,
    CARD_WIDTH,
)


# --- Card constructs with a DragHandle child ---
acc = Account(username="u", password="p", game_name="Faker", tag_line="KR1",
              region="kr")
card = AccountCard(acc)
assert isinstance(card.drag_btn, DragHandle)
assert card.drag_btn.parent() is card
print("OK: AccountCard wires a DragHandle as drag_btn")


# --- DragHandle is hidden by default; shown together with edit/delete ---
assert card.drag_btn.isHidden()
assert card.edit_btn.isHidden()
assert card.delete_btn.isHidden()
print("OK: all three hover icons hidden by default")


# --- Hover icons are positioned right-to-left: delete, edit, drag ---
# Each icon is HOVER_ICON_SIZE wide with HOVER_ICON_MARGIN between them.
expected_delete_x = CARD_WIDTH - HOVER_ICON_SIZE - HOVER_ICON_MARGIN
expected_edit_x = CARD_WIDTH - 2 * HOVER_ICON_SIZE - 2 * HOVER_ICON_MARGIN
expected_drag_x = CARD_WIDTH - 3 * HOVER_ICON_SIZE - 3 * HOVER_ICON_MARGIN
assert card.delete_btn.x() == expected_delete_x, \
    f"delete_btn at x={card.delete_btn.x()} expected {expected_delete_x}"
assert card.edit_btn.x() == expected_edit_x, \
    f"edit_btn at x={card.edit_btn.x()} expected {expected_edit_x}"
assert card.drag_btn.x() == expected_drag_x, \
    f"drag_btn at x={card.drag_btn.x()} expected {expected_drag_x}"
# All at the same y.
assert card.delete_btn.y() == card.edit_btn.y() == card.drag_btn.y() == HOVER_ICON_MARGIN
print("OK: hover icons positioned left-to-right: drag, edit, delete")


# --- DragHandle reports the card's account_id when constructing its mime ---
# Indirect test: confirm the handle holds a reference to the card so the
# account_id is available when mousePressEvent fires.
assert card.drag_btn._card is card
assert card.drag_btn._card.account.id == acc.id
print("OK: DragHandle wires back to its owning AccountCard")


# --- The card no longer has the old _press_pos / _dragging state ---
assert not hasattr(card, "_press_pos"), \
    "_press_pos should be gone — drag moved to DragHandle"
assert not hasattr(card, "_dragging"), \
    "_dragging should be gone — drag moved to DragHandle"
print("OK: old body-drag state attributes removed from AccountCard")


# --- Card body single-click does NOT fire switch_requested ---
fired = []
card.switch_requested.connect(lambda aid: fired.append(aid))

from PyQt6.QtCore import QEvent, QPointF, Qt as _Qt
from PyQt6.QtGui import QMouseEvent

def mk(kind, button=_Qt.MouseButton.LeftButton,
       pos=QPointF(50, 50),
       buttons=_Qt.MouseButton.LeftButton):
    return QMouseEvent(kind, pos, button, buttons,
                       _Qt.KeyboardModifier.NoModifier)

card.mousePressEvent(mk(QEvent.Type.MouseButtonPress))
# No mouseReleaseEvent override now; super's default does nothing here.
assert fired == [], f"single-click should not fire switch, got {fired}"
print("OK: single-click on card body is a no-op")


# --- Double-click DOES fire switch_requested ---
card.mouseDoubleClickEvent(mk(QEvent.Type.MouseButtonDblClick))
assert fired == [acc.id], f"double-click should fire switch, got {fired}"
print("OK: double-click on card body fires switch_requested")


# --- Reorder signal still exists ---
assert hasattr(card, "reorder_requested")
print("OK: reorder_requested signal still exposed")


# --- Drop side of the protocol is intact ---
assert hasattr(card, "dragEnterEvent")
assert hasattr(card, "dragMoveEvent")
assert hasattr(card, "dragLeaveEvent")
assert hasattr(card, "dropEvent")
assert ACCOUNT_CARD_MIME == "application/x-account-card-id"
print("OK: drop-target plumbing still in place")


# --- DragHandle is NOT marked transparent-for-mouse (it must receive clicks) ---
TRANS = Qt.WidgetAttribute.WA_TransparentForMouseEvents
assert not card.drag_btn.testAttribute(TRANS), \
    "DragHandle must remain interactive"
assert not card.edit_btn.testAttribute(TRANS)
assert not card.delete_btn.testAttribute(TRANS)
print("OK: all three hover buttons remain interactive (NOT transparent)")


print("\nALL DRAG-HANDLE CHECKS PASSED")
