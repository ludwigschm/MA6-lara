"""Tests for the Neon PupilBridge event pipeline."""

from __future__ import annotations

import importlib
import sys
import types
from typing import Dict, List, Tuple

import pytest


@pytest.fixture
def neon_bridge_module(monkeypatch):
    """Provide the Neon bridge module with a stubbed realtime API."""

    fake_pkg = types.ModuleType("pupil_labs")
    fake_api = types.ModuleType("pupil_labs.realtime_api")

    class _StubDevice:
        def __init__(self, host: str, port: int | None = None) -> None:
            self.host = host
            self.port = port
            self.annotations = types.SimpleNamespace()

    fake_api.Device = _StubDevice
    fake_pkg.realtime_api = fake_api
    monkeypatch.setitem(sys.modules, "pupil_labs", fake_pkg)
    monkeypatch.setitem(sys.modules, "pupil_labs.realtime_api", fake_api)

    sys.modules.pop("tabletop.devices.neon_bridge", None)
    module = importlib.import_module("tabletop.devices.neon_bridge")
    return module


@pytest.fixture
def neon_bridge(neon_bridge_module):
    bridge = neon_bridge_module.PupilBridge()
    try:
        yield bridge
    finally:
        bridge.close()


class _RecordingAnnotations:
    def __init__(self) -> None:
        self.calls: List[Tuple[str, Dict[str, object]]] = []

    def create_marker(self, *, label: str, properties: Dict[str, object] | None = None) -> None:
        self.calls.append((label, dict(properties or {})))


def _install_device(bridge, player: str, annotations) -> None:
    canonical = bridge._canonicalize_player(player)
    device = types.SimpleNamespace(annotations=annotations, name=f"device-{canonical}")
    bridge._devices[canonical] = device
    bridge._connected.add(canonical)


def _install_clock_device(bridge, player: str, clock) -> None:
    canonical = bridge._canonicalize_player(player)
    device = types.SimpleNamespace(clock=clock, name=f"device-{canonical}")
    bridge._devices[canonical] = device
    bridge._connected.add(canonical)


def test_send_event_emits_marker(neon_bridge):
    annotations = _RecordingAnnotations()
    _install_device(neon_bridge, "p1", annotations)
    neon_bridge._start_worker()

    payload = {"button": "start"}
    neon_bridge.send_event("ui.button", "p1", payload)

    neon_bridge._event_q.join()

    assert annotations.calls == [("ui.button", {"button": "start"})]
    assert payload == {"button": "start"}


def test_high_priority_preempts_queue(neon_bridge):
    annotations = _RecordingAnnotations()
    _install_device(neon_bridge, "p1", annotations)

    neon_bridge.send_event("ui.low", "p1")
    neon_bridge.send_event("ui.high", "p1", priority="high")

    neon_bridge._start_worker()
    neon_bridge._event_q.join()

    assert [label for label, _ in annotations.calls] == ["ui.high", "ui.low"]


def test_transient_error_retries(neon_bridge):
    class _TransientError(Exception):
        def __init__(self) -> None:
            super().__init__("transient")
            self.transient = True

    class _FlakyAnnotations(_RecordingAnnotations):
        def __init__(self) -> None:
            super().__init__()
            self._failures = 0

        def create_marker(
            self, *, label: str, properties: Dict[str, object] | None = None
        ) -> None:
            if self._failures < 1:
                self._failures += 1
                raise _TransientError()
            super().create_marker(label=label, properties=properties)

    annotations = _FlakyAnnotations()
    _install_device(neon_bridge, "p1", annotations)
    neon_bridge._event_retry_base = 0.01
    neon_bridge._start_worker()

    neon_bridge.send_event("ui.retry", "p1", {"value": 1})

    neon_bridge._event_q.join()

    assert annotations.calls == [("ui.retry", {"value": 1})]
    assert annotations._failures == 1


def test_estimate_time_offset_prefers_roundtrip_result(neon_bridge, caplog):
    class _Clock:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def measure_roundtrip(self, sample_count: int = 8) -> dict[str, object]:
            self.calls.append({"sample_count": sample_count})
            return {"offset_ns": 1_500_000}

    clock = _Clock()
    _install_clock_device(neon_bridge, "p1", clock)

    caplog.set_level("DEBUG")
    offset = neon_bridge.estimate_time_offset("p1")

    assert offset == pytest.approx(0.0015)
    assert clock.calls == [{"sample_count": 8}]
    assert any("Zeitoffset für VP1" in record.getMessage() for record in caplog.records)


def test_estimate_time_offset_handles_missing_clock(neon_bridge, caplog):
    bridge = neon_bridge
    canonical = bridge._canonicalize_player("p2")
    bridge._devices[canonical] = types.SimpleNamespace(name=f"device-{canonical}")
    bridge._connected.add(canonical)

    caplog.set_level("WARNING")
    assert bridge.estimate_time_offset("p2") is None
    assert any("Clock-Schnittstelle" in record.getMessage() for record in caplog.records)


def test_estimate_time_offset_logs_measurement_failure(neon_bridge, caplog):
    class _FailingClock:
        def measure_time_offset(self, **_kwargs) -> float:
            raise RuntimeError("boom")

    _install_clock_device(neon_bridge, "p3", _FailingClock())

    caplog.set_level("WARNING")
    assert neon_bridge.estimate_time_offset("p3") is None
    assert any("Roundtrip-Zeitmessung" in record.getMessage() for record in caplog.records)


def test_connect_starts_http_stream(monkeypatch, neon_bridge_module):
    started: list[str] = []

    def _fake_start(client, *, start_timeout, logger):
        started.append(client.base_url)
        return True

    monkeypatch.setattr(neon_bridge_module, "ensure_tracker_running", _fake_start)
    monkeypatch.setattr(neon_bridge_module.PupilBridge, "_start_tracker_monitor", lambda self: None)
    monkeypatch.setattr(
        neon_bridge_module.PupilBridge,
        "_probe_endpoint",
        lambda self, player, host, hostname, port: True,
    )

    bridge = neon_bridge_module.PupilBridge()
    try:
        bridge.configure_hosts({"VP1": "10.0.0.2:8080"})
        bridge.connect()
        assert started == ["http://10.0.0.2:8080"]
        assert "VP1" in bridge._tracker_last_seen
    finally:
        bridge.close()
