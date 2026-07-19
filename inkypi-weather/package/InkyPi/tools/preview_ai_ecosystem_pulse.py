from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from plugins.ai_ecosystem_pulse.ai_ecosystem_pulse import (  # noqa: E402
    AiEcosystemPulse,
    PLUGIN_ID,
)


class PreviewDevice:
    """Small PC-only device adapter used by the preview command."""

    def get_resolution(self):
        return (800, 480)

    def get_config(self, key=None, default=None):
        values = {
            "timezone": "America/Los_Angeles",
            "orientation": "horizontal",
            "theme_mode": "day",
        }
        return values if key is None else values.get(key, default)

    def load_env_key(self, key):
        if key != "GITHUB_SECRET":
            return ""
        return os.getenv("GITHUB_SECRET", "") or os.getenv("GITHUB_TOKEN", "")


def _preview_status(plugin, cache_dir, mode):
    if mode == "fixture":
        return {
            "aggregate": "DEMO",
            "sources": {
                "skills": "fixture",
                "huggingface": "fixture",
                "github": "fixture",
            },
            "errors": {"skills": "", "huggingface": "", "github": ""},
            "provenance": "local_fallback",
        }

    payload = plugin._read_json(cache_dir / "aggregate.json", {})
    status = payload.get("status") if isinstance(payload, dict) else {}
    if not isinstance(status, dict):
        status = {}
    return {
        "aggregate": status.get("aggregate", "DEMO"),
        "sources": status.get("sources", {}),
        "errors": status.get("errors", {}),
        "provenance": payload.get("_source_provenance", "local_fallback")
        if isinstance(payload, dict)
        else "local_fallback",
    }


def main():
    parser = argparse.ArgumentParser(description="Render an AI Ecosystem Pulse PC preview.")
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    cache_dir = output.parent / "cache"
    state_dir = output.parent / "state"
    os.environ["AI_ECOSYSTEM_PULSE_CACHE_DIR"] = str(cache_dir)
    os.environ["AI_ECOSYSTEM_PULSE_DATA_DIR"] = str(state_dir)

    plugin = AiEcosystemPulse({"id": PLUGIN_ID})
    if args.mode == "fixture":
        fixed_now = datetime(2026, 7, 17, 20, 0, tzinfo=timezone.utc)
        fixture = plugin._fixture_payload(fixed_now)
        plugin._now_for_device = lambda _device: fixed_now
        plugin._payload = lambda *_args, **_kwargs: fixture

    image = plugin.generate_image(
        {"themeMode": "day", "forceRefresh": args.mode == "live"},
        PreviewDevice(),
    )
    image.save(output, format="PNG")
    status = _preview_status(plugin, cache_dir, args.mode)
    print(
        json.dumps(
            {
                "output": str(output),
                "size": list(image.size),
                "mode": args.mode,
                "aggregate": status["aggregate"],
                "source_states": status["sources"],
                "source_errors": status["errors"],
                "provenance": status["provenance"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
