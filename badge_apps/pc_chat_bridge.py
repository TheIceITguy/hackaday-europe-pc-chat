"""USB companion bridge for the Hackaday Europe chat protocol."""

import os
import select
import struct
import sys
import time

import lvgl

from apps.base_app import BaseApp
from net.net import BROADCAST_ADDRESS, MY_ADDRESS, register_receiver, send
from net.protocols import NetworkFrame, Protocol
from ui import styles
from ui.page import Page

try:
    import bluetooth
except ImportError:
    bluetooth = None


APP_NAME = "PC Chat"

MAX_MESSAGE_LEN = 100
SAFE_MESSAGE_LEN = 60
MAX_SERIAL_LINE_LEN = 1200
RADIO_PACKET_INTERVAL_MS = 4000
BLE_NAME_PREFIX = "LC26-"
TEXT_CHAT = Protocol(port=6, name="TEXT_CHAT", structdef="!H10s%ds" % MAX_MESSAGE_LEN)

BLE_UART_UUID = bluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E") if bluetooth else None
BLE_UART_TX = bluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E") if bluetooth else None
BLE_UART_RX = bluetooth.UUID("6E400002-B5A3-F393-E0A9-E50E24DCCA9E") if bluetooth else None
BLE_NOTIFY_CHUNK = 20
BLE_WRITE_BUFFER = 256

_IRQ_CENTRAL_CONNECT = 1
_IRQ_CENTRAL_DISCONNECT = 2
_IRQ_GATTS_WRITE = 3
_FLAG_READ = 0x0002
_FLAG_WRITE = 0x0008
_FLAG_NOTIFY = 0x0010
_ADV_TYPE_FLAGS = 0x01
_ADV_TYPE_NAME = 0x09


