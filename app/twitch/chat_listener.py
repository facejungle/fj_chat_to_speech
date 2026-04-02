import errno
from logging import getLogger
import re
import socket
from threading import Thread
from time import sleep, time
from urllib.parse import urlparse
import requests

from app.translations import _, translate_text
from app.twitch.auth_worker import AuthWorker

logger = getLogger("main")

TIMEOUT = 5
MAX_RETRIES = 10
SERVER = "irc.chat.twitch.tv"
PORT = 6667
DONATION_MSG_IDS = frozenset(
    {
        "sub",
        "resub",
        "subgift",
        "anonsubgift",
        "submysterygift",
        "anonsubmysterygift",
        "giftpaidupgrade",
        "anongiftpaidupgrade",
        "primepaidupgrade",
        "standardpayforward",
        "communitypayforward",
    }
)

SOCKET_BROKEN_ERRORS = frozenset(
    {
        # Connection reset by peer
        errno.ECONNRESET,
        errno.WSAECONNRESET,
        # Broken pipe
        errno.EPIPE,
        errno.WSAECONNABORTED,
        # Connection aborted
        errno.ECONNABORTED,
        # Socket not connected
        errno.ENOTCONN,
        errno.WSAENOTCONN,
        # Network is down
        errno.ENETDOWN,
        errno.WSAENETDOWN,
        # NOT A SOCKET
        errno.ENOTSOCK,
        errno.WSAENOTSOCK,
    }
)

_avatar_url_cache: dict[str, str | None] = {}


