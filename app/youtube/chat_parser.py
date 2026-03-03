from threading import Thread, current_thread, main_thread
from time import sleep
import pytchat
from pytchat.core import PytchatCore
import urllib

from app.translations import _, translate_text
from app.utils import parse_youtube_video_id


class YouTubeChatParser:
    MAX_RETRIES = 5
    POLL_TYPES = {
        "poll",
        "liveChatPoll",
        "pollOpened",
        "pollUpdated",
        "pollClosed",
    }

    def __init__(
        self,
        url: str,
        on_message,
        on_connect,
        on_disconnect,
        on_error,
        lang: str = "en",
    ):
        super().__init__()
        self.url = url
        self.on_message = on_message
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.on_error = on_error
        self.lang = lang

        self.is_connected = False
        self.video_id = parse_youtube_video_id(url)

    def _stop_chat(self, chat):
        if not chat:
            return
        terminate = getattr(chat, "terminate", None)
        if callable(terminate):
            try:
                terminate()
            except Exception:
                pass

    def _create_chat(self) -> PytchatCore:
        use_interruptable = current_thread() is main_thread()

        if not self.video_id:
            raise AttributeError(_(self.lang, "not_determine_video_id"))

        return pytchat.create(video_id=self.video_id, interruptable=use_interruptable)

    def _build_message_payload(self, message):
        message_type = str(getattr(message, "type", "") or "").strip()
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
        elif message_type == "donation":
            text = _(self.lang, "Donation")
            is_donate = True
        elif message_type == "superChat":
            text = _(self.lang, "Super Chat")
            is_donate = True
        elif message_type == "superSticker":
            text = _(self.lang, "Super Sticker")
            is_donate = True
        elif message_type == "newSponsor":
            text = _(self.lang, "New sponsor")
            is_donate = True

        if amount_text and currency_text:
            text += f" [{amount_text} {currency_text}]"
        elif amount_text:
            text += f" [{amount_text}]"

        if message_text:
            text += f": {message_text}"

        return {"msg": text, "is_donate": is_donate}

    def _stream_chat(self, chat: PytchatCore) -> str:
        errors = 0

        while errors < self.MAX_RETRIES:
            try:
                while chat.is_alive() and self.is_connected:
                    data = chat.get()
                    for message in data.sync_items():
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

                    raise_for_status = getattr(chat, "raise_for_status", None)
                    if callable(raise_for_status):
                        raise_for_status()

                    errors = 0
                else:
                    raise ConnectionError(_(self.lang, "connection_failed"))

            except Exception as e:
                if not self.is_connected:
                    return
                errors += 1
                self.on_error(
                    f"{_(self.lang, "error_fetch_messages")}. {translate_text(str(e), self.lang)}. {_(self.lang, 'Reconnect')} {errors}/{self.MAX_RETRIES}"
                )

                sleep(errors)

    def _connect(self):
        self.is_connected = True
        chat = None

        try:
            chat = self._create_chat()
            self.on_connect()
            self._stream_chat(chat)

        except Exception as e:
            self.on_error(f"{_(self.lang, "connection_failed")}. {translate_text(str(e), self.lang)}")

        finally:
            self._stop_chat(chat)
            self.disconnect()

    def disconnect(self):
        if self.is_connected:
            self.on_disconnect()
        self.is_connected = False

    def run(self):
        th = Thread(target=self._connect, daemon=True)
        th.start()
