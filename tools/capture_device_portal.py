#!/usr/bin/env python3
"""Capture sanitized playlist metadata and exact cached device frames."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


SOURCE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "inkypi-weather"
    / "package"
    / "InkyPi"
    / "src"
)
sys.path.insert(0, str(SOURCE_ROOT))

from device_portal_capture import capture_device_portal_snapshot  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read the InkyPi playlist and cached PNG routes without persisting the admin HTML.",
    )
    parser.add_argument("--device-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timezone", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = capture_device_portal_snapshot(
        args.device_url,
        args.output,
        timezone_name=args.timezone,
    )
    print(
        "Captured read-only device snapshot: "
        f"configured={report.configured} "
        f"available={report.available} "
        f"unavailable={report.unavailable}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