class App(BaseApp):
    """Send and receive normal badge chat messages over USB serial or BLE."""

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
        self.compose_active = False
        self.topic_picker_active = False
        self.show_all_topics = False
        self.auto_follow = True
        self.last_radio_send_ms = self._ticks_ms() - RADIO_PACKET_INTERVAL_MS
        self.radio_packet_interval_ms = RADIO_PACKET_INTERVAL_MS
        self.usb_debug_sleep_ms = None
        self.usb_debug_app = None
        self.ble = None
        self.ble_name = BLE_NAME_PREFIX + ("%08x" % MY_ADDRESS)[-8:]
        self.ble_pair_code = self._new_pair_code()
        self.ble_conn_handle = None
        self.ble_tx_handle = None
        self.ble_rx_handle = None
        self.ble_line_buffer = ""
        self.ble_pending_lines = []
        self.ble_paired = False
        self.ble_status = "BLE off"
        self.ble_notice = None

    def start(self):
        super().start()
        register_receiver(TEXT_CHAT, self.receive_message)
        self._start_ble()

    def switch_to_foreground(self):
        self._slow_usb_debug()
        self._start_ble()
        self.compose_active = False
        self.topic_picker_active = False
        self.page = Page()
        self.page.create_infobar((self._left_info(), "PC Chat"))
        self.page.create_content()
        self.page.add_message_rows(1, 80)
        self.page.create_menubar(["Post", self._filter_button_label(), "Latest", "Topic", "Home"])
        self._apply_colors()
        self.page.replace_screen()
        self._print("READY\t%s\t%d" % (self._my_alias(), self.active_topic))
        self._add_row("ready", "F1 Post  F2 All  F4 Topic")
        self._add_row("ble", self._ble_display_status())
        self._refresh(force=True)
        return super().switch_to_foreground()

    def switch_to_background(self):
        self._restore_usb_debug()
        self.page = None
        return super().switch_to_background()

    def run_foreground(self):
        self._read_usb_lines()
        self._read_ble_lines()
        self._flush_ble_notice()

        if self.compose_active:
            self._run_compose()
            self._refresh()
            return

        if self.topic_picker_active:
            self._run_topic_picker()
            self._refresh()
            return

        if self.badge.keyboard.f5():
            self.switch_to_background()
            return

        key = self.badge.keyboard.read_key()
        scroll_amount = 13
        if self.badge.keyboard.shift_pressed:
            scroll_amount *= 5
        if key == self.badge.keyboard.UP:
            self.page.scroll_up(scroll_amount)
            self.auto_follow = False
        elif key == self.badge.keyboard.DOWN:
            self.page.scroll_down(scroll_amount)
            self.auto_follow = False
        elif key == self.badge.keyboard.LEFT:
            self._set_topic(str(self.active_topic - 1))
        elif key == self.badge.keyboard.RIGHT:
            self._set_topic(str(self.active_topic + 1))

        if self.badge.keyboard.f1():
            self._start_compose()
            return
        if self.badge.keyboard.f2():
            self._toggle_topic_filter()
            return
        if self.badge.keyboard.f3():
            self.auto_follow = True
            if self.page is not None:
                self.page.scroll_bottom()
        if self.badge.keyboard.f4():
            self._start_topic_picker()
            return

        if self.auto_follow and self.page is not None:
            self.page.scroll_bottom()
        self._refresh()

    def _start_compose(self):
        if self.page is None:
            return
        self.page.create_text_box(char_limit=MAX_MESSAGE_LEN)
        self.compose_active = True
        self.page.infobar_right.set_text("F1 send  Esc cancel")

    def _run_compose(self):
        key, text = self.page.text_box_type(self.badge.keyboard)
        self.page.infobar_right.set_text("%d/%d  F1 send" % (len(text), MAX_MESSAGE_LEN))
        if self.badge.keyboard.escape_pressed:
            self.page.close_text_box()
            self.compose_active = False
            return
        if self.badge.keyboard.f1() or key == self.badge.keyboard.ENTER:
            message_text = self.page.close_text_box()
            self.compose_active = False
            if message_text:
                self._send_chat(message_text)

    def _start_topic_picker(self):
        if self.page is None:
            return
        self.page.create_text_box(default_text=str(self.active_topic), one_line=True, char_limit=2)
        self.topic_picker_active = True
        self.page.infobar_left.set_text("Enter topic 1-99")
        self.page.infobar_right.set_text("F4 set")

    def _run_topic_picker(self):
        key, text = self.page.text_box_type(self.badge.keyboard)
        self.page.infobar_right.set_text("%d/2  F4 set" % len(text))
        if self.badge.keyboard.escape_pressed:
            self.page.close_text_box()
            self.topic_picker_active = False
            return
        if self.badge.keyboard.f4() or key == self.badge.keyboard.ENTER:
            topic_text = self.page.close_text_box()
            self.topic_picker_active = False
            self._set_topic(topic_text)

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
        if self.show_all_topics or topic == self.active_topic:
            self._add_chat_row(topic, alias, text)

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

    def _handle_line(self, line, source="usb"):
        if not line:
            return
        parts = line.split("\t", 2)
        command = parts[0].upper()
        if command == "PAIR":
            self._handle_pair(parts, source)
            return
        if source == "ble" and not self.ble_paired:
            self._ble_notify("PCCHAT\tPAIR\tREQUIRED", allow_unpaired=True)
            return
        if command == "PING":
            self._print("PONG\t%s\t%d" % (self._my_alias(), self.active_topic))
            return
        if command == "TOPIC":
            if len(parts) < 2:
                self._print("ERR\tmissing topic")
                return
            self._set_topic(parts[1])
            return
        if command == "GAP":
            if len(parts) < 2:
                self._print("ERR\tmissing gap")
                return
            self._set_gap(parts[1])
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
            self._wait_for_radio_slot()
            text_bytes = chunk.encode()
            send(
                NetworkFrame().set_fields(
                    protocol=TEXT_CHAT,
                    destination=BROADCAST_ADDRESS,
                    ttl=ttl,
                    payload=(channel, alias.encode()[:10], text_bytes),
                )
            )
            self.last_radio_send_ms = self._ticks_ms()
            self.tx_count += 1
            self._print("TX\t%d\t%s\t%s" % (self.active_topic, alias, self._clean_field(chunk)))
            self._add_chat_row(self.active_topic, alias, chunk)

    def _set_topic(self, topic_text):
        try:
            self.active_topic = max(1, min(99, int(topic_text)))
        except ValueError:
            self._print("ERR\tbad topic")
            return
        self._print("TOPIC\t%d" % self.active_topic)
        self._refresh(force=True)

    def _toggle_topic_filter(self):
        self.show_all_topics = not self.show_all_topics
        if self.show_all_topics:
            self._add_row("filter", "showing all topics")
        else:
            self._add_row("filter", "showing topic %02d" % self.active_topic)
        self._refresh_filter_button()
        self._refresh(force=True)

    def _set_gap(self, gap_text):
        try:
            seconds = float(gap_text)
        except ValueError:
            self._print("ERR\tbad gap")
            return
        seconds = max(1.0, min(15.0, seconds))
        self.radio_packet_interval_ms = int(seconds * 1000)
        self._print("GAP\t%.1f" % seconds)
        self._refresh(force=True)

    def _refresh(self, force=False):
        if self.page is None:
            return
        self.page.infobar_left.set_text(self._left_info())
        self.page.infobar_right.set_text("TX %d  RX %d" % (self.tx_count, self.rx_count))
        if force:
            self.page.populate_message_rows(self.rows or [("pc", self.last_status)])

    def _add_chat_row(self, topic, alias, text):
        if self.show_all_topics:
            self._add_row("T%02d %s" % (topic, alias[:6]), text)
        else:
            self._add_row(alias[:10], text)

    def _add_row(self, left, right):
        self.rows.append((left, right))
        self.rows = self.rows[-6:]
        if self.page is not None:
            self.page.populate_message_rows(self.rows)

    def _filter_button_label(self):
        return "Topic" if self.show_all_topics else "All"

    def _refresh_filter_button(self):
        if self.page is None:
            return
        try:
            self.page.set_menubar_button_label(1, self._filter_button_label())
        except Exception:
            pass

    def _apply_colors(self):
        if self.page is None:
            return
        dark = lvgl.color_hex(0x101315)
        panel = lvgl.color_hex(0x182025)
        blue = lvgl.color_hex(0x66a6ff)
        green = lvgl.color_hex(0x65d39b)
        line = lvgl.color_hex(0x384047)
        try:
            self.page.scr.set_style_bg_color(dark, 0)
            self.page.flex_container.set_style_bg_color(dark, 0)
            self.page.infobar.set_style_bg_color(styles.hackaday_grey, 0)
            self.page.infobar_left.set_style_text_color(styles.hackaday_yellow, 0)
            self.page.infobar_right.set_style_text_color(green, 0)
            self.page.content.set_style_bg_color(dark, 0)
            self.page.message_rows.set_style_bg_color(panel, 0)
            self.page.message_rows.set_style_bg_color(panel, lvgl.PART.ITEMS)
            self.page.message_rows.set_style_text_color(styles.hackaday_white, lvgl.PART.ITEMS)
            self.page.message_rows.set_style_border_color(line, lvgl.PART.ITEMS)
            self.page.menubar.set_style_bg_color(styles.hackaday_grey, 0)
            for button in self.page.menubar_buttons:
                button.set_style_bg_color(styles.hackaday_grey, 0)
                button.set_style_text_color(styles.hackaday_yellow, 0)
                button.get_child(0).set_style_text_color(styles.hackaday_yellow, 0)
            self.page.menubar_buttons[1].get_child(0).set_style_text_color(blue, 0)
        except Exception:
            pass

    def _left_info(self):
        if self.show_all_topics:
            return "All topics  BLE %s" % self.ble_pair_code
        return "Topic %02d  BLE %s" % (self.active_topic, self.ble_pair_code)

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

    def _ticks_ms(self):
        try:
            return time.ticks_ms()
        except AttributeError:
            return int(time.time() * 1000)

    def _ticks_diff(self, newer, older):
        try:
            return time.ticks_diff(newer, older)
        except AttributeError:
            return newer - older

    def _sleep_ms(self, delay_ms):
        try:
            time.sleep_ms(delay_ms)
        except AttributeError:
            time.sleep(delay_ms / 1000)

    def _wait_for_radio_slot(self):
        elapsed = self._ticks_diff(self._ticks_ms(), self.last_radio_send_ms)
        delay = self.radio_packet_interval_ms - elapsed
        if delay > 0:
            if self.page is not None:
                self.page.infobar_right.set_text("wait %.1fs" % (delay / 1000))
            self._sleep_ms(delay)

    def _print(self, line):
        packet = "PCCHAT\t" + line
        print(packet)
        self._ble_notify(packet)

    def _new_pair_code(self):
        value = (MY_ADDRESS ^ self._ticks_ms()) % 1000000
        try:
            random_bytes = os.urandom(4)
            random_value = 0
            for byte in random_bytes:
                random_value = (random_value << 8) | byte
            value = (value ^ random_value) % 1000000
        except Exception:
            pass
        return "%06d" % value

    def _handle_pair(self, parts, source):
        if source != "ble":
            self._print("PAIR\tUSB")
            return
        if len(parts) < 2:
            self._ble_notify("PCCHAT\tERR\tmissing pair code", allow_unpaired=True)
            return
        code = parts[1].strip()
        if code == self.ble_pair_code:
            self.ble_paired = True
            self.ble_status = "BLE paired"
            self._ble_notify("PCCHAT\tPAIR\tOK", allow_unpaired=True)
            self._ble_notify(
                "PCCHAT\tREADY\t%s\t%d" % (self._my_alias(), self.active_topic),
                allow_unpaired=True,
            )
            self._add_row("ble", "paired with computer")
            self._refresh(force=True)
            return
        self.ble_paired = False
        self._ble_notify("PCCHAT\tERR\tbad pair code", allow_unpaired=True)
        self._add_row("ble", "bad pair code")

    def _start_ble(self):
        if self.ble is not None:
            return
        if bluetooth is None:
            self.ble_status = "BLE unavailable"
            return
        try:
            self.ble = bluetooth.BLE()
            self.ble.active(True)
            try:
                self.ble.config(gap_name=self.ble_name)
            except Exception:
                pass
            service = (
                BLE_UART_UUID,
                (
                    (BLE_UART_TX, _FLAG_READ | _FLAG_NOTIFY),
                    (BLE_UART_RX, _FLAG_WRITE),
                ),
            )
            ((self.ble_tx_handle, self.ble_rx_handle),) = self.ble.gatts_register_services((service,))
            try:
                self.ble.gatts_set_buffer(self.ble_rx_handle, BLE_WRITE_BUFFER)
            except Exception:
                pass
            self.ble.irq(self._ble_irq)
            self._ble_advertise()
            self.ble_status = "BLE code %s" % self.ble_pair_code
        except Exception as exc:
            self.ble_status = "BLE failed"
            print("PCCHAT\tERR\tBLE %s" % self._clean_field(exc))
            self.ble = None

    def _ble_irq(self, event, data):
        if event == _IRQ_CENTRAL_CONNECT:
            self.ble_conn_handle = data[0]
            self.ble_paired = False
            self.ble_status = "BLE connected"
            self.ble_notice = "enter code %s" % self.ble_pair_code
            self._ble_notify("PCCHAT\tPAIR\tREQUIRED", allow_unpaired=True)
            return
        if event == _IRQ_CENTRAL_DISCONNECT:
            self.ble_conn_handle = None
            self.ble_paired = False
            self.ble_line_buffer = ""
            self.ble_status = "BLE code %s" % self.ble_pair_code
            self.ble_notice = "disconnected"
            self._ble_advertise()
            return
        if event == _IRQ_GATTS_WRITE and self.ble is not None:
            try:
                conn_handle, value_handle = data
            except Exception:
                return
            if value_handle != self.ble_rx_handle:
                return
            try:
                chunk = self.ble.gatts_read(self.ble_rx_handle).decode()
            except Exception:
                return
            self.ble_line_buffer += chunk
            while "\n" in self.ble_line_buffer:
                line, self.ble_line_buffer = self.ble_line_buffer.split("\n", 1)
                self.ble_pending_lines.append(line.strip())

    def _read_ble_lines(self):
        while self.ble_pending_lines:
            line = self.ble_pending_lines.pop(0)
            self._handle_line(line, source="ble")

    def _ble_notify(self, line, allow_unpaired=False):
        if self.ble is None or self.ble_conn_handle is None or self.ble_tx_handle is None:
            return
        if not self.ble_paired and not allow_unpaired:
            return
        payload = (line + "\n").encode()
        for offset in range(0, len(payload), BLE_NOTIFY_CHUNK):
            try:
                self.ble.gatts_notify(
                    self.ble_conn_handle,
                    self.ble_tx_handle,
                    payload[offset : offset + BLE_NOTIFY_CHUNK],
                )
            except Exception:
                return
            self._sleep_ms(10)

    def _flush_ble_notice(self):
        if self.ble_notice:
            self._add_row("ble", self.ble_notice)
            self.ble_notice = None
            self._refresh(force=True)

    def _ble_advertise(self):
        if self.ble is None:
            return
        payload = self._ble_advertising_payload(self.ble_name)
        try:
            self.ble.gap_advertise(100000, adv_data=payload)
        except Exception:
            self.ble_status = "BLE advertise failed"

    def _ble_advertising_payload(self, name):
        payload = bytearray()

        def append(adv_type, value):
            payload.extend(struct.pack("BB", len(value) + 1, adv_type))
            payload.extend(value)

        append(_ADV_TYPE_FLAGS, b"\x06")
        append(_ADV_TYPE_NAME, name.encode()[:24])
        return payload

    def _ble_display_status(self):
        if bluetooth is None:
            return "not in firmware"
        if self.ble_conn_handle is None:
            return "%s waiting" % self.ble_name
        if self.ble_paired:
            return "paired"
        return "code %s" % self.ble_pair_code

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
