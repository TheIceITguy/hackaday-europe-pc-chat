# Hackaday Europe PC Chat

Type Hackaday Europe 2026 badge chat messages from your computer while the badge still does the LoRa radio transmit and receive.

Other badges do not need any update. They see your messages in the stock badge Chat app.

## What This Is

- `badge_apps/pc_chat_bridge.py` runs on the badge.
- `tools/pc_chat_web.py` is the local browser UI.
- `tools/pc_chat_companion.py` is an optional terminal UI.

This targets the Hackaday Europe firmware chat protocol: LoRa slot `1`, chat port `6`, channel `100 + topic`.

## Install

From this repo:

```sh
python3 -m pip install -r requirements.txt
sudo install -m 0644 udev/99-hackaday-europe-badge.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=tty --action=change
```

Unplug and replug the badge if the serial permissions do not update immediately.

Then copy the bridge app to the badge:

```sh
mpremote connect /dev/ttyACM0 cp badge_apps/pc_chat_bridge.py :/apps/pc_chat_bridge.py
mpremote connect /dev/ttyACM0 reset
```

If your badge is not `/dev/ttyACM0`, check:

```sh
ls -l /dev/ttyACM* /dev/ttyUSB*
```

## Use The Browser UI

1. On the badge, open `Apps`.
2. Open `PC Chat`.
3. On the computer, run:

```sh
python3 tools/pc_chat_web.py
```

Then open:

```text
http://127.0.0.1:8765
```

The web UI binds to `127.0.0.1` by default, so it is only reachable from your own computer and is not exposed on the LAN.

## Features

- Topic support for `01`-`99`.
- Quick buttons for topics `01`-`05`.
- Optional `Show all topics` mode.
- Auto reconnect after suspend or USB replug.
- Status and serial permission errors are shown in the right-side status panel.
- Long messages are split into normal chat lines.

The badge protocol can carry `100` bytes of text, but this companion uses `60` byte chunks for better practical compatibility with long unbroken strings like URLs. Multipart messages are sent as normal chat lines with prefixes like `1/3`, `2/3`, and `3/3`.

## Terminal UI

```sh
python3 tools/pc_chat_companion.py /dev/ttyACM0
```

Commands:

```text
/topic 7
/quit
```

## Notes

- Keep `PC Chat` open on the badge while using the computer companion.
- Your nick is the badge `alias`; the stock chat payload has a 10-character alias field.
- On Linux, the included udev rule should make the badge serial port writable automatically after every replug.
- If you still get `Permission denied` for `/dev/ttyACM0`, replug the badge and check that `/etc/udev/rules.d/99-hackaday-europe-badge.rules` exists.
