#!/usr/bin/env python3
"""Parse every requested release script without executing any of them."""

from __future__ import annotations

import argparse
import subprocess
import sys


def main(argv: list[str] | None = None) -> int:
    """Return failure as soon as any individually parsed script is invalid."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="Shell scripts to parse")
    args = parser.parse_args(argv)
    for path in args.paths:
        try:
            result = subprocess.run(["bash", "-n", path], check=False)
        except OSError as error:
            print(f"Could not parse {path}: {error}", file=sys.stderr)
            return 2
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
