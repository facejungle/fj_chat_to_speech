from logging import getLogger
import re
import socket
from threading import Thread
from time import sleep, time
from urllib.parse import urlparse

import requests

from app.translations import _, translate_text

logger = getLogger("main")


class TwitchChatListener:
    TIMEOUT = 3
    MAX_RETRIES = 5
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

    def __init__(
        self,
        client_id,
        token,
        channel,
        nickname,
        on_message,
        on_connect,
        on_disconnect,
        on_error,
        on_reconnect,
        on_expiries_access,
        lang="en",
    ):
        self.client_id = client_id
        self.token = token
        self.channel = self._parse_channel(channel)
        self.nickname = str(nickname).lower()
        self.on_message = on_message
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.on_error = on_error
        self.on_expiries_access = on_expiries_access
        self.on_reconnect = on_reconnect
        self.lang = lang

        self.sock = None
        self.is_connected = False
        self.listen_thread = None
        self.last_ping = time()
        self._is_stopping = False

    def _handle_expired_access(self):
        try:
            self.token = self.on_expiries_access()
            return True
        except Exception as e:
            self.on_error(
                f"{_(self.lang, 'connection_failed')}. {translate_text(str(e), self.lang)}"
            )
            return False

    def _send_command(self, command):
        try:
            self.sock.send(f"{command}\r\n".encode("utf-8"))
        except Exception as e:
            self.on_error(
                f"{_(self.lang, 'Error send a command')} {command}. {translate_text(str(e), self.lang)}"
            )

    def _recv(self):
        try:
            return self.sock.recv(4096).decode("utf-8", errors="ignore")
        except Exception as e:
            self.on_error(
                f"{_(self.lang, 'Error recv data')}. {translate_text(str(e), self.lang)}"
            )

    def _parse_channel(self, channel_input):
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

    def _create_socket(self):
        try:
            if self.sock:
                self._close_socket()

            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(self.TIMEOUT)
            self.sock.connect((self.SERVER, self.PORT))

            self._send_command(
                "CAP REQ :twitch.tv/tags twitch.tv/commands twitch.tv/membership"
            )
            self._send_command(f"PASS oauth:{self.token}")
            self._send_command(f"NICK {self.nickname}")

            response = self.sock.recv(4096).decode("utf-8", errors="ignore")
            if "Login authentication failed" in response:
                if self._handle_expired_access():
                    self.sock.close()
                    return self._create_socket()
                else:
                    self.on_error(_(self.lang, "Failed to refresh access token"))
                    return False

            self._send_command(f"JOIN #{self.channel}")
            return True

        except Exception as e:
            self.on_error(_(self.lang, "Failed to create socket connection"))
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
        reconnect_attempts = 0

        try:
            while reconnect_attempts < self.MAX_RETRIES and not self._is_stopping:
                self.is_connected = False
                is_socket_created = self._create_socket()
                
                while (
                    is_socket_created
                    and not self.is_connected
                    and not self._is_stopping
                ):
                    try:
                        response = self.sock.recv(4096).decode("utf-8", errors="ignore")
                        lines = response.strip().split("\r\n")
                        for line in lines:
                            if "Login authentication failed" in line:
                                if self._handle_expired_access():
                                    self._close_socket()
                                    if self._create_socket():
                                        break
                                    reconnect_attempts += 1
                                    break
                                self.on_error(
                                    _(self.lang, "Failed to refresh access token")
                                )
                                self.on_disconnect()
                                return

                            if f"JOIN #{self.channel}" in line:
                                self.is_connected = True
                                break

                            if "466" in line:  # ERR_ERRONEOUSNICKNAME
                                self.on_error(_(self.lang, "Incorrect nickname format"))
                                return
                            if "433" in line:  # ERR_NICKNAMEINUSE
                                self.on_error(
                                    _(self.lang, "The nickname is already in use")
                                )
                                return

                    except socket.timeout:
                        continue

                if self._is_stopping:
                    return

                if not self.is_connected:
                    reconnect_attempts += 1
                    self.on_reconnect()
                    self.on_error(
                        f"{_(self.lang, 'connection_failed')}. {_(self.lang, 'Reconnect')} {reconnect_attempts}/{self.MAX_RETRIES}"
                    )
                    self._close_socket()
                    sleep(reconnect_attempts)
                    continue

                reconnect_attempts = 0
                self.on_connect()
                self._listen_chat()

            if not self._is_stopping:
                self.on_error(_(self.lang, "connection_failed"))

        except Exception as e:
            if self._is_stopping:
                return
            self.on_error(
                f"{_(self.lang, 'connection_failed')}. {translate_text(str(e), self.lang)}"
            )
            return
        finally:
            self.disconnect()

    def disconnect(self):
        was_connected = self.is_connected
        if was_connected:
            self.on_disconnect()
        self._is_stopping = True
        self.is_connected = False
        self._close_socket()

    def _parse_message(self, line):
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
                        or tag_dict.get("msg-id") in self.DONATION_MSG_IDS,
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
            self.on_error(
                f"{_(self.lang, 'Error parsing message')}. {translate_text(str(e), self.lang)}"
            )

        return None

    def _listen_chat(self):
        buffer = ""
        errors = 0
        try:
            while self.is_connected and self.sock and not self._is_stopping:
                if time() - self.last_ping > 60:
                    try:
                        self._send_command("PING")
                        self.last_ping = time()
                    except Exception:
                        pass

                try:
                    data = self.sock.recv(4096).decode("utf-8", errors="ignore")
                except socket.timeout:
                    errors += 1
                    if errors > self.MAX_RETRIES:
                        self.on_reconnect()
                        return self._connect()
                    continue

                if not data:
                    raise ConnectionError("empty socket data")

                buffer += data

                while "\r\n" in buffer and not self._is_stopping:
                    line, buffer = buffer.split("\r\n", 1)

                    if not line:
                        continue

                    if line.startswith("PING"):
                        self._send_command("PONG")
                        self.last_ping = time()
                        continue

                    msg_data = self._parse_message(line)

                    if msg_data:
                        badges = msg_data.get("badges", "")
                        tags = msg_data.get("tags", {})

                        is_owner = (
                            "broadcaster/1" in badges
                            or tags.get("user-id") == tags.get("room-id")
                            or str(msg_data["username"]).lower() == self.channel
                        )
                        is_staff = (
                            msg_data["mod"]
                            or "staff/1" in badges
                            or "admin/1" in badges
                            or "global_mod/1" in badges
                        )
                        bits = tags.get("bits")
                        paid_amount = tags.get("pinned-chat-paid-amount")
                        notice_event = tags.get("msg-id")
                        is_donate = (
                            (bits and bits != "0")
                            or (paid_amount and paid_amount != "0")
                            or notice_event in self.DONATION_MSG_IDS
                        )

                        self.on_message(
                            msg_id=msg_data["id"],
                            author=msg_data["username"],
                            msg=msg_data["message"],
                            is_sponsor=msg_data["subscriber"],
                            is_staff=is_staff,
                            is_owner=is_owner,
                            is_donate=is_donate,
                        )

                errors = 0
                sleep(1)

            return self._is_stopping

        except Exception as e:
            if self._is_stopping:
                return True
            self.on_error(
                f"{_(self.lang, 'error_fetch_messages')}. {translate_text(str(e), self.lang)}"
            )
            self.is_connected = False
            self._close_socket()
            return False

    def run(self):
        try:
            self.listen_thread = Thread(target=self._connect, daemon=True)
            self.listen_thread.start()
            return True
        except Exception as e:
            self.on_error(
                f"{_(self.lang, 'Runtime error')}. {translate_text(str(e), self.lang)}"
            )
            return False
