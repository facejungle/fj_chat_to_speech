from typing import TypedDict

from PyQt6.QtWidgets import QStyledItemDelegate
from PyQt6.QtCore import Qt, QAbstractListModel, QModelIndex, QSize
from PyQt6.QtGui import QFont, QColor, QFontMetrics, QIcon, QPainter

from app.constants import COLORS, PLATFORM_ICON
from app.utils import avatar_colors_from_name, resource_path


class ChatMessage(TypedDict):
    time: str
    platform: str
    author: str
    text: str
    color: str | None
    background: str | None


class ChatMessageListModel(QAbstractListModel):
    MessageRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._messages = []

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._messages)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole) -> ChatMessage | str | None:
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self._messages):
            return None

        message: ChatMessage = self._messages[row]
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                f"[{message['time']}] [{message['platform']}] "
                f"{message['author']}: {message['text']}"
            )
        if role == self.MessageRole:
            return message
        return None

    def add_message(self, message: ChatMessage):
        row = len(self._messages)
        self.beginInsertRows(QModelIndex(), row, row)
        self._messages.append(message)
        self.endInsertRows()

    def clear(self):
        if not self._messages:
            return
        self.beginResetModel()
        self._messages.clear()
        self.endResetModel()

    def messages(self):
        return list(self._messages)


