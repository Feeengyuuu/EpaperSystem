#!/usr/bin/env python3
"""Attach selected device rasters and publish them into the formal ledger."""

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

from publication_bootstrap import (  # noqa: E402
    attach_cached_raster_snapshot,
    publish_cached_raster_bundle,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish exact cached e-paper frames through the formal web Edition Ledger.",
    )
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--publication-root", required=True, type=Path)
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="Optional selected-cache snapshot to attach before publishing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.snapshot is not None:
        attachment = attach_cached_raster_snapshot(args.bundle, args.snapshot)
        print(
            "Attached raster snapshot: "
            f"configured={attachment.configured} "
            f"available={attachment.available} "
            f"unavailable={attachment.unavailable}"
        )
    report = publish_cached_raster_bundle(args.bundle, args.publication_root)
    print(
        "Published raster bootstrap: "
        f"attempted={report.attempted} "
        f"published={report.published} "
        f"skipped={report.skipped} "
        f"unavailable={report.unavailable}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
