"""Minimal stubs for the legacy Pupil Labs bridge interface.

This module intentionally keeps a very small surface so that existing parts of

the tabletop application can run without the optional eye-tracking integration.
All methods are implemented as no-ops and no external dependencies are
required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional, Tuple


@dataclass
class PupilBridge:
    """No-op replacement for the historical Pupil bridge.

    The original project integrated with the Pupil Labs realtime API to control
    recordings and forward events. For the current setup we only need the CSV
    logging, so the bridge collapses into a minimal stub that fulfils the
    interface expected by the UI without performing any side effects.
    """

    _connected: Dict[str, bool] = field(default_factory=dict)
    _hosts: Dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Connection handling
    def configure_hosts(self, hosts: Dict[str, str]) -> None:
        self._hosts = dict(hosts)

    def connect(self) -> None:
        """Pretend to establish a connection to eye-tracking devices."""

    def close(self) -> None:
        """Pretend to close the bridge connection."""

    shutdown = close  # Backwards compatibility alias

    # ------------------------------------------------------------------
    # Device discovery helpers
    def connected_players(self) -> Iterable[str]:
        """Return the identifiers of connected players (always empty)."""

        return tuple(self._connected.keys())

    def is_connected(self, player: str) -> bool:
        return bool(self._connected.get(player, False))

    # ------------------------------------------------------------------
    # Recording helpers – implemented as no-ops
    def ensure_recordings(
        self,
        *,
        session: Optional[int],
        block: Optional[int],
        players: Optional[Iterable[str]] = None,
    ) -> None:
        return None

    def start_recording(self, session: int, block: int, player: str) -> None:
        return None

    def stop_recording(self, player: str) -> None:
        return None

    # ------------------------------------------------------------------
    # Event helpers
    def send_event(
        self,
        name: str,
        player: str,
        payload: Optional[Dict[str, object]] = None,
        *,
        priority: str | None = None,
    ) -> None:
        return None

    def refine_event(
        self,
        player: str,
        event_id: str,
        t_ref_ns: int,
        *,
        confidence: float,
        mapping_version: int,
        extra: Optional[Dict[str, object]] = None,
    ) -> None:
        return None

    def event_queue_load(self) -> Tuple[int, int]:
        return (0, 1)

    def estimate_time_offset(self, player: str) -> Optional[float]:
        return None


__all__ = ["PupilBridge"]
