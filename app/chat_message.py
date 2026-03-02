from PyQt6.QtWidgets import QStyledItemDelegate
from PyQt6.QtCore import Qt, QAbstractListModel, QModelIndex, QSize
from PyQt6.QtGui import QFont, QColor, QFontMetrics


class ChatMessageListModel(QAbstractListModel):
    MessageRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._messages = []

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._messages)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self._messages):
            return None

        message = self._messages[row]
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                f"[{message['time']}] [{message['platform']}] "
                f"{message['author']}: {message['text']}"
            )
        if role == self.MessageRole:
            return message
        return None

    def add_message(self, message):
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

    @staticmethod
    def _to_color(value, fallback):
        color = QColor(value) if value else QColor(fallback)
        if not color.isValid():
            color = QColor(fallback)
        return color

    def _bubble_width(self, option):
        width = option.rect.width()
        view = self.parent()
        if width <= 0 and hasattr(view, "viewport"):
            width = view.viewport().width()
        if width <= 0:
            width = 900
        content_width = max(260, width - (2 * self.OUTER_MARGIN))
        return max(140, content_width - self.AVATAR_SIZE - self.SPACING)

    def _measure(self, option, message):
        bubble_width = self._bubble_width(option)

        header_font = QFont(option.font)
        header_font.setBold(True)
        header_height = QFontMetrics(header_font).height()

        body_width = max(20, bubble_width - (2 * self.BUBBLE_PADDING))
        body_text = str(message.get("text", ""))
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
        item_height = max(self.AVATAR_SIZE, bubble_height)
        return bubble_width, bubble_height, item_height, header_height

    def sizeHint(self, option, index):
        message = index.data(ChatMessageListModel.MessageRole) or {}
        bubble_width, _, item_height, _ = self._measure(option, message)
        total_width = (
            self.OUTER_MARGIN
            + self.AVATAR_SIZE
            + self.SPACING
            + bubble_width
            + self.OUTER_MARGIN
        )
        return QSize(total_width, item_height + (2 * self.OUTER_MARGIN))

    def paint(self, painter, option, index):
        message = index.data(ChatMessageListModel.MessageRole) or {}
        bubble_width, bubble_height, item_height, header_height = self._measure(
            option, message
        )

        item_top = option.rect.y() + self.OUTER_MARGIN
        avatar_x = option.rect.x() + self.OUTER_MARGIN
        avatar_y = item_top
        bubble_x = avatar_x + self.AVATAR_SIZE + self.SPACING
        bubble_y = item_top

        avatar_bg = self._to_color(message.get("avatar_bg"), "#555555")
        avatar_fg = self._to_color(message.get("avatar_fg"), "#ffffff")
        bubble_color = self._to_color(message.get("background"), "#444444")
        text_color = self._to_color(message.get("color"), "#ffffff")

        painter.save()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(avatar_bg)
        painter.drawRoundedRect(
            avatar_x, avatar_y, self.AVATAR_SIZE, self.AVATAR_SIZE, 8, 8
        )

        avatar_font = QFont(option.font)
        avatar_font.setBold(True)
        avatar_font.setPointSize(max(option.font.pointSize() + 6, 16))
        painter.setFont(avatar_font)
        painter.setPen(avatar_fg)
        painter.drawText(
            avatar_x,
            avatar_y,
            self.AVATAR_SIZE,
            self.AVATAR_SIZE,
            int(Qt.AlignmentFlag.AlignCenter),
            str(message.get("avatar_text", "?")),
        )

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bubble_color)
        painter.drawRoundedRect(bubble_x, bubble_y, bubble_width, bubble_height, 8, 8)

        text_left = bubble_x + self.BUBBLE_PADDING
        text_top = bubble_y + self.BUBBLE_PADDING
        text_width = max(20, bubble_width - (2 * self.BUBBLE_PADDING))

        header_text = (
            f"{message.get('author', '')} "
            f"[{message.get('time', '')}] "
            f"[{message.get('platform', '')}]"
        )
        header_font = QFont(option.font)
        header_font.setBold(True)
        header_metrics = QFontMetrics(header_font)
        painter.setFont(header_font)
        painter.setPen(text_color)
        painter.drawText(
            text_left,
            text_top + header_metrics.ascent(),
            header_metrics.elidedText(
                header_text, Qt.TextElideMode.ElideRight, text_width
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
            str(message.get("text", "")),
        )

        painter.restore()
