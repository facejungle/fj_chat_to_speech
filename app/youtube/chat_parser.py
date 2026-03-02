import re
from threading import Thread, current_thread, main_thread
from time import sleep
import httpx
import pytchat
from pytchat import util
import urllib

from app.translations import _, translate_text

_CHANNEL_PATTERNS = (
    re.compile(r'"channelId":"(UC[a-zA-Z0-9_-]{22})"'),
    re.compile(r'\\"channelId\\":\\"(UC[a-zA-Z0-9_-]{22})\\"'),
)


def _extract_channel_id(video_id: str) -> str:
    headers = {
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "accept-language": "en-US,en;q=0.9",
    }

    urls = (
        f"https://www.youtube.com/watch?v={video_id}",
        f"https://www.youtube.com/embed/{video_id}",
        f"https://m.youtube.com/watch?v={video_id}",
    )

    with httpx.Client(
        http2=True, follow_redirects=True, timeout=20.0, headers=headers
    ) as client:
        for url in urls:
            text = client.get(url).text
            for pattern in _CHANNEL_PATTERNS:
                match = pattern.search(text)
                if match:
                    return match.group(1)

    raise pytchat.exceptions.InvalidVideoIdException(
        f"Cannot find channel id for video id:{video_id}."
    )


# Patch pytchat channel-id resolvers to avoid brittle built-in regex fallback.
util.get_channelid = lambda client, video_id: _extract_channel_id(video_id)
util.get_channelid_2nd = lambda client, video_id: _extract_channel_id(video_id)


class YouTubeChatParser:
    MAX_RETRIES = 5

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
        self.video_id = self._parse_video_id(self.url)

    def _stop_chat(self, chat):
        if not chat:
            return
        terminate = getattr(chat, "terminate", None)
        if callable(terminate):
            try:
                terminate()
            except Exception:
                pass

    def _create_chat(self, video_id: str):
        use_interruptable = current_thread() is main_thread()
        return pytchat.create(video_id=video_id, interruptable=use_interruptable)

    def _stream_chat(self, chat) -> str:
        errors = 0

        while errors < self.MAX_RETRIES:
            try:
                while chat.is_alive() and self.is_connected:
                    data = chat.get()
                    for message in data.sync_items():
                        if message.type == "textMessage":
                            author_details = message.author
                            self.on_message(
                                msg_id=message.id,
                                author=author_details.name,
                                msg=message.message,
                                is_sponsor=author_details.isChatSponsor,
                                is_staff=author_details.isChatModerator,
                                is_owner=author_details.isChatOwner,
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
            chat = self._create_chat(self.video_id)
            self.on_connect()
            self._stream_chat(chat)

        except Exception as e:
            self.on_error(
                f"{_(self.lang, "connection_failed")}. {translate_text(str(e), self.lang)}"
            )

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

    def _parse_video_id(self, url):
        try:
            video_id = url
            if url.startswith("watch?v="):
                url = url.removeprefix("watch?v=")
            elif "youtube.com" in url or "youtu.be" in url:
                parsed = urllib.parse.urlparse(url)
                if "youtu.be" in parsed.netloc:
                    video_id = parsed.path[1:]
                elif "watch" in parsed.path:
                    query = urllib.parse.parse_qs(parsed.query)
                    video_id = query.get("v", [None])[0]
                elif "embed" in parsed.path:
                    video_id = parsed.path.split("/")[-1]
                elif "studio.youtube.com" in parsed.netloc and "/video/" in parsed.path:
                    path_parts = [part for part in parsed.path.split("/") if part]
                    if "video" in path_parts:
                        video_index = path_parts.index("video")
                        if video_index + 1 < len(path_parts):
                            video_id = path_parts[video_index + 1]

        except Exception as e:
            self.on_error(
                f"{_(self.lang, "not_determine_video_id")}. {translate_text(str(e), self.lang)}"
            )
            self.disconnect()
            return

        return video_id