class TwitchChatListener:
    def __init__(
        self,
        client_id,
        access,
        refresh,
        channel,
        nickname,
        on_message,
        on_connect,
        on_disconnect,
        on_error,
        on_reconnect,
        on_expiries_access,
        on_expiries_refresh,
        lang="en",
    ):
        self.client_id = client_id
        self.access = access
        self.refresh = refresh
        self.channel = _parse_channel(channel)
        self.nickname = nickname
        self.on_message = on_message
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.on_error = on_error
        self.on_expiries_access = on_expiries_access
        self.on_expiries_refresh = on_expiries_refresh
        self.on_reconnect = on_reconnect
        self.lang = lang

        self.sock = None
        self.is_connected = False
        self.listen_thread = None
        self.last_ping = time()
        self._is_stopping = False
        self.connect_attempt = 0

    def disconnect(self):
        self._is_stopping = True
        if self.is_connected:
            self.on_disconnect()
            self.is_connected = False
        self._close_socket()

    def run(self):
        self.listen_thread = Thread(target=self._connect, daemon=True)
        self.listen_thread.start()

    def _send_command(self, command):
        self.sock.send(f"{command}\r\n".encode("utf-8"))

    def _create_socket(self):
        try:
            if self.sock:
                self._close_socket()

            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(TIMEOUT)
            self.sock.connect((SERVER, PORT))
            return True

        except Exception:
            return False

    def _close_socket(self):
        if not self.sock:
            return
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass
        self.sock = None

    def _connect(self):
        self.is_connected = True
        while not self._is_stopping and self.connect_attempt < MAX_RETRIES:
            try:
                self.on_reconnect()

                _socket = self._create_socket()
                if not _socket:
                    self.on_error(
                        f"{_(self.lang, 'Failed to create socket connection')}"
                    )
                    break

                # Authentication

                try:
                    self._send_command(
                        "CAP REQ :twitch.tv/tags twitch.tv/commands twitch.tv/membership"
                    )
                    self._send_command(f"PASS oauth:{self.access}")
                    self._send_command(f"NICK {self.nickname}")
                except socket.timeout:
                    continue

                sleep(1)

                try:
                    response = self.sock.recv(4096).decode("utf-8", errors="ignore")
                except socket.timeout:
                    response = self.sock.recv(4096).decode("utf-8", errors="ignore")

                if "authentication failed" in response:
                    if not self._on_expiries_access():
                        self.on_error(_(self.lang, "Failed to refresh access token"))
                        self.disconnect()
                        return self.on_expiries_refresh()
                    continue

                # Join to channel

                joined = False
                join_attempts = 0

                try:
                    self._send_command(f"JOIN #{self.channel}")
                except socket.timeout:
                    self._send_command(f"JOIN #{self.channel}")

                sleep(0.5)

                while not joined and not self._is_stopping and join_attempts < 3:
                    try:
                        response = self.sock.recv(4096).decode("utf-8", errors="ignore")
                    except socket.timeout:
                        join_attempts += 1
                        sleep(join_attempts)
                        pass

                    if "authentication failed" in response:
                        if not self._on_expiries_access():
                            self.on_error(
                                _(self.lang, "Failed to refresh access token")
                            )
                            self.disconnect()
                            return self.on_expiries_refresh()
                        continue
                    if f"JOIN #{self.channel}" in response:
                        joined = True
                        break
                    if "466" in response:
                        self.on_error(_(self.lang, "Incorrect nickname format"))
                        break
                    if "433" in response:
                        self.on_error(_(self.lang, "The nickname is already in use"))
                        break

                if joined:
                    self.on_connect()
                else:
                    self.on_error(
                        f"{_(self.lang, 'Failed to join to channel')}: {self.channel}"
                    )
                    break

                self._listen_chat()

            except Exception as e:
                self.connect_attempt += 1
                err_str = str(e)
                self.on_error(
                    f"{_(self.lang, 'connection_failed')}. {translate_text(err_str, self.lang)}. {_(self.lang, 'Reconnect')} {self.connect_attempt}/{MAX_RETRIES}"
                )
                continue

        return self.disconnect()

    def _listen_chat(self):
        timeout_errors = 0
        data_empty = 0
        buffer = ""
        while (
            self.sock and not self._is_stopping and self.connect_attempt < MAX_RETRIES
        ):
            try:
                if time() - self.last_ping > 60:
                    try:
                        self._send_command("PING")
                        self.last_ping = time()
                    except Exception:
                        pass

                try:
                    data = self.sock.recv(4096).decode("utf-8", errors="ignore")
                except socket.timeout:
                    timeout_errors += 1
                    if timeout_errors > 5:
                        self.connect_attempt += 1
                        self.on_error(
                            f"{_(self.lang, 'error_fetch_messages')}. {_(self.lang, 'Too many timeouts in a row')}. {_(self.lang, 'Reconnect')} {self.connect_attempt}/{MAX_RETRIES}"
                        )
                        return
                    sleep(timeout_errors)
                    continue

                if not data:
                    data_empty += 1
                    if data_empty > 3:
                        self.connect_attempt += 1
                        self.on_error(
                            f"{_(self.lang, 'error_fetch_messages')}. {_(self.lang, 'Too many empty data in a row')}. {_(self.lang, 'Reconnect')} {self.connect_attempt}/{MAX_RETRIES}"
                        )
                        return
                    sleep(data_empty)
                    continue

                buffer += data

                if "\r\n" not in buffer:
                    continue

                lines = buffer.split("\r\n")
                buffer = lines.pop()

                for line in lines:
                    if self._is_stopping:
                        break

                    if not line:
                        continue

                    if line.startswith("PING"):
                        try:
                            self._send_command("PONG")
                            self.last_ping = time()
                            continue
                        except:
                            pass

                    msg_data = _parse_message(line)

                    if msg_data:
                        badges = msg_data.get("badges", "")
                        tags = msg_data.get("tags", {})
                        badge_names = {
                            badge.split("/", 1)[0]
                            for badge in badges.split(",")
                            if badge
                        }

                        is_owner = (
                            "broadcaster" in badge_names
                            or tags.get("user-id") == tags.get("room-id")
                            or str(msg_data["username"]).lower() == self.channel
                        )
                        is_staff = msg_data["mod"] or "moderator" in badge_names
                        is_sponsor = (
                            tags.get("subscriber") == "1"
                            or "subscriber" in badge_names
                            or "founder" in badge_names
                        )
                        bits = tags.get("bits")
                        paid_amount = tags.get("pinned-chat-paid-amount")
                        notice_event = tags.get("msg-id")
                        is_donate = (
                            (bits and bits != "0")
                            or (paid_amount and paid_amount != "0")
                            or notice_event in DONATION_MSG_IDS
                        )

                        self.on_message(
                            msg_id=msg_data["id"],
                            author=msg_data["username"],
                            msg=msg_data["message"],
                            msg_ex=_parse_emote_segments(msg_data["message"], tags),
                            is_sponsor=is_sponsor,
                            is_staff=is_staff,
                            is_owner=is_owner,
                            is_donate=is_donate,
                            avatar_url=_get_avatar_url(
                                msg_data["username"],
                                client_id=self.client_id,
                                access_token=self.access,
                            ),
                        )

                self.connect_attempt = 0
                timeout_errors = 0
                data_empty = 0
                sleep(1)

            except socket.error as e:
                if self._is_stopping:
                    return

                self.connect_attempt += 1
                self.on_error(
                    f"{_(self.lang, 'error_fetch_messages')}. {translate_text(str(e), self.lang)}. {_(self.lang, 'Reconnect')} {self.connect_attempt}/{MAX_RETRIES}"
                )

                if e.errno in SOCKET_BROKEN_ERRORS:
                    return

                sleep(self.connect_attempt)

            except Exception as e:
                if self._is_stopping:
                    return
                self.connect_attempt += 1
                self.on_error(
                    f"{_(self.lang, 'error_fetch_messages')}. {translate_text(str(e), self.lang)}. {_(self.lang, 'Reconnect')} {self.connect_attempt}/{MAX_RETRIES}"
                )
                sleep(self.connect_attempt)

    def _on_expiries_access(self):
        try:
            access, refresh = AuthWorker.ensure_valid_access_token(
                client_id=self.client_id,
                access_token=self.access,
                refresh_token=self.refresh,
                lang=self.lang,
            )
            self.access = access
            self.refresh = refresh
            self.on_expiries_access(self.access, self.refresh)
            return True
        except Exception:
            return False


