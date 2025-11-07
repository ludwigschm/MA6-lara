"""Centralised configuration values for the tabletop application.

This module exposes path helpers that are required across multiple
subsystems.  Keeping them in a dedicated place avoids having to recompute
directory information in different modules and allows the UI layer to share
the same assumptions as the engine layer without duplicating constants.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


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


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


@dataclass(frozen=True)
class AppConfig:
    """Runtime configuration for the tabletop application."""

    overlay_enabled: bool = False
    fullscreen_on_session: bool = False
    tracker_hosts: dict[str, str] = field(default_factory=dict)
    tracker_start_timeout_s: float = 8.0


def load_app_config(
    *,
    overlay_enabled: Optional[bool] = None,
    fullscreen_on_session: Optional[bool] = None,
    tracker_start_timeout_s: Optional[float] = None,
) -> AppConfig:
    """Load application configuration merging environment variables and overrides."""

    hosts = load_tracker_hosts()
    default_overlay = _env_flag("NEON_OVERLAY_ENABLED", False)
    default_fullscreen = _env_flag("NEON_FULLSCREEN_ON_SESSION", False)
    default_timeout = _env_float("NEON_TRACKER_START_TIMEOUT", 8.0)

    return AppConfig(
        overlay_enabled=default_overlay if overlay_enabled is None else bool(overlay_enabled),
        fullscreen_on_session=
            default_fullscreen if fullscreen_on_session is None else bool(fullscreen_on_session),
        tracker_hosts=hosts,
        tracker_start_timeout_s=
            default_timeout if tracker_start_timeout_s is None else float(tracker_start_timeout_s),
    )


__all__ = [
    "ROOT",
    "UX_DIR",
    "CARD_DIR",
    "CARD_COMBINATIONS_DIR",
    "ARUCO_OVERLAY_PATH",
    "load_tracker_hosts",
    "AppConfig",
    "load_app_config",
]

