#!/usr/bin/env python3
"""Run the PC Chat browser UI using the local virtual environment if present."""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
if os.name == "nt":
    VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
else:
    VENV_PYTHON = ROOT / ".venv" / "bin" / "python"

TARGET = ROOT / "tools" / "pc_chat_web.py"


def main() -> int:
    python = VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable)
    os.execv(str(python), [str(python), str(TARGET), *sys.argv[1:]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
