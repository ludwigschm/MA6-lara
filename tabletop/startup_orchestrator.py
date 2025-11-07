from __future__ import annotations

import logging
import time
from enum import Enum, auto
from typing import Iterable, Optional, TYPE_CHECKING, Set

from kivy.clock import Clock

if TYPE_CHECKING:  # pragma: no cover - typing helper
    from tabletop.pupil_bridge import PupilBridge
    from tabletop.tabletop_view import TabletopRoot


log = logging.getLogger(__name__)


class StartupState(Enum):
    INIT = auto()
    WAIT_SESSION = auto()
    CONNECTING = auto()
    STARTING = auto()
    READY = auto()
    RUNNING = auto()
    ERROR = auto()


class StartupOrchestrator:
    CONNECT_TIMEOUT_S = 8.0
    START_RETRIES = 3
    START_TIMEOUT_S = 4.0
    RETRY_DELAY_S = 0.8
    CONNECT_POLL_INTERVAL_S = 0.2

    def __init__(self) -> None:
        self._state = StartupState.INIT
        self._root: Optional["TabletopRoot"] = None
        self._bridge: Optional["PupilBridge"] = None
        self._session_id: Optional[str] = None
        self._block_id: Optional[str] = None
        self._connect_deadline = 0.0
        self._start_deadline = 0.0
        self._start_retries_left = 0
        self._connect_event = None
        self._start_event = None
        self._start_retry_event = None
        self._error_message = ""
        self._current_start_attempt = 0

    # ------------------------------------------------------------------
    # Public API
    def attach(self, root: "TabletopRoot", bridge: "PupilBridge") -> None:
        self._root = root
        self._bridge = bridge
        self._cancel_connect_poll()
        self._cancel_start_poll()
        self._cancel_start_retry()
        if self._state == StartupState.INIT:
            self._transition(StartupState.WAIT_SESSION)
        log.debug("StartupOrchestrator attached (bridge=%s)", type(bridge).__name__)
        self._notify_status_update()

    def set_session(self, session_id: str, block_id: Optional[str] = None) -> None:
        self._session_id = session_id
        self._block_id = block_id
        self._cancel_connect_poll()
        self._cancel_start_poll()
        self._cancel_start_retry()
        self._error_message = ""
        if self._state != StartupState.WAIT_SESSION:
            self._transition(StartupState.WAIT_SESSION)
        log.info(
            "StartupOrchestrator: Session gesetzt (session=%s, block=%s)",
            session_id,
            block_id,
        )
        self._notify_status_update()

    def begin_connect(self) -> None:
        if self._state not in (StartupState.WAIT_SESSION, StartupState.ERROR):
            log.debug(
                "StartupOrchestrator: begin_connect ignoriert im Zustand %s",
                self._state.name,
            )
            return
        if not self._session_id:
            log.info(
                "StartupOrchestrator: Verbindung nicht gestartet – Session fehlt."
            )
            return
        if not self._bridge:
            log.info(
                "StartupOrchestrator: Verbindung nicht gestartet – keine Bridge vorhanden."
            )
            return

        self._cancel_connect_poll()
        self._cancel_start_poll()
        self._cancel_start_retry()
        self._error_message = ""
        self._current_start_attempt = 0
        self._transition(StartupState.CONNECTING)
        self._connect_deadline = time.monotonic() + self.CONNECT_TIMEOUT_S
        hosts = getattr(self._bridge, "_hosts", {})
        try:
            detected_players = tuple(self._bridge.connected_players())
        except Exception:  # pragma: no cover - defensive logging
            log.debug(
                "StartupOrchestrator: Initial connected_players lookup fehlgeschlagen",
                exc_info=True,
            )
            detected_players = tuple()
        hosts_description = ", ".join(
            f"{player}:{host}" for player, host in sorted(hosts.items())
        ) or "-"
        log.info(
            "StartupOrchestrator: Verbinde Tracker (Timeout %.1fs, session=%s, block=%s, gefundene Spieler=%d, hosts=%s)",
            self.CONNECT_TIMEOUT_S,
            self._session_id,
            self._block_id,
            len(detected_players),
            hosts_description,
        )
        try:
            self._bridge.connect()
        except Exception:  # pragma: no cover - defensive logging
            log.exception("StartupOrchestrator: bridge.connect() fehlgeschlagen")
        self._schedule_connect_poll(delay=0.0)
        self._notify_status_update()

    def begin_start_recordings(self) -> None:
        if self._state not in (StartupState.CONNECTING, StartupState.STARTING):
            log.debug(
                "StartupOrchestrator: begin_start_recordings ignoriert im Zustand %s",
                self._state.name,
            )
            return
        if not self._root:
            log.debug("StartupOrchestrator: Kein Root für Recording-Start vorhanden")
            return

        self._transition(StartupState.STARTING)
        self._start_retries_left = self.START_RETRIES
        self._current_start_attempt = 0
        self._start_next_attempt()

    def is_ready(self) -> bool:
        return self._state == StartupState.READY

    def current_state(self) -> StartupState:
        return self._state

    def error_message(self) -> str:
        return self._error_message

    def current_start_attempt(self) -> int:
        return self._current_start_attempt

    # ------------------------------------------------------------------
    # Internal helpers
    def should_attempt_recordings(self) -> bool:
        return self._state in (
            StartupState.STARTING,
            StartupState.READY,
            StartupState.RUNNING,
        )

    def mark_running(self) -> None:
        if self._state == StartupState.READY:
            self._transition(StartupState.RUNNING)

    def _transition(self, new_state: StartupState) -> None:
        if self._state == new_state:
            return
        log.info(
            "StartupOrchestrator: %s -> %s",
            self._state.name,
            new_state.name,
        )
        self._state = new_state
        if new_state != StartupState.STARTING:
            self._current_start_attempt = 0
        if new_state != StartupState.ERROR:
            self._error_message = ""
        self._notify_state_changed()
        self._notify_status_update()

    def _notify_state_changed(self) -> None:
        root = self._root
        if root is None:
            return
        try:
            root.on_startup_state_changed(self._state)
        except Exception:
            log.debug("StartupOrchestrator: state callback failed", exc_info=True)

    def _notify_status_update(self) -> None:
        root = self._root
        if root is None:
            return
        try:
            root.update_startup_status_overlay()
        except Exception:
            log.debug("StartupOrchestrator: status callback failed", exc_info=True)

    def _schedule_connect_poll(self, *, delay: float) -> None:
        self._cancel_connect_poll()
        self._connect_event = Clock.schedule_once(self._poll_connect_status, delay)

    def _cancel_connect_poll(self) -> None:
        event = self._connect_event
        if event is not None:
            try:
                event.cancel()
            except Exception:
                log.debug("StartupOrchestrator: Cancel connect poll failed", exc_info=True)
        self._connect_event = None

    def _poll_connect_status(self, _dt: float) -> None:
        if self._state != StartupState.CONNECTING:
            return
        now = time.monotonic()
        connected = self._connected_players()
        expected = self._expected_players()
        self._notify_status_update()
        bridge = self._bridge
        connected_players_raw = set()
        connected_flags: Set[str] = set()
        if bridge is not None:
            try:
                connected_players_raw = {str(player) for player in bridge.connected_players() if player}
            except Exception:  # pragma: no cover - defensive logging
                log.debug(
                    "StartupOrchestrator: Verbinde Poll connected_players fehlgeschlagen",
                    exc_info=True,
                )
                connected_players_raw = set(connected)
            for player in expected:
                try:
                    if bridge.is_connected(player):
                        connected_flags.add(player)
                except Exception:  # pragma: no cover - defensive logging
                    log.debug(
                        "StartupOrchestrator: is_connected(%s) fehlgeschlagen", player, exc_info=True
                    )
        if expected and expected.issubset(connected) and expected.issubset(connected_players_raw) and expected.issubset(connected_flags):
            log.info(
                "StartupOrchestrator: Alle Tracker verbunden (%s)",
                ", ".join(sorted(connected)),
            )
            self.begin_start_recordings()
            return
        if now >= self._connect_deadline:
            log.error("StartupOrchestrator: Verbindungsaufbau abgelaufen")
            self._error_message = "Tracker nicht erreichbar"
            self._transition(StartupState.ERROR)
            return
        log.debug(
            "StartupOrchestrator: Tracker warten – erwartet=%s, verbunden=%s",
            sorted(expected),
            sorted(connected),
        )
        self._schedule_connect_poll(delay=self.CONNECT_POLL_INTERVAL_S)

    def _start_next_attempt(self) -> None:
        if self._state != StartupState.STARTING:
            return
        if self._start_retries_left <= 0:
            log.error("StartupOrchestrator: Recording-Start fehlgeschlagen")
            self._error_message = "Aufnahmen konnten nicht gestartet werden."
            self._transition(StartupState.ERROR)
            return

        self._start_retries_left -= 1
        attempt_number = self.START_RETRIES - self._start_retries_left
        self._current_start_attempt = attempt_number
        log.info(
            "StartupOrchestrator: Starte Aufnahmen (Versuch %d/%d)",
            attempt_number,
            self.START_RETRIES,
        )
        try:
            self._root._ensure_bridge_recordings(force=True)  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - defensive logging
            log.exception("StartupOrchestrator: ensure_recordings fehlgeschlagen")
        self._start_deadline = time.monotonic() + self.START_TIMEOUT_S
        self._schedule_start_poll(delay=max(self.RETRY_DELAY_S, 0.2))
        self._cancel_start_retry()
        self._notify_status_update()

    def _schedule_start_poll(self, *, delay: float) -> None:
        self._cancel_start_poll()
        self._start_event = Clock.schedule_once(self._poll_start_status, delay)

    def _cancel_start_poll(self) -> None:
        event = self._start_event
        if event is not None:
            try:
                event.cancel()
            except Exception:
                log.debug("StartupOrchestrator: Cancel start poll failed", exc_info=True)
        self._start_event = None

    def _poll_start_status(self, _dt: float) -> None:
        if self._state != StartupState.STARTING:
            return
        now = time.monotonic()
        self._notify_status_update()
        if self._recordings_active():
            log.info("StartupOrchestrator: Alle Aufnahmen laufen.")
            self._transition(StartupState.READY)
            self._cancel_start_poll()
            self._cancel_start_retry()
            return
        if now >= self._start_deadline:
            self._schedule_start_attempt_retry()
            return
        self._schedule_start_poll(delay=self.RETRY_DELAY_S)

    def _schedule_start_attempt_retry(self) -> None:
        if self._state != StartupState.STARTING:
            return
        if self._start_retries_left <= 0:
            log.error("StartupOrchestrator: Aufnahmen konnten nicht gestartet werden")
            self._error_message = "Aufnahmen konnten nicht gestartet werden."
            self._transition(StartupState.ERROR)
            return
        self._cancel_start_retry()
        self._start_retry_event = Clock.schedule_once(
            self._execute_start_retry, self.RETRY_DELAY_S
        )
        self._notify_status_update()

    def _execute_start_retry(self, _dt: float) -> None:
        self._start_next_attempt()

    def _recordings_active(self) -> bool:
        root = self._root
        if root is None:
            return False
        expected = self._expected_players()
        active: Set[str] = set(getattr(root, "_bridge_recordings_active", set()))
        if expected:
            return expected.issubset(active)
        return bool(active)

    def _cancel_start_retry(self) -> None:
        event = self._start_retry_event
        if event is not None:
            try:
                event.cancel()
            except Exception:
                log.debug("StartupOrchestrator: Cancel start retry failed", exc_info=True)
        self._start_retry_event = None

    def _expected_players(self) -> Set[str]:
        root = self._root
        if root is None:
            return set()
        players: Set[str] = set()
        for value in getattr(root, "_bridge_players", set()):
            if value:
                players.add(str(value))
        fallback = getattr(root, "_bridge_player", None)
        if fallback:
            players.add(str(fallback))
        return players

    def _connected_players(self) -> Set[str]:
        root = self._root
        if root is not None:
            try:
                return set(root._bridge_ready_players())  # type: ignore[attr-defined]
            except Exception:
                log.debug("StartupOrchestrator: bridge_ready_players fehlgeschlagen", exc_info=True)
        if self._bridge is None:
            return set()
        try:
            players: Iterable[str] = self._bridge.connected_players()
        except Exception:  # pragma: no cover - defensive logging
            log.debug("StartupOrchestrator: connected_players fehlgeschlagen", exc_info=True)
            return set()
        return {str(player) for player in players if player}

