from typing import Iterable

from app.translations import _

from PyQt6.QtWidgets import (
    QDialog,
    QSizePolicy,
    QVBoxLayout,
    QHBoxLayout,
    QMessageBox,
    QListWidget,
    QListWidgetItem,
    QLabel,
    QPushButton,
    QLineEdit,
    QComboBox,
    QWidget,
)
from PyQt6.QtCore import Qt

size_policy_fixed = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
window_flag_fixed = (
    Qt.WindowType.Window
    | Qt.WindowType.CustomizeWindowHint
    | Qt.WindowType.WindowTitleHint
    | Qt.WindowType.WindowCloseButtonHint
)


class BannedListDialog(QDialog):
    def __init__(self, parent=None, banned_items: Iterable[str] = (), lang=None):
        super().__init__(parent)
        self.setFixedSize(600, 300)
        self.setSizePolicy(size_policy_fixed)
        self.setWindowFlags(window_flag_fixed)
        self.lang = lang
        self.setWindowTitle(_(lang, "List of banned"))

        self.banned_set = set(banned_items)

        root_layout = QVBoxLayout(self)

        # Search field + clear button
        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(_(self.lang, "Search"))
        self.search_input.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self.search_input, 1)

        clear_list_btn = QPushButton(_(self.lang, "Clear list"))
        clear_list_btn.clicked.connect(self._on_clear_list)
        clear_list_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        search_row.addWidget(clear_list_btn)

        root_layout.addLayout(search_row)

        self.list_widget = QListWidget()
        root_layout.addWidget(self.list_widget, 1)

        self._rebuild_list()

        # Add row for new ban
        add_layout = QHBoxLayout()
        self.platform_combo = QComboBox()
        self.platform_combo.addItems(("twitch", "youtube"))
        self.platform_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        add_layout.addWidget(self.platform_combo)

        self.author_input = QLineEdit()
        self.author_input.setPlaceholderText(_(lang, "User name"))
        add_layout.addWidget(self.author_input, 1)

        add_btn = QPushButton(_(lang, "Add"))
        add_btn.clicked.connect(self._on_add)
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_layout.addWidget(add_btn)

        root_layout.addLayout(add_layout)

    def _rebuild_list(self):
        self.list_widget.clear()
        remove_str = _(self.lang, "Remove")
        font = self.font()
        font.setPointSize(15)
        for item in sorted(self.banned_set):
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(3, 3, 3, 3)
            label = QLabel(item)
            label.setFont(font)
            h.addWidget(label)
            h.addStretch(1)
            btn = QPushButton(remove_str)
            btn.setProperty("ban_item", item)
            btn.clicked.connect(self._on_remove_clicked)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            h.addWidget(btn)

            list_item = QListWidgetItem()
            list_item.setSizeHint(w.sizeHint())
            self.list_widget.addItem(list_item)
            self.list_widget.setItemWidget(list_item, w)

        # apply current search filter after rebuild
        current_search = self.search_input.text().strip()
        if current_search:
            self._on_search_changed(current_search)

    def _on_remove_clicked(self):
        btn = self.sender()
        item = btn.property("ban_item")
        if item in self.banned_set:
            self.banned_set.remove(item)
            self._rebuild_list()
            parent = self.parent()
            if parent is not None and hasattr(parent, "banned_set"):
                try:
                    parent.banned_set = set(self.banned_set)
                except Exception:
                    pass

    def _on_add(self):
        author = self.author_input.text().strip()
        if not author:
            return
        platform = self.platform_combo.currentText().strip()
        key = f"{platform}:{author}" if platform and platform != "all" else author
        self.banned_set.add(key)
        self.author_input.clear()
        self._rebuild_list()
        parent = self.parent()
        if parent is not None and hasattr(parent, "banned_set"):
            try:
                parent.banned_set = set(self.banned_set)
            except Exception:
                pass

    def _on_search_changed(self, text: str):
        t = (text or "").strip().lower()
        for idx in range(self.list_widget.count()):
            item = self.list_widget.item(idx)
            w = self.list_widget.itemWidget(item)
            if w is None:
                continue
            # find first QLabel inside widget
            label = w.findChild(QLabel)
            lbl_text = label.text() if label is not None else ""
            item.setHidden(False if t == "" or t in lbl_text.lower() else True)

    def _on_clear_list(self):
        title = _(self.lang, "Clear list")
        text = _(self.lang, "Clear banned list?")
        res = QMessageBox.warning(
            self,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if res == QMessageBox.StandardButton.Yes:
            self.banned_set.clear()
            self._rebuild_list()
            parent = self.parent()
            if parent is not None and hasattr(parent, "banned_set"):
                try:
                    parent.banned_set = set(self.banned_set)
                except Exception:
                    pass

    def closeEvent(self, event):
        parent = self.parent()
        parent.banned_set = set(list(self.banned_set) + list(parent.banned_set))
        parent.save_banned_list()
        msg = "Saved"
        if hasattr(parent, "language"):
            from app.translations import _

            msg = _(parent.language, "Saved")
        parent.statusBar().showMessage(msg, 3000)

        super().closeEvent(event)