def _parse_message(line):
    try:
        if line.startswith("@"):
            parts = line.split(" ", 1)
            if len(parts) < 2:
                return None

            tags_str = parts[0][1:]
            rest = parts[1]

            tag_dict = {}
            for tag in tags_str.split(";"):
                if "=" in tag:
                    key, value = tag.split("=", 1)
                    value = (
                        value.replace("\\s", " ")
                        .replace("\\:", ";")
                        .replace("\\\\", "\\")
                    )
                    tag_dict[key] = value

            match = re.search(
                r":(\w+)!\w+@\w+\.tmi\.twitch\.tv PRIVMSG #\w+ :(.*)", rest
            )

            if match:
                username = match.group(1)
                message = match.group(2)

                msg_id = tag_dict.get("id")
                is_member = tag_dict.get("subscriber") == "1"
                is_mod = tag_dict.get("mod") == "1"
                is_vip = tag_dict.get("vip") == "1"
                badges = tag_dict.get("badges", "")

                return {
                    "id": msg_id,
                    "username": username,
                    "message": message,
                    "subscriber": is_member,
                    "mod": is_mod,
                    "vip": is_vip,
                    "badges": badges,
                    "tags": tag_dict,
                }

            if " USERNOTICE " in rest:
                notice_message = tag_dict.get("system-msg", "")
                trailing_message = ""
                split_pos = rest.find(" :")
                if split_pos != -1:
                    trailing_message = rest[split_pos + 2 :]
                if trailing_message:
                    notice_message = (
                        f"{notice_message}: {trailing_message}"
                        if notice_message
                        else trailing_message
                    )

                return {
                    "id": tag_dict.get("id"),
                    "username": tag_dict.get("display-name")
                    or tag_dict.get("login")
                    or "twitch",
                    "message": notice_message,
                    "subscriber": tag_dict.get("subscriber") == "1"
                    or tag_dict.get("msg-id") in DONATION_MSG_IDS,
                    "mod": tag_dict.get("mod") == "1",
                    "vip": tag_dict.get("vip") == "1",
                    "badges": tag_dict.get("badges", ""),
                    "tags": tag_dict,
                }
        else:
            match = re.search(
                r":(\w+)!\w+@\w+\.tmi\.twitch\.tv PRIVMSG #\w+ :(.*)", line
            )
            if match:
                username = match.group(1)
                message = match.group(2)
                return {
                    "id": None,
                    "username": username,
                    "message": message,
                    "subscriber": False,
                    "mod": False,
                    "vip": False,
                    "badges": "",
                    "tags": {},
                }

    except Exception as e:
        logger.error("Error parsing message. %s", str(e))

    return None


