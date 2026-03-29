import hashlib
import os
from html import escape
from typing import TypedDict

from PyQt6 import sip
from PyQt6.QtWidgets import QStyledItemDelegate, QListView
from PyQt6.QtCore import Qt, QAbstractListModel, QModelIndex, QSize, QObject, QUrl
from PyQt6.QtGui import (
    QFont,
    QColor,
    QFontMetrics,
    QIcon,
    QPainter,
    QImage,
    QPainterPath,
    QPixmap,
    QTextDocument,
)
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

from app.constants import PLATFORM_ICON
from app.constants_qt import COLORS_RGBA, COLORS_SOLID
from app.utils import avatar_colors_from_name, get_emoji_cache_path, resource_path

OUTER_MARGIN = 5
AVATAR_SIZE = 32
SPACING = 10
BUBBLE_PADDING = 10
HEADER_SPACING = 4
EMOJI_CACHE_SIZE = 24


class ChatMessageSegment(TypedDict, total=False):
    id: str
    text: str
    txt: str
    url: str


class ChatMessage(TypedDict):
    time: str
    platform: str
    author: str
    text: str
    color: str | None
    background: str | None
    segments: list[ChatMessageSegment | str] | None
    avatar_url: str | None


class _ChatEmojiStore(QObject):
    def __init__(self):
        super().__init__()
        self._manager = QNetworkAccessManager(self)
        self._cache: dict[str, QImage] = {}
        self._pending: dict[str, list[QListView]] = {}
        self._cache_dir = get_emoji_cache_path()

    def _cache_path(self, url: str) -> str:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return os.path.join(self._cache_dir, f"{digest}.png")

    def get(self, url: str) -> QImage | None:
        image = self._cache.get(url)
        if image is not None:
            return image

        if not self._cache_dir:
            return None

        cache_path = self._cache_path(url)
        if not os.path.exists(cache_path):
            return None

        image = QImage(cache_path)
        if image.isNull():
            return None

        self._cache[url] = image
        return image

    def ensure(self, url: str, view: QListView | None):
        if not url or not url.startswith(("http://", "https://")) or url in self._cache:
            return

        waiters = self._pending.get(url)
        if waiters is not None:
            if view is not None and view not in waiters:
                waiters.append(view)
            return

        waiters = []
        if view is not None:
            waiters.append(view)
        self._pending[url] = waiters

        reply = self._manager.get(QNetworkRequest(QUrl(url)))
        reply.finished.connect(lambda r=reply, u=url: self._finish(u, r))

    def _finish(self, url: str, reply: QNetworkReply):
        try:
            if sip.isdeleted(reply):
                return

            if reply.error() == QNetworkReply.NetworkError.NoError:
                payload = bytes(reply.readAll())
                image = QImage()
                image.loadFromData(payload)
                if not image.isNull():
                    if (
                        image.width() > EMOJI_CACHE_SIZE
                        or image.height() > EMOJI_CACHE_SIZE
                    ):
                        image = image.scaled(
                            EMOJI_CACHE_SIZE,
                            EMOJI_CACHE_SIZE,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    self._cache[url] = image
                    if self._cache_dir:
                        try:
                            image.save(self._cache_path(url), "PNG")
                        except OSError:
                            pass
        finally:
            waiters = self._pending.pop(url, [])
            if not sip.isdeleted(reply):
                reply.deleteLater()
            for view in waiters:
                if view is not None:
                    view.doItemsLayout()
                    view.viewport().update()


_emoji_store: _ChatEmojiStore | None = None
_avatar_store = None


def _get_emoji_store() -> _ChatEmojiStore:
    global _emoji_store
    if _emoji_store is None:
        _emoji_store = _ChatEmojiStore()
    return _emoji_store


class _ChatAvatarStore(QObject):
    CACHE_SIZE = 32

    def __init__(self):
        super().__init__()
        self._manager = QNetworkAccessManager(self)
        self._cache: dict[str, QPixmap] = {}
        self._pending: dict[str, list[QListView]] = {}

    def get(self, url: str) -> QPixmap | None:
        return self._cache.get(url)

    def ensure(self, url: str, view: QListView | None):
        if not url or url in self._cache:
            return

        waiters = self._pending.get(url)
        if waiters is not None:
            if view is not None and view not in waiters:
                waiters.append(view)
            return

        waiters = []
        if view is not None:
            waiters.append(view)
        self._pending[url] = waiters

        reply = self._manager.get(QNetworkRequest(QUrl(url)))
        reply.finished.connect(lambda r=reply, u=url: self._finish(u, r))

    def _finish(self, url: str, reply: QNetworkReply):
        try:
            if sip.isdeleted(reply):
                return

            if reply.error() == QNetworkReply.NetworkError.NoError:
                payload = bytes(reply.readAll())
                image = QImage()
                if image.loadFromData(payload) and not image.isNull():
                    image = image.scaled(
                        self.CACHE_SIZE,
                        self.CACHE_SIZE,
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self._cache[url] = QPixmap.fromImage(image)
        finally:
            waiters = self._pending.pop(url, [])
            if not sip.isdeleted(reply):
                reply.deleteLater()
            for view in waiters:
                if view is not None:
                    view.doItemsLayout()
                    view.viewport().update()


def _get_avatar_store() -> _ChatAvatarStore:
    global _avatar_store
    if _avatar_store is None:
        _avatar_store = _ChatAvatarStore()
    return _avatar_store


def _normalize_http_avatar_url(url: str | None) -> str | None:
    avatar_url = str(url or "").strip()
    if not avatar_url:
        return None
    if avatar_url.startswith(("http://", "https://")):
        return avatar_url
    return None


def _body_html(
    message: ChatMessage,
    emoji_height: int,
    view: QListView | None,
) -> str:
    segments = message.get("segments")
    if not segments:
        return escape(message["text"]).replace("\n", "<br>")

    store = _get_emoji_store()
    parts: list[str] = []

    for segment in segments:
        if isinstance(segment, str):
            parts.append(escape(segment).replace("\n", "<br>"))
            continue

        text = str(segment.get("text", "") or "")
        if text:
            parts.append(escape(text).replace("\n", "<br>"))
            continue

        url = str(segment.get("url", "") or "")
        alt = str(segment.get("txt", "") or "")
        image = store.get(url)
        if image is None:
            store.ensure(url, view)
            parts.append(escape(alt))
            continue

        width = max(1, round(image.width() * (emoji_height / max(1, image.height()))))
        parts.append(
            f'<img src="{escape(url, quote=True)}" '
            f'width="{width}" height="{emoji_height}">'
        )

    html = "".join(parts)
    if html:
        return html
    return escape(message["text"]).replace("\n", "<br>")


def _build_body_document(
    message: ChatMessage,
    font: QFont,
    width: int,
    text_color: QColor,
    view: QListView | None,
) -> QTextDocument:
    document = QTextDocument()
    document.setDefaultFont(font)
    document.setDocumentMargin(0)
    document.setTextWidth(width)
    document.setDefaultStyleSheet(f"body {{ color: {text_color.name()}; }}")

    for segment in message.get("segments") or ():
        if isinstance(segment, str):
            continue
        url = str(segment.get("url", "") or "")
        image = _get_emoji_store().get(url)
        if image is not None:
            document.addResource(
                QTextDocument.ResourceType.ImageResource,
                QUrl(url),
                image,
            )

    document.setHtml(
        _body_html(
            message=message,
            emoji_height=max(16, QFontMetrics(font).height()),
            view=view,
        )
    )
    return document


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
        message = ChatMessage(
            time=message["time"],
            platform=message["platform"],
            author=message["author"],
            text=message["text"],
            color=message["color"],
            background=message["background"],
            segments=message["segments"],
            avatar_url=_normalize_http_avatar_url(message.get("avatar_url")),
        )
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

    def __init__(
        self,
        parent=...,
        only_system_msg: bool = False,
        hide_system_msg: bool = False,
        with_avatar: bool = True,
        is_transparent: bool = True,
    ):
        self.only_system_msg = only_system_msg
        self.hide_system_msg = hide_system_msg
        self.with_avatar = with_avatar
        self.avatar_size = AVATAR_SIZE if with_avatar else 0
        self.spacing = SPACING if with_avatar else 0
        self.color = COLORS_RGBA if is_transparent else COLORS_SOLID

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
        content_width = max(260, width - (2 * OUTER_MARGIN))
        return max(140, content_width - self.avatar_size - self.spacing)

    def _measure(self, option, message: ChatMessage):
        bubble_width = self._bubble_width(option)

        header_font = QFont(option.font)
        header_font.setBold(True)
        header_height = QFontMetrics(header_font).height()

        body_width = max(20, bubble_width - (2 * BUBBLE_PADDING))
        view = self.parent() if isinstance(self.parent(), QListView) else None
        text_color = _to_color(message["color"], self.color["WHITE"], self.color)
        document = _build_body_document(
            message, option.font, body_width, text_color, view
        )
        body_height = max(1, int(document.size().height()))

        bubble_height = (
            BUBBLE_PADDING
            + header_height
            + HEADER_SPACING
            + body_height
            + BUBBLE_PADDING
        )
        item_height = max(self.avatar_size, bubble_height)
        return bubble_width, bubble_height, item_height, header_height, document

    def sizeHint(self, option, index):
        message = index.data(ChatMessageListModel.MessageRole)
        if not message or self._is_hidden_message(message):
            return QSize(0, 0)
        bubble_width, _, item_height, _, _ = self._measure(option, message)
        total_width = (
            OUTER_MARGIN + self.avatar_size + self.spacing + bubble_width + OUTER_MARGIN
        )
        return QSize(total_width, item_height + (2 * OUTER_MARGIN))

    def paint(self, painter: QPainter, option, index):
        message = index.data(ChatMessageListModel.MessageRole)
        if not message or self._is_hidden_message(message):
            return

        bubble_width, bubble_height, item_height, header_height, document = (
            self._measure(option, message)
        )

        item_top = option.rect.y() + OUTER_MARGIN
        avatar_y = item_top
        avatar_x = option.rect.x() + OUTER_MARGIN
        bubble_x = avatar_x + self.avatar_size + self.spacing
        bubble_y = item_top
        text_color = _to_color(message["color"], self.color["WHITE"], self.color)
        bubble_color = _to_color(message["background"], self.color["BLACK"], self.color)
        view = self.parent() if isinstance(self.parent(), QListView) else None

        painter.save()

        if self.with_avatar:
            avatar_url = str(message.get("avatar_url", "") or "")
            avatar_pixmap = _get_avatar_store().get(avatar_url) if avatar_url else None
            if avatar_pixmap is None and avatar_url:
                _get_avatar_store().ensure(avatar_url, view)

            if avatar_pixmap is not None:
                path = QPainterPath()
                path.addRoundedRect(
                    float(avatar_x),
                    float(avatar_y),
                    float(self.avatar_size),
                    float(self.avatar_size),
                    8.0,
                    8.0,
                )
                painter.setClipPath(path)
                painter.drawPixmap(avatar_x, avatar_y, avatar_pixmap)
                painter.setClipping(False)
            else:
                avatar_bg, avatar_fg = avatar_colors_from_name(message["author"])
                avatar_bg = _to_color(avatar_bg, self.color["GRAY"], self.color)
                avatar_fg = _to_color(avatar_fg, self.color["WHITE"], self.color)

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

        text_left = bubble_x + BUBBLE_PADDING
        text_top = bubble_y + BUBBLE_PADDING
        text_width = max(20, bubble_width - (2 * BUBBLE_PADDING))

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
            header_text_left += icon_size + HEADER_SPACING
            header_text_width = max(20, text_width - icon_size - HEADER_SPACING)
        painter.drawText(
            header_text_left,
            text_top + header_metrics.ascent(),
            header_metrics.elidedText(
                header_text, Qt.TextElideMode.ElideRight, header_text_width
            ),
        )

        body_top = text_top + header_height + HEADER_SPACING
        painter.translate(text_left, body_top)
        document.drawContents(painter)

        painter.restore()


def _to_color(value, fallback, color_dict):
    if isinstance(value, QColor):
        return value
    color = (
        QColor(value) if value else _to_color(fallback, color_dict["BLACK"], color_dict)
    )
    if not color.isValid():
        color = _to_color(fallback, color_dict["BLACK"], color_dict)
    return color
