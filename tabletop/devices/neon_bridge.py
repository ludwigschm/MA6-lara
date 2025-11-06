"""Neon-based implementation skeleton for the tabletop Pupil bridge."""

from __future__ import annotations

import logging
import queue
import threading
from typing import Dict, Iterable, Optional, Tuple


class PupilBridge:
    """Experimental Neon bridge implementation.

    The class mirrors the public interface expected by the rest of the
    application but only logs the usage for now. A dedicated worker thread is
    prepared for future event handling responsibilities.
    """

    def __init__(self) -> None:
        self._logger = logging.getLogger(__name__)
        self._hosts: dict[str, str] = {}
        self._devices: dict[str, object] = {}
        self._connected: set[str] = set()
        self._event_q: queue.Queue = queue.Queue(maxsize=2048)
        self._worker = threading.Thread(
            target=self._event_worker,
            name="NeonBridgeWorker",
            daemon=True,
        )
        self._logger.info("Neon PupilBridge initialisiert – Funktionen noch nicht verfügbar.")

    # ------------------------------------------------------------------
    # Connection handling
    def configure_hosts(self, hosts: dict[str, str]) -> None:
        self._hosts = {str(key): str(value) for key, value in hosts.items()}
        if self._hosts:
            formatted = ", ".join(f"{key}={value}" for key, value in sorted(self._hosts.items()))
            self._logger.info("Tracker-Hosts konfiguriert: %s", formatted)
        else:
            self._logger.info("Tracker-Hosts konfiguriert: (leer)")

    def connect(self) -> None:
        self._logger.warning("connect() wurde aufgerufen, ist aber noch nicht implementiert.")

    def close(self) -> None:
        self._logger.warning("close() wurde aufgerufen, ist aber noch nicht implementiert.")

    shutdown = close  # Backwards compatibility alias

    # ------------------------------------------------------------------
    # Device discovery helpers
    def connected_players(self) -> Iterable[str]:
        self._logger.debug("connected_players() abgefragt – derzeit keine Verbindungen.")
        return tuple(self._connected)

    def is_connected(self, player: str) -> bool:
        state = player in self._connected
        self._logger.debug("is_connected(%s) -> %s", player, state)
        return state

    # ------------------------------------------------------------------
    # Recording helpers – implemented as placeholders
    def ensure_recordings(
        self,
        *,
        session: Optional[int],
        block: Optional[int],
        players: Optional[Iterable[str]] = None,
    ) -> None:
        self._logger.warning(
            "ensure_recordings(session=%s, block=%s, players=%s) noch nicht implementiert.",
            session,
            block,
            tuple(players) if players is not None else None,
        )

    def start_recording(self, session: int, block: int, player: str) -> None:
        self._logger.warning(
            "start_recording(session=%s, block=%s, player=%s) noch nicht implementiert.",
            session,
            block,
            player,
        )

    def stop_recording(self, player: str) -> None:
        self._logger.warning("stop_recording(player=%s) noch nicht implementiert.", player)

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
        self._logger.warning(
            "send_event(name=%s, player=%s, payload=%s, priority=%s) noch nicht implementiert.",
            name,
            player,
            payload,
            priority,
        )

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
        self._logger.warning(
            "refine_event(player=%s, event_id=%s, t_ref_ns=%s, confidence=%s, mapping_version=%s, extra=%s) noch nicht implementiert.",
            player,
            event_id,
            t_ref_ns,
            confidence,
            mapping_version,
            extra,
        )

    def event_queue_load(self) -> Tuple[int, int]:
        load = self._event_q.qsize()
        max_size = self._event_q.maxsize if self._event_q.maxsize > 0 else -1
        self._logger.debug("event_queue_load() -> (load=%s, capacity=%s)", load, max_size)
        return load, max_size

    def estimate_time_offset(self, player: str) -> Optional[float]:
        self._logger.warning("estimate_time_offset(player=%s) noch nicht implementiert.", player)
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    def _event_worker(self) -> None:
        raise NotImplementedError("Der Neon-Bridge Event-Worker ist noch nicht implementiert.")


__all__ = ["PupilBridge"]
