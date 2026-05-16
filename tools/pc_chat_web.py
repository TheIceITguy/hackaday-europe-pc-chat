#!/usr/bin/env python3
"""Local browser UI for the Hackaday Europe badge PC Chat bridge."""

from __future__ import annotations

import argparse
import asyncio
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
    from serial.tools import list_ports
except ImportError as exc:  # pragma: no cover
    raise SystemExit("pyserial is required: python3 -m pip install pyserial") from exc


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
BADGE_USB_VID = 0x303A
BADGE_USB_PID = 0x1001
# The radio protocol can carry 100 bytes of chat text, but long unbroken
# strings like URLs are easier for the stock badge UI and mesh if fragmented.
MAX_CHAT_BYTES = 60
MAX_OUTBOUND_TEXT_BYTES = 1200
DEFAULT_PACKET_SEND_INTERVAL_S = 4.0
MIN_PACKET_SEND_INTERVAL_S = 1.0
MAX_PACKET_SEND_INTERVAL_S = 15.0
HANDSHAKE_PING_INTERVAL_S = 1.0
BLE_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
BLE_RX_CHAR_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
BLE_TX_CHAR_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
BLE_DEFAULT_NAME = "LC26-"
BLE_WRITE_CHUNK_BYTES = 20


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
    button, input, textarea {
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
      align-items: center;
      gap: 12px;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 5px;
    }
    .meta-actions {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
      justify-content: flex-end;
    }
    .meta-actions span {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
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
    .reply-button {
      min-height: 26px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: transparent;
      color: var(--muted);
      padding: 3px 8px;
      cursor: pointer;
      font-size: 12px;
      font-weight: 650;
      flex: 0 0 auto;
    }
    .reply-button:hover {
      border-color: var(--accent-2);
      color: var(--text);
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
    .connection-grid {
      display: grid;
      gap: 8px;
    }
    .transport-buttons {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    .button.active {
      border-color: rgba(101, 211, 155, .72);
      background: #244936;
      color: var(--text);
    }
    .ble-row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    .art-actions {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
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
    .reply-bar {
      grid-column: 1 / -1;
      min-width: 0;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: center;
      border: 1px solid rgba(130, 183, 255, .42);
      border-radius: 6px;
      background: #202936;
      padding: 8px 10px;
      color: var(--muted);
      font-size: 13px;
    }
    .reply-bar[hidden] {
      display: none;
    }
    #replyLabel {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    #composer {
      min-height: 42px;
    }
    .settings-view {
      position: fixed;
      inset: 0;
      z-index: 10;
      display: grid;
      grid-template-rows: auto 1fr;
      background: var(--bg);
      color: var(--text);
    }
    .settings-view[hidden] {
      display: none;
    }
    .settings-header {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      align-items: center;
      padding: 14px 18px;
      border-bottom: 1px solid var(--line);
      background: #191d20;
    }
    .settings-title {
      font-size: 18px;
      font-weight: 650;
    }
    .settings-body {
      min-height: 0;
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(260px, 360px);
      gap: 16px;
      padding: 16px;
      overflow: auto;
    }
    .settings-panel {
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .art-editor {
      width: 100%;
      min-height: 260px;
      resize: vertical;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      line-height: 1.35;
      white-space: pre;
    }
    .art-preview {
      min-height: 260px;
      margin: 0;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #101315;
      color: var(--accent);
      padding: 12px;
      overflow: auto;
      font: 14px/1.35 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      white-space: pre;
    }
    .settings-actions {
      display: flex;
      gap: 8px;
      justify-content: flex-end;
      flex-wrap: wrap;
    }
    .settings-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(90px, 130px);
      gap: 8px;
      align-items: end;
    }
    .settings-grid label {
      color: var(--muted);
      font-size: 13px;
    }
    .settings-grid label span {
      display: block;
      margin-bottom: 5px;
    }
    .art-meta {
      min-height: 20px;
      color: var(--muted);
      font-size: 13px;
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
      .reply-bar {
        grid-template-columns: 1fr;
      }
      .message {
        width: 100%;
      }
      .settings-body {
        grid-template-columns: 1fr;
      }
      .settings-header {
        grid-template-columns: 1fr;
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
            <div class="fact"><span>Mode</span><strong id="transportMode">USB</strong></div>
            <div class="fact"><span>TX</span><strong id="txValue">0</strong></div>
            <div class="fact"><span>RX</span><strong id="rxValue">0</strong></div>
            <div class="fact status-fact"><span>Status</span><strong id="statusDetail">Starting</strong></div>
          </div>
        </div>
        <div>
          <div class="section-title">Connection</div>
          <div class="connection-grid">
            <div class="transport-buttons">
              <button id="usbModeButton" class="button secondary" type="button">USB</button>
              <button id="bleModeButton" class="button secondary" type="button">BLE</button>
            </div>
            <input id="bleNameInput" class="field" autocomplete="off" placeholder="BLE name, e.g. LC26-abcd">
            <div class="ble-row">
              <input id="bleCodeInput" class="field" inputmode="numeric" maxlength="6" autocomplete="off" placeholder="Code">
              <button id="bleConnectButton" class="button secondary" type="button">Connect</button>
            </div>
          </div>
        </div>
        <div>
          <div class="section-title">Badge Art</div>
          <div class="art-actions">
            <button id="artButton" class="button secondary" type="button">Send Art</button>
            <button id="settingsButton" class="button secondary" type="button">Settings</button>
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
      <div id="replyBar" class="reply-bar" hidden>
        <div id="replyLabel"></div>
        <button id="clearReply" class="button secondary" type="button">Cancel</button>
      </div>
      <input id="composer" class="field" maxlength="1200" autocomplete="off" placeholder="Write a badge chat message">
      <button id="sendButton" class="button" type="button">Send</button>
    </footer>
    <section id="settingsView" class="settings-view" hidden>
      <div class="settings-header">
        <div>
          <div class="settings-title">Badge Image Art</div>
          <div class="subtitle">Saved in this browser</div>
        </div>
        <button id="closeSettings" class="button secondary" type="button">Back</button>
      </div>
      <div class="settings-body">
        <div class="settings-panel">
          <div class="section-title">Image Art</div>
          <textarea id="artEditor" class="field art-editor" spellcheck="false"></textarea>
          <div id="artMeta" class="art-meta"></div>
          <div class="settings-grid">
            <label>
              <span>Seconds between radio packets</span>
              <input id="packetGapInput" class="field" type="number" min="1" max="15" step="0.5" value="4">
            </label>
          </div>
          <div class="settings-actions">
            <button id="resetArt" class="button secondary" type="button">Reset</button>
            <button id="saveArt" class="button" type="button">Save</button>
          </div>
        </div>
        <div class="settings-panel">
          <div class="section-title">Preview</div>
          <pre id="artPreview" class="art-preview"></pre>
        </div>
      </div>
    </section>
  </div>
  <script>
    const el = (id) => document.getElementById(id);
    const messages = el("messages");
    const state = { topic: 1, connected: false, tx: 0, rx: 0, showAll: false };
    const allMessages = [];
    let replyContext = null;
    const ART_STORAGE_KEY = "pcChatBadgeImageArtV2";
    const PACKET_GAP_STORAGE_KEY = "pcChatPacketGap";
    const BLE_NAME_STORAGE_KEY = "pcChatBleName";
    const MAX_ART_LINES = 8;
    const MAX_ART_LINE_CHARS = 32;
    const DEFAULT_PACKET_GAP_SECONDS = 4.0;
    const MIN_PACKET_GAP_SECONDS = 1.0;
    const MAX_PACKET_GAP_SECONDS = 15.0;
    const DEFAULT_BADGE_ART = [
      "       /\\",
      "      /  \\",
      "     / /\\ \\",
      " __ / /  \\ \\ __",
      "/__/ /____\\ \\__\\",
      "   \\_\\    /_/",
      "    /_/  \\_\\",
      "   ~~      ~~"
    ];

    function two(n) {
      const value = Number(n || 1);
      return String(Math.max(1, Math.min(99, value))).padStart(2, "0");
    }

    function formatTime(timestamp) {
      const date = timestamp ? new Date(timestamp * 1000) : new Date();
      const year = date.getFullYear();
      const month = String(date.getMonth() + 1).padStart(2, "0");
      const day = String(date.getDate()).padStart(2, "0");
      const hour = String(date.getHours()).padStart(2, "0");
      const minute = String(date.getMinutes()).padStart(2, "0");
      return `${year}-${month}-${day} ${hour}:${minute}`;
    }

    function appendMessage(data) {
      allMessages.push(data);
      if (allMessages.length > 500) allMessages.shift();
      renderMessages();
    }

    function defaultArtText() {
      return DEFAULT_BADGE_ART.join("\n");
    }

    function sanitizeArtText(value) {
      const lines = String(value || "")
        .replace(/\r/g, "")
        .replace(/\t/g, "  ")
        .split("\n")
        .slice(0, MAX_ART_LINES)
        .map((line) => line.replace(/[^\x20-\x7E]/g, "").slice(0, MAX_ART_LINE_CHARS).trimEnd());
      while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
      return lines.join("\n");
    }

    function loadArtText() {
      try {
        const saved = localStorage.getItem(ART_STORAGE_KEY);
        const clean = sanitizeArtText(saved || defaultArtText());
        return clean || defaultArtText();
      } catch {
        return defaultArtText();
      }
    }

    function saveArtText(value) {
      const clean = sanitizeArtText(value) || defaultArtText();
      localStorage.setItem(ART_STORAGE_KEY, clean);
      return clean;
    }

    function clampPacketGap(value) {
      const numeric = Number(value);
      if (!Number.isFinite(numeric)) return DEFAULT_PACKET_GAP_SECONDS;
      return Math.max(MIN_PACKET_GAP_SECONDS, Math.min(MAX_PACKET_GAP_SECONDS, numeric));
    }

    function loadPacketGap() {
      try {
        return clampPacketGap(localStorage.getItem(PACKET_GAP_STORAGE_KEY) || DEFAULT_PACKET_GAP_SECONDS);
      } catch {
        return DEFAULT_PACKET_GAP_SECONDS;
      }
    }

    function savePacketGap(value) {
      const clean = clampPacketGap(value);
      localStorage.setItem(PACKET_GAP_STORAGE_KEY, String(clean));
      return clean;
    }

    function loadBleName() {
      try {
        return localStorage.getItem(BLE_NAME_STORAGE_KEY) || "";
      } catch {
        return "";
      }
    }

    function saveBleName(value) {
      const clean = String(value || "").trim();
      localStorage.setItem(BLE_NAME_STORAGE_KEY, clean);
      return clean;
    }

    function artLines() {
      return loadArtText()
        .split("\n")
        .map((line) => line.trimEnd())
        .filter((line) => line.trim());
    }

    function renderArtSettings() {
      const clean = sanitizeArtText(el("artEditor").value);
      const lines = clean ? clean.split("\n") : [];
      el("artPreview").textContent = clean || "";
      el("artMeta").textContent = `${lines.length}/${MAX_ART_LINES} lines, max ${MAX_ART_LINE_CHARS} ASCII chars per line`;
      el("packetGapInput").value = String(clampPacketGap(el("packetGapInput").value));
    }

    function openSettings() {
      el("artEditor").value = loadArtText();
      el("packetGapInput").value = String(loadPacketGap());
      renderArtSettings();
      el("settingsView").hidden = false;
      el("artEditor").focus();
    }

    function closeSettings() {
      el("settingsView").hidden = true;
      el("composer").focus();
    }

    function cleanReplyName(value) {
      return String(value || "badge")
        .replace(/[\t\r\n]+/g, " ")
        .replace(/^@+/, "")
        .trim()
        .slice(0, 20) || "badge";
    }

    function replySnippet(value) {
      const clean = String(value || "").replace(/\s+/g, " ").trim();
      return clean.length > 60 ? clean.slice(0, 57) + "..." : clean;
    }

    function setReply(data) {
      replyContext = {
        alias: cleanReplyName(data.alias || data.source || "badge"),
        topic: Number(data.topic || state.topic || 1),
        text: data.text || ""
      };
      renderReplyBar();
      updateComposerHint();
      el("composer").focus();
    }

    function clearReply() {
      replyContext = null;
      renderReplyBar();
      updateComposerHint();
    }

    function renderReplyBar() {
      const bar = el("replyBar");
      if (!replyContext) {
        bar.hidden = true;
        el("replyLabel").textContent = "";
        return;
      }
      const snippet = replySnippet(replyContext.text);
      const suffix = snippet ? " - " + snippet : "";
      el("replyLabel").textContent = "Replying to @" + replyContext.alias + " on topic " + two(replyContext.topic) + suffix;
      bar.hidden = false;
    }

    function prefixedReplyText(text) {
      if (!replyContext) return text;
      return "re @" + replyContext.alias + ": " + text;
    }

    function updateComposerHint() {
      if (replyContext) {
        el("composer").placeholder = "Reply to @" + replyContext.alias + " on topic " + two(replyContext.topic);
      } else {
        el("composer").placeholder = "Write a badge chat message to topic " + two(state.topic);
      }
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
      detail.textContent = formatTime(data.timestamp) + "  topic " + two(data.topic) + (data.rssi ? "  " + data.rssi + " dBm" : "");
      const actions = document.createElement("div");
      actions.className = "meta-actions";
      const reply = document.createElement("button");
      reply.className = "reply-button";
      reply.type = "button";
      reply.textContent = "Reply";
      reply.addEventListener("click", () => setReply(data));
      const text = document.createElement("div");
      text.className = "text";
      text.textContent = data.text || "";
      actions.append(detail, reply);
      meta.append(sender, actions);
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
      el("transportMode").textContent = String(data.transport || "usb").toUpperCase();
      el("txValue").textContent = String(data.tx || 0);
      el("rxValue").textContent = String(data.rx || 0);
      el("rssiValue").textContent = data.rssi || "-";
      el("snrValue").textContent = data.snr || "-";
      el("sendButton").disabled = !data.connected;
      el("artButton").disabled = !data.connected;
      el("usbModeButton").classList.toggle("active", (data.transport || "usb") === "usb");
      el("bleModeButton").classList.toggle("active", (data.transport || "usb") === "ble");
      if (data.ble_name && data.ble_name !== "LC26-" && !el("bleNameInput").value) {
        el("bleNameInput").value = data.ble_name;
      }
      if (data.packet_gap && el("settingsView").hidden) {
        el("packetGapInput").value = String(clampPacketGap(data.packet_gap));
      }
      updateComposerHint();
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

    function wait(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    }

    async function sendChatText(topic, text, preserveSpacing = false) {
      await postJSON("/api/send", { topic, text, preserve_spacing: preserveSpacing });
    }

    async function applyPacketGap(value) {
      const packetGap = savePacketGap(value);
      await postJSON("/api/settings", { packet_gap: packetGap });
      return packetGap;
    }

    async function setTopic(topic) {
      const value = Math.max(1, Math.min(99, Number(topic || 1)));
      el("topicInput").value = value;
      await postJSON("/api/topic", { topic: value });
    }

    async function setTransport(transport) {
      const payload = { transport };
      if (transport === "ble") {
        const code = el("bleCodeInput").value.trim();
        const name = saveBleName(el("bleNameInput").value);
        if (!name || name === "LC26-") {
          throw new Error("Enter the exact BLE name shown on the badge");
        }
        if (!/^\d{6}$/.test(code)) {
          throw new Error("Enter the 6 digit BLE code shown on the badge");
        }
        payload.pair_code = code;
        payload.ble_name = name;
      }
      await postJSON("/api/transport", payload);
    }

    el("sendButton").addEventListener("click", async () => {
      const input = el("composer");
      const text = input.value.trim();
      if (!text) return;
      const topic = replyContext ? Number(replyContext.topic) : Number(el("topicInput").value);
      const outgoing = prefixedReplyText(text);
      try {
        await sendChatText(topic, outgoing);
        input.value = "";
        clearReply();
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

    el("usbModeButton").addEventListener("click", async () => {
      try {
        await setTransport("usb");
        appendStatus("Switching to USB");
      } catch (err) {
        appendStatus(err.message);
      }
    });

    el("bleModeButton").addEventListener("click", async () => {
      try {
        await setTransport("ble");
        appendStatus("Switching to BLE");
      } catch (err) {
        appendStatus(err.message);
      }
    });

    el("bleConnectButton").addEventListener("click", async () => {
      try {
        await setTransport("ble");
        appendStatus("Connecting with BLE");
      } catch (err) {
        appendStatus(err.message);
      }
    });

    el("bleCodeInput").addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        el("bleConnectButton").click();
      }
    });

    el("clearReply").addEventListener("click", clearReply);

    el("artButton").addEventListener("click", async () => {
      const button = el("artButton");
      const topic = Number(el("topicInput").value);
      const lines = artLines();
      if (!lines.length) {
        appendStatus("Badge art is empty");
        return;
      }
      clearReply();
      button.disabled = true;
      appendStatus("Sending badge art...");
      try {
        for (const line of lines) {
          await sendChatText(topic, line, true);
          await wait(120);
        }
        appendStatus("Badge art sent to topic " + two(topic));
      } catch (err) {
        appendStatus(err.message);
      } finally {
        button.disabled = !state.connected;
      }
    });

    el("settingsButton").addEventListener("click", openSettings);
    el("closeSettings").addEventListener("click", closeSettings);
    el("artEditor").addEventListener("input", renderArtSettings);
    el("packetGapInput").addEventListener("change", renderArtSettings);
    el("saveArt").addEventListener("click", async () => {
      try {
        el("artEditor").value = saveArtText(el("artEditor").value);
        const packetGap = await applyPacketGap(el("packetGapInput").value);
        el("packetGapInput").value = String(packetGap);
        renderArtSettings();
        appendStatus("Badge art saved; packet gap " + packetGap.toFixed(1) + "s");
        closeSettings();
      } catch (err) {
        appendStatus(err.message);
      }
    });
    el("resetArt").addEventListener("click", () => {
      el("artEditor").value = defaultArtText();
      el("packetGapInput").value = String(DEFAULT_PACKET_GAP_SECONDS);
      renderArtSettings();
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

    el("bleNameInput").value = loadBleName();

    fetch("/api/state")
      .then((res) => res.json())
      .then((data) => {
        applyState(data);
        renderMessages();
        applyPacketGap(loadPacketGap()).catch((err) => appendStatus(err.message));
      })
      .catch((err) => appendStatus(err.message));
  </script>
</body>
</html>
"""


def find_port() -> str | None:
    ports = list(list_ports.comports())
    for port in ports:
        if port.vid == BADGE_USB_VID and port.pid == BADGE_USB_PID:
            return port.device
    if ports:
        usb_ports = [
            port.device
            for port in ports
            if "usb" in (port.device or "").lower()
            or "usb" in (port.description or "").lower()
            or "com" in (port.device or "").lower()
        ]
        if usb_ports:
            return sorted(usb_ports)[0]

    patterns = [
        "/dev/ttyACM*",
        "/dev/ttyUSB*",
        "/dev/cu.usbmodem*",
        "/dev/cu.usbserial*",
        "/dev/tty.usbmodem*",
        "/dev/tty.usbserial*",
    ]
    fallback_ports: list[str] = []
    for pattern in patterns:
        fallback_ports.extend(glob.glob(pattern))
    return sorted(fallback_ports)[0] if fallback_ports else None


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
    def __init__(
        self,
        port: str | None,
        topic: int,
        auto_port: bool = True,
        packet_interval_s: float = DEFAULT_PACKET_SEND_INTERVAL_S,
        transport: str = "usb",
        ble_name: str = BLE_DEFAULT_NAME,
        ble_address: str | None = None,
        ble_pair_code: str = "",
    ):
        self.preferred_port = port
        self.port = port or "auto"
        self.auto_port = auto_port
        self.topic = max(1, min(99, topic))
        self.packet_interval_s = _clamp_packet_interval(packet_interval_s)
        self.transport = _parse_transport(transport)
        self.ble_name = ble_name or BLE_DEFAULT_NAME
        self.ble_address = ble_address or ""
        self.ble_pair_code = _clean_pair_code(ble_pair_code)
        self.alias = ""
        self.connected = False
        self.status = "Waiting for badge"
        self.tx = 0
        self.rx = 0
        self.last_rssi = ""
        self.last_snr = ""
        self._serial: serial.Serial | None = None
        self._serial_lock = threading.Lock()
        self._ble_loop: asyncio.AbstractEventLoop | None = None
        self._ble_client: Any | None = None
        self._ble_line_buffer = ""
        self._events: list[dict[str, Any]] = []
        self._event_seq = 0
        self._event_cond = threading.Condition()
        self._stop = threading.Event()
        self._reconnect = threading.Event()
        self._transport_lock = threading.Lock()
        self._pending_tx_echoes: list[tuple[int, str]] = []
        self._tx_lock = threading.Lock()
        self._last_packet_at = 0.0

    def start(self) -> None:
        thread = threading.Thread(target=self._run, name="badge-chat-transport", daemon=True)
        thread.start()

    def state(self) -> dict[str, Any]:
        return {
            "port": self.port,
            "transport": self.transport,
            "ble_name": self.ble_name,
            "ble_address": self.ble_address,
            "ble_pair_needed": self.transport == "ble" and not self.ble_pair_code,
            "topic": self.topic,
            "alias": self.alias,
            "connected": self.connected,
            "status": self.status,
            "tx": self.tx,
            "rx": self.rx,
            "rssi": self.last_rssi,
            "snr": self.last_snr,
            "packet_gap": self.packet_interval_s,
        }

    def set_packet_interval(self, seconds: float) -> float:
        self.packet_interval_s = _clamp_packet_interval(seconds)
        if self.connected:
            self._write_line(f"GAP\t{self.packet_interval_s:.2f}")
        self._add_event("state", self.state())
        self._add_event("status", {"text": f"Packet gap set to {self.packet_interval_s:.1f}s"})
        return self.packet_interval_s

    def set_transport(
        self,
        transport: str,
        pair_code: str = "",
        ble_name: str = "",
        ble_address: str = "",
    ) -> None:
        next_transport = _parse_transport(transport)
        if next_transport == "ble":
            code = _clean_pair_code(pair_code or self.ble_pair_code)
            if not code:
                raise ValueError("Enter the 6 digit BLE code shown on the badge")
            self.ble_pair_code = code
            if ble_name.strip():
                self.ble_name = ble_name.strip()
            if ble_address.strip():
                self.ble_address = ble_address.strip()
        with self._transport_lock:
            changed = self.transport != next_transport
            self.transport = next_transport
        self._reconnect.set()
        self._close_current_transport()
        self.connected = False
        self.alias = ""
        self.status = "Switching to %s" % next_transport.upper() if changed else "Reconnecting %s" % next_transport.upper()
        self.port = self._transport_port_label()
        self._add_event("status", {"text": self.status})
        self._add_event("state", self.state())

    def send_message(self, topic: int, text: str, preserve_spacing: bool = False) -> None:
        if not self.connected:
            raise RuntimeError("Open Apps -> PC Chat on the badge before sending")
        topic = max(1, min(99, int(topic)))
        text = text.replace("\t", " ").replace("\r", " ").replace("\n", " ")
        text = text.rstrip() if preserve_spacing else text.strip()
        if not text.strip():
            raise ValueError("Message is empty")
        if len(text.encode("utf-8")) > MAX_OUTBOUND_TEXT_BYTES:
            raise ValueError(f"Message is too long; keep it under {MAX_OUTBOUND_TEXT_BYTES} bytes")
        self.topic = topic
        chunks = split_text_for_chat(text)
        if len(chunks) > 1:
            self._add_event("status", {"text": f"Splitting message into {len(chunks)} packets"})
        with self._tx_lock:
            for chunk in chunks:
                self._wait_for_packet_slot()
                self._queue_pending_tx(topic, chunk)
                try:
                    command = "SENDRAW" if preserve_spacing else "SEND"
                    self._write_line(f"{command}\t{topic}\t{chunk}")
                except Exception:
                    self._drop_pending_tx(topic, chunk)
                    raise
                self._last_packet_at = time.monotonic()
                self._echo_local_tx(topic, chunk)

    def set_topic(self, topic: int) -> None:
        self.topic = max(1, min(99, int(topic)))
        if not self.connected:
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
            self._reconnect.clear()
            try:
                if self.transport == "ble":
                    self._run_ble()
                else:
                    port = self._select_port()
                    if port is None:
                        self._mark_disconnected("No badge serial port found")
                        self._wait_before_retry(1.5)
                        continue
                    self._connect_and_read(port)
            except Exception as exc:
                self._mark_disconnected(str(exc))
                self._wait_before_retry(1.5)

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
            self.connected = False
            self.alias = ""
            self.status = "USB connected. Open Apps -> PC Chat on the badge."
            self._add_event("status", {"text": self.status})
            self._add_event("state", self.state())
            self._write_line("PING")
            self._write_line(f"TOPIC\t{self.topic}")
            self._write_line(f"GAP\t{self.packet_interval_s:.2f}")
            next_ping = time.monotonic() + HANDSHAKE_PING_INTERVAL_S

            while not self._stop.is_set() and not self._reconnect.is_set() and self.transport == "usb":
                raw = ser.readline()
                if not raw:
                    if not self.connected and time.monotonic() >= next_ping:
                        self._write_line("PING")
                        next_ping = time.monotonic() + HANDSHAKE_PING_INTERVAL_S
                    continue
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    self._handle_serial_line(line)
        with self._serial_lock:
            self._serial = None

    def _mark_disconnected(self, reason: str) -> None:
        with self._serial_lock:
            self._serial = None
        was_connected = self.connected
        previous_status = self.status
        self.connected = False
        self._ble_client = None
        if self.transport == "ble":
            self.port = self._transport_port_label()
        elif self.preferred_port and self.auto_port:
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
        if kind == "PAIR" and len(parts) >= 3:
            self._add_event("status", {"text": "BLE paired" if parts[2] == "OK" else "BLE " + parts[2]})
            return
        if kind in {"READY", "PONG"} and len(parts) >= 4:
            self.alias = parts[2]
            self.topic = _parse_topic(parts[3], self.topic)
            was_connected = self.connected
            self.connected = True
            self.status = "Connected"
            if not was_connected:
                self._add_event("status", {"text": f"PC Chat ready on {self.port}"})
            self._add_event("state", self.state())
            return
        if kind == "TOPIC" and len(parts) >= 3:
            self.topic = _parse_topic(parts[2], self.topic)
            self._add_event("topic", {"topic": self.topic, "state": self.state()})
            return
        if kind == "GAP" and len(parts) >= 3:
            self.packet_interval_s = _clamp_packet_interval(parts[2])
            self._add_event("state", self.state())
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
                    "timestamp": time.time(),
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
                    "timestamp": time.time(),
                },
            )
            self._add_event("state", self.state())
            return
        if kind == "ERR" and len(parts) >= 3:
            self._add_event("status", {"text": parts[2]})

    def _run_ble(self) -> None:
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError:
            self._mark_disconnected("BLE needs bleak: python3 -m pip install bleak")
            self._wait_before_retry(2.0)
            return
        asyncio.run(self._run_ble_async(BleakScanner, BleakClient))

    async def _run_ble_async(self, scanner_cls: Any, client_cls: Any) -> None:
        self._ble_loop = asyncio.get_running_loop()
        self._ble_line_buffer = ""
        try:
            while not self._stop.is_set() and not self._reconnect.is_set() and self.transport == "ble":
                if not self.ble_pair_code:
                    self._mark_disconnected("Enter the BLE code shown on the badge")
                    await asyncio.sleep(1.0)
                    continue
                self.status = "Scanning for BLE badge"
                self.port = self._transport_port_label()
                self._add_event("state", self.state())
                device = await self._find_ble_device(scanner_cls)
                if device is None:
                    self._mark_disconnected("No BLE badge found")
                    await asyncio.sleep(1.5)
                    continue
                if self._reconnect.is_set() or self.transport != "ble":
                    return
                await self._connect_ble_device(client_cls, device)
        finally:
            self._ble_client = None
            self._ble_loop = None

    async def _find_ble_device(self, scanner_cls: Any) -> Any | None:
        if self.ble_address:
            return await scanner_cls.find_device_by_address(self.ble_address, timeout=6.0)
        devices = await scanner_cls.discover(timeout=6.0)
        target = self.ble_name.strip()
        fallback_prefix = BLE_DEFAULT_NAME
        for device in devices:
            name = (getattr(device, "name", "") or "").strip()
            if target and name == target:
                return device
        prefix_matches = []
        for device in devices:
            name = (getattr(device, "name", "") or "").strip()
            if target and target.endswith("-") and name.startswith(target):
                prefix_matches.append(device)
                continue
            if not target and name.startswith(fallback_prefix):
                prefix_matches.append(device)
                continue
            if target == fallback_prefix and name.startswith(fallback_prefix):
                prefix_matches.append(device)
        if len(prefix_matches) == 1:
            return prefix_matches[0]
        if len(prefix_matches) > 1:
            self._add_event("status", {"text": "Multiple BLE badges found; enter the exact BLE name"})
        return None

    async def _connect_ble_device(self, client_cls: Any, device: Any) -> None:
        name = (getattr(device, "name", "") or "").strip()
        address = getattr(device, "address", "")
        self.port = "BLE " + (name or address or "badge")
        self.status = "BLE connecting"
        self.connected = False
        self.alias = ""
        self._add_event("state", self.state())

        async with client_cls(device) as client:
            if self._reconnect.is_set() or self.transport != "ble":
                return
            self._ble_client = client
            self.status = "BLE connected. Pairing..."
            self._add_event("status", {"text": self.status})
            self._add_event("state", self.state())
            await client.start_notify(BLE_TX_CHAR_UUID, self._handle_ble_notification)
            if self._reconnect.is_set() or self.transport != "ble":
                return
            await self._ble_write_line_async(f"PAIR\t{self.ble_pair_code}")
            await self._ble_write_line_async("PING")
            await self._ble_write_line_async(f"TOPIC\t{self.topic}")
            await self._ble_write_line_async(f"GAP\t{self.packet_interval_s:.2f}")
            while (
                not self._stop.is_set()
                and not self._reconnect.is_set()
                and self.transport == "ble"
                and client.is_connected
            ):
                await asyncio.sleep(0.2)
            try:
                await client.stop_notify(BLE_TX_CHAR_UUID)
            except Exception:
                pass
        self._ble_client = None
        if not self._reconnect.is_set() and self.transport == "ble":
            self._mark_disconnected("BLE disconnected")

    def _handle_ble_notification(self, _sender: Any, data: bytearray) -> None:
        self._ble_line_buffer += bytes(data).decode("utf-8", "replace")
        while "\n" in self._ble_line_buffer:
            line, self._ble_line_buffer = self._ble_line_buffer.split("\n", 1)
            line = line.strip()
            if line:
                self._handle_serial_line(line)

    async def _ble_write_line_async(self, line: str) -> None:
        client = self._ble_client
        if client is None or not client.is_connected:
            raise RuntimeError("Badge BLE is not connected")
        payload = (line + "\n").encode("utf-8")
        for offset in range(0, len(payload), BLE_WRITE_CHUNK_BYTES):
            await client.write_gatt_char(
                BLE_RX_CHAR_UUID,
                payload[offset : offset + BLE_WRITE_CHUNK_BYTES],
                response=True,
            )
            await asyncio.sleep(0.03)

    def _close_current_transport(self) -> None:
        with self._serial_lock:
            serial_conn = self._serial
            self._serial = None
        if serial_conn is not None:
            try:
                serial_conn.close()
            except Exception:
                pass
        loop = self._ble_loop
        client = self._ble_client
        if loop is not None and client is not None:
            try:
                future = asyncio.run_coroutine_threadsafe(client.disconnect(), loop)
                future.result(timeout=3)
            except Exception:
                pass
        self._ble_client = None

    def _transport_port_label(self) -> str:
        if self.transport == "ble":
            if self.ble_address:
                return "BLE " + self.ble_address
            return "BLE " + (self.ble_name or BLE_DEFAULT_NAME)
        return self.preferred_port or "auto"

    def _wait_before_retry(self, seconds: float) -> None:
        self._reconnect.wait(seconds)

    def _write_line(self, line: str) -> None:
        if self.transport == "ble":
            loop = self._ble_loop
            if loop is None or self._ble_client is None:
                raise RuntimeError("Badge BLE is not connected")
            future = asyncio.run_coroutine_threadsafe(self._ble_write_line_async(line), loop)
            future.result(timeout=5)
            return
        with self._serial_lock:
            if self._serial is None:
                raise RuntimeError("Badge serial port is not connected")
            self._serial.write((line + "\n").encode("utf-8"))
            self._serial.flush()

    def _wait_for_packet_slot(self) -> None:
        elapsed = time.monotonic() - self._last_packet_at
        delay = self.packet_interval_s - elapsed
        if delay > 0:
            time.sleep(delay)

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
                "timestamp": time.time(),
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


def _parse_transport(value: str) -> str:
    clean = (value or "usb").strip().lower()
    if clean in {"serial", "usb"}:
        return "usb"
    if clean == "ble":
        return "ble"
    raise ValueError("transport must be usb or ble")


def _clean_pair_code(value: str) -> str:
    code = "".join(char for char in str(value or "") if char.isdigit())
    if not code:
        return ""
    if len(code) != 6:
        raise ValueError("BLE pair code must be 6 digits")
    return code


def _clamp_packet_interval(value: float) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = DEFAULT_PACKET_SEND_INTERVAL_S
    return max(MIN_PACKET_SEND_INTERVAL_S, min(MAX_PACKET_SEND_INTERVAL_S, seconds))


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
        if parsed.path == "/api/settings":
            self._send_json({"packet_gap": self.server.bridge.packet_interval_s})
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
                self.server.bridge.send_message(
                    payload.get("topic", 1),
                    payload.get("text", ""),
                    bool(payload.get("preserve_spacing", False)),
                )
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/topic":
                self.server.bridge.set_topic(payload.get("topic", 1))
                self._send_json({"ok": True})
                return
            if parsed.path == "/api/settings":
                packet_gap = self.server.bridge.set_packet_interval(payload.get("packet_gap", DEFAULT_PACKET_SEND_INTERVAL_S))
                self._send_json({"ok": True, "packet_gap": packet_gap})
                return
            if parsed.path == "/api/transport":
                self.server.bridge.set_transport(
                    payload.get("transport", "usb"),
                    payload.get("pair_code", ""),
                    payload.get("ble_name", ""),
                    payload.get("ble_address", ""),
                )
                self._send_json({"ok": True, "state": self.server.bridge.state()})
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
    parser.add_argument("--transport", choices=["usb", "serial", "ble"], default="usb", help="Badge connection transport, default usb")
    parser.add_argument("--ble-name", default=BLE_DEFAULT_NAME, help="BLE badge name or prefix, default LC26-")
    parser.add_argument("--ble-address", default="", help="BLE address to connect to")
    parser.add_argument("--ble-code", default="", help="6 digit BLE code shown on the badge")
    parser.add_argument("-t", "--topic", type=int, default=1, help="Chat topic 1-99, default 1")
    parser.add_argument(
        "--packet-gap",
        type=float,
        default=DEFAULT_PACKET_SEND_INTERVAL_S,
        help="Seconds between radio packets, default 4.0",
    )
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
    try:
        bridge = ChatBridge(
            args.port,
            args.topic,
            auto_port=True,
            packet_interval_s=args.packet_gap,
            transport=args.transport,
            ble_name=args.ble_name,
            ble_address=args.ble_address,
            ble_pair_code=args.ble_code,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
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
