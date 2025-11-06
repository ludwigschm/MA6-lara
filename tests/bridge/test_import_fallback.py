from __future__ import annotations

import importlib
import sys
import types


def test_stub_loaded_when_realtime_missing(monkeypatch):
    # Ensure fresh imports for the bridge modules
    for name in list(sys.modules):
        if name.startswith("tabletop.pupil_bridge") or name.startswith("tabletop.devices.neon_bridge"):
            sys.modules.pop(name)

    # Simulate missing realtime API
    monkeypatch.setitem(sys.modules, "pupil_labs", types.ModuleType("pupil_labs"))
    sys.modules.pop("pupil_labs.realtime_api", None)

    module = importlib.import_module("tabletop.pupil_bridge")
    from tabletop import pupil_bridge_stub

    assert module.PupilBridge is pupil_bridge_stub.PupilBridge