class ChatMessageDelegate(QStyledItemDelegate):
    OUTER_MARGIN = 5
    AVATAR_SIZE = 40
    SPACING = 10
    BUBBLE_PADDING = 10
    HEADER_SPACING = 4

    def __init__(
        self,
        parent=...,
        only_system_msg: bool = False,
        hide_system_msg: bool = False,
        with_avatar: bool = True,
    ):
        self.only_system_msg = only_system_msg
        self.hide_system_msg = hide_system_msg
        self.with_avatar = with_avatar
        self.avatar_size = self.AVATAR_SIZE if with_avatar else 0
        self.spacing = self.SPACING if with_avatar else 0

        super().__init__(parent)
        self._sync_hidden_rows()

        view = self.parent()
        if view is not None and hasattr(view, "model"):
            model = view.model()
            if model is not None:
                model.rowsInserted.connect(self._sync_hidden_rows)
                model.rowsRemoved.connect(self._sync_hidden_rows)
                model.modelReset.connect(self._sync_hidden_rows)
                model.layoutChanged.connect(self._sync_hidden_rows)
                model.dataChanged.connect(self._sync_hidden_rows)

    def _is_hidden_message(self, message: ChatMessage | None) -> bool:
        if not message:
            return False

        if self.hide_system_msg and message["platform"] == "system":
            return True

        if self.only_system_msg:
            if message["platform"] == "system":
                return False

            return True

        return False

    def _sync_hidden_rows(self, *_args):
        view = self.parent()
        if (
            view is None
            or not hasattr(view, "setRowHidden")
            or not hasattr(view, "model")
        ):
            return

        model = view.model()
        if model is None:
            return

        for row in range(model.rowCount()):
            index = model.index(row, 0)
            message = index.data(ChatMessageListModel.MessageRole)
            view.setRowHidden(row, self._is_hidden_message(message))

    def _bubble_width(self, option):
        width = option.rect.width()
        view = self.parent()
        if width <= 0 and hasattr(view, "viewport"):
            width = view.viewport().width()
        if width <= 0:
            width = 900
        content_width = max(260, width - (2 * self.OUTER_MARGIN))
        return max(140, content_width - self.avatar_size - self.spacing)

    def _measure(self, option, message: ChatMessage):
        bubble_width = self._bubble_width(option)

        header_font = QFont(option.font)
        header_font.setBold(True)
        header_height = QFontMetrics(header_font).height()

        body_width = max(20, bubble_width - (2 * self.BUBBLE_PADDING))
        body_text = message["text"]
        body_height = (
            QFontMetrics(option.font)
            .boundingRect(
                0,
                0,
                body_width,
                10000,
                int(Qt.TextFlag.TextWordWrap),
                body_text,
            )
            .height()
        )

        bubble_height = (
            self.BUBBLE_PADDING
            + header_height
            + self.HEADER_SPACING
            + body_height
            + self.BUBBLE_PADDING
        )
        item_height = max(self.avatar_size, bubble_height)
        return bubble_width, bubble_height, item_height, header_height

    def sizeHint(self, option, index):
        message = index.data(ChatMessageListModel.MessageRole)
        if not message or self._is_hidden_message(message):
            return QSize(0, 0)
        bubble_width, _, item_height, _ = self._measure(option, message)
        total_width = (
            self.OUTER_MARGIN
            + self.avatar_size
            + self.spacing
            + bubble_width
            + self.OUTER_MARGIN
        )
        return QSize(total_width, item_height + (2 * self.OUTER_MARGIN))

    def paint(self, painter: QPainter, option, index):
        message = index.data(ChatMessageListModel.MessageRole)
        if not message or self._is_hidden_message(message):
            return

        bubble_width, bubble_height, item_height, header_height = self._measure(
            option, message
        )

        item_top = option.rect.y() + self.OUTER_MARGIN
        avatar_y = item_top
        avatar_x = option.rect.x() + self.OUTER_MARGIN
        bubble_x = avatar_x + self.avatar_size + self.spacing
        bubble_y = item_top
        text_color = _to_color(message["color"], COLORS["WHITE_Q"])
        bubble_color = _to_color(message["background"], COLORS["TRANSPARENT_BLACK2_Q"])

        painter.save()

        if self.with_avatar:
            avatar_bg, avatar_fg = avatar_colors_from_name(message["author"])
            avatar_bg = _to_color(avatar_bg, COLORS["GRAY_Q"])
            avatar_fg = _to_color(avatar_fg, COLORS["WHITE_Q"])

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(avatar_bg)
            painter.drawRoundedRect(
                avatar_x, avatar_y, self.avatar_size, self.avatar_size, 8, 8
            )

            avatar_font = QFont(option.font)
            avatar_font.setBold(True)
            avatar_font.setPointSize(max(option.font.pointSize() + 6, 16))
            painter.setFont(avatar_font)
            painter.setPen(avatar_fg)
            painter.drawText(
                avatar_x,
                avatar_y,
                self.avatar_size,
                self.avatar_size,
                int(Qt.AlignmentFlag.AlignCenter),
                message["author"][:1].upper(),
            )

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bubble_color)
        painter.drawRoundedRect(bubble_x, bubble_y, bubble_width, bubble_height, 8, 8)

        text_left = bubble_x + self.BUBBLE_PADDING
        text_top = bubble_y + self.BUBBLE_PADDING
        text_width = max(20, bubble_width - (2 * self.BUBBLE_PADDING))

        icon = QIcon(resource_path(PLATFORM_ICON[message["platform"]]))
        header_text = f"{message['author']} [{message['time']}]"
        header_font = QFont(option.font)
        header_font.setBold(True)
        header_metrics = QFontMetrics(header_font)
        painter.setFont(header_font)
        painter.setPen(text_color)
        icon_size = header_metrics.height()
        header_text_left = text_left
        header_text_width = text_width
        if not icon.isNull():
            icon_y = text_top + max(0, (header_height - icon_size) // 2)
            painter.drawPixmap(
                text_left,
                icon_y,
                icon.pixmap(icon_size, icon_size),
            )
            header_text_left += icon_size + self.HEADER_SPACING
            header_text_width = max(20, text_width - icon_size - self.HEADER_SPACING)
        painter.drawText(
            header_text_left,
            text_top + header_metrics.ascent(),
            header_metrics.elidedText(
                header_text, Qt.TextElideMode.ElideRight, header_text_width
            ),
        )

        body_top = text_top + header_height + self.HEADER_SPACING
        painter.setFont(option.font)
        painter.drawText(
            text_left,
            body_top,
            text_width,
            max(20, item_height - (body_top - bubble_y) - self.BUBBLE_PADDING),
            int(
                Qt.TextFlag.TextWordWrap
                | Qt.AlignmentFlag.AlignLeft
                | Qt.AlignmentFlag.AlignTop
            ),
            message["text"],
        )

        painter.restore()


def _to_color(value, fallback):
    if isinstance(value, QColor):
        return value
    color = QColor(value) if value else _to_color(fallback, COLORS["GRAY_Q"])
    if not color.isValid():
        color = _to_color(fallback, COLORS["GRAY_Q"])
    return color
