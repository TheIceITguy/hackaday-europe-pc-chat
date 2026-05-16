"""USB companion bridge for the Hackaday Europe chat protocol."""

import select
import sys
import time

import lvgl

from apps.base_app import BaseApp
from net.net import BROADCAST_ADDRESS, MY_ADDRESS, register_receiver, send
from net.protocols import NetworkFrame, Protocol
from ui import styles
from ui.page import Page


APP_NAME = "PC Chat"

MAX_MESSAGE_LEN = 100
SAFE_MESSAGE_LEN = 60
MAX_SERIAL_LINE_LEN = 1200
TEXT_CHAT = Protocol(port=6, name="TEXT_CHAT", structdef="!H10s%ds" % MAX_MESSAGE_LEN)


class App(BaseApp):
    """Send and receive normal badge chat messages over USB serial."""

    def __init__(self, name, badge):
        super().__init__(name, badge)
        self.foreground_sleep_ms = 5
        self.background_sleep_ms = 2000
        self.active_topic = 1
        self.line_buffer = ""
        self.poll = select.poll()  # type: ignore
        self.poll.register(sys.stdin, select.POLLIN)  # type: ignore
        self.page = None
        self.rows = []
        self.tx_count = 0
        self.rx_count = 0
        self.last_status = "Launch companion on computer"
        self.usb_debug_sleep_ms = None
        self.usb_debug_app = None

    def start(self):
        super().start()
        register_receiver(TEXT_CHAT, self.receive_message)

    def switch_to_foreground(self):
        self._slow_usb_debug()
        self.page = Page()
        self.page.create_infobar((self._left_info(), "USB chat bridge"))
        self.page.create_content()
        self.page.add_message_rows(1, 80)
        self.page.create_menubar(["", "", "", "", "Home"])
        self.page.replace_screen()
        self._print("READY\t%s\t%d" % (self._my_alias(), self.active_topic))
        self._add_row("ready", self.last_status)
        self._refresh(force=True)
        return super().switch_to_foreground()

    def switch_to_background(self):
        self._restore_usb_debug()
        self.page = None
        return super().switch_to_background()

    def run_foreground(self):
        self._read_usb_lines()
        if self.badge.keyboard.f5():
            self.switch_to_background()
            return
        self._refresh()

    def receive_message(self, message):
        if message.source == MY_ADDRESS:
            return
        try:
            channel, alias_bytes, text_bytes = message.payload
        except Exception:
            return

        topic = channel % 100
        alias = self._decode(alias_bytes) or ("%08x" % message.source)[-8:]
        text = self._decode(text_bytes)
        rssi = self.badge.lora.get_rssi()
        snr = self.badge.lora.get_snr()
        self.rx_count += 1
        self._print(
            "RX\t%d\t%08x\t%s\t%s\t%.0f\t%.1f"
            % (
                topic,
                message.source,
                self._clean_field(alias),
                self._clean_field(text),
                rssi,
                snr,
            )
        )
        if topic == self.active_topic:
            self._add_row(alias[:10], text)

    def _read_usb_lines(self):
        while self.poll.poll(0):
            try:
                char = sys.stdin.read(1)
            except UnicodeError:
                continue
            if not char:
                return
            if char == "\r":
                continue
            if char == "\n":
                line = self.line_buffer
                self.line_buffer = ""
                self._handle_line(line.strip())
            else:
                self.line_buffer += char
                if len(self.line_buffer) > MAX_SERIAL_LINE_LEN:
                    self.line_buffer = ""
                    self._print("ERR\tline too long")

    def _handle_line(self, line):
        if not line:
            return
        parts = line.split("\t", 2)
        command = parts[0].upper()
        if command == "PING":
            self._print("PONG\t%s\t%d" % (self._my_alias(), self.active_topic))
            return
        if command == "TOPIC":
            if len(parts) < 2:
                self._print("ERR\tmissing topic")
                return
            self._set_topic(parts[1])
            return
        if command == "SEND":
            if len(parts) < 3:
                self._print("ERR\tusage SEND<TAB>topic<TAB>message")
                return
            self._set_topic(parts[1])
            self._send_chat(parts[2])
            return
        self._print("ERR\tunknown command")

    def _send_chat(self, text):
        text = text.strip()
        if not text:
            self._print("ERR\tempty message")
            return
        chunks = self._split_text_for_chat(text)
        alias = self._my_alias()
        channel = 100 + self.active_topic
        try:
            ttl = int(self.badge.config.get("chat_ttl", b"3"))
        except Exception:
            ttl = 3

        for chunk in chunks:
            text_bytes = chunk.encode()
            send(
                NetworkFrame().set_fields(
                    protocol=TEXT_CHAT,
                    destination=BROADCAST_ADDRESS,
                    ttl=ttl,
                    payload=(channel, alias.encode()[:10], text_bytes),
                )
            )
            self.tx_count += 1
            self._print("TX\t%d\t%s\t%s" % (self.active_topic, alias, self._clean_field(chunk)))
            self._add_row(alias[:10], chunk)

    def _set_topic(self, topic_text):
        try:
            self.active_topic = max(1, min(99, int(topic_text)))
        except ValueError:
            self._print("ERR\tbad topic")
            return
        self._print("TOPIC\t%d" % self.active_topic)
        self._refresh(force=True)

    def _refresh(self, force=False):
        if self.page is None:
            return
        self.page.infobar_left.set_text(self._left_info())
        self.page.infobar_right.set_text("TX %d  RX %d" % (self.tx_count, self.rx_count))
        if force:
            self.page.populate_message_rows(self.rows or [("pc", self.last_status)])

    def _add_row(self, left, right):
        self.rows.append((left, right))
        self.rows = self.rows[-6:]
        if self.page is not None:
            self.page.populate_message_rows(self.rows)

    def _left_info(self):
        return "Topic %02d  %08x  %s" % (self.active_topic, MY_ADDRESS, self._my_alias())

    def _my_alias(self):
        try:
            alias = self.badge.config.get("alias").decode().strip()
        except Exception:
            alias = ""
        if not alias:
            alias = ("%08x" % MY_ADDRESS)[-8:]
        return alias[:10]

    def _decode(self, value):
        if isinstance(value, bytes):
            return value.strip(b"\0").decode()
        return str(value).strip()

    def _split_text_for_chat(self, text):
        if len(text.encode()) <= SAFE_MESSAGE_LEN:
            return [text]

        total = max(2, (len(text.encode()) + SAFE_MESSAGE_LEN - 1) // SAFE_MESSAGE_LEN)
        for _ in range(10):
            parts = self._chunk_for_total(text, total)
            if len(parts) == total:
                return [
                    "%d/%d %s" % (index + 1, total, part)
                    for index, part in enumerate(parts)
                ]
            total = len(parts)

        parts = self._chunk_for_total(text, total)
        return [
            "%d/%d %s" % (index + 1, len(parts), part)
            for index, part in enumerate(parts)
        ]

    def _chunk_for_total(self, text, total):
        parts = []
        part = ""
        part_len = 0
        for char in text:
            prefix = "%d/%d " % (len(parts) + 1, total)
            available = SAFE_MESSAGE_LEN - len(prefix.encode())
            char_len = len(char.encode())
            if char_len > available:
                continue
            if part and part_len + char_len > available:
                parts.append(part)
                part = char
                part_len = char_len
            else:
                part += char
                part_len += char_len
        if part:
            parts.append(part)
        return parts

    def _clean_field(self, value):
        return str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")

    def _print(self, line):
        print("PCCHAT\t" + line)

    def _slow_usb_debug(self):
        for app in BaseApp.all_apps:
            if app.name == "USB Debug":
                self.usb_debug_app = app
                self.usb_debug_sleep_ms = app.background_sleep_ms
                app.background_sleep_ms = 3600000
                return

    def _restore_usb_debug(self):
        if self.usb_debug_app is not None and self.usb_debug_sleep_ms is not None:
            self.usb_debug_app.background_sleep_ms = self.usb_debug_sleep_ms
        self.usb_debug_app = None
        self.usb_debug_sleep_ms = None
