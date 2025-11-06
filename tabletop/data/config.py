"""Centralised configuration values for the tabletop application.

This module exposes path helpers that are required across multiple
subsystems.  Keeping them in a dedicated place avoids having to recompute
directory information in different modules and allows the UI layer to share
the same assumptions as the engine layer without duplicating constants.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


def load_tracker_hosts() -> dict[str, str]:
    path = Path("tracker_hosts.txt")
    if not path.is_absolute():
        path = ROOT / path
    hosts: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r"(\w+)\s*=\s*([0-9\.]+(?::\d{2,5})?)$", line)
            if match:
                hosts[match.group(1)] = match.group(2)
    hosts.setdefault("VP1", os.getenv("NEON_VP1_HOST", ""))
    hosts.setdefault("VP2", os.getenv("NEON_VP2_HOST", ""))
    return {key: value for key, value in hosts.items() if value}


# --- Root paths -----------------------------------------------------------

#: Absolute path to the repository root (directory containing ``app_vorbereitung.py``).
ROOT: Path = Path(__file__).resolve().parents[2]

#: Base directory holding UX assets such as button icons and backgrounds.
UX_DIR: Path = ROOT / "UX"

#: Directory containing all card face textures.
CARD_DIR: Path = ROOT / "Karten"

#: Directory holding CSV round definitions for card combinations.
CARD_COMBINATIONS_DIR: Path = ROOT / "Kartenkombinationen"

#: Location of the optional ArUco overlay helper script.
ARUCO_OVERLAY_PATH: Path = ROOT / "tabletop" / "aruco_overlay.py"


__all__ = [
    "ROOT",
    "UX_DIR",
    "CARD_DIR",
    "CARD_COMBINATIONS_DIR",
    "ARUCO_OVERLAY_PATH",
    "load_tracker_hosts",
]

