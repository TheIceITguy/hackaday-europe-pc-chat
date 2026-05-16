#!/usr/bin/env python3
"""Terminal companion for the Hackaday Europe badge PC Chat bridge."""

from __future__ import annotations

import argparse
import glob
import queue
import sys
import threading
import time

try:
    import serial
except ImportError as exc:  # pragma: no cover
    raise SystemExit("pyserial is required: python3 -m pip install pyserial") from exc


# The radio protocol can carry 100 bytes of chat text, but long unbroken
# strings like URLs are easier for the stock badge UI and mesh if fragmented.
MAX_CHAT_BYTES = 60
MAX_OUTBOUND_TEXT_BYTES = 1200
CHUNK_SEND_DELAY_S = 0.12


def find_port() -> str | None:
    ports = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    return ports[0] if ports else None


def split_text_for_chat(text: str, byte_limit: int = MAX_CHAT_BYTES) -> list[str]:
    if len(text.encode("utf-8")) <= byte_limit:
        return [text]

    total = max(2, (len(text.encode("utf-8")) + byte_limit - 1) // byte_limit)
    for _ in range(10):
        parts = _chunk_for_total(text, total, byte_limit)
        if len(parts) == total:
            return [f"{index + 1}/{total} {part}" for index, part in enumerate(parts)]
        total = len(parts)

    parts = _chunk_for_total(text, total, byte_limit)
    return [f"{index + 1}/{len(parts)} {part}" for index, part in enumerate(parts)]


def _chunk_for_total(text: str, total: int, byte_limit: int) -> list[str]:
    parts: list[str] = []
    part = ""
    part_len = 0
    for char in text:
        prefix = f"{len(parts) + 1}/{total} "
        available = byte_limit - len(prefix.encode("utf-8"))
        char_len = len(char.encode("utf-8"))
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


def reader(ser: serial.Serial, lines: queue.Queue[str]) -> None:
    while True:
        try:
            raw = ser.readline()
        except Exception as exc:
            lines.put(f"[serial read stopped: {exc}]")
            return
        if not raw:
            continue
        text = raw.decode("utf-8", "replace").strip()
        if text:
            lines.put(text)


def format_bridge_line(line: str) -> str | None:
    if not line.startswith("PCCHAT\t"):
        return None
    parts = line.split("\t")
    kind = parts[1] if len(parts) > 1 else ""
    if kind == "READY" and len(parts) >= 4:
        return f"[bridge ready: alias {parts[2]}, topic {parts[3]}]"
    if kind == "PONG" and len(parts) >= 4:
        return f"[badge online: alias {parts[2]}, topic {parts[3]}]"
    if kind == "TOPIC" and len(parts) >= 3:
        return f"[topic set to {parts[2]}]"
    if kind == "TX" and len(parts) >= 5:
        return f"[sent topic {parts[2]}] <{parts[3]}> {parts[4]}"
    if kind == "RX" and len(parts) >= 8:
        return f"[topic {parts[2]} rssi {parts[6]} snr {parts[7]}] <{parts[4]}> {parts[5]}"
    if kind == "ERR" and len(parts) >= 3:
        return f"[badge error: {parts[2]}]"
    return f"[bridge] {line[7:]}"


def drain(lines: queue.Queue[str]) -> None:
    while True:
        try:
            line = lines.get_nowait()
        except queue.Empty:
            return
        formatted = format_bridge_line(line)
        if formatted:
            print(formatted, flush=True)


def send_line(ser: serial.Serial, line: str) -> None:
    ser.write((line + "\n").encode("utf-8"))
    ser.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="Chat through a Hackaday Europe badge over USB.")
    parser.add_argument("port", nargs="?", help="Serial port, for example /dev/ttyACM0")
    parser.add_argument("-t", "--topic", type=int, default=1, help="Chat topic 1-99, default 1")
    args = parser.parse_args()

    port = args.port or find_port()
    if not port:
        print("No badge serial port found. Plug it in and try /dev/ttyACM0.", file=sys.stderr)
        return 2

    topic = max(1, min(99, args.topic))
    print(f"Connecting to {port}. On the badge, open Apps -> PC Chat.")
    print("Type messages and press Enter. Commands: /topic N, /quit")

    with serial.Serial(port, 115200, timeout=0.1, write_timeout=1) as ser:
        lines: queue.Queue[str] = queue.Queue()
        thread = threading.Thread(target=reader, args=(ser, lines), daemon=True)
        thread.start()
        time.sleep(0.2)
        send_line(ser, "PING")
        send_line(ser, f"TOPIC\t{topic}")

        while True:
            drain(lines)
            try:
                text = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            drain(lines)
            if not text:
                continue
            if text in ("/q", "/quit", "/exit"):
                return 0
            if text.startswith("/topic "):
                try:
                    topic = max(1, min(99, int(text.split(None, 1)[1])))
                except ValueError:
                    print("[bad topic]")
                    continue
                send_line(ser, f"TOPIC\t{topic}")
                continue
            clean = text.replace("\t", " ")
            if len(clean.encode("utf-8")) > MAX_OUTBOUND_TEXT_BYTES:
                print(f"[message too long; keep it under {MAX_OUTBOUND_TEXT_BYTES} bytes]")
                continue
            chunks = split_text_for_chat(clean)
            if len(chunks) > 1:
                print(f"[splitting message into {len(chunks)} packets]")
            for index, chunk in enumerate(chunks):
                send_line(ser, f"SEND\t{topic}\t{chunk}")
                if index < len(chunks) - 1:
                    time.sleep(CHUNK_SEND_DELAY_S)


if __name__ == "__main__":
    raise SystemExit(main())
