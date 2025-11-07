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
        self._start_sequence_event = None
        self._start_sequence_players: list[str] = []
        self._start_sequence_index = 0
        self._start_sequence_current_player: Optional[str] = None
        self._start_sequence_started_at = 0.0
        self._start_attempt_started_at = 0.0
        self._error_message = ""
        self._current_start_attempt = 0
        self._connect_attempt = 0
        self._connect_started_at = 0.0
        self._startup_began_at = 0.0

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
        self._connect_attempt += 1
        self._transition(StartupState.CONNECTING)
        self._connect_deadline = time.monotonic() + self.CONNECT_TIMEOUT_S
        self._connect_started_at = time.monotonic()
        self._startup_began_at = self._connect_started_at
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
        self._start_sequence_players = ["VP1", "VP2"]
        self._start_sequence_index = 0
        self._start_sequence_current_player = None
        self._start_sequence_started_at = 0.0
        self._start_attempt_started_at = 0.0
        self._cancel_start_poll()
        self._cancel_start_retry()
        self._schedule_start_sequence(delay=0.3)
        self._notify_status_update()

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
            self._cancel_start_sequence_event()
            self._start_sequence_current_player = None
            self._start_sequence_started_at = 0.0
            self._start_attempt_started_at = 0.0
        if new_state != StartupState.ERROR:
            self._error_message = ""
        else:
            self._log_error_state()
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
            elapsed_ms = int(max(0.0, (time.monotonic() - self._connect_started_at) * 1000.0))
            self._log_attempt_result(
                phase=StartupState.CONNECTING,
                player="*",
                attempt=self._connect_attempt,
                elapsed_ms=elapsed_ms,
                result="success",
                details={
                    "expected": ",".join(sorted(expected)) or "-",
                    "connected": ",".join(sorted(connected)) or "-",
                },
            )
            log.info(
                "StartupOrchestrator: Alle Tracker verbunden (%s)",
                ", ".join(sorted(connected)),
            )
            self.begin_start_recordings()
            return
        if now >= self._connect_deadline:
            elapsed_ms = int(max(0.0, (time.monotonic() - self._connect_started_at) * 1000.0))
            self._log_attempt_result(
                phase=StartupState.CONNECTING,
                player="*",
                attempt=self._connect_attempt,
                elapsed_ms=elapsed_ms,
                result="timeout",
                details={
                    "expected": ",".join(sorted(expected)) or "-",
                    "connected": ",".join(sorted(connected)) or "-",
                },
                level=logging.ERROR,
            )
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

    def _schedule_start_sequence(self, *, delay: float) -> None:
        self._cancel_start_sequence_event()
        self._start_sequence_event = Clock.schedule_once(
            self._start_sequence_step, delay
        )

    def _cancel_start_sequence_event(self) -> None:
        event = self._start_sequence_event
        if event is not None:
            try:
                event.cancel()
            except Exception:
                log.debug("StartupOrchestrator: Cancel start sequence failed", exc_info=True)
        self._start_sequence_event = None

    def _poll_start_status(self, _dt: float) -> None:
        self._start_event = None
        if self._state != StartupState.STARTING:
            return
        now = time.monotonic()
        player = self._start_sequence_current_player
        if player and self._player_is_recording(player):
            self._on_player_started(player)
            return
        if now >= self._start_deadline:
            self._schedule_start_attempt_retry()
            return
        self._schedule_start_poll(delay=0.2)

    def _schedule_start_attempt_retry(self) -> None:
        if self._state != StartupState.STARTING:
            return
        player = self._start_sequence_current_player
        attempt = self._current_start_attempt or 0
        elapsed_ms = int(
            max(0.0, (time.monotonic() - self._start_attempt_started_at) * 1000.0)
        )
        if player and attempt:
            self._log_attempt_result(
                phase=StartupState.STARTING,
                player=player,
                attempt=attempt,
                elapsed_ms=elapsed_ms,
                result="timeout",
                level=logging.WARNING,
            )
        if self._start_retries_left <= 0:
            self._handle_sequence_failure(
                player,
                message=f"Timeout nach {self.START_TIMEOUT_S:.1f}s",
                log_attempt=False,
            )
            return
        if player:
            log.warning(
                "StartupOrchestrator: Aufnahme %s wurde nicht bestätigt – nächster Versuch in %.1fs.",
                player,
                self.RETRY_DELAY_S,
            )
        self._cancel_start_retry()
        self._start_retry_event = Clock.schedule_once(
            self._execute_start_retry, self.RETRY_DELAY_S
        )
        self._start_attempt_started_at = 0.0
        self._notify_status_update()

    def _execute_start_retry(self, _dt: float) -> None:
        self._start_retry_event = None
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

    def _start_sequence_step(self, _dt: float) -> None:
        self._start_sequence_event = None
        if self._state != StartupState.STARTING:
            return
        if not self._start_sequence_players:
            self._start_sequence_players = ["VP1", "VP2"]
        self._start_sequence_index = 0
        self._advance_start_sequence()

    def _advance_start_sequence(self) -> None:
        if self._state != StartupState.STARTING:
            return
        players = self._start_sequence_players or ["VP1", "VP2"]
        expected = self._expected_players()
        while self._start_sequence_index < len(players):
            player = players[self._start_sequence_index]
            if expected and player not in expected:
                log.info(
                    "StartupOrchestrator: Aufnahme %s nicht erwartet – überspringe Start.",
                    player,
                )
                self._start_sequence_index += 1
                continue
            if self._player_is_recording(player):
                log.info(
                    "StartupOrchestrator: Aufnahme %s läuft bereits – überspringe Start.",
                    player,
                )
                self._start_sequence_index += 1
                self._mark_player_recording(player)
                continue
            self._start_sequence_current_player = player
            self._start_sequence_started_at = 0.0
            self._start_retries_left = self.START_RETRIES
            self._current_start_attempt = 0
            self._start_attempt_started_at = 0.0
            self._cancel_start_poll()
            self._cancel_start_retry()
            self._start_next_attempt()
            return

        self._start_sequence_current_player = None
        self._finish_start_sequence()

    def _start_next_attempt(self) -> None:
        if self._state != StartupState.STARTING:
            return
        player = self._start_sequence_current_player
        if not player:
            self._finish_start_sequence()
            return
        if self._player_is_recording(player):
            self._on_player_started(player)
            return
        if self._start_retries_left <= 0:
            self._handle_sequence_failure(player)
            return

        attempt_number = self.START_RETRIES - self._start_retries_left + 1
        self._current_start_attempt = attempt_number
        if attempt_number == 1:
            self._start_sequence_started_at = time.monotonic()
        self._start_retries_left -= 1
        self._start_attempt_started_at = time.monotonic()

        session_value, block_value = self._resolve_session_block()
        if session_value is None or block_value is None:
            log.error(
                "StartupOrchestrator: Session oder Block fehlt – kann Aufnahme %s nicht starten.",
                player,
            )
            self._handle_sequence_failure(player, message="Session oder Block fehlt")
            return

        bridge = self._bridge
        if bridge is None:
            log.error(
                "StartupOrchestrator: Keine Bridge verfügbar – Aufnahme %s kann nicht gestartet werden.",
                player,
            )
            self._handle_sequence_failure(player, message="Bridge nicht verfügbar")
            return

        log.info(
            "StartupOrchestrator: Starte Aufnahme %s (Versuch %d/%d)",
            player,
            self._current_start_attempt,
            self.START_RETRIES,
        )
        try:
            bridge.start_recording(session_value, block_value, player)
        except Exception:
            log.exception(
                "StartupOrchestrator: start_recording(%s) fehlgeschlagen",
                player,
            )

        self._start_deadline = time.monotonic() + self.START_TIMEOUT_S
        self._schedule_start_poll(delay=0.2)
        self._cancel_start_retry()
        self._notify_status_update()

    def _on_player_started(self, player: str) -> None:
        started_at = self._start_sequence_started_at or time.monotonic()
        attempt_started_at = self._start_attempt_started_at or started_at
        elapsed_ms = max(0.0, (time.monotonic() - attempt_started_at) * 1000.0)
        attempt = self._current_start_attempt or 0
        if attempt:
            self._log_attempt_result(
                phase=StartupState.STARTING,
                player=player,
                attempt=attempt,
                elapsed_ms=int(elapsed_ms),
                result="success",
            )
        log.info(
            "StartupOrchestrator: Player %s läuft (t=%dms)",
            player,
            int(elapsed_ms),
        )
        self._mark_player_recording(player)
        self._start_sequence_index += 1
        self._start_sequence_current_player = None
        self._current_start_attempt = 0
        self._start_attempt_started_at = 0.0
        self._start_retries_left = self.START_RETRIES
        self._cancel_start_poll()
        self._cancel_start_retry()
        self._notify_status_update()
        self._advance_start_sequence()

    def _finish_start_sequence(self) -> None:
        expected = self._expected_players()
        players = [
            player
            for player in self._start_sequence_players
            if player and (not expected or player in expected)
        ]
        if players and not all(self._player_is_recording(player) for player in players):
            self._handle_sequence_failure(None)
            return
        session_value, block_value = self._resolve_session_block()
        if self._startup_began_at:
            total_elapsed_ms = int(
                max(0.0, (time.monotonic() - self._startup_began_at) * 1000.0)
            )
        else:
            total_elapsed_ms = 0
        log.info(
            "Startup READY session_id=%s block_id=%s players=%s total_elapsed_ms=%d",
            self._session_id or session_value,
            self._block_id or block_value,
            ",".join(players) or "-",
            total_elapsed_ms,
        )
        log.info("StartupOrchestrator: Alle Aufnahmen laufen.")
        self._transition(StartupState.READY)
        self._cancel_start_poll()
        self._cancel_start_retry()

    def _handle_sequence_failure(
        self,
        player: Optional[str],
        *,
        message: str | None = None,
        log_attempt: bool = True,
    ) -> None:
        attempt = self._current_start_attempt or 0
        elapsed_ms = int(
            max(0.0, (time.monotonic() - self._start_attempt_started_at) * 1000.0)
        )
        player_label = player or "*"
        if attempt and log_attempt:
            self._log_attempt_result(
                phase=StartupState.STARTING,
                player=player_label,
                attempt=attempt,
                elapsed_ms=elapsed_ms,
                result="error",
                details={"reason": message or "-"},
                level=logging.ERROR,
            )
        if player:
            log.error(
                "StartupOrchestrator: Aufnahme %s konnte nicht gestartet werden.",
                player,
            )
            reason = f": {message}" if message else ""
            self._error_message = f"Aufnahme {player} konnte nicht gestartet werden{reason}."
        else:
            log.error("StartupOrchestrator: Aufnahmen konnten nicht gestartet werden")
            reason = f": {message}" if message else ""
            self._error_message = f"Aufnahmen konnten nicht gestartet werden{reason}."
        self._start_attempt_started_at = 0.0
        self._cancel_start_poll()
        self._cancel_start_retry()
        self._cancel_start_sequence_event()
        self._start_sequence_current_player = None
        self._transition(StartupState.ERROR)

    def _player_is_recording(self, player: str) -> bool:
        root = self._root
        if root is not None:
            try:
                active = getattr(root, "_bridge_recordings_active", set())
            except Exception:
                active = set()
            else:
                if player in active:
                    return True

        bridge = self._bridge
        if bridge is None:
            return False
        try:
            method = getattr(bridge, "is_recording")
        except AttributeError:
            return False
        try:
            return bool(method(player))
        except Exception:
            log.debug(
                "StartupOrchestrator: is_recording(%s) fehlgeschlagen", player, exc_info=True
            )
            return False

    def _mark_player_recording(self, player: str) -> None:
        root = self._root
        if root is None:
            return
        try:
            active = getattr(root, "_bridge_recordings_active")
        except Exception:
            active = None
        if not isinstance(active, set):
            active = set()
            setattr(root, "_bridge_recordings_active", active)
        active.add(player)
        _session, block = self._resolve_session_block()
        if block is not None:
            try:
                setattr(root, "_bridge_recording_block", block)
            except Exception:
                log.debug("StartupOrchestrator: Block-Update fehlgeschlagen", exc_info=True)

    def _resolve_session_block(self) -> tuple[Optional[int], Optional[int]]:
        def _parse(value: object) -> Optional[int]:
            if value is None:
                return None
            try:
                return int(str(value))
            except (TypeError, ValueError):
                return None

        session_value = _parse(self._session_id)
        block_value = _parse(self._block_id)

        root = self._root
        if root is not None:
            if session_value is None:
                session_value = _parse(getattr(root, "_bridge_session", None))
            if block_value is None:
                block_value = _parse(getattr(root, "_bridge_block", None))

        return session_value, block_value

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

    def _log_attempt_result(
        self,
        *,
        phase: StartupState,
        player: str,
        attempt: int,
        elapsed_ms: int,
        result: str,
        details: Optional[dict[str, object]] = None,
        level: int = logging.INFO,
    ) -> None:
        parts = [
            f"phase={phase.name}",
            f"player={player}",
            f"attempt={attempt}",
            f"elapsed_ms={elapsed_ms}",
            f"result={result}",
        ]
        for key, value in (details or {}).items():
            parts.append(f"{key}={value}")
        log.log(level, "StartupAttempt %s", " ".join(parts))

    def _log_error_state(self) -> None:
        user_message = self._error_message or "Unbekannter Fehler"
        expected = sorted(self._expected_players())
        connected = sorted(self._connected_players())
        recording = [player for player in expected if self._player_is_recording(player)]
        log.error("Startup fehlgeschlagen: %s", user_message)
        log.error(
            "Startup error user_message=\"%s\" connect_timeout_s=%.1f start_timeout_s=%.1f expected=%s connected=%s recording=%s state=%s",
            user_message,
            self.CONNECT_TIMEOUT_S,
            self.START_TIMEOUT_S,
            ",".join(expected) or "-",
            ",".join(connected) or "-",
            ",".join(sorted(recording)) or "-",
            self._state.name,
        )

