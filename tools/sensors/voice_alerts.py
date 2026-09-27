#!/usr/bin/env python3
"""Spoken alerts; the code now lives in app/rescuebot/voice.py.

Kept so `rescue_sensors.py --voice` and the old test command still work:
    python3 voice_alerts.py "Warning. Person detected ahead."
"""

from pathlib import Path
import sys

try:
    import rescuebot.voice  # noqa: F401  (installed package)
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

from rescuebot.voice import *  # noqa: E402,F401,F403
from rescuebot.voice import main  # noqa: E402

if __name__ == "__main__":
    main()
