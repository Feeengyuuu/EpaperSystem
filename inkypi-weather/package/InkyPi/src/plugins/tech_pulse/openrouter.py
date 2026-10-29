"""OpenRouter's newest models for Tech Pulse's bottom strip (keyless: its public model list).

The list is large (every model with its pricing), so it is fetched at most every few hours and
cut to the newest few distinct models; a model's ``:free`` / ``:batch`` variants fold into it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from utils.http_client import get_http_client


MODELS_URL = "https://openrouter.ai/api/v1/models"
USER_AGENT = "InkyPi TechPulse/1.0 (personal e-paper display)"
REQUEST_TIMEOUT = (4, 15)
MAX_BYTES = 3 * 1024 * 1024
NEWEST = 6
# A model's prices come per token; the strip shows dollars per million.
PER_MILLION = 1_000_000


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def model_name(raw_name, model_id):
    """"OpenAI: GPT-6.1 Terra" -> "GPT-6.1 Terra"; the id's own name when there is none."""
    name = str(raw_name or "").strip()
    if ": " in name:
        name = name.split(": ", 1)[1]
    return name or str(model_id or "").split("/")[-1]


def normalize_models(payload, now):
    """{"week": models added in the past week, "models": the NEWEST newest, each with its provider,
    name, added time, context length, $/M prices and whether it reads images}."""
    rows = (payload or {}).get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("OpenRouter returned no models")
    seen, models = set(), []
    week_ago = (now.astimezone(timezone.utc) - timedelta(days=7)).timestamp()
    week = 0
    for row in sorted((row for row in rows if isinstance(row, dict)), key=lambda row: row.get("created") or 0,
                      reverse=True):
        base = str(row.get("id") or "").split(":", 1)[0]
        if not base or "/" not in base or base in seen:
            continue
        seen.add(base)
        created = _float(row.get("created")) or 0
        if created >= week_ago:
            week += 1
        # A model's sizes and editions (gpt-6.1-terra, gpt-6.1-terra-pro) share one card.
        if len(models) >= NEWEST or any(base.startswith(model["id"] + "-") or model["id"].startswith(base + "-")
                                        for model in models):
            continue
        pricing = row.get("pricing") if isinstance(row.get("pricing"), dict) else {}
        prompt, completion = _float(pricing.get("prompt")), _float(pricing.get("completion"))
        architecture = row.get("architecture") if isinstance(row.get("architecture"), dict) else {}
        inputs = architecture.get("input_modalities") if isinstance(architecture.get("input_modalities"), list) else []
        models.append({
            "id": base, "provider": base.split("/", 1)[0], "name": model_name(row.get("name"), base)[:40],
            "created": datetime.fromtimestamp(created, timezone.utc).isoformat() if created else None,
            "context": int(_float(row.get("context_length")) or 0),
            "prompt": None if prompt is None or prompt < 0 else prompt * PER_MILLION,
            "completion": None if completion is None or completion < 0 else completion * PER_MILLION,
            "vision": "image" in inputs,
        })
    return {"week": week, "models": models}


def fetch_models(now, context=None):
    response = get_http_client().request_json("GET", MODELS_URL, context=context, timeout=REQUEST_TIMEOUT,
                                              max_bytes=MAX_BYTES, headers={"User-Agent": USER_AGENT,
                                                                           "Accept": "application/json"})
    return normalize_models(response.data, now)


def context_text(tokens):
    """1048576 -> "1M", 262144 -> "262K"."""
    tokens = int(tokens or 0)
    if tokens >= 1_000_000:
        value = tokens / 1_000_000
        return f"{value:.0f}M" if value >= 10 or value == int(value) else f"{value:.1f}M"
    if tokens >= 1000:
        return f"{tokens / 1000:.0f}K"
    return str(tokens) if tokens else ""


def price_text(model):
    """"$2/$8" per million tokens in and out, "免费" for free, "" when unknown."""
    prompt, completion = model.get("prompt"), model.get("completion")
    if prompt is None or completion is None:
        return ""
    if prompt == 0 and completion == 0:
        return "免费"

    def money(value):
        return f"${value:.2f}".rstrip("0").rstrip(".") if value < 10 else f"${value:.0f}"

    return f"{money(prompt)}/{money(completion)}"
