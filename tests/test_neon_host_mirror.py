"""Tests for host mirror handling in the Neon Pupil bridge."""

from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path

import pytest


def _load_bridge(monkeypatch: pytest.MonkeyPatch):
    """Import the Neon bridge module with a stubbed realtime API."""

    stub_api = types.SimpleNamespace(Device=object)
    pupil_pkg = types.ModuleType("pupil_labs")
    pupil_pkg.realtime_api = stub_api
    monkeypatch.setitem(sys.modules, "pupil_labs", pupil_pkg)
    monkeypatch.setitem(sys.modules, "pupil_labs.realtime_api", stub_api)
    sys.modules.pop("tabletop.devices.neon_bridge", None)
    return importlib.import_module("tabletop.devices.neon_bridge")


def test_send_host_mirror_stores_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    neon_bridge = _load_bridge(monkeypatch)
    bridge = neon_bridge.PupilBridge()
    bridge._host_log_path = tmp_path / "mirror.jsonl"

    bridge.send_host_mirror("VP1", "evt-1", 123_456_789, extra={"session": 1})

    entry = bridge._host_mirror["VP1"]["evt-1"]
    assert entry["last_host_sample"]["t_ref_ns"] == 123_456_789
    assert entry["last_host_sample"]["extra"] == {"session": 1}
    assert entry["host_samples"], "expected stored host samples"

    log_lines = (tmp_path / "mirror.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(log_lines) == 1
    logged = json.loads(log_lines[0])
    assert logged["kind"] == "host_mirror"
    assert logged["player"] == "VP1"


def test_refine_event_records_refinement(monkeypatch: pytest.MonkeyPatch) -> None:
    neon_bridge = _load_bridge(monkeypatch)
    bridge = neon_bridge.PupilBridge()

    bridge.refine_event(
        "VP2",
        "evt-2",
        555_000_000,
        confidence=0.75,
        mapping_version=3,
        extra={"note": "host-only"},
    )

    entry = bridge._host_mirror["VP2"]["evt-2"]
    last = entry["last_refinement"]
    assert last["t_ref_ns"] == 555_000_000
    assert last["confidence"] == pytest.approx(0.75)
    assert last["mapping_version"] == 3
    assert last["extra"] == {"note": "host-only"}


def test_refine_event_calls_device_when_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Annotations:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def refine(
            self,
            *,
            event_id: str,
            timestamp_ns: int,
            confidence: float,
            mapping_version: int,
            extra: dict[str, object],
        ) -> None:
            self.calls.append(
                {
                    "event_id": event_id,
                    "timestamp_ns": timestamp_ns,
                    "confidence": confidence,
                    "mapping_version": mapping_version,
                    "extra": extra,
                }
            )

    class _Device:
        def __init__(self) -> None:
            self.annotations = _Annotations()

    stub_api = types.SimpleNamespace(Device=_Device)
    pupil_pkg = types.ModuleType("pupil_labs")
    pupil_pkg.realtime_api = stub_api
    monkeypatch.setitem(sys.modules, "pupil_labs", pupil_pkg)
    monkeypatch.setitem(sys.modules, "pupil_labs.realtime_api", stub_api)
    sys.modules.pop("tabletop.devices.neon_bridge", None)
    neon_bridge = importlib.import_module("tabletop.devices.neon_bridge")

    bridge = neon_bridge.PupilBridge()
    bridge._host_log_path = tmp_path / "mirror.jsonl"
    bridge._devices["VP1"] = _Device()

    bridge.refine_event(
        "VP1",
        "evt-3",
        999,
        confidence=0.9,
        mapping_version=7,
        extra={"quality": "good"},
    )

    entry = bridge._host_mirror["VP1"]["evt-3"]
    assert entry["refinements"], "expected stored refinement"

    annotation_calls = bridge._devices["VP1"].annotations.calls
    assert len(annotation_calls) == 1
    call = annotation_calls[0]
    assert call["event_id"] == "evt-3"
    assert call["timestamp_ns"] == 999
    assert call["confidence"] == pytest.approx(0.9)
    assert call["mapping_version"] == 7
    assert call["extra"] == {"quality": "good"}
