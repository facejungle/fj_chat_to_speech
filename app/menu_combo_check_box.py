from logging import getLogger

from PyQt6.QtWidgets import (
    QWidget,
    QPushButton,
    QHBoxLayout,
    QMenu,
)
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QAction

logger = getLogger("main")


class MenuComboCheckBox(QWidget):
    changed = pyqtSignal(list)

    def __init__(self, title: str, items=None):
        super().__init__()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.button = QPushButton(title)
        self.menu = QMenu()
        self.actions = []
        self.selected_items = []

        if items:
            for item_text in items:
                action = QAction(item_text, self)
                action.setCheckable(True)
                action.triggered.connect(self.update_display)
                self.menu.addAction(action)
                self.actions.append(action)

        self.button.setMenu(self.menu)

        layout.addWidget(self.button)

    def update_display(self):
        """Update button text when actions are toggled"""
        self.selected_items = [
            action.text() for action in self.actions if action.isChecked()
        ]
        self.changed.emit(self.selected_items)

    def getSelected(self):
        return self.selected_items

    def getSelectedIndex(self):
        selected_indices = []
        for i, action in enumerate(self.actions):
            if action.isChecked():
                selected_indices.append(i)
        return selected_indices

    def setItems(self, items):
        self.menu.clear()
        self.actions = []

        for item_text in items:
            action = QAction(item_text, self)
            action.setCheckable(True)
            action.triggered.connect(self.update_display)
            self.menu.addAction(action)
            self.actions.append(action)

        self.selected_items = []

    def setSelectedIndices(self, indices):
        if indices is None or indices == []:
            # Clear all selections
            for action in self.actions:
                action.setChecked(False)
            self.update_display()
            return

        if isinstance(indices, int):
            indices = [indices]

        if isinstance(indices, tuple):
            indices = list(indices)

        for action in self.actions:
            action.setChecked(False)

        for idx in indices:
            if 0 <= idx < len(self.actions):
                self.actions[idx].setChecked(True)
            else:
                logger.warning(
                    f"Warning: Index {idx} is out of range (0-{len(self.actions)-1})"
                )

        self.update_display()

    def setSelected(self, items):
        for action in self.actions:
            action.setChecked(action.text() in items)
        self.update_display()

    def setTitle(self, title: str):
        self.button.setText(title)
