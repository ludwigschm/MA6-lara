"""Neon-based implementation skeleton for the tabletop Pupil bridge."""

from __future__ import annotations

import asyncio
import concurrent.futures
import itertools
import json
import logging
import queue
import socket
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import urlparse

import pupil_labs.realtime_api as plrt


class PupilBridge:
    """Experimental Neon bridge implementation.

    The class mirrors the public interface expected by the rest of the
    application but only logs the usage for now. A dedicated worker thread is
    prepared for future event handling responsibilities.
    """

    def __init__(self) -> None:
        self._logger = logging.getLogger(__name__)
        self._hosts: dict[str, str] = {}
        self._devices: dict[str, plrt.Device] = {}
        self._connected: set[str] = set()
        self._event_q: "queue.PriorityQueue[tuple[int, int, object]]" = queue.PriorityQueue(
            maxsize=2048
        )
        self._event_sequence = itertools.count()
        self._queue_sentinel = object()
        self._worker_stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._recording_states: dict[str, bool] = {}
        self._recording_labels: dict[str, str] = {}
        self._event_retry_base = 0.2
        self._mirror_lock = threading.RLock()
        self._host_mirror: dict[str, dict[str, dict[str, Any]]] = {}
        self._host_log_lock = threading.Lock()
        self._host_log_path = Path.cwd() / "runs" / "neon_host_mirror.jsonl"
        self._logger.info("Neon PupilBridge initialisiert – bereit für Verbindungen.")
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, name="NeonAsyncLoop", daemon=True
        )
        self._loop_thread.start()

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
        if not self._hosts:
            self._logger.info("Keine Tracker-Hosts konfiguriert – überspringe Verbindung.")
            return

        self._logger.info("Starte Verbindungsaufbau zu %d Trackern.", len(self._hosts))

        for player, host in sorted(self._hosts.items()):
            if player in self._connected:
                continue

            hostname, port = self._resolve_endpoint(host)
            if not self._probe_endpoint(player, host, hostname, port):
                continue

            try:
                device = self._call_in_loop(plrt.Device, hostname, port)
            except Exception as exc:  # pragma: no cover - depends on external API
                self._logger.warning(
                    "Neon-Tracker %s (%s) konnte nicht verbunden werden: %s",
                    player,
                    host,
                    exc,
                )
                continue

            self._devices[player] = device
            self._connected.add(player)
            self._logger.info(
                "Neon-Tracker %s verbunden (Host %s:%s).", player, hostname, port
            )

        if self._connected:
            self._start_worker()
        else:
            self._logger.warning(
                "Kein Neon-Tracker erreichbar – verbleibe im Offline-Modus."
            )

    def close(self) -> None:
        if self._worker and self._worker.is_alive():
            self._logger.debug("Stoppe Neon-Event-Worker.")
            self._worker_stop.set()
            sentinel_token = (2, next(self._event_sequence), self._queue_sentinel)
            inserted = False
            while not inserted:
                try:
                    self._event_q.put(sentinel_token, timeout=0.5)
                except queue.Full:
                    self._logger.debug(
                        "Event-Warteschlange voll beim Stoppen – verwerfe ältesten Eintrag."
                    )
                    try:
                        self._event_q.get_nowait()
                    except queue.Empty:
                        continue
                    else:
                        self._event_q.task_done()
                else:
                    inserted = True
            self._worker.join(timeout=2.0)
        self._worker = None
        self._worker_stop.clear()

        for player, device in list(self._devices.items()):
            close = getattr(device, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as exc:  # pragma: no cover - external API dependent
                    self._logger.warning(
                        "Schließen des Neon-Trackers %s fehlgeschlagen: %s",
                        player,
                        exc,
                    )
            self._logger.info("Neon-Tracker %s getrennt.", player)

        self._devices.clear()
        self._connected.clear()
        try:
            if hasattr(self, "_loop"):
                self._loop.call_soon_threadsafe(self._loop.stop)
                if hasattr(self, "_loop_thread"):
                    self._loop_thread.join(timeout=1.0)
        except Exception:
            self._logger.debug("Async-Loop shutdown failed", exc_info=True)
        self._drain_queue()

    shutdown = close  # Backwards compatibility alias

    # ------------------------------------------------------------------
    # Device discovery helpers
    def connected_players(self) -> Iterable[str]:
        players = tuple(sorted(self._connected))
        self._logger.debug("connected_players() -> %s", players)
        return players

    def is_connected(self, player: str) -> bool:
        result = player in self._connected
        self._logger.debug("is_connected(%s) -> %s", player, result)
        return result

    # ------------------------------------------------------------------
    # Recording helpers – implemented as placeholders
    def ensure_recordings(
        self,
        *,
        session: Optional[int],
        block: Optional[int],
        players: Optional[Iterable[str]] = None,
    ) -> None:
        if session is None or block is None:
            self._logger.debug(
                "ensure_recordings() übersprungen – Session oder Block fehlt (session=%s, block=%s).",
                session,
                block,
            )
            return

        if players is None:
            active_players: Tuple[str, ...] = tuple(self.connected_players())
        else:
            active_players = tuple(str(player) for player in players if player)

        if not active_players:
            self._logger.debug("ensure_recordings() – keine aktiven Spieler gefunden.")
            return

        for player in active_players:
            if not self._is_recording(player):
                self.start_recording(session, block, player)

    def start_recording(self, session: int, block: int, player: str) -> None:
        device = self._devices.get(player)
        if device is None:
            self._logger.warning(
                "Start der Aufnahme für Spieler %s übersprungen – kein verbundenes Gerät.",
                player,
            )
            return

        label = f"s{session:02d}_b{block:02d}_{player}"
        recordings = getattr(device, "recordings", None)
        if recordings is None:
            self._logger.warning(
                "Gerät %s unterstützt keine Aufnahmen – start_recording übersprungen.",
                player,
            )
            return

        if self._is_recording(player, device=device):
            self._logger.debug(
                "Spieler %s nimmt bereits auf – starte nicht erneut (Label %s).",
                player,
                label,
            )
            self._recording_labels[player] = label
            return

        start_callable = getattr(recordings, "start", None)
        if not callable(start_callable):
            self._logger.warning(
                "Neon-API bietet keine start()-Methode für Spieler %s – Aufnahme kann nicht gestartet werden.",
                player,
            )
            return

        max_attempts = 3
        backoff_base = 0.2
        for attempt in range(1, max_attempts + 1):
            try:
                try:
                    start_callable(label=label)
                except TypeError:
                    start_callable(label)
                self._logger.info(
                    "Starte Neon-Aufnahme für Spieler %s (Label %s, Versuch %d/%d).",
                    player,
                    label,
                    attempt,
                    max_attempts,
                )
            except Exception as exc:  # pragma: no cover - depends on external API
                self._logger.error(
                    "Start der Neon-Aufnahme für Spieler %s fehlgeschlagen: %s", player, exc
                )
            else:
                if self._wait_for_recording_state(player, True, device=device):
                    self._recording_states[player] = True
                    self._recording_labels[player] = label
                    return
                self._logger.warning(
                    "Neon-Aufnahme für Spieler %s wurde nicht bestätigt – erneuter Versuch.",
                    player,
                )

            if attempt < max_attempts:
                backoff = backoff_base * (2 ** (attempt - 1))
                time.sleep(backoff)

        self._logger.error(
            "Neon-Aufnahme für Spieler %s konnte nach %d Versuchen nicht gestartet werden.",
            player,
            max_attempts,
        )

    def stop_recording(self, player: str) -> None:
        device = self._devices.get(player)
        if device is None:
            self._logger.warning(
                "Stoppen der Aufnahme für Spieler %s übersprungen – kein verbundenes Gerät.",
                player,
            )
            self._recording_states.pop(player, None)
            self._recording_labels.pop(player, None)
            return

        recordings = getattr(device, "recordings", None)
        if recordings is None:
            self._logger.warning(
                "Gerät %s unterstützt keine Aufnahmen – stop_recording übersprungen.",
                player,
            )
            self._recording_states.pop(player, None)
            self._recording_labels.pop(player, None)
            return

        if not self._is_recording(player, device=device):
            self._logger.debug(
                "Spieler %s führt keine aktive Aufnahme – Stop wird übersprungen.",
                player,
            )
            self._recording_states.pop(player, None)
            return

        stop_callable = (
            getattr(recordings, "stop_and_save", None)
            or getattr(recordings, "stop", None)
        )
        if not callable(stop_callable):
            self._logger.warning(
                "Neon-API bietet keine Stop-Methode für Spieler %s – Aufnahme kann nicht beendet werden.",
                player,
            )
            return

        try:
            label = self._recording_labels.get(player)
            if label is not None:
                try:
                    stop_callable(label=label)
                except TypeError:
                    stop_callable(label)
                except Exception:
                    stop_callable()
            else:
                stop_callable()
            self._logger.info("Stoppe Neon-Aufnahme für Spieler %s.", player)
        except Exception as exc:  # pragma: no cover - depends on external API
            self._logger.error(
                "Stoppen der Neon-Aufnahme für Spieler %s fehlgeschlagen: %s", player, exc
            )
            return

        if self._wait_for_recording_state(player, False, device=device, timeout=8.0):
            self._logger.info("Neon-Aufnahme für Spieler %s beendet und gespeichert.", player)
        else:
            self._logger.error(
                "Beenden der Neon-Aufnahme für Spieler %s wurde nicht bestätigt (Timeout).",
                player,
            )

        self._recording_states.pop(player, None)
        self._recording_labels.pop(player, None)

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
        if player not in self._connected:
            self._logger.warning(
                "Event %s ignoriert – Spieler %s nicht verbunden.",
                name,
                player,
            )
            return

        event_payload: Dict[str, object] = dict(payload or {})
        priority_level = 0 if priority == "high" else 1
        event: Dict[str, object] = {
            "player": player,
            "name": name,
            "payload": event_payload,
        }

        try:
            self._event_q.put_nowait(
                (priority_level, next(self._event_sequence), event)
            )
        except queue.Full:
            self._logger.warning(
                "Event-Warteschlange voll – priorisiere Ereignis %s für Spieler %s.",
                name,
                player,
            )
            evicted = self._drop_low_priority_event()
            if evicted:
                try:
                    self._event_q.put_nowait(
                        (priority_level, next(self._event_sequence), event)
                    )
                except queue.Full:
                    self._logger.warning(
                        "Event-Warteschlange voll – Ereignis %s für Spieler %s verworfen.",
                        name,
                        player,
                    )
            else:
                self._logger.warning(
                    "Event-Warteschlange voll – Ereignis %s für Spieler %s verworfen.",
                    name,
                    player,
                )

    def _drop_low_priority_event(self) -> bool:
        """Remove one low-priority event to make room, if available."""

        recovered: list[tuple[int, int, object]] = []
        evicted = False
        while True:
            try:
                priority_level, sequence, item = self._event_q.get_nowait()
            except queue.Empty:
                break
            if not evicted and priority_level > 0:
                evicted = True
                self._event_q.task_done()
                break
            recovered.append((priority_level, sequence, item))
            self._event_q.task_done()

        for entry in recovered:
            try:
                self._event_q.put_nowait(entry)
            except queue.Full:
                self._logger.debug(
                    "Event queue refill failed while recovering priority entries"
                )
                break

        return evicted

    def send_host_mirror(
        self,
        player: str,
        event_id: str,
        t_ref_ns: int,
        extra: Optional[Dict[str, object]] = None,
    ) -> None:
        entry = self._store_host_mirror(
            player,
            event_id,
            t_ref_ns,
            extra=extra,
        )
        self._logger.debug(
            "Host mirror gespeichert: %s/%s -> %dns (samples=%d)",
            player,
            event_id,
            int(t_ref_ns),
            len(entry.get("host_samples", [])),
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
        mirror_entry = self._store_host_refinement(
            player,
            event_id,
            t_ref_ns,
            confidence=confidence,
            mapping_version=mapping_version,
            extra=extra,
        )

        refinements = mirror_entry.get("refinements", [])
        if not refinements:
            return
        payload = refinements[-1]

        annotations = None
        device = self._devices.get(player)
        if device is not None:
            annotations = getattr(device, "annotations", None)

        if annotations is None:
            self._logger.info(
                "Gerät %s unterstützt keine Refinements – speichere hostseitig.",
                player,
            )
            return

        refine_fn = getattr(annotations, "refine", None)
        if not callable(refine_fn):
            self._logger.info(
                "Annotations-Interface des Geräts %s bietet kein refine().", player
            )
            return

        try:
            refine_fn(
                event_id=event_id,
                timestamp_ns=payload["t_ref_ns"],
                confidence=payload["confidence"],
                mapping_version=payload["mapping_version"],
                extra=payload["extra"],
            )
        except TypeError:
            try:
                refine_fn(
                    event_id,
                    payload["t_ref_ns"],
                    payload["confidence"],
                    payload["mapping_version"],
                    payload["extra"],
                )
            except Exception as exc:  # pragma: no cover - defensive fallback
                self._logger.info(
                    "Refinement für %s/%s konnte nicht an Gerät übermittelt werden: %s",
                    player,
                    event_id,
                    exc,
                )
        except Exception as exc:  # pragma: no cover - defensive fallback
            self._logger.info(
                "Refinement für %s/%s konnte nicht an Gerät übermittelt werden: %s",
                player,
                event_id,
                exc,
            )

    def event_queue_load(self) -> Tuple[int, int]:
        load = self._event_q.qsize()
        max_size = self._event_q.maxsize if self._event_q.maxsize > 0 else -1
        self._logger.debug("event_queue_load() -> (load=%s, capacity=%s)", load, max_size)
        return load, max_size

    def estimate_time_offset(self, player: str) -> Optional[float]:
        device = self._devices.get(player)
        if device is None:
            self._logger.warning(
                "Zeitoffset für Spieler %s kann nicht bestimmt werden – kein verbundenes Gerät.",
                player,
            )
            return None

        clock = getattr(device, "clock", None) or getattr(device, "time_sync", None)
        if clock is None:
            self._logger.warning(
                "Gerät %s stellt keine Clock-Schnittstelle für Offset-Schätzung bereit.",
                player,
            )
            return None

        try:
            measurement = self._measure_clock_offset(clock)
        except AttributeError:
            self._logger.warning(
                "Gerät %s unterstützt keine Roundtrip-Zeitmessung über die Realtime-API.",
                player,
            )
            return None
        except Exception as exc:  # pragma: no cover - depends on external API
            self._logger.warning(
                "Roundtrip-Zeitmessung für Spieler %s fehlgeschlagen: %s",
                player,
                exc,
            )
            return None

        offset_seconds = self._extract_offset_seconds(measurement)
        if offset_seconds is None:
            self._logger.warning(
                "Offset-Messung für Spieler %s lieferte kein verwertbares Ergebnis: %r",
                player,
                measurement,
            )
            return None

        self._logger.debug(
            "Zeitoffset für %s geschätzt: %.6fs (Realtime Roundtrip)", player, offset_seconds
        )
        return offset_seconds

    def _measure_clock_offset(self, clock: Any) -> Any:
        """Use the realtime clock interface to perform a roundtrip measurement."""

        call_order = (
            "measure_time_offset",
            "estimate_time_offset",
            "measure_offset",
            "estimate_offset",
            "measure_roundtrip",
            "measure",
        )
        kw_variants = (
            {"sample_count": 8},
            {"num_samples": 8},
            {"samples": 8},
            {},
        )

        last_error: Exception | None = None
        for attr in call_order:
            fn = getattr(clock, attr, None)
            if not callable(fn):
                continue
            for kwargs in kw_variants:
                try:
                    return fn(**kwargs)
                except TypeError:
                    continue
                except Exception as exc:  # pragma: no cover - depends on external API
                    last_error = exc
                    break
            if last_error is not None:
                break

        if last_error is not None:
            raise last_error
        raise AttributeError("clock interface exposes no compatible measurement method")

    def _extract_offset_seconds(self, measurement: Any) -> Optional[float]:
        """Normalise various measurement result formats to seconds."""

        if measurement is None:
            return None

        if isinstance(measurement, (int, float)):
            return float(measurement)

        if isinstance(measurement, (list, tuple)):
            for item in measurement:
                offset = self._extract_offset_seconds(item)
                if offset is not None:
                    return offset
            return None

        candidates = (
            ("offset_seconds", 1.0),
            ("offset_s", 1.0),
            ("offset", 1.0),
            ("clock_offset", 1.0),
            ("dt_seconds", 1.0),
            ("dt", 1.0),
            ("delta", 1.0),
            ("offset_ns", 1e-9),
            ("clock_offset_ns", 1e-9),
            ("offset_ms", 1e-3),
        )

        mapping: Dict[str, Any] = {}
        if isinstance(measurement, dict):
            mapping = measurement
        else:
            for name, _scale in candidates:
                if hasattr(measurement, name):
                    try:
                        mapping[name] = getattr(measurement, name)
                    except Exception:  # pragma: no cover - attribute access shouldn't fail
                        continue

        for name, scale in candidates:
            value = mapping.get(name)
            if value is None and not isinstance(measurement, dict):
                value = getattr(measurement, name, None)
            if value is None:
                continue
            try:
                return float(value) * scale
            except (TypeError, ValueError):
                continue

        return None

    # ------------------------------------------------------------------
    # Internal helpers
    def _is_recording(self, player: str, *, device: plrt.Device | None = None) -> bool:
        if device is None:
            device = self._devices.get(player)
        if device is None:
            return False

        recordings = getattr(device, "recordings", None)
        if recordings is None:
            return False

        state = self._query_recording_state(recordings)
        if state is None:
            return bool(self._recording_states.get(player, False))

        self._recording_states[player] = state
        return state

    def _wait_for_recording_state(
        self,
        player: str,
        expected: bool,
        *,
        device: plrt.Device | None = None,
        timeout: float = 4.0,
        poll_interval: float = 0.2,
    ) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            if self._is_recording(player, device=device) == expected:
                return True
            time.sleep(poll_interval)
        return self._is_recording(player, device=device) == expected

    def _query_recording_state(self, recordings: object) -> Optional[bool]:
        candidates = (
            "is_recording",
            "isRecording",
            "recording",
            "recording_active",
            "status",
            "state",
        )
        for name in candidates:
            value = getattr(recordings, name, None)
            if value is None:
                continue
            try:
                result = value() if callable(value) else value
            except Exception:  # pragma: no cover - defensive
                continue

            if isinstance(result, bool):
                return result
            if isinstance(result, str):
                lowered = result.strip().lower()
                if lowered in {"recording", "saving", "stopping", "active"}:
                    return True
                if lowered in {"idle", "ready", "stopped", "saved", "inactive", "none"}:
                    return False

        return None

    def _start_worker(self) -> None:
        if self._worker and self._worker.is_alive():
            return

        self._worker_stop.clear()
        self._worker = threading.Thread(
            target=self._event_worker,
            name="NeonBridgeWorker",
            daemon=True,
        )
        self._worker.start()
        self._logger.debug("Neon-Event-Worker gestartet.")

    def _event_worker(self) -> None:
        while True:
            try:
                _priority, _seq, item = self._event_q.get()
            except Exception:  # pragma: no cover - defensive, queue shouldn't fail
                if self._worker_stop.is_set():
                    break
                continue

            try:
                if item is self._queue_sentinel:
                    break

                if not isinstance(item, dict):
                    continue

                player = str(item.get("player"))
                name = str(item.get("name"))
                payload = item.get("payload")
                device = self._devices.get(player)
                if device is None:
                    self._logger.warning(
                        "Event %s für unbekannten Spieler %s verworfen.",
                        name,
                        player,
                    )
                    continue

                payload_dict = payload if isinstance(payload, dict) else {}
                if not self._send_marker_with_retry(device, name, payload_dict):
                    self._logger.warning(
                        "Marker %s für Spieler %s konnte nach mehreren Versuchen nicht gesendet werden.",
                        name,
                        player,
                    )
            except Exception as exc:  # pragma: no cover - depends on external API
                self._logger.warning("Senden eines Events fehlgeschlagen: %s", exc)
            finally:
                self._event_q.task_done()

        self._logger.debug("Neon-Event-Worker beendet.")

    def _send_marker_with_retry(
        self, device: plrt.Device, name: str, payload: Dict[str, object]
    ) -> bool:
        max_attempts = 3
        delay = max(0.02, float(self._event_retry_base))
        for attempt in range(1, max_attempts + 1):
            try:
                self._emit_marker(device, name, payload)
            except Exception as exc:  # pragma: no cover - depends on external API
                if attempt >= max_attempts or not self._is_transient_error(exc):
                    self._logger.debug(
                        "Marker-Senden fehlgeschlagen (Versuch %d/%d): %s",
                        attempt,
                        max_attempts,
                        exc,
                    )
                    return False
                sleep_for = delay * (2 ** (attempt - 1))
                self._logger.debug(
                    "Marker-Senden fehlgeschlagen (Versuch %d/%d) – neuer Versuch in %.3fs: %s",
                    attempt,
                    max_attempts,
                    sleep_for,
                    exc,
                )
                time.sleep(min(sleep_for, 2.0))
                continue
            else:
                return True
        return False

    def _emit_marker(
        self, device: plrt.Device, name: str, payload: Dict[str, object]
    ) -> None:
        annotations = getattr(device, "annotations", None)
        payload = payload or {}
        if annotations is not None:
            if self._try_annotation_methods(annotations, name, payload):
                return

        emitter = getattr(getattr(device, "events", None), "emit_event", None)
        if callable(emitter):
            try:
                emitter(name=name, payload=payload)
            except TypeError:
                emitter(name)
            return

        raise RuntimeError(
            f"Gerät {getattr(device, 'name', '<unknown>')} unterstützt keine Marker-Schnittstelle"
        )

    def _try_annotation_methods(
        self, annotations: Any, name: str, payload: Dict[str, object]
    ) -> bool:
        candidate_calls = (
            ("create_marker", {"label": name, "properties": payload}),
            ("record_marker", {"label": name, "properties": payload}),
            ("create_annotation", {"label": name, "properties": payload}),
            ("record_annotation", {"label": name, "properties": payload}),
            ("create", {"label": name, "properties": payload}),
            ("record", {"label": name, "properties": payload}),
        )

        for attr, kwargs in candidate_calls:
            fn = getattr(annotations, attr, None)
            if not callable(fn):
                continue
            try:
                self._invoke_annotation_callable(fn, name, payload, kwargs)
            except TypeError:
                continue
            else:
                return True
        return False

    def _invoke_annotation_callable(
        self,
        fn: Any,
        name: str,
        payload: Dict[str, object],
        kwargs: Dict[str, object],
    ) -> None:
        try:
            if payload:
                fn(**kwargs)
            else:
                minimal = dict(kwargs)
                minimal.pop("properties", None)
                fn(**minimal) if minimal else fn(name)
        except TypeError:
            if payload:
                try:
                    fn(name, payload)
                except TypeError:
                    fn(name)
            else:
                fn(name)

    def _is_transient_error(self, exc: Exception) -> bool:
        transient_types = (TimeoutError, ConnectionError, OSError)
        if isinstance(exc, transient_types):
            return True
        if getattr(exc, "transient", False):
            return True
        status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
        if isinstance(status, int) and 500 <= status < 600:
            return True
        message = str(exc).lower()
        keywords = ("timeout", "temporarily", "temporary", "connection", "unavailable")
        return any(word in message for word in keywords)

    # ------------------------------------------------------------------
    # Host mirror helpers
    def _store_host_mirror(
        self,
        player: str,
        event_id: str,
        t_ref_ns: int,
        *,
        extra: Optional[Dict[str, object]] = None,
    ) -> dict[str, Any]:
        sample = {
            "t_ref_ns": int(t_ref_ns),
            "extra": dict(extra or {}),
            "recorded_at_ns": time.time_ns(),
        }
        with self._mirror_lock:
            player_bucket = self._host_mirror.setdefault(player, {})
            entry = player_bucket.setdefault(
                event_id,
                {"event_id": event_id, "host_samples": [], "refinements": []},
            )
            host_samples = entry.setdefault("host_samples", [])
            host_samples.append(sample)
            entry["last_host_sample"] = sample
        self._append_host_log(
            {
                "kind": "host_mirror",
                "player": player,
                "event_id": event_id,
                "payload": sample,
            }
        )
        return entry

    def _store_host_refinement(
        self,
        player: str,
        event_id: str,
        t_ref_ns: int,
        *,
        confidence: float,
        mapping_version: int,
        extra: Optional[Dict[str, object]] = None,
    ) -> dict[str, Any]:
        refinement = {
            "t_ref_ns": int(t_ref_ns),
            "confidence": float(confidence),
            "mapping_version": int(mapping_version),
            "extra": dict(extra or {}),
            "recorded_at_ns": time.time_ns(),
        }
        with self._mirror_lock:
            player_bucket = self._host_mirror.setdefault(player, {})
            entry = player_bucket.setdefault(
                event_id,
                {"event_id": event_id, "host_samples": [], "refinements": []},
            )
            refinements = entry.setdefault("refinements", [])
            refinements.append(refinement)
            entry["last_refinement"] = refinement
        self._append_host_log(
            {
                "kind": "refine_event",
                "player": player,
                "event_id": event_id,
                "payload": refinement,
            }
        )
        return entry

    def _append_host_log(self, entry: dict[str, Any]) -> None:
        try:
            with self._host_log_lock:
                self._host_log_path.parent.mkdir(parents=True, exist_ok=True)
                with self._host_log_path.open("a", encoding="utf-8") as handle:
                    json.dump(entry, handle, ensure_ascii=False, sort_keys=True)
                    handle.write("\n")
        except Exception:
            self._logger.debug(
                "Host-Mirror-Log konnte nicht geschrieben werden.", exc_info=True
            )

    def _drain_queue(self) -> None:
        while True:
            try:
                _priority, _seq, _item = self._event_q.get_nowait()
            except queue.Empty:
                break
            else:
                self._event_q.task_done()

    def _resolve_endpoint(self, host: str) -> tuple[str, int]:
        cleaned = str(host).strip()
        if cleaned.endswith("]") and cleaned.count("]") > cleaned.count("["):
            cleaned = cleaned.rstrip("]")
        parsed = urlparse(cleaned if "://" in cleaned else f"http://{cleaned}")
        hostname = parsed.hostname or parsed.path or host
        port = parsed.port or 8080
        return hostname, port

    def _probe_endpoint(
        self, player: str, host: str, hostname: str, port: int
    ) -> bool:
        try:
            with socket.create_connection((hostname, port), timeout=5.0):
                return True
        except OSError as exc:
            self._logger.warning(
                "Neon-Tracker %s (%s) nicht erreichbar (%s).",
                player,
                host,
                exc,
            )
            return False

    def _call_in_loop(self, fn, *args, **kwargs):
        """Führt fn(*args, **kwargs) im Async-Loop-Thread aus und gibt das Ergebnis zurück."""

        fut: concurrent.futures.Future = concurrent.futures.Future()

        def _runner() -> None:
            try:
                res = fn(*args, **kwargs)
            except Exception as e:  # pragma: no cover - relies on external API
                fut.set_exception(e)
            else:
                fut.set_result(res)

        self._loop.call_soon_threadsafe(_runner)
        return fut.result()


__all__ = ["PupilBridge"]
