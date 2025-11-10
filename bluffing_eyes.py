"""Minimal starter to launch the tabletop Kivy application."""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from typing import Sequence

from tabletop.app import main as app_main


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments for the experiment launcher."""

    parser = argparse.ArgumentParser(description="Start the Bluffing Eyes tabletop app")
    parser.add_argument(
        "--session",
        type=str,
        required=False,
        default=None,
        help="Optional: Session-ID. Wird automatisch erzeugt, falls nicht gesetzt.",
    )
    parser.add_argument(
        "--block",
        type=int,
        required=False,
        default=1,
        help="Blocknummer (Standard: 1).",  # Safe default for quick CLI launches. (change)
    )
    parser.add_argument(
        "--player",
        type=str,
        default="A",
        required=False,
        help="Spielerkennung (Standard: 'A').",
    )
    parser.add_argument(
        "--perf",
        action="store_true",
        help="Aktiviere zusätzliche Performance-Logs (für Debugging).",
    )
    # CLI toggles propagate overlay/fullscreen to tabletop.app. (change)
    parser.add_argument(
        "--overlay",
        dest="overlay",
        action="store_true",
        help="Erzwinge den Overlay-Start.",
    )
    parser.add_argument(
        "--no-overlay",
        dest="overlay",
        action="store_false",
        help="Deaktiviere den Overlay-Start.",
    )
    parser.set_defaults(overlay=None)
    parser.add_argument(
        "--fullscreen",
        dest="fullscreen",
        action="store_true",
        help="Starte im Vollbildmodus.",
    )
    parser.add_argument(
        "--windowed",
        dest="fullscreen",
        action="store_false",
        help="Starte im Fenstermodus.",
    )
    parser.set_defaults(fullscreen=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """Entry point that wires CLI arguments into the Kivy application."""

    args = parse_args(argv)
    if args.perf:
        os.environ["TABLETOP_PERF"] = "1"

    session = args.session
    if not session:
        # Auto-generate a readable session identifier when none is supplied.
        session = f"session-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    app_main(
        session=session,
        block=args.block,
        player=args.player,
        overlay=args.overlay,
        fullscreen=args.fullscreen,
    )


if __name__ == "__main__":  # pragma: no cover - convenience wrapper
    main()
