from typing import TypedDict

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QHBoxLayout,
    QListView,
    QFrame,
    QPushButton,
)
from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QFont, QIcon, QCloseEvent, QMouseEvent

from app.chat_message import ChatMessageDelegate, ChatMessageListModel

from app.constants import APP_NAME
from app.utils import icon_path, resource_path


class ChatOverlayWindow(QWidget):
    def __init__(
        self,
        parent,
        model: ChatMessageListModel,
        font: QFont,
        always_on_top: bool = False,
    ):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self._main_window = parent
        self._drag_offset = None
        self._resize_origin = None
        self._resize_size = None
        self.always_on_top = always_on_top
        self.setWindowTitle(APP_NAME + " - chat")
        icon = QIcon(resource_path(icon_path()))
        self.setWindowIcon(icon)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMinimumSize(320, 240)
        self.resize(640, 720)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.title_bar = QWidget(self)
        self.title_bar.setCursor(Qt.CursorShape.SizeAllCursor)
        self.title_bar.setStyleSheet(
            "background-color: rgba(15, 15, 15, 150);"
            "border-radius: 10px;"
            "font-size: 12px;"
        )
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(14, 10, 14, 10)
        title_layout.setSpacing(0)
        self.title_label = QLabel(APP_NAME, self.title_bar)
        title_font = QFont(font)
        title_font.setBold(True)
        title_font.setPointSize(max(font.pointSize(), 12))
        self.title_label.setFont(title_font)
        self.title_label.setStyleSheet("color: white; background: transparent;")
        title_layout.addWidget(self.title_label)
        title_layout.addStretch(1)
        self.title_bar.installEventFilter(self)
        self.title_label.installEventFilter(self)
        layout.addWidget(self.title_bar)

        self.chat_view = QListView(self)
        self.chat_view.setEditTriggers(QListView.EditTrigger.NoEditTriggers)
        self.chat_view.setSelectionMode(QListView.SelectionMode.NoSelection)
        self.chat_view.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.chat_view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.chat_view.setWordWrap(True)
        self.chat_view.setUniformItemSizes(False)
        self.chat_view.setSpacing(4)
        self.chat_view.setFont(font)
        self.chat_view.setFrameShape(QFrame.Shape.NoFrame)
        self.chat_view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.chat_view.viewport().setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        self.chat_view.setStyleSheet(
            "QListView { background: transparent; border: none; }"
            "QListView::item { background: transparent; }"
        )
        self.chat_view.setModel(model)
        self.chat_view.setItemDelegate(
            ChatMessageDelegate(self.chat_view, hide_system_msg=True)
        )
        layout.addWidget(self.chat_view)

        resize_layout = QHBoxLayout()
        self.always_on_top_button = QPushButton("^")
        self.always_on_top_button.setCheckable(True)
        self.always_on_top_button.setChecked(self.always_on_top)
        self.always_on_top_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.always_on_top_button.setToolTip("Always On Top")
        self.always_on_top_button.setStyleSheet(
            "QPushButton {"
            "color: white;"
            "background-color: rgba(255, 255, 255, 24);"
            "border: 1px solid rgba(255, 255, 255, 45);"
            "border-radius: 10px;"
            "padding: 4px 10px;"
            "}"
            "QPushButton:checked {"
            "background-color: rgba(15, 15, 15, 150);"
            "}"
        )
        self.always_on_top_button.clicked.connect(self._toggle_always_on_top)
        resize_layout.addWidget(self.always_on_top_button)
        self._toggle_always_on_top(self.always_on_top)

        resize_layout.setContentsMargins(0, 0, 8, 8)
        resize_layout.addStretch(1)
        self.size_grip = QLabel("><", self)
        self.size_grip.setToolTip("Resize")
        self.size_grip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.size_grip.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.size_grip.setStyleSheet(
            "color: white;"
            "background-color: rgba(15, 15, 15, 150);"
            "border: 1px solid rgba(255, 255, 255, 45);"
            "padding: 4px 10px;"
            "border-radius: 10px;"
        )
        self.size_grip.installEventFilter(self)
        resize_layout.addWidget(
            self.size_grip,
            0,
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight,
        )
        layout.addLayout(resize_layout)

    def _toggle_always_on_top(self, checked: bool):
        geometry = self.geometry()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, checked)
        self.show()
        self.setGeometry(geometry)
        self.always_on_top = checked

    def eventFilter(self, watched, event):
        if not hasattr(self, "title_bar") or not hasattr(self, "size_grip"):
            return super().eventFilter(watched, event)

        if watched in (self.title_bar, self.title_label, self.size_grip) and isinstance(
            event, QMouseEvent
        ):
            if (
                event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
            ):
                if watched is self.size_grip:
                    self._resize_origin = event.globalPosition().toPoint()
                    self._resize_size = self.size()
                    return True
                if watched in (self.title_bar, self.title_label):
                    self._drag_offset = (
                        event.globalPosition().toPoint()
                        - self.frameGeometry().topLeft()
                    )
                    return True

            if event.type() == QEvent.Type.MouseMove:
                if self._resize_origin is not None and self._resize_size is not None:
                    delta = event.globalPosition().toPoint() - self._resize_origin
                    self.resize(
                        max(self.minimumWidth(), self._resize_size.width() + delta.x()),
                        max(
                            self.minimumHeight(), self._resize_size.height() + delta.y()
                        ),
                    )
                    return True
                if self._drag_offset is not None:
                    self.move(event.globalPosition().toPoint() - self._drag_offset)
                    return True

            if (
                event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
            ):
                self._drag_offset = None
                self._resize_origin = None
                self._resize_size = None
                return True

        return super().eventFilter(watched, event)

    def closeEvent(self, event: QCloseEvent):
        self._main_window.save_chat_overlay_geometry(self)
        self._main_window.on_chat_overlay_closed()
        super().closeEvent(event)
