#!/usr/bin/env python3
"""Local browser UI for the Hackaday Europe badge PC Chat bridge."""

from __future__ import annotations

import argparse
import glob
import json
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

try:
    import serial
except ImportError as exc:  # pragma: no cover
    raise SystemExit("pyserial is required: python3 -m pip install pyserial") from exc


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
# The radio protocol can carry 100 bytes of chat text, but long unbroken
# strings like URLs are easier for the stock badge UI and mesh if fragmented.
MAX_CHAT_BYTES = 60
MAX_OUTBOUND_TEXT_BYTES = 1200
CHUNK_SEND_DELAY_S = 0.12


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PC Chat</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #141719;
      --panel: #1d2226;
      --panel-2: #252b30;
      --line: #384047;
      --text: #eef1f2;
      --muted: #aeb7bd;
      --accent: #65d39b;
      --accent-2: #82b7ff;
      --warn: #f2bd68;
      --bad: #ff7b7b;
      --shadow: rgba(0, 0, 0, .28);
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 15px/1.4 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
      overflow: hidden;
    }
    button, input {
      font: inherit;
      letter-spacing: 0;
    }
    .app {
      height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr auto;
      overflow: hidden;
    }
    header {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 16px;
      align-items: center;
      padding: 14px 18px;
      border-bottom: 1px solid var(--line);
      background: #191d20;
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
    }
    .subtitle {
      color: var(--muted);
      font-size: 12px;
      margin-top: 2px;
    }
    .status-bar {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .pill {
      min-height: 28px;
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 6px;
      padding: 4px 9px;
      color: var(--muted);
      white-space: nowrap;
      max-width: 260px;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .pill strong {
      color: var(--text);
      font-weight: 600;
      margin-left: 5px;
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      display: inline-block;
      margin-right: 7px;
      background: var(--warn);
      box-shadow: 0 0 0 3px rgba(242, 189, 104, .12);
      flex: 0 0 auto;
    }
    .dot.connected {
      background: var(--accent);
      box-shadow: 0 0 0 3px rgba(101, 211, 155, .12);
    }
    main {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 280px;
      min-height: 0;
      overflow: hidden;
    }
    .chat {
      min-width: 0;
      min-height: 0;
      display: grid;
      grid-template-rows: 1fr;
      border-right: 1px solid var(--line);
      overflow: hidden;
    }
    #messages {
      min-height: 0;
      overflow-y: auto;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 10px;
      scroll-behavior: smooth;
    }
    .message {
      width: min(780px, 92%);
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      padding: 10px 12px;
      box-shadow: 0 8px 24px var(--shadow);
    }
    .message.out {
      align-self: flex-end;
      border-color: rgba(101, 211, 155, .48);
      background: #1f2b27;
    }
    .message.status {
      align-self: center;
      width: min(620px, 92%);
      background: #24282b;
      box-shadow: none;
    }
    .meta {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 5px;
    }
    .sender {
      color: var(--accent-2);
      font-weight: 650;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .message.out .sender { color: var(--accent); }
    .text {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    aside {
      min-width: 0;
      padding: 16px;
      background: #171b1e;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .section-title {
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0;
      margin-bottom: 8px;
    }
    .topic-row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8px;
    }
    .quick-topics {
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 6px;
      margin-top: 8px;
    }
    .topic-chip {
      min-height: 32px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      color: var(--muted);
      cursor: pointer;
      padding: 4px 0;
      font-weight: 650;
    }
    .topic-chip.active {
      border-color: rgba(101, 211, 155, .62);
      background: #244936;
      color: var(--text);
    }
    .toggle-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 10px;
      color: var(--muted);
      font-size: 13px;
    }
    .toggle-row input {
      width: 16px;
      height: 16px;
      accent-color: var(--accent);
    }
    .field {
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      color: var(--text);
      padding: 8px 10px;
      outline: none;
    }
    .field:focus {
      border-color: var(--accent-2);
      box-shadow: 0 0 0 3px rgba(130, 183, 255, .12);
    }
    .button {
      min-height: 38px;
      border: 1px solid #579874;
      border-radius: 6px;
      background: #27533f;
      color: var(--text);
      padding: 8px 12px;
      cursor: pointer;
      font-weight: 650;
    }
    .button.secondary {
      background: var(--panel-2);
      border-color: var(--line);
      color: var(--muted);
    }
    .button:hover { filter: brightness(1.08); }
    .button:disabled {
      cursor: not-allowed;
      opacity: .55;
    }
    .facts {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    .fact {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 9px;
      background: var(--panel);
      min-width: 0;
    }
    .fact span {
      display: block;
      color: var(--muted);
      font-size: 12px;
    }
    .fact strong {
      display: block;
      margin-top: 2px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .fact.status-fact {
      grid-column: 1 / -1;
    }
    .fact.status-fact strong {
      white-space: normal;
      overflow-wrap: anywhere;
    }
    footer {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 10px;
      padding: 12px 16px;
      border-top: 1px solid var(--line);
      background: #191d20;
    }
    #composer {
      min-height: 42px;
    }
    @media (max-width: 760px) {
      header {
        grid-template-columns: 1fr;
      }
      .status-bar {
        justify-content: flex-start;
      }
      main {
        grid-template-columns: 1fr;
        grid-template-rows: 1fr auto;
      }
      .chat {
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }
      aside {
        padding: 12px 16px;
      }
      footer {
        grid-template-columns: 1fr;
      }
      .message {
        width: 100%;
      }
    }
  </style>
</head>
<body>
  <div class="app">
    <header>
      <div>
        <h1>PC Chat</h1>
        <div class="subtitle">Hackaday Europe badge radio bridge</div>
      </div>
      <div class="status-bar">
        <div class="pill"><span id="dot" class="dot"></span><span id="status">Starting</span></div>
        <div class="pill">Topic <strong id="topicBadge">01</strong></div>
        <div class="pill">Alias <strong id="aliasBadge">unknown</strong></div>
      </div>
    </header>
    <main>
      <section class="chat">
        <div id="messages" aria-live="polite"></div>
      </section>
      <aside>
        <div>
          <div class="section-title">Topic</div>
          <form id="topicForm" class="topic-row">
            <input id="topicInput" class="field" type="number" min="1" max="99" value="1">
            <button class="button secondary" type="submit">Set</button>
          </form>
          <div class="quick-topics" id="quickTopics">
            <button class="topic-chip active" type="button" data-topic="1">01</button>
            <button class="topic-chip" type="button" data-topic="2">02</button>
            <button class="topic-chip" type="button" data-topic="3">03</button>
            <button class="topic-chip" type="button" data-topic="4">04</button>
            <button class="topic-chip" type="button" data-topic="5">05</button>
          </div>
          <label class="toggle-row">
            <input id="showAllTopics" type="checkbox">
            <span>Show all topics</span>
          </label>
        </div>
        <div>
          <div class="section-title">Badge</div>
          <div class="facts">
            <div class="fact"><span>Port</span><strong id="portValue">-</strong></div>
            <div class="fact"><span>Mode</span><strong>Local</strong></div>
            <div class="fact"><span>TX</span><strong id="txValue">0</strong></div>
            <div class="fact"><span>RX</span><strong id="rxValue">0</strong></div>
            <div class="fact status-fact"><span>Status</span><strong id="statusDetail">Starting</strong></div>
          </div>
        </div>
        <div>
          <div class="section-title">Last Signal</div>
          <div class="facts">
            <div class="fact"><span>RSSI</span><strong id="rssiValue">-</strong></div>
            <div class="fact"><span>SNR</span><strong id="snrValue">-</strong></div>
          </div>
        </div>
      </aside>
    </main>
    <footer>
      <input id="composer" class="field" maxlength="1200" autocomplete="off" placeholder="Write a badge chat message">
      <button id="sendButton" class="button" type="button">Send</button>
    </footer>
  </div>
  <script>
    const el = (id) => document.getElementById(id);
    const messages = el("messages");
    const state = { topic: 1, connected: false, tx: 0, rx: 0, showAll: false };
    const allMessages = [];

    function two(n) {
      const value = Number(n || 1);
      return String(Math.max(1, Math.min(99, value))).padStart(2, "0");
    }

    function appendMessage(data) {
      allMessages.push(data);
      if (allMessages.length > 500) allMessages.shift();
      renderMessages();
    }

    function makeMessageRow(data) {
      const row = document.createElement("div");
      row.className = "message " + (data.direction || "in");
      const meta = document.createElement("div");
      meta.className = "meta";
      const sender = document.createElement("span");
      sender.className = "sender";
      sender.textContent = data.alias || data.source || "badge";
      const detail = document.createElement("span");
      detail.textContent = "topic " + two(data.topic) + (data.rssi ? "  " + data.rssi + " dBm" : "");
      const text = document.createElement("div");
      text.className = "text";
      text.textContent = data.text || "";
      meta.append(sender, detail);
      row.append(meta, text);
      return row;
    }

    function renderMessages() {
      messages.replaceChildren();
      const activeTopic = Number(state.topic || 1);
      const visible = state.showAll
        ? allMessages
        : allMessages.filter((message) => Number(message.topic || 1) === activeTopic);
      if (!visible.length) {
        const row = document.createElement("div");
        row.className = "message status";
        const body = document.createElement("div");
        body.className = "text";
        body.textContent = state.showAll ? "No messages heard yet." : "No messages on topic " + two(activeTopic) + " yet.";
        row.appendChild(body);
        messages.appendChild(row);
      } else {
        visible.forEach((message) => messages.appendChild(makeMessageRow(message)));
      }
      scrollToBottom();
    }

    function appendStatus(text) {
      el("statusDetail").textContent = text || "";
    }

    function scrollToBottom() {
      requestAnimationFrame(() => {
        messages.scrollTop = messages.scrollHeight;
      });
    }

    function applyState(data) {
      const oldTopic = Number(state.topic || 1);
      Object.assign(state, data);
      const statusText = data.connected ? "Connected" : (data.status || "Waiting");
      el("status").textContent = statusText;
      el("statusDetail").textContent = data.connected ? ("Connected to " + (data.port || "badge")) : statusText;
      el("dot").classList.toggle("connected", Boolean(data.connected));
      el("topicBadge").textContent = two(data.topic);
      el("topicInput").value = Number(data.topic || 1);
      el("aliasBadge").textContent = data.alias || "unknown";
      el("portValue").textContent = data.port || "-";
      el("txValue").textContent = String(data.tx || 0);
      el("rxValue").textContent = String(data.rx || 0);
      el("rssiValue").textContent = data.rssi || "-";
      el("snrValue").textContent = data.snr || "-";
      el("sendButton").disabled = !data.connected;
      el("composer").placeholder = "Write a badge chat message to topic " + two(data.topic);
      document.querySelectorAll(".topic-chip").forEach((button) => {
        button.classList.toggle("active", Number(button.dataset.topic) === Number(data.topic || 1));
      });
      if (oldTopic !== Number(data.topic || 1)) renderMessages();
    }

    async function postJSON(path, body) {
      const res = await fetch(path, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body)
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({ error: res.statusText }));
        throw new Error(data.error || res.statusText);
      }
      return res.json();
    }

    async function setTopic(topic) {
      const value = Math.max(1, Math.min(99, Number(topic || 1)));
      el("topicInput").value = value;
      await postJSON("/api/topic", { topic: value });
    }

    el("sendButton").addEventListener("click", async () => {
      const input = el("composer");
      const text = input.value.trim();
      if (!text) return;
      try {
        await postJSON("/api/send", { topic: Number(el("topicInput").value), text });
        input.value = "";
        input.focus();
      } catch (err) {
        appendStatus(err.message);
      }
    });

    el("composer").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        el("sendButton").click();
      }
    });

    el("topicForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await setTopic(el("topicInput").value);
      } catch (err) {
        appendStatus(err.message);
      }
    });

    el("quickTopics").addEventListener("click", async (event) => {
      const button = event.target.closest("[data-topic]");
      if (!button) return;
      try {
        await setTopic(button.dataset.topic);
      } catch (err) {
        appendStatus(err.message);
      }
    });

    el("showAllTopics").addEventListener("change", (event) => {
      state.showAll = event.target.checked;
      renderMessages();
    });

    const events = new EventSource("/api/events");
    events.addEventListener("state", (event) => applyState(JSON.parse(event.data)));
    events.addEventListener("message", (event) => appendMessage(JSON.parse(event.data)));
    events.addEventListener("status", (event) => appendStatus(JSON.parse(event.data).text));
    events.addEventListener("topic", (event) => {
      const data = JSON.parse(event.data);
      applyState(data.state || data);
      appendStatus("Topic set to " + two(data.topic));
      renderMessages();
    });
    events.onerror = () => {
      el("status").textContent = "UI reconnecting";
      el("dot").classList.remove("connected");
    };

    fetch("/api/state")
      .then((res) => res.json())
      .then((data) => {
        applyState(data);
        renderMessages();
      })
      .catch((err) => appendStatus(err.message));
  </script>
