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
- installs `mpremote`, `pyserial`, and `bleak`
- installs the Linux udev rule when running on Linux
- detects the badge serial port
- copies `PC Chat` to the badge
- copies the included Mastodon QR image to the badge
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
mpremote connect <PORT> cp badge_assets/images/mastodon_qr.png :/images/mastodon_qr.png
mpremote connect <PORT> cp badge_apps/pc_chat_bridge.py :/apps/pc_chat_bridge.py
mpremote connect <PORT> reset
```

Examples:

```sh
mpremote connect /dev/ttyACM0 cp badge_assets/images/mastodon_qr.png :/images/mastodon_qr.png
mpremote connect /dev/ttyACM0 cp badge_apps/pc_chat_bridge.py :/apps/pc_chat_bridge.py
mpremote connect /dev/cu.usbmodem1101 cp badge_assets/images/mastodon_qr.png :/images/mastodon_qr.png
mpremote connect /dev/cu.usbmodem1101 cp badge_apps/pc_chat_bridge.py :/apps/pc_chat_bridge.py
mpremote connect COM3 cp badge_assets/images/mastodon_qr.png :/images/mastodon_qr.png
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

## Bluetooth BLE Mode

USB mode is the default and is still needed once to copy the badge app. After the updated `PC Chat` app is on the badge, the browser UI can switch to BLE so the chat can run without the USB cable.

1. Start `PC Chat` on the badge.
2. Look at the badge screen for the exact BLE name, for example `LC26-1234abcd`, and the 6 digit code.
3. Open `http://127.0.0.1:8765`.
4. In the `Connection` panel, enter the exact BLE name and code.
5. Click `BLE` or `Connect`.

This uses Bluetooth Low Energy, not Bluetooth Classic serial. The browser still only talks to the local Python server on `127.0.0.1`; the Python server talks BLE to the badge.

You can also start directly in BLE mode:

```sh
python3 run_web.py --transport ble --ble-name LC26-1234abcd --ble-code 123456
```

On Windows PowerShell:

```powershell
py run_web.py --transport ble --ble-name LC26-1234abcd --ble-code 123456
```

## Features

- Topic support for `01`-`99`.
- Quick buttons for topics `01`-`05`.
- Optional `Show all topics` mode in the browser UI and on the badge.
- Reply button in the browser UI. Replies are sent as normal chat text with a prefix like `re @nick:`, so other badges do not need this companion app.
- `Send Art` button for a small row-based ASCII image sized for the stock Hackaday Europe chat display.
- Badge art settings page for editing the ASCII image and the seconds between radio packets in the browser.
- Auto reconnect after suspend or USB replug.
- BLE connection mode with a 6 digit code shown on the badge screen.
- Badge-side message notification: visible incoming messages pulse the side/debug LED and flash the screen backlight briefly.
- Nametag page inside the badge app, using the badge's existing nametag name/image settings and the included Mastodon QR image while chat keeps running.
- Status and serial permission errors are shown in the right-side status panel.
- Long messages are split into normal chat lines.
- Outbound radio packets are throttled to one packet every `4` seconds to avoid losing later lines in multi-line art.
- The badge chat page can also be used without the PC UI: `F1` posts a message, `F2` toggles between the selected topic and all topics, `F3` jumps to latest, `F4` changes topic, and `F5` opens the nametag page.
- The badge nametag page uses `F1` to return to chat, `F2` to refresh name/image settings, `F3` to return to latest chat, `F4` to exit, and `F5` to go to the next page.
- The badge app uses a colorized Hackaday-style UI on the badge display.

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
- BLE mode requires the `bleak` Python package and a working OS Bluetooth stack. On Linux, make sure Bluetooth is powered on in the desktop settings or with `bluetoothctl`.
- When several badges are running this app, use the exact BLE name shown on your badge screen. The browser UI refuses the generic `LC26-` prefix to avoid connecting to the wrong badge.
- On Linux, the included udev rule should make the badge serial port writable automatically after every replug.
- If you still get `Permission denied` for `/dev/ttyACM0`, replug the badge and check that `/etc/udev/rules.d/99-hackaday-europe-badge.rules` exists.
