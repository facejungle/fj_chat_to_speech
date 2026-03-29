from datetime import datetime

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame, QScrollArea
from PyQt6.QtCore import Qt, QTimer

MSG_STATUS_COLOR = {
    "default": "#444444",
    "warning": "#7c610e",
    "error": "#641111",
    "success": "#175a00",
}


class MessageItem(QFrame):
    def __init__(self, author: str, text: str, status: str, font_size: int):
        super().__init__()

        self.font_size = font_size

        color = MSG_STATUS_COLOR.get(status, MSG_STATUS_COLOR["default"])
        time = datetime.now().strftime("%H:%M:%S")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)

        self.label = QLabel(f"<b>{time} | {author}</b> - {text}")
        self.label.setWordWrap(True)

        layout.addWidget(self.label)

        self.setStyleSheet(f"""
            QFrame {{
                background-color: {color};
                border-radius: 8px;
            }}
        """)

        self.update_font()

    def update_font(self):
        self.label.setStyleSheet(f"color: white; font-size: {self.font_size}px;")

    def set_font_size(self, size: int):
        self.font_size = size
        self.update_font()


class MessageWidget(QWidget):
    def __init__(self, parent=None, font_size: int = 14):
        super().__init__(parent)

        self.font_size = font_size + 4
        self._auto_scroll = True

        main_layout = QVBoxLayout(self)

        self.container = QWidget()
        self.messages_layout = QVBoxLayout(self.container)
        self.messages_layout.setSpacing(6)
        self.messages_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.container)

        self.scroll.setStyleSheet("""
            QScrollArea {
                background-color: #333333;
                border: 1px solid rgba(0, 0, 0, 45);
            }
        """)

        main_layout.addWidget(self.scroll)

        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)

    def _on_scroll(self):
        bar = self.scroll.verticalScrollBar()
        self._auto_scroll = bar.value() >= bar.maximum() - 5

    def clear(self):
        while self.messages_layout.count():
            item = self.messages_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def add_message(self, author: str, text: str, status: str = None):
        msg = MessageItem(author, text, status, self.font_size)
        self.messages_layout.addWidget(msg)

        if self._auto_scroll:
            QTimer.singleShot(0, self.scroll_to_bottom)

    def scroll_to_bottom(self):
        self.container.adjustSize()
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def set_font_size(self, size: int):
        self.font_size = size + 4

        for i in range(self.messages_layout.count()):
            item = self.messages_layout.itemAt(i)
            widget = item.widget()

            if isinstance(widget, MessageItem):
                widget.set_font_size(self.font_size)

    def set_autoscroll(self, value: bool):
        self._auto_scroll = value
