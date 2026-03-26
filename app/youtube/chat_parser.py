from threading import Thread, current_thread, main_thread
from time import sleep
import pytchat
from pytchat.core import PytchatCore
import re
from threading import Thread, current_thread, main_thread
from urllib.request import Request, urlopen

from app.translations import _, translate_text
from app.utils import parse_youtube_video_id


class YouTubeChatParser:
    MAX_RETRIES = 10
    POLL_TYPES = {
        "poll",
        "liveChatPoll",
        "pollOpened",
        "pollUpdated",
        "pollClosed",
    }
    DONATE_TYPE_LABELS = {
        "donation": "Donation",
        "superChat": "Super Chat",
        "paidMessage": "Super Chat",
        "tickerPaidMessageItem": "Super Chat",
        "superSticker": "Super Sticker",
        "paidSticker": "Super Sticker",
        "tickerPaidStickerItem": "Super Sticker",
        "newSponsor": "New sponsor",
        "membershipItem": "New sponsor",
        "legacyPaidMessage": "New sponsor",
        "tickerSponsorItem": "New sponsor",
        "membershipGiftPurchase": "New sponsor",
        "sponsorshipsGiftPurchaseAnnouncement": "New sponsor",
        "giftMembershipReceived": "New sponsor",
    }
    DONATE_TYPE_KEYWORDS = (
        "donation",
        "superchat",
        "supersticker",
        "superthanks",
        "paidmessage",
        "paidsticker",
        "membership",
        "sponsor",
        "gift",
        "purchase",
    )

    def __init__(
        self,
        url: str,
        on_message,
        on_connect,
        on_disconnect,
        on_reconnect,
        on_error,
        lang: str = "en",
    ):
        super().__init__()
        self.url = url
        self.on_message = on_message
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.on_reconnect = on_reconnect
        self.on_error = on_error
        self.disconnect_signal = False
        self.is_connected = False
        self.lang = lang

        self.chat: PytchatCore | None = None
        self.video_id = parse_youtube_video_id(url)

    def _fetch_watch_page(self) -> str:
        if not self.video_id:
            return ""

        watch_url = f"https://www.youtube.com/watch?v={self.video_id}"
        request = Request(
            watch_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urlopen(request, timeout=15) as response:
            return response.read().decode("utf-8", errors="ignore")

    def _has_live_chat(self) -> bool:
        html = self._fetch_watch_page()
        if not html:
            return False

        # Reject regular uploads and finished streams.
        if not re.search(r'"isLiveNow"\s*:\s*true', html):
            return False

        # Ensure chat is available for this live.
        return bool(
            re.search(r'"liveChatRenderer"\s*:', html)
            or re.search(r'"liveChatFrameEndpoint"\s*:', html)
            or re.search(r'"conversationBar"\s*:', html)
        )

    def _stop_chat(self):
        if not self.chat:
            return
        terminate = getattr(self.chat, "terminate", None)
        if callable(terminate):
            try:
                terminate()
            except Exception:
                pass

    def _create_chat(self) -> PytchatCore:
        use_interruptable = current_thread() is main_thread()

        if not self.video_id:
            raise AttributeError("not_determine_video_id")

        self._stop_chat()
        self.chat = pytchat.create(
            video_id=self.video_id, interruptable=use_interruptable
        )
        return self.chat

    def _emit_messages(self, data, fast: bool = False):
        if self.disconnect_signal:
            return

        if fast:
            items = getattr(data, "items", None)
            if items is None:
                items = data.sync_items()
        else:
            items = data.sync_items()

        for message in items:
            if self.disconnect_signal:
                return

            author_details = getattr(message, "author", None)
            if author_details is None:
                continue

            payload = self._build_message_payload(message)
            if not payload["msg"]:
                continue

            self.on_message(
                msg_id=message.id,
                author=getattr(author_details, "name", ""),
                msg=payload["msg"],
                is_sponsor=getattr(author_details, "isChatSponsor", False),
                is_staff=getattr(author_details, "isChatModerator", False),
                is_owner=getattr(author_details, "isChatOwner", False),
                is_donate=payload["is_donate"],
            )

    def _ensure_stream_is_live(self):
        if not self.chat:
            return False

        data = self.chat.get()
        if self.chat.is_replay():
            self.on_error(_(self.lang, "video_not_found"))
            return False

        raise_for_status = getattr(self.chat, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()

        return data

    def _build_message_payload(self, message):
        message_type = str(getattr(message, "type", "") or "").strip()
        normalized_type = message_type.casefold()
        message_text = str(getattr(message, "message", "") or "").strip()
        amount_text = str(getattr(message, "amountString", "") or "").strip()
        currency_text = str(getattr(message, "currency", "") or "").strip()

        text = ""
        is_donate = False

        if message_type == "textMessage":
            return {"msg": message_text, "is_donate": is_donate}
        elif message_type in self.POLL_TYPES:
            if message_type == "pollOpened":
                text = _(self.lang, "Poll is opened")
            elif message_type == "pollUpdated":
                text = _(self.lang, "Poll is updated")
            elif message_type == "pollClosed":
                text = _(self.lang, "Poll is closed")
            else:
                text = _(self.lang, "Poll")
        else:
            label = self.DONATE_TYPE_LABELS.get(message_type)
            if label is None and any(
                keyword in normalized_type for keyword in self.DONATE_TYPE_KEYWORDS
            ):
                if "sticker" in normalized_type:
                    label = "Super Sticker"
                elif any(
                    keyword in normalized_type
                    for keyword in ("sponsor", "membership", "gift")
                ):
                    label = "New sponsor"
                elif any(
                    keyword in normalized_type
                    for keyword in ("chat", "message", "thanks")
                ):
                    label = "Super Chat"
                else:
                    label = "Donation"

            if label:
                text = _(self.lang, label)
                is_donate = True

        if not text:
            text = message_text

        if text and amount_text and currency_text:
            text += f" [{amount_text} {currency_text}]"
        elif text and amount_text:
            text += f" [{amount_text}]"

        if message_text and text != message_text:
            text += f": {message_text}"

        return {"msg": text, "is_donate": is_donate}

    def _stream_chat(self, initial_data=None) -> str:
        try:
            if initial_data is not None:
                self._emit_messages(initial_data)

            while self.chat and self.chat.is_alive() and not self.disconnect_signal:
                data = self.chat.get()
                self._emit_messages(data)

                raise_for_status = getattr(self.chat, "raise_for_status", None)
                if callable(raise_for_status):
                    raise_for_status()

                sleep(1)

        except Exception as e:
            if self.disconnect_signal:
                return

            raise ConnectionError(
                f"{_(self.lang, 'error_fetch_messages')}. {translate_text(str(e), self.lang)}"
            )

    def _connect(self):
        errors = 0
        self.is_connected = True

        while errors < self.MAX_RETRIES and not self.disconnect_signal:
            try:
                if not self._has_live_chat():
                    self.on_error(
                        f"{_(self.lang, 'connection_failed')}. {_(self.lang, 'video_not_found')}"
                    )
                    break

                self._create_chat()
                initial_data = self._ensure_stream_is_live()

                if initial_data is False:
                    break

                if self.chat and self.chat.is_alive():
                    errors = 0
                    self.on_connect()
                    self._stream_chat(initial_data=initial_data)

            except Exception as e:
                if self.disconnect_signal:
                    break

                error_str = str(e)
                errors += 1

                if "not_determine_video_id" in error_str:
                    self.on_error(
                        f"{_(self.lang, 'connection_failed')}. {_(self.lang, error_str)}"
                    )
                    break

                if (
                    "URL can't contain" in error_str
                    or "Invalid video id" in error_str
                    or "Chat data stream is empty" in error_str
                ):
                    self.on_error(
                        f"{_(self.lang, 'connection_failed')}. {translate_text(error_str, self.lang)}"
                    )
                    break

                self.on_reconnect()
                self.on_error(
                    f"{_(self.lang, 'connection_failed')}. {translate_text(error_str, self.lang)}. {_(self.lang, 'Reconnect')} {errors}/{self.MAX_RETRIES}"
                )
                sleep(errors * 2)

        self.disconnect()

    def parse_old_chat(self):
        if not self.video_id:
            self.on_error(_(self.lang, "not_determine_video_id"))
            return

        self.disconnect_signal = False
        self.is_connected = True

        try:
            use_interruptable = current_thread() is main_thread()
            self._stop_chat()

            try:
                self.chat = pytchat.create(
                    video_id=self.video_id,
                    interruptable=use_interruptable,
                    force_replay=True,
                )
            except TypeError:
                self.chat = pytchat.create(
                    video_id=self.video_id,
                    interruptable=use_interruptable,
                )

            if not self.chat:
                self.on_error(_(self.lang, "video_not_found"))
                return

            data = self.chat.get()
            raise_for_status = getattr(self.chat, "raise_for_status", None)
            if callable(raise_for_status):
                raise_for_status()

            if not self.chat.is_replay():
                self.on_error(_(self.lang, "video_not_found"))
                return

            self.on_connect()
            if data is not None:
                self._emit_messages(data, fast=True)

            while self.chat and self.chat.is_alive() and not self.disconnect_signal:
                data = self.chat.get()

                items = getattr(data, "items", None)
                had_items = bool(items)
                self._emit_messages(data, fast=True)

                raise_for_status = getattr(self.chat, "raise_for_status", None)
                if callable(raise_for_status):
                    raise_for_status()

                if (not had_items) and getattr(self.chat, "continuation", None) is None:
                    break

        except Exception as e:
            err_text = str(e)
            if "Finished chat data" in err_text:
                return self.disconnect()

            if not self.disconnect_signal:
                self.on_error(
                    f"{_(self.lang, 'error_fetch_messages')}. {translate_text(str(e), self.lang)}"
                )

        self.disconnect()

    def disconnect(self):
        self.disconnect_signal = True
        self._stop_chat()
        if self.is_connected:
            self.on_disconnect()
        self.is_connected = False

    def run(self):
        th = Thread(target=self._connect, daemon=True)
        th.start()