def _parse_channel(channel_input):
    if not channel_input:
        return None

    channel_input = channel_input.strip().lower()

    if channel_input.startswith(("http://", "https://")):
        parsed = urlparse(channel_input)
        path = parsed.path.strip("/")

        if "twitch.tv" in parsed.netloc or "twitch.tv" in channel_input:
            parts = path.split("/")
            if parts and parts[0]:
                return parts[0]

    channel_input = channel_input.lstrip("@#")
    channel_input = re.sub(r"[^a-zA-Z0-9_]", "", channel_input)

    return channel_input


def _normalize_avatar_url(url: str | None) -> str | None:
    avatar_url = str(url or "").strip()
    if not avatar_url:
        return None

    return avatar_url


def _fetch_avatar_url(login: str, client_id: str, access_token: str) -> str | None:
    login = str(login or "").strip().lower()
    response = requests.get(
        "https://api.twitch.tv/helix/users",
        params={"login": login},
        headers={
            "Client-ID": client_id,
            "Authorization": f"Bearer {access_token}",
        },
        timeout=10,
    )

    if response.status_code != 200:
        return None

    payload = response.json()
    for user in payload.get("data", []):
        original_url = str(user.get("profile_image_url") or "").strip()
        return _normalize_avatar_url(original_url)
    return None


def _get_avatar_url(username: str, client_id: str, access_token: str) -> str | None:
    login = str(username or "").strip().lower()
    if not login:
        return None

    if login in _avatar_url_cache:
        return _avatar_url_cache[login]

    try:
        avatar_url = _fetch_avatar_url(login, client_id, access_token)
        _avatar_url_cache[login] = avatar_url
        return avatar_url
    except Exception:
        _avatar_url_cache[login] = None
        return None


def _parse_emote_segments(message: str, tags: dict) -> list | None:
    emotes = str(tags.get("emotes", "") or "").strip()
    if not message or not emotes:
        return None

    ranges = []
    for emote_group in emotes.split("/"):
        if not emote_group or ":" not in emote_group:
            continue

        emote_id, positions = emote_group.split(":", 1)
        emote_id = emote_id.strip()
        if not emote_id:
            continue

        for position in positions.split(","):
            if "-" not in position:
                continue
            start_str, end_str = position.split("-", 1)
            try:
                start = int(start_str)
                end = int(end_str)
            except ValueError:
                continue
            if start < 0 or end < start or end >= len(message):
                continue

            ranges.append((start, end, emote_id))

    if not ranges:
        return None

    ranges.sort(key=lambda item: item[0])
    segments = []
    cursor = 0

    for start, end, emote_id in ranges:
        if start < cursor:
            continue

        if start > cursor:
            segments.append(message[cursor:start])

        emote_text = message[start : end + 1]
        segments.append(
            {
                "id": emote_id,
                "txt": emote_text,
                "url": f"https://static-cdn.jtvnw.net/emoticons/v2/{emote_id}/default/dark/3.0",
            }
        )
        cursor = end + 1

    if cursor < len(message):
        segments.append(message[cursor:])

    return segments or None
