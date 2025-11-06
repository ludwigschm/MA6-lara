from __future__ import annotations

import importlib
import sys
import types


class DummyDevice:
    def __init__(self) -> None:
        self.markers: list[tuple[str, dict[str, object]]] = []


def _prepare_bridge(monkeypatch):
    monkeypatch.setitem(sys.modules, "pupil_labs", types.ModuleType("pupil_labs"))
    fake_api = types.ModuleType("pupil_labs.realtime_api")
    fake_api.Device = DummyDevice
    monkeypatch.setitem(sys.modules, "pupil_labs.realtime_api", fake_api)

    module_name = "tabletop.devices.neon_bridge"
    module = sys.modules.get(module_name)
    if module is None:
        module = importlib.import_module(module_name)
    else:
        module = importlib.reload(module)
    return module.PupilBridge


def test_event_queue_load_reports_items(monkeypatch):
    Bridge = _prepare_bridge(monkeypatch)
    bridge = Bridge()

    bridge._connected.add("VP1")
    bridge._devices["VP1"] = DummyDevice()

    bridge.send_event("fix.start", "VP1")
    bridge.send_event("fix.beep", "VP1", payload={"tone": "high"})

    load, capacity = bridge.event_queue_load()

    assert load == 2
    assert capacity >= 2

    # Ensure queue preserves event order (priority, then sequence)
    first = bridge._event_q.get_nowait()
    second = bridge._event_q.get_nowait()

    assert first[2]["name"] == "fix.start"
    assert second[2]["name"] == "fix.beep"
