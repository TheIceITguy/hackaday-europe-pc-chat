# Hackaday Europe PC Chat

Type Hackaday Europe 2026 badge chat messages from your computer while the badge still does the LoRa radio transmit and receive.

Other badges do not need any update. They see your messages in the stock badge Chat app.

## What This Is

- `badge_apps/pc_chat_bridge.py` runs on the badge.
- `tools/pc_chat_web.py` is the local browser UI.
- `tools/pc_chat_companion.py` is an optional terminal UI.

This targets the Hackaday Europe firmware chat protocol: LoRa slot `1`, chat port `6`, channel `100 + topic`.

## Install

### Easy Installer

From this repo on Linux or macOS:

```sh
python3 install.py
```

On Windows PowerShell:

```powershell
py install.py
```

The installer:

- creates a local `.venv`
- installs `mpremote` and `pyserial`
- installs the Linux udev rule when running on Linux
- detects the badge serial port
- copies `PC Chat` to the badge
- resets the badge

After install, open `Apps -> PC Chat` on the badge and run:

```sh
python3 run_web.py
```

On Windows PowerShell:

```powershell
py run_web.py
```

Then open:

```text
http://127.0.0.1:8765
```

You can also install and start the web UI in one command:

```sh
python3 install.py --start-web
```

On Windows PowerShell:

```powershell
py install.py --start-web
```

### Manual Install

Use these steps if the installer cannot detect your badge or you want to run each step yourself.

Install dependencies:

```sh
python3 -m pip install -r requirements.txt
```

On Windows PowerShell:

```powershell
py -m pip install -r requirements.txt
```

#### Linux

Install the included udev rule once so the serial port is writable after every replug:

```sh
sudo install -m 0644 udev/99-hackaday-europe-badge.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=tty --action=change
```

Unplug and replug the badge if the serial permissions do not update immediately.

Find the badge port:

```sh
mpremote devs
```

Common Linux ports look like `/dev/ttyACM0`.

#### macOS

Find the badge port:

```sh
mpremote devs
ls /dev/cu.usbmodem* /dev/cu.usbserial* 2>/dev/null
```

Common macOS ports look like `/dev/cu.usbmodem1101`.

#### Windows

Install Python 3 from <https://www.python.org/> if needed. In PowerShell, find the badge port:

```powershell
mpremote devs
```

Common Windows ports look like `COM3`.

#### Copy The Badge App

Replace `<PORT>` with the port from the previous step:

```sh
mpremote connect <PORT> cp badge_apps/pc_chat_bridge.py :/apps/pc_chat_bridge.py
mpremote connect <PORT> reset
```

Examples:

```sh
mpremote connect /dev/ttyACM0 cp badge_apps/pc_chat_bridge.py :/apps/pc_chat_bridge.py
mpremote connect /dev/cu.usbmodem1101 cp badge_apps/pc_chat_bridge.py :/apps/pc_chat_bridge.py
mpremote connect COM3 cp badge_apps/pc_chat_bridge.py :/apps/pc_chat_bridge.py
```

## Use The Browser UI

1. On the badge, open `Apps`.
2. Open `PC Chat`.
3. On the computer, run:

```sh
python3 run_web.py
```

On Windows PowerShell:

```powershell
py run_web.py
```

If you are not using the installer, the direct command is:

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
python3 run_terminal.py
```

On Windows PowerShell:

```powershell
py run_terminal.py
```

Commands:

```text
/topic 7
/quit
```

## Notes

- Keep `PC Chat` open on the badge while using the computer companion.
- Your nick is the badge `alias`; the stock chat payload has a 10-character alias field.
- The browser and terminal companions auto-detect Linux, macOS, and Windows serial ports. You can still pass the port explicitly, for example `python3 tools/pc_chat_web.py /dev/ttyACM0`, `python3 tools/pc_chat_web.py /dev/cu.usbmodem1101`, or `py tools\pc_chat_web.py COM3`.
- On Linux, the included udev rule should make the badge serial port writable automatically after every replug.
- If you still get `Permission denied` for `/dev/ttyACM0`, replug the badge and check that `/etc/udev/rules.d/99-hackaday-europe-badge.rules` exists.
