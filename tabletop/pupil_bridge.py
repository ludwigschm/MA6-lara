from __future__ import annotations

try:
    from .devices.neon_bridge import PupilBridge  # produktiv
except Exception as e:  # pragma: no cover - fallback path
    from .pupil_bridge_stub import PupilBridge  # existierender Stub
    import logging

    logging.getLogger(__name__).warning(
        "Neon-Bridge nicht geladen (%s) – verwende Stub.", e
    )


__all__ = ["PupilBridge"]