</body>
</html>
"""


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


class ChatBridge:
    def __init__(self, port: str | None, topic: int, auto_port: bool = True):
        self.preferred_port = port
        self.port = port or "auto"
        self.auto_port = auto_port
        self.topic = max(1, min(99, topic))
        self.alias = ""
        self.connected = False
        self.status = "Waiting for badge"
        self.tx = 0
        self.rx = 0
        self.last_rssi = ""
        self.last_snr = ""
        self._serial: serial.Serial | None = None
        self._serial_lock = threading.Lock()
        self._events: list[dict[str, Any]] = []
        self._event_seq = 0
        self._event_cond = threading.Condition()
        self._stop = threading.Event()
        self._pending_tx_echoes: list[tuple[int, str]] = []

    def start(self) -> None:
        thread = threading.Thread(target=self._run, name="badge-serial", daemon=True)
        thread.start()

    def state(self) -> dict[str, Any]:
        return {
            "port": self.port,
            "topic": self.topic,
            "alias": self.alias,
            "connected": self.connected,
            "status": self.status,
            "tx": self.tx,
            "rx": self.rx,
            "rssi": self.last_rssi,
            "snr": self.last_snr,
        }

    def send_message(self, topic: int, text: str) -> None:
        topic = max(1, min(99, int(topic)))
        text = text.replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()
        if not text:
            raise ValueError("Message is empty")
        if len(text.encode("utf-8")) > MAX_OUTBOUND_TEXT_BYTES:
            raise ValueError(f"Message is too long; keep it under {MAX_OUTBOUND_TEXT_BYTES} bytes")
        self.topic = topic
        chunks = split_text_for_chat(text)
        if len(chunks) > 1:
            self._add_event("status", {"text": f"Splitting message into {len(chunks)} packets"})
        for index, chunk in enumerate(chunks):
            self._queue_pending_tx(topic, chunk)
            try:
                self._write_line(f"SEND\t{topic}\t{chunk}")
            except Exception:
                self._drop_pending_tx(topic, chunk)
                raise
            self._echo_local_tx(topic, chunk)
            if index < len(chunks) - 1:
                time.sleep(CHUNK_SEND_DELAY_S)

    def set_topic(self, topic: int) -> None:
        self.topic = max(1, min(99, int(topic)))
        if self._serial is None:
            self._add_event("topic", {"topic": self.topic, "state": self.state()})
            return
        self._write_line(f"TOPIC\t{self.topic}")

    def wait_events(self, after: int, timeout: float) -> list[dict[str, Any]]:
        with self._event_cond:
            events = [event for event in self._events if event["id"] > after]
            if events:
                return events
            self._event_cond.wait(timeout)
            return [event for event in self._events if event["id"] > after]

    def add_state_event(self) -> dict[str, Any]:
        return {"id": self._event_seq, "type": "state", "data": self.state()}

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                port = self._select_port()
                if port is None:
                    self._mark_disconnected("No badge serial port found")
                    time.sleep(1.5)
                    continue
                self._connect_and_read(port)
            except Exception as exc:
                self._mark_disconnected(str(exc))
                time.sleep(1.5)

    def _select_port(self) -> str | None:
        if self.preferred_port and (Path(self.preferred_port).exists() or not self.auto_port):
            return self.preferred_port
        return find_port()

    def _connect_and_read(self, port: str) -> None:
        self.port = port
        self.status = "Opening serial port"
        self._add_event("state", self.state())
        with serial.Serial(port, 115200, timeout=0.2, write_timeout=1) as ser:
            with self._serial_lock:
                self._serial = ser
            self.connected = True
            self.status = "Connected"
            self._add_event("status", {"text": f"Connected to {port}"})
            self._add_event("state", self.state())
            self._write_line("PING")
            self._write_line(f"TOPIC\t{self.topic}")

            while not self._stop.is_set():
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    self._handle_serial_line(line)

    def _mark_disconnected(self, reason: str) -> None:
        with self._serial_lock:
            self._serial = None
        was_connected = self.connected
        previous_status = self.status
        self.connected = False
        if self.preferred_port and self.auto_port:
            self.port = self.preferred_port + " (searching)"
        elif not self.preferred_port:
            self.port = "auto"
        self.status = reason
        if was_connected:
            self._add_event("status", {"text": f"Badge disconnected: {reason}"})
        elif reason != previous_status:
            self._add_event("status", {"text": reason})
        self._add_event("state", self.state())

    def _handle_serial_line(self, line: str) -> None:
        if not line.startswith("PCCHAT\t"):
            return
        parts = line.split("\t")
        kind = parts[1] if len(parts) > 1 else ""
        if kind in {"READY", "PONG"} and len(parts) >= 4:
            self.alias = parts[2]
            self.topic = _parse_topic(parts[3], self.topic)
            self.status = "Connected"
            self._add_event("state", self.state())
            return
        if kind == "TOPIC" and len(parts) >= 3:
            self.topic = _parse_topic(parts[2], self.topic)
            self._add_event("topic", {"topic": self.topic, "state": self.state()})
            return
        if kind == "TX" and len(parts) >= 5:
            topic = _parse_topic(parts[2], self.topic)
            text = parts[4]
            if self._is_pending_tx_echo(topic, text):
                return
            self.tx += 1
            self._add_event(
                "message",
                {
                    "direction": "out",
                    "topic": topic,
                    "alias": parts[3],
                    "text": text,
                },
            )
            self._add_event("state", self.state())
            return
        if kind == "RX" and len(parts) >= 8:
            self.rx += 1
            self.last_rssi = parts[6]
            self.last_snr = parts[7]
            self._add_event(
                "message",
                {
                    "direction": "in",
                    "topic": _parse_topic(parts[2], self.topic),
                    "source": parts[3],
                    "alias": parts[4],
                    "text": parts[5],
                    "rssi": parts[6],
                    "snr": parts[7],
                },
            )
            self._add_event("state", self.state())
            return
        if kind == "ERR" and len(parts) >= 3:
            self._add_event("status", {"text": parts[2]})

    def _write_line(self, line: str) -> None:
        with self._serial_lock:
            if self._serial is None:
                raise RuntimeError("Badge serial port is not connected")
            self._serial.write((line + "\n").encode("utf-8"))
            self._serial.flush()

    def _queue_pending_tx(self, topic: int, text: str) -> None:
        self._pending_tx_echoes.append((topic, text))
        self._pending_tx_echoes = self._pending_tx_echoes[-100:]

    def _drop_pending_tx(self, topic: int, text: str) -> None:
        try:
            self._pending_tx_echoes.remove((topic, text))
        except ValueError:
            pass

    def _is_pending_tx_echo(self, topic: int, text: str) -> bool:
        try:
            self._pending_tx_echoes.remove((topic, text))
            return True
        except ValueError:
            return False

    def _echo_local_tx(self, topic: int, text: str) -> None:
        self.tx += 1
        self._add_event(
            "message",
            {
                "direction": "out",
                "topic": topic,
                "alias": self.alias or "me",
                "text": text,
            },
        )
        self._add_event("state", self.state())

    def _add_event(self, event_type: str, data: dict[str, Any]) -> None:
        with self._event_cond:
            self._event_seq += 1
            self._events.append({"id": self._event_seq, "type": event_type, "data": data})
            self._events = self._events[-500:]
            self._event_cond.notify_all()


def _parse_topic(value: str, fallback: int) -> int:
    try:
        return max(1, min(99, int(value)))
    except ValueError:
        return fallback


class ChatHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], bridge: ChatBridge, local_only: bool):
        super().__init__(address, ChatHandler)
        self.bridge = bridge
        self.local_only = local_only


class ChatHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: ChatHTTPServer

    def do_GET(self) -> None:
        if not self._allowed_client():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_bytes(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/state":
            self._send_json(self.server.bridge.state())
            return
        if parsed.path == "/api/events":
            self._send_events(parsed.query)
            return
        self._send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_HEAD(self) -> None:
        if not self._allowed_client():
            return
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = INDEX_HTML.encode("utf-8")
            self._send_headers(HTTPStatus.OK, "text/html; charset=utf-8", len(body))
            return
        if parsed.path == "/api/state":
            body = json.dumps(self.server.bridge.state()).encode("utf-8")
            self._send_headers(HTTPStatus.OK, "application/json", len(body))
            return
        self._send_headers(HTTPStatus.NOT_FOUND, "application/json", 0)

    def do_POST(self) -> None:
        if not self._allowed_client():
            return
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path == "/api/send":
                self.server.bridge.send_message(payload.get("topic", 1), payload.get("text", ""))
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/topic":
                self.server.bridge.set_topic(payload.get("topic", 1))
                self._send_json({"ok": True})
                return
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")
        except Exception as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _allowed_client(self) -> bool:
        if not self.server.local_only:
            return True
        host = self.client_address[0]
        if host in {"127.0.0.1", "::1"}:
            return True
        self._send_error(HTTPStatus.FORBIDDEN, "Local connections only")
        return False

    def _send_events(self, query: str) -> None:
        params = parse_qs(query)
        try:
            after = int(params.get("after", ["0"])[0])
        except ValueError:
            after = 0
        last_id = self.headers.get("Last-Event-ID")
        if last_id:
            try:
                after = max(after, int(last_id))
            except ValueError:
                pass

        self.send_response(HTTPStatus.OK)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "keep-alive")
        self.end_headers()
        if not self._try_write_sse(self.server.bridge.add_state_event()):
            return
        while True:
            events = self.server.bridge.wait_events(after, 20)
            if not events:
                try:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                continue
            for event in events:
                if not self._try_write_sse(event):
                    return
                after = max(after, event["id"])

    def _try_write_sse(self, event: dict[str, Any]) -> bool:
        try:
            self._write_sse(event)
            return True
        except (BrokenPipeError, ConnectionResetError):
            return False

    def _write_sse(self, event: dict[str, Any]) -> None:
        packet = (
            f"id: {event['id']}\n"
            f"event: {event['type']}\n"
            f"data: {json.dumps(event['data'])}\n\n"
        )
        self.wfile.write(packet.encode("utf-8"))
        self.wfile.flush()

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0"))
        if length <= 0:
            return {}
        data = self.rfile.read(length)
        return json.loads(data.decode("utf-8"))

    def _send_json(self, data: dict[str, Any]) -> None:
        body = json.dumps(data).encode("utf-8")
        self._send_bytes(body, "application/json")

    def _send_bytes(self, body: bytes, content_type: str) -> None:
        self._send_headers(HTTPStatus.OK, content_type, len(body))
        self.wfile.write(body)

    def _send_headers(self, status: HTTPStatus, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(length))
        self.send_header("cache-control", "no-store")
        self.end_headers()

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local web UI for Hackaday Europe badge chat.")
    parser.add_argument("port", nargs="?", help="Serial port, for example /dev/ttyACM0")
    parser.add_argument("-t", "--topic", type=int, default=1, help="Chat topic 1-99, default 1")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host, default 127.0.0.1")
    parser.add_argument("--web-port", type=int, default=8765, help="Web UI port, default 8765")
    parser.add_argument(
        "--allow-lan",
        action="store_true",
        help="Allow binding to a non-loopback address",
    )
    args = parser.parse_args()
    if args.host not in LOOPBACK_HOSTS and not args.allow_lan:
        parser.error("refusing to bind outside loopback without --allow-lan")
    return args


def main() -> int:
    args = parse_args()
    local_only = args.host in LOOPBACK_HOSTS and not args.allow_lan
    bridge = ChatBridge(args.port, args.topic, auto_port=True)
    bridge.start()

    try:
        httpd = ChatHTTPServer((args.host, args.web_port), bridge, local_only)
    except OSError as exc:
        print(f"Could not start web server on {args.host}:{args.web_port}: {exc}", file=sys.stderr)
        return 2

    bound_host, bound_port = httpd.server_address[:2]
    display_host = "127.0.0.1" if bound_host in {"0.0.0.0", ""} else bound_host
    print(f"PC Chat Web UI: http://{display_host}:{bound_port}")
    if local_only:
        print("Listening on loopback only. This is not exposed to the LAN.")
    else:
        print("LAN access is enabled because --allow-lan was used.")
    print("On the badge, open Apps -> PC Chat.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
        return 0
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
