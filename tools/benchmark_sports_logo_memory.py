"""Measure native peak RSS and pixels for a supported 2048px remote team logo.

Run in a fresh process: python tools/benchmark_sports_logo_memory.py --max-peak-mb 40
Use --repo to compare a baseline checkout. RSS budgets are platform-specific;
this benchmark is an explicit profiling check, not a portable CI timing test.
"""

import argparse
import gc
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys
import threading
import time

import psutil
from PIL import Image, ImageDraw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--max-peak-mb", type=float)
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo.resolve() / "inkypi-weather/package/InkyPi/src"))
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard

    source = Image.new("RGBA", (2048, 2048), (255, 255, 255, 255))
    draw = ImageDraw.Draw(source)
    draw.rectangle((64, 64, 1984, 1984), fill=(20, 60, 120, 250))
    buffer = BytesIO()
    source.save(buffer, format="PNG")
    payload = buffer.getvalue()
    del draw
    source.close()
    gc.collect()
    process = psutil.Process()
    baseline = process.memory_info().rss
    rss = [baseline]
    stop = threading.Event()

    def sample():
        while not stop.wait(0.001):
            rss.append(process.memory_info().rss)

    thread = threading.Thread(target=sample)
    thread.start()
    started = time.perf_counter()
    try:
        result = SportsDashboard._team_logo_from_bytes(payload, 32)
    finally:
        stop.set()
        thread.join()
    measurement = {
        "baseline_mb": baseline / 1048576,
        "peak_delta_mb": (max(rss) - baseline) / 1048576,
        "seconds": time.perf_counter() - started,
        "size": result.size,
        "pixels_sha256": hashlib.sha256(result.tobytes()).hexdigest(),
    }
    result.close()
    print(json.dumps(measurement))
    assert measurement["size"] == (32, 32)
    assert measurement["pixels_sha256"] == "e9daf346e5523b21dd1c14d7f956f01792fafb133eaf2bb58944a6089e6d8e8f"
    if args.max_peak_mb is not None:
        assert measurement["peak_delta_mb"] <= args.max_peak_mb, "image peak exceeds memory budget"


if __name__ == "__main__":
    main()
