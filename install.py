#!/usr/bin/env python3
"""Installer for Hackaday Europe PC Chat."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
VENV_DIR = REPO_ROOT / ".venv"
BADGE_APP = REPO_ROOT / "badge_apps" / "pc_chat_bridge.py"
UDEV_RULE = REPO_ROOT / "udev" / "99-hackaday-europe-badge.rules"
BADGE_USB_VID = 0x303A
BADGE_USB_PID = 0x1001
BADGE_ASSETS = [
    (REPO_ROOT / "badge_assets" / "images" / "mastodon_qr.png", ":/images/mastodon_qr.png"),
]


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print("+ " + " ".join(cmd))
    return subprocess.run(cmd, cwd=REPO_ROOT, check=check)


def create_venv() -> Path:
    py = venv_python()
    if not py.exists():
        try:
            run([sys.executable, "-m", "venv", str(VENV_DIR)])
        except subprocess.CalledProcessError:
            if platform.system() == "Linux":
                print("Could not create .venv. On Debian/Ubuntu, install python3-venv and retry:")
                print("  sudo apt install python3-venv")
            raise
    run([str(py), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(py), "-m", "pip", "install", "-r", "requirements.txt"])
    return py


def install_linux_udev(assume_yes: bool, skip_udev: bool) -> None:
    if platform.system() != "Linux" or skip_udev:
        return
    if not UDEV_RULE.exists():
        return
    if not assume_yes and not confirm("Install Linux udev rule for automatic serial permissions?", True):
        return
    run(["sudo", "install", "-m", "0644", str(UDEV_RULE), "/etc/udev/rules.d/"])
    run(["sudo", "udevadm", "control", "--reload-rules"])
    run(["sudo", "udevadm", "trigger", "--subsystem-match=tty", "--action=change"], check=False)
    print("Linux udev rule installed. If permissions do not update, unplug and replug the badge.")


def list_ports(py: Path) -> list[dict[str, object]]:
    code = r"""
import json
from serial.tools import list_ports
ports = []
for port in list_ports.comports():
    ports.append({
        "device": port.device,
        "description": port.description,
        "vid": port.vid,
        "pid": port.pid,
    })
print(json.dumps(ports))
"""
    result = subprocess.run(
        [str(py), "-c", code],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def choose_port(py: Path, explicit_port: str | None, assume_yes: bool) -> str:
    if explicit_port:
        return explicit_port

    while True:
        ports = list_ports(py)
        badge_ports = [
            port
            for port in ports
            if port.get("vid") == BADGE_USB_VID and port.get("pid") == BADGE_USB_PID
        ]
        if len(badge_ports) == 1:
            port = str(badge_ports[0]["device"])
            print(f"Found Hackaday badge on {port}")
            return port
        if len(badge_ports) > 1:
            return choose_from_ports(badge_ports, assume_yes)

        likely_ports = [
            port
            for port in ports
            if "usb" in str(port.get("device", "")).lower()
            or "usb" in str(port.get("description", "")).lower()
            or str(port.get("device", "")).upper().startswith("COM")
        ]
        if len(likely_ports) == 1:
            port = str(likely_ports[0]["device"])
            print(f"Using likely serial port {port}")
            return port
        if len(likely_ports) > 1:
            return choose_from_ports(likely_ports, assume_yes)

        print("No badge serial port found.")
        print("Plug in the badge, make sure it is powered on, then press Enter to retry.")
        if assume_yes:
            raise SystemExit("No serial port found")
        input()


def choose_from_ports(ports: list[dict[str, object]], assume_yes: bool) -> str:
    print("Multiple serial ports found:")
    for index, port in enumerate(ports, start=1):
        desc = port.get("description") or ""
        print(f"  {index}. {port['device']}  {desc}")
    if assume_yes:
        raise SystemExit("Multiple serial ports found; rerun with --port <PORT>")
    while True:
        choice = input("Choose port number: ").strip()
        try:
            index = int(choice)
        except ValueError:
            continue
        if 1 <= index <= len(ports):
            return str(ports[index - 1]["device"])


def copy_badge_app(py: Path, port: str, assume_yes: bool, skip_badge: bool) -> None:
    if skip_badge:
        return
    if not BADGE_APP.exists():
        raise SystemExit(f"Missing badge app: {BADGE_APP}")
    if not assume_yes and not confirm(f"Copy PC Chat bridge to badge on {port}?", True):
        return
    for source, destination in BADGE_ASSETS:
        if source.exists():
            run([str(py), "-m", "mpremote", "connect", port, "cp", str(source), destination])
    run([str(py), "-m", "mpremote", "connect", port, "cp", str(BADGE_APP), ":/apps/pc_chat_bridge.py"])
    run([str(py), "-m", "mpremote", "connect", port, "reset"])
    print("Badge app copied. On the badge, open Apps -> PC Chat.")


def confirm(prompt: str, default_yes: bool) -> bool:
    suffix = " [Y/n] " if default_yes else " [y/N] "
    answer = input(prompt + suffix).strip().lower()
    if not answer:
        return default_yes
    return answer in {"y", "yes"}


def run_web(py: Path) -> None:
    print("Starting web UI on http://127.0.0.1:8765")
    os.execv(str(py), [str(py), str(REPO_ROOT / "tools" / "pc_chat_web.py")])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install Hackaday Europe PC Chat.")
    parser.add_argument("--port", help="Badge serial port, for example /dev/ttyACM0, /dev/cu.usbmodem1101, or COM3")
    parser.add_argument("--yes", "-y", action="store_true", help="Use defaults without interactive prompts")
    parser.add_argument("--skip-udev", action="store_true", help="Do not install the Linux udev rule")
    parser.add_argument("--skip-badge", action="store_true", help="Install dependencies only; do not copy to the badge")
    parser.add_argument("--start-web", action="store_true", help="Start the browser UI after install")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        py = create_venv()
        install_linux_udev(args.yes, args.skip_udev)
        if args.skip_badge:
            print("Skipping badge copy.")
        else:
            port = choose_port(py, args.port, args.yes)
            copy_badge_app(py, port, args.yes, args.skip_badge)

        print("")
        print("Install complete.")
        if not args.skip_badge:
            print("Open Apps -> PC Chat on the badge.")
        print("Then start the browser UI with:")
        if os.name == "nt":
            print("  py run_web.py")
        else:
            print("  python3 run_web.py")

        if args.start_web:
            run_web(py)
        return 0
    except KeyboardInterrupt:
        print("\nInstall cancelled.")
        return 130
    except subprocess.CalledProcessError as exc:
        print("")
        print(f"Command failed with exit code {exc.returncode}.")
        print("Check the output above, then retry. If the badge is busy, close the web UI and run install again.")
        return exc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
