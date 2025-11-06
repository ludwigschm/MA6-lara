"""CLI smoke test for the Neon Pupil bridge."""

from __future__ import annotations

import logging
import sys
import time
from contextlib import suppress

from tabletop.data.config import load_tracker_hosts
from tabletop.pupil_bridge import PupilBridge

DEFAULT_PLAYER = "VP1"
DEFAULT_SESSION = 1
DEFAULT_BLOCK = 1
_EVENTS = ("fix.start", "fix.beep", "trial.start")


def configure_logging() -> None:
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )


def run_smoke() -> int:
    configure_logging()
    logger = logging.getLogger("scripts.neon_smoke")

    hosts = load_tracker_hosts()
    if hosts:
        logger.info("Geladene Tracker-Hosts: %s", ", ".join(f"{k}={v}" for k, v in sorted(hosts.items())))
    else:
        logger.warning("Keine Tracker-Hosts gefunden – versuche Stub-/Offline-Modus.")

    bridge = PupilBridge()
    bridge.configure_hosts(hosts)
    bridge.connect()

    player = DEFAULT_PLAYER
    session = DEFAULT_SESSION
    block = DEFAULT_BLOCK

    logger.info(
        "Starte Smoke-Test für Spieler %s (Session %s, Block %s).", player, session, block
    )

    try:
        bridge.start_recording(session, block, player)
        for event_name in _EVENTS:
            logger.info("Sende Event %s", event_name)
            bridge.send_event(event_name, player)
            time.sleep(0.05)
    except KeyboardInterrupt:
        logger.warning("Smoke-Test durch Nutzer abgebrochen.")
        return 130
    finally:
        with suppress(Exception):
            bridge.stop_recording(player)
        with suppress(Exception):
            bridge.close()

    logger.info("Smoke-Test abgeschlossen.")
    return 0


def main() -> int:
    return run_smoke()


if __name__ == "__main__":
    sys.exit(main())
