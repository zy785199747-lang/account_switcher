# Reorder dialog — allows users to change account card order.

import logging
from typing import List

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QLabel,
    QHBoxLayout,
    QPushButton,
)

from src.models import Account

log = logging.getLogger(__name__)


class ReorderDialog(QDialog):
    def __init__(self, accounts: List[Account], parent=None):
        super().__init__(parent)
        self.accounts = accounts
        self.setWindowTitle("Reorder Accounts")
        self.resize(400, 500)

        self._build_ui()
        self._load_accounts()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel("Reorder Accounts")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title)

        info = QLabel(
            "Drag items to reorder, or use the Move Up/Down buttons. "
            "Click OK to save the new order."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #888;")
        layout.addWidget(info)

        # List widget for accounts
        self.account_list = QListWidget()
        self.account_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.account_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        layout.addWidget(self.account_list)

        # Move up/down buttons
        button_layout = QHBoxLayout()
        move_up_btn = QPushButton("Move Up")
        move_up_btn.clicked.connect(self._move_up)
        button_layout.addWidget(move_up_btn)

        move_down_btn = QPushButton("Move Down")
        move_down_btn.clicked.connect(self._move_down)
        button_layout.addWidget(move_down_btn)

        button_layout.addStretch(1)
        layout.addLayout(button_layout)

        # OK / Cancel
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).clicked.connect(self.accept)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _load_accounts(self) -> None:
        # Populate list with account names (GameName#TagLine)
        for account in self.accounts:
            name = f"{account.game_name}#{account.tag_line}"
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, account.id)  # store ID for later
            self.account_list.addItem(item)

    def _move_up(self) -> None:
        current_row = self.account_list.currentRow()
        if current_row > 0:
            item = self.account_list.takeItem(current_row)
            self.account_list.insertItem(current_row - 1, item)
            self.account_list.setCurrentRow(current_row - 1)
            log.debug("moved item up from row %d to %d", current_row, current_row - 1)

    def _move_down(self) -> None:
        current_row = self.account_list.currentRow()
        if current_row < self.account_list.count() - 1:
            item = self.account_list.takeItem(current_row)
            self.account_list.insertItem(current_row + 1, item)
            self.account_list.setCurrentRow(current_row + 1)
            log.debug("moved item down from row %d to %d", current_row, current_row + 1)

    def get_new_order(self) -> List[str]:
        # Return list of account IDs in the new order
        order = []
        for i in range(self.account_list.count()):
            item = self.account_list.item(i)
            account_id = item.data(Qt.ItemDataRole.UserRole)
            order.append(account_id)
        return order
