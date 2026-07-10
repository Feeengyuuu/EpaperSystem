# AI Image Multiverse
from __future__ import annotations

# ---- standard library ----
import json
from io import BytesIO
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
from typing import Any, Dict, Tuple

VENDOR_DIR = Path(__file__).parent / "_vendor"
if VENDOR_DIR.is_dir():
    sys.path.insert(0, str(VENDOR_DIR))

# ---- third-party ----
from dotenv import load_dotenv
from flask import abort, jsonify, request
from google import genai
from google.genai import types
from google.genai.errors import ClientError
from openai import BadRequestError, OpenAI
from PIL import Image, ImageColor, ImageOps

# ---- project / local ----
from blueprints.plugin import plugin_bp
from plugins.base_plugin.base_plugin import BasePlugin
from runtime.long_task_executor import (
    InstanceIdentity,
    LongTaskExecutor,
    LongTaskFailure,
    LongTaskQueueFull,
    current_instance_identity,
    task_context_or_default,
)
from runtime.refresh_contracts import TaskCancelled, TaskContext, TaskDeadlineExceeded
from utils.http_client import HttpClientError, HttpStatusError, get_http_client
from utils.image_utils import pad_image_blur
from utils.safe_image import safe_open_base64_image, safe_open_image

from .e_ink_prompt import e_ink_prompt
from .randomizer import randomizer

logger = logging.getLogger(__name__)

MODEL_PIPELINE = {
    # OpenAI (image + LLM both OpenAI)
    "gpt-image-2": {
        "label": "Image 2 (OpenAI)",
        "image": {"provider": "openai", "env": "OPEN_AI_SECRET"},
        "llm":   {"provider": "openai", "env": "OPEN_AI_SECRET"},
        "quality": [
            {"label": "High",   "value": "high"},
            {"label": "Medium", "value": "medium"},
            {"label": "Low",    "value": "low"},
        ],
    },
    "gpt-image-1-mini": {
        "label": "Image 1 Mini (OpenAI)",
        "image": {"provider": "openai", "env": "OPEN_AI_SECRET"},
        "llm":   {"provider": "openai", "env": "OPEN_AI_SECRET"},
        "quality": [
            {"label": "High",   "value": "high"},
            {"label": "Medium", "value": "medium"},
            {"label": "Low",    "value": "low"},
        ],
    },
    "gpt-image-1": {
        "label": "Image 1 (OpenAI)",
        "image": {"provider": "openai", "env": "OPEN_AI_SECRET"},
        "llm":   {"provider": "openai", "env": "OPEN_AI_SECRET"},
        "quality": [
            {"label": "High",   "value": "high"},
            {"label": "Medium", "value": "medium"},
            {"label": "Low",    "value": "low"},
        ],
    },
    "gpt-image-1.5": {
        "label": "Image 1.5 (OpenAI)",
        "image": {"provider": "openai", "env": "OPEN_AI_SECRET"},
        "llm":   {"provider": "openai", "env": "OPEN_AI_SECRET"},
        "quality": [
            {"label": "High",   "value": "high"},
            {"label": "Medium", "value": "medium"},
            {"label": "Low",    "value": "low"},
        ],
    },

 # Gemini (image + LLM both Gemini)

"gemini-nano-banana": {
    "label": "Gemini - Nano Banana 2 (Fast)",  # Nano Banana 2 = Gemini 3.1 Flash Image
    "image": {"provider": "gemini", "env": "NANO_BANANA_KEY", "model": "gemini-3.1-flash-image-preview"},
    # Use a current Gemini 3.x text model for randomizer / rewriting (pick your preferred one)
    "llm":   {"provider": "gemini", "env": "NANO_BANANA_KEY", "model": "gemini-3.1-pro-preview"},
    "default_quality": "1K",
    "quality": [
        {"label": "Standard (1K)",   "value": "1K"},
        {"label": "HD (2K)",         "value": "2K"},
        {"label": "Ultra (4K)",      "value": "4K"},
    ],
},

"gemini-nano-banana-pro": {
    "label": "Gemini - Nano Banana Pro",
    "image": {"provider": "gemini", "env": "NANO_BANANA_KEY", "model": "gemini-3-pro-image-preview"},
    "llm":   {"provider": "gemini", "env": "NANO_BANANA_KEY", "model": "gemini-3.1-pro-preview"},
    "default_quality": "2K",
    "quality": [
        {"label": "Standard (1K)",   "value": "1K"},
        {"label": "HD (2K)",         "value": "2K"},
        {"label": "Ultra (4K)",      "value": "4K"},
    ],
},

    # AI Horde (image via Horde, LLM via Groq/Llama)
    "ai-horde": {
        "label": "AI Horde - First available",
        "image": {"provider": "horde", "env": "AI_HORDE_KEY"},
        "llm":   {"provider": "groq", "env": "GROQ_API_KEY", "model": "llama-3.3-70b-versatile"},
        "default_quality": "standard",
        "quality": [
            {"label": "Standard", "value": "standard"},
            {"label": "High",     "value": "high"},
            {"label": "Ultra",    "value": "ultra"},
            
        ],
    },

    "ai-horde-albedobase-xl": {
        "label": "AI Horde - AlbedoBase XL 3.1",
        "image": {"provider": "horde", "env": "AI_HORDE_KEY"},
        "llm":   {"provider": "groq", "env": "GROQ_API_KEY", "model": "llama-3.3-70b-versatile"},
        "default_quality": "standard",
        "quality": [
            {"label": "Standard", "value": "standard"},
            {"label": "High",     "value": "high"},
            {"label": "Ultra",    "value": "ultra"},
            
        ],
        "horde_models": ["AlbedoBase XL 3.1"],
    },
}

# Helpers 
def _has_key(v: Any) -> bool:
    return bool(v and str(v).strip())

# Find those API Keys
def _get_env_map(device_config) -> Dict[str, bool]:
    # Match the "Correct Lookup" pattern: use device_config.load_env_key(...)
    return {
        "OPEN_AI_SECRET": _has_key(device_config.load_env_key("OPEN_AI_SECRET") or device_config.load_env_key("OPENAI_API_KEY")),
        "NANO_BANANA_KEY": _has_key(device_config.load_env_key("NANO_BANANA_KEY")),
        "AI_HORDE_KEY": _has_key(device_config.load_env_key("AI_HORDE_KEY")),
        "GROQ_API_KEY": _has_key(device_config.load_env_key("GROQ_API_KEY")),
    }


def _get_pipeline_entry(settings: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    model_id = (settings.get("imageModel") or "").strip()
    if not model_id:
        raise RuntimeError("Image Model is not set.")
    entry = (MODEL_PIPELINE or {}).get(model_id)
    if not entry:
        raise RuntimeError(f"Unknown image model '{model_id}' (not in MODEL_PIPELINE).")
    return model_id, entry

# Adapter that lets code treat Groq like OpenAI
def _groq_openai_compat_client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")

# Build Final Prompt
def build_final_prompt(settings, device_config) -> str:
    _, entry = _get_pipeline_entry(settings)

    text_prompt = (settings.get("textPrompt") or "").strip()
    randomize_on = (settings.get("randomizePrompt") == "true")

    if randomize_on:
        llm_client, chat_model, llm_provider = build_llm_client_and_model_unchecked(entry, device_config)
        text_prompt = randomizer(
            llm_client,
            text_prompt,          # blank => random, non-blank => rewrite
            llm_provider=llm_provider,
            chat_model=chat_model,
            temperature=1,
        )

    text_prompt = (text_prompt or "").strip()
    if not text_prompt:
        raise RuntimeError("Please enter a text prompt or click the randomize text prompt.")

    final_prompt = f"{text_prompt}\n\n{e_ink_prompt()}".strip()

    # ---------- DEBUG: PRINT EXACT FINAL PROMPT ----------
    #logger.info("FINAL PROMPT (len=%d)", len(final_prompt))
    #print("\n==== FINAL PROMPT TO IMAGE MODEL ====\n" + final_prompt + "\n==== END FINAL PROMPT ====\n", flush=True)
    # -----------------------------------------------------

    return final_prompt

# Reads the selected pipeline’s LLM settings
def build_llm_client_and_model_unchecked(entry: dict, device_config):
    llm = entry["llm"]
    provider = llm["provider"].strip().lower()
    key = device_config.load_env_key(llm["env"])

    if not key:
        raise RuntimeError(f"Missing {llm['env']}")

    if provider == "openai":
        return OpenAI(api_key=key), "gpt-4o", "openai"
    
    if provider == "gemini":
        return genai.Client(api_key=key), llm["model"], "gemini"

    if provider == "groq":
        return _groq_openai_compat_client(key), llm["model"], "groq"

    raise RuntimeError(f"Unsupported llm.provider: {provider}")

# Apply Pad / Background Blur / Background Color
def _apply_pad_background(img: Image.Image, settings: dict, target_dimensions: tuple[int, int]) -> Image.Image:
    """
    Shared post-process for all image providers.

    If padImage != "true": returns img unchanged.
    If backgroundOption == "blur": uses pad_image_blur.
    Else: pads with backgroundColor (default #ffffff).
    """
    if settings.get("padImage") != "true":
        return img

    if settings.get("backgroundOption") == "blur":
        return pad_image_blur(img, target_dimensions)

    bg_hex = settings.get("backgroundColor") or "#ffffff"
    background_color = ImageColor.getcolor(bg_hex, "RGB")
    return ImageOps.pad(
        img,
        target_dimensions,
        color=background_color,
        method=Image.Resampling.LANCZOS,
    )

# Get screen orientation
def _get_target_dims(device_config) -> tuple[str, int, int]:
    """
    Returns (orientation, w, h) where w/h are normalized to match orientation.

    - orientation: "horizontal" or "vertical"
    - w, h: device resolution with swap applied so w>h for horizontal, h>w for vertical
    """
    orientation = (device_config.get_config("orientation") or "horizontal").lower()

    w, h = device_config.get_resolution()

    # Normalize dimensions to match selected orientation
    if orientation == "vertical" and w > h:
        w, h = h, w
    elif orientation == "horizontal" and h > w:
        w, h = h, w

    return orientation, w, h

# OpenAI Code (uses prepared_final_prompt + blur/pad)
def generate_openai_image(settings, device_config, final_prompt: str) -> Image.Image:
    model_id, entry = _get_pipeline_entry(settings)

    image_cfg = (entry.get("image") or {})
    image_provider = (image_cfg.get("provider") or "").strip().lower()
    if image_provider != "openai":
        raise RuntimeError(f"generate_openai_image called for non-openai model '{model_id}'.")

    image_env = (image_cfg.get("env") or "").strip()
    api_key = device_config.load_env_key(image_env) if image_env else None
    if not api_key:
        raise RuntimeError(f"OpenAI API key not configured ({image_env}).")

    # Quality must be provided by UI; if missing, use pipeline default_quality if present
    quality = (settings.get("quality") or "").strip()
    if not quality:
        quality = (entry.get("default_quality") or "").strip()
    if not quality:
        raise RuntimeError("Quality is missing (and no default_quality set in pipeline).")

    # Device sizing / orientation
    orientation, w, h = _get_target_dims(device_config)

    # OpenAI supported sizes (your standard)
    size = "1536x1024" if orientation == "horizontal" else "1024x1536"

    client = OpenAI(api_key=api_key)

    logger.info("OpenAI image gen | model=%s quality=%s size=%s target=%dx%d", model_id, quality, size, w, h)

    # Call OpenAI
    try:
        resp = client.images.generate(
            model=model_id,          # IMPORTANT: model_id is the OpenAI image model string for these entries
            prompt=final_prompt,
            size=size,
            n=1,
            quality=quality,
        )
    except BadRequestError as e:
        err = getattr(e, "body", None) or {}
        err_obj = (err or {}).get("error", {}) if isinstance(err, dict) else {}
        if err_obj.get("code") == "moderation_blocked":
            logger.error("Moderation blocked: %s", err_obj.get("message"))
            raise RuntimeError("OpenAI safety system blocked this prompt.")
        raise RuntimeError(f"OpenAI Image Error: {e}")
    except Exception as e:
        raise RuntimeError(f"OpenAI image generation failed: {e}")

    # Decode image (memory only)
    img0 = resp.data[0] if (resp.data and len(resp.data) > 0) else None
    if not img0:
        raise RuntimeError("OpenAI returned no image data.")

    if getattr(img0, "b64_json", None):
        img = safe_open_base64_image(img0.b64_json).convert("RGB")
    elif getattr(img0, "url", None):
        payload = get_http_client().request_bytes(
            "GET",
            img0.url,
            timeout=20,
        ).data
        img = safe_open_image(payload).convert("RGB")
    else:
        raise RuntimeError("OpenAI returned neither b64_json nor url.")

    # Blur/pad for e-ink (optional)
    return _apply_pad_background(img, settings, (w, h))


# Gemini Code
def generate_gemini_image(settings, device_config, final_prompt: str) -> Image.Image:
   
    def _coerce_to_pil(obj) -> Image.Image:
        # 1) Already a PIL image
        if isinstance(obj, Image.Image):
            return obj.convert("RGB")

        # 2) google.genai.types.Image (has image_bytes)
        b = getattr(obj, "image_bytes", None)
        if isinstance(b, (bytes, bytearray)):
            return safe_open_image(b).convert("RGB")

        # 3) Raw bytes
        if isinstance(obj, (bytes, bytearray)):
            return safe_open_image(obj).convert("RGB")

        raise RuntimeError(f"Unsupported image type from Gemini: {type(obj)}")

    model_id, entry = _get_pipeline_entry(settings)

    image_cfg = (entry.get("image") or {})
    image_provider = (image_cfg.get("provider") or "").strip().lower()
    if image_provider != "gemini":
        raise RuntimeError(f"generate_gemini_image called for non-gemini model '{model_id}'.")

    image_env = (image_cfg.get("env") or "").strip()
    api_key = device_config.load_env_key(image_env) if image_env else None
    if not api_key:
        raise RuntimeError(f"Gemini API key not configured ({image_env}).")

    gemini_model = (image_cfg.get("model") or "").strip()
    if not gemini_model:
        raise RuntimeError(f"Gemini model is missing in MODEL_PIPELINE for '{model_id}' (image.model).")

    quality = (settings.get("quality") or "").strip()
    if not quality:
        quality = (entry.get("default_quality") or "").strip()
    if not quality:
        quality = "standard"

    orientation, w, h = _get_target_dims(device_config)
    ratio = "16:9" if orientation == "horizontal" else "9:16"

    client = genai.Client(api_key=api_key)

    logger.info(
        "Gemini image gen | pipeline_id=%s model=%s quality=%s ratio=%s target=%dx%d",
        model_id, gemini_model, quality, ratio, w, h
    )

    try:
        resp = client.models.generate_content(
            model=gemini_model,
            contents=final_prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio=ratio),
            ),
        )

        img_obj = None

        # A) Newer responses: generated_images[0].image
        gen_list = getattr(resp, "generated_images", None)
        if gen_list:
            first = gen_list[0]
            img_obj = getattr(first, "image", None) or first

        # B) Some responses expose .parts directly
        if img_obj is None:
            parts = getattr(resp, "parts", None)
            if parts:
                for part in parts:
                    if getattr(part, "inline_data", None) is not None:
                        img_obj = part.as_image()
                        break
                    maybe_img = getattr(part, "image", None)
                    if maybe_img is not None:
                        img_obj = maybe_img
                        break

        # C) Candidate-based responses: resp.candidates[0].content.parts
        if img_obj is None:
            candidates = getattr(resp, "candidates", None) or []
            for cand in candidates:
                content = getattr(cand, "content", None)
                parts = getattr(content, "parts", None) if content else None
                if not parts:
                    continue
                for part in parts:
                    if getattr(part, "inline_data", None) is not None:
                        img_obj = part.as_image()
                        break
                    maybe_img = getattr(part, "image", None)
                    if maybe_img is not None:
                        img_obj = maybe_img
                        break
                if img_obj is not None:
                    break

        if img_obj is None:
            raise RuntimeError("Gemini API returned no image data.")

        img = _coerce_to_pil(img_obj)

    except ClientError as e:
        raw = str(e)

        model_name = gemini_model
        m = re.search(r"model:\s*([a-zA-Z0-9\.\-\_]+)", raw)
        if m:
            model_name = m.group(1)

        if "RESOURCE_EXHAUSTED" in raw and "limit: 0" in raw:
            raise RuntimeError(
                f"Gemini quota exhausted for model {model_name} (free-tier limit is 0). "
                f"Update billing/quota or switch model."
            )

        raise RuntimeError(f"Gemini request failed: {raw}")

    except Exception as e:
        raise RuntimeError(f"Gemini image generation failed: {e}")

    return _apply_pad_background(img, settings, (w, h))

# AI Horde Code

def _get_horde_kudos(api_key: str) -> str:
    if not api_key or api_key == "0000000000":
        return "Anon"
    try:
        url = "https://stablehorde.net/api/v2/find_user"
        data = get_http_client().request_json(
            "GET",
            url,
            headers={"apikey": api_key},
            timeout=5,
        ).data
        return str(int(data.get("kudos", 0)))
    except HttpStatusError:
        return "0"
    except Exception:
        return "Error"

def _get_horde_model_stats(model_name: str) -> tuple[str, str]:
    
    try:
        url = "https://aihorde.net/api/v2/status/models"
        params = {"type": "image", "model_state": "all"}
        data = get_http_client().request_json(
            "GET",
            url,
            params=params,
            timeout=10,
        ).data

        if not isinstance(data, list):
            return "x", "x"

        target = (model_name or "").strip().lower()

        for m in data:
            if not isinstance(m, dict):
                continue

            name = (
                (m.get("name") or m.get("model") or m.get("model_name") or m.get("title") or "")
                .strip()
                .lower()
            )
            if name == target:
                workers = int(m.get("count") or m.get("workers") or 0)
                queued = int(m.get("queued") or 0)
                pending = int(m.get("pending") or m.get("processing") or 0)
                jobs = queued + pending
                return f"{workers:,}", f"{jobs:,}"

        return "x", "x"
    except Exception:
        return "x", "x"

HORDE_TASK_NAME = "ai_horde_generate"
HORDE_TIMEOUT_SECONDS = 180.0
HORDE_IMAGE_MAX_BYTES = 24 * 1024 * 1024
_HORDE_EXECUTOR = None
_HORDE_EXECUTOR_LOCK = threading.Lock()


def _get_horde_executor() -> LongTaskExecutor:
    global _HORDE_EXECUTOR
    with _HORDE_EXECUTOR_LOCK:
        if _HORDE_EXECUTOR is None or _HORDE_EXECUTOR.closed:
            _HORDE_EXECUTOR = LongTaskExecutor(
                {HORDE_TASK_NAME: _horde_long_task},
                max_workers=1,
                max_queue=1,
                register_global=True,
            )
        return _HORDE_EXECUTOR


def _horde_long_task(worker_payload, cancel_event):
    """Submit, poll, and decode one Horde image inside a killable process."""

    try:
        timeout_seconds = float(
            worker_payload.get("timeout_seconds", HORDE_TIMEOUT_SECONDS)
        )
    except (TypeError, ValueError, OverflowError):
        timeout_seconds = HORDE_TIMEOUT_SECONDS
    timeout_seconds = max(0.01, min(HORDE_TIMEOUT_SECONDS, timeout_seconds))
    # Spawned workers do not inherit ContextVars. Rebind the process-safe
    # cancellation event and a local absolute deadline explicitly.
    context = TaskContext(
        cancel_event,
        time.monotonic() + timeout_seconds,
        time.monotonic,
    )
    client = get_http_client()
    headers = {
        "apikey": worker_payload["api_key"],
        "Content-Type": "application/json",
        "Client-Agent": "AIImageMultiverse:1.0:Unknown",
    }
    submit_url = "https://aihorde.net/api/v2/generate/async"
    status_url = "https://aihorde.net/api/v2/generate/status/"

    try:
        context.raise_if_cancelled()
        submitted = client.request_json(
            "POST",
            submit_url,
            json=worker_payload["request"],
            headers=headers,
            context=context,
            timeout=(5, 30),
        ).data
        if not isinstance(submitted, dict) or not submitted.get("id"):
            raise LongTaskFailure(
                "horde_invalid_response",
                "AI Horde did not return a valid job id.",
            )
        job_id = str(submitted["id"])

        while True:
            context.raise_if_cancelled()
            data = client.request_json(
                "GET",
                status_url + job_id,
                headers=headers,
                context=context,
                timeout=10,
            ).data
            if not isinstance(data, dict):
                raise LongTaskFailure(
                    "horde_invalid_response",
                    "AI Horde returned an invalid status response.",
                )

            generations = data.get("generations")
            if generations and isinstance(generations, list):
                assigned_model = generations[0].get("model")
                if assigned_model:
                    logger.info("Horde assigned model: %s", assigned_model)

            if data.get("done"):
                if not generations or not isinstance(generations[0], dict):
                    raise LongTaskFailure(
                        "horde_invalid_response",
                        "AI Horde completed without an image.",
                    )
                image_data = generations[0].get("img")
                if isinstance(image_data, str) and image_data.startswith(
                    ("http://", "https://")
                ):
                    image_bytes = client.request_bytes(
                        "GET",
                        image_data,
                        context=context,
                        timeout=20,
                        max_bytes=HORDE_IMAGE_MAX_BYTES,
                    ).data
                    image = safe_open_image(image_bytes).convert("RGB")
                else:
                    if isinstance(image_data, str) and "base64," in image_data:
                        image_data = image_data.split("base64,", 1)[1]
                    image = safe_open_base64_image(image_data).convert("RGB")

                output = BytesIO()
                image.save(output, format="PNG", optimize=True)
                return {"image_png": output.getvalue()}

            logger.info(
                "Horde pending | queue=%s | estimate_seconds=%s",
                data.get("queue_position", "?"),
                data.get("wait_time", 0),
            )
            wait_seconds = min(10.0, context.remaining_seconds())
            context.raise_if_cancelled()
            cancel_event.wait(wait_seconds)

    except LongTaskFailure:
        raise
    except HttpStatusError as error:
        if error.status == 401:
            raise LongTaskFailure(
                "horde_api_key_rejected",
                "AI Horde rejected your API key. Please check AI_HORDE_KEY.",
            ) from None
        if error.status == 403:
            raise LongTaskFailure(
                "horde_request_rejected",
                "AI Horde rejected the request. Lower the quality and try again.",
            ) from None
        if error.status == 429:
            raise LongTaskFailure(
                "horde_busy",
                "AI Horde is busy right now. Please try again later.",
            ) from None
        raise LongTaskFailure(
            "horde_http_error",
            "AI Horde submission failed. Please try again later.",
        ) from None
    except HttpClientError:
        raise LongTaskFailure(
            "horde_connection_failed",
            "AI Horde connection failed. Please try again.",
        ) from None


def _horde_identity_is_current(device_config, identity: InstanceIdentity) -> bool:
    if identity.instance_uuid is None:
        return True
    snapshot = device_config.get_playlist_manager().validate_instance_revision(
        identity.instance_uuid,
        expected_generation=identity.structural_generation,
        expected_settings_revision=identity.settings_revision,
    )
    return snapshot is not None


def _raise_horde_result_error(result) -> None:
    if result.status == "abandoned":
        raise TaskDeadlineExceeded("AI Horde generation deadline expired")
    if result.status == "canceled":
        raise TaskCancelled("AI Horde generation was canceled")
    if result.status == "stale":
        raise TaskCancelled("AI Horde result belongs to a changed plugin instance")
    raise RuntimeError(result.error or "AI Horde generation failed.")


def generate_horde_image(settings, device_config, final_prompt: str) -> Image.Image:
    model_id, entry = _get_pipeline_entry(settings)
    image_cfg = entry.get("image") or {}
    if (image_cfg.get("provider") or "").strip().lower() != "horde":
        raise RuntimeError(
            f"generate_horde_image called for non-horde model '{model_id}'."
        )

    image_env = (image_cfg.get("env") or "").strip()
    api_key = device_config.load_env_key(image_env) if image_env else None
    if not api_key:
        raise RuntimeError(f"AI Horde API key not configured ({image_env}).")

    quality = (
        (settings.get("quality") or "").strip()
        or (entry.get("default_quality") or "").strip()
        or "standard"
    ).lower()
    orientation = (device_config.get_config("orientation") or "horizontal").lower()
    width, height = (1024, 640) if orientation == "horizontal" else (640, 1024)
    if quality == "ultra":
        steps, cfg_scale, sampler = 40, 8.0, "k_dpmpp_2m"
    elif quality == "high":
        steps, cfg_scale, sampler = 35, 7.8, "k_dpmpp_2m"
    else:
        steps, cfg_scale, sampler = 20, 7.5, "k_euler_a"

    request_payload = {
        "prompt": final_prompt,
        "params": {
            "width": width,
            "height": height,
            "steps": steps,
            "sampler_name": sampler,
            "cfg_scale": cfg_scale,
            "karras": True,
            "n": 1,
        },
    }
    horde_models = entry.get("horde_models") or []
    if horde_models:
        request_payload["models"] = list(horde_models)

    context = task_context_or_default(HORDE_TIMEOUT_SECONDS)
    context.raise_if_cancelled()
    timeout_seconds = min(HORDE_TIMEOUT_SECONDS, context.remaining_seconds())
    identity = current_instance_identity() or InstanceIdentity(None, None, None)
    try:
        handle = _get_horde_executor().submit(
            HORDE_TASK_NAME,
            {
                "api_key": api_key,
                "request": request_payload,
                "timeout_seconds": timeout_seconds,
            },
            context=context,
            instance_identity=identity,
            identity_validator=lambda candidate: _horde_identity_is_current(
                device_config,
                candidate,
            ),
        )
    except LongTaskQueueFull as error:
        raise RuntimeError(
            "AI Horde is already processing the maximum number of requests."
        ) from error

    try:
        result = handle.result(
            timeout=max(0.1, context.remaining_seconds() + 1.0)
        )
    except TimeoutError as error:
        handle.cancel()
        context.raise_if_cancelled()
        raise RuntimeError("AI Horde isolated worker did not stop in time.") from error

    if result.status != "succeeded":
        _raise_horde_result_error(result)
    context.raise_if_cancelled()
    value = result.value
    if not isinstance(value, dict) or not isinstance(value.get("image_png"), bytes):
        raise RuntimeError("AI Horde isolated worker returned an invalid image.")
    image = safe_open_image(value["image_png"]).convert("RGB")
    _, target_width, target_height = _get_target_dims(device_config)
    return _apply_pad_background(
        image,
        settings,
        (target_width, target_height),
    )


# Presets

LEGACY_PRESETS_FILE = Path(__file__).parent / "presets.json"


def _presets_file() -> Path:
    data_root_raw = os.getenv("INKYPI_DATA_DIR", "").strip()
    data_root = Path(data_root_raw).expanduser() if data_root_raw else None
    override = os.getenv("INKYPI_AI_MULTIVERSE_PRESETS_FILE", "").strip()
    if override:
        path = Path(override).expanduser()
        if data_root is not None and not path.is_absolute():
            path = data_root / "plugins" / "ai_image_multiverse" / path
        return path
    if data_root is not None:
        return data_root / "plugins" / "ai_image_multiverse" / "presets.json"
    return LEGACY_PRESETS_FILE

def _atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

def _slugify(label: str) -> str:
    s = (label or "").strip().lower()
    s = re.sub(r"[\"']", "", s)
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"^_+|_+$", "", s)
    return s or "preset"

def _read_presets() -> list:
    presets_file = _presets_file()
    try:
        if not presets_file.exists():
            _atomic_write_json(presets_file, [])
            return []
        data = json.loads(presets_file.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []

def _sorted_presets(presets: list) -> list:
    return sorted(
        [p for p in presets if isinstance(p, dict) and (p.get("id") or "").strip()],
        key=lambda p: str(p.get("label") or p.get("id") or "").casefold()
    )

@plugin_bp.get("/plugin/<plugin_id>/presets/list")
def presets_list(plugin_id):
    if plugin_id != "ai_image_multiverse":
        abort(404)
    presets = _sorted_presets(_read_presets())
    resp = jsonify({"ok": True, "presets": presets})
    resp.headers["Cache-Control"] = "no-store"
    return resp

@plugin_bp.post("/plugin/<plugin_id>/presets/add")
def presets_add(plugin_id):
    if plugin_id != "ai_image_multiverse":
        abort(404)

    payload = request.get_json(silent=True) or {}
    label = (payload.get("label") or "").strip()
    prompt = (payload.get("prompt") or "").strip()

    if not label or not prompt:
        return jsonify({"ok": False, "error": "label_and_prompt_required"}), 400

    presets = _read_presets()

    label_norm = re.sub(r"\s+", " ", label).strip().casefold()
    existing_labels = {
        re.sub(r"\s+", " ", (p.get("label") or "")).strip().casefold()
        for p in presets
        if isinstance(p, dict)
    }
    if label_norm in existing_labels:
        return jsonify({
            "ok": False,
            "error": "duplicate_preset_label",
            "message": "That preset name already exists. Please pick another name."
        }), 409

    base_id = _slugify(label)
    existing_ids = {str(p.get("id") or "") for p in presets if isinstance(p, dict)}
    if base_id in existing_ids:
        return jsonify({
            "ok": False,
            "error": "duplicate_preset_id",
            "message": "That preset name would create a duplicate ID. Please pick another name."
        }), 409

    preset_id = base_id
    presets.append({"id": preset_id, "label": label, "prompt": prompt})
    presets = _sorted_presets(presets)

    _atomic_write_json(_presets_file(), presets)
    return jsonify({"ok": True, "presets": presets, "added_id": preset_id})

@plugin_bp.post("/plugin/<plugin_id>/presets/delete")
def presets_delete(plugin_id):
    if plugin_id != "ai_image_multiverse":
        abort(404)

    payload = request.get_json(silent=True) or {}
    preset_id = (payload.get("id") or "").strip()

    if not preset_id:
        return jsonify({"ok": False, "error": "id_required"}), 400

    presets = [
        p for p in _read_presets()
        if isinstance(p, dict) and (p.get("id") or "").strip() != preset_id
    ]
    presets = _sorted_presets(presets)

    _atomic_write_json(_presets_file(), presets)
    return jsonify({"ok": True, "presets": presets, "deleted_id": preset_id})

class AIImageMultiverse(BasePlugin):

    def generate_settings_template(self):
        template_params = super().generate_settings_template()

        from flask import current_app
        device_config = current_app.config["DEVICE_CONFIG"]
        env_map = _get_env_map(device_config)

        # Pull kudos once (only if Horde key exists)
        horde_key = (device_config.load_env_key("AI_HORDE_KEY") or "").strip()
        horde_kudos = _get_horde_kudos(horde_key) if horde_key else "0"

        available_models = []
        for mid, cfg in (MODEL_PIPELINE or {}).items():
            image_cfg = (cfg or {}).get("image") or {}
            image_env = (image_cfg.get("env") or "").strip()
            provider = (image_cfg.get("provider") or "").strip().lower()

            # existing filter by IMAGE env availability
            if image_env and not env_map.get(image_env, False):
                continue

            label = (cfg or {}).get("label", mid)

            # ---- Horde label decoration
            if provider == "horde":
                horde_models = (cfg or {}).get("horde_models") or []
                if horde_models:
                    workers, jobs = _get_horde_model_stats(horde_models[0])
                    label = f"{label} (Workers {workers} | Load {jobs})"
                else:
                    label = f"{label} ({horde_kudos} Kudos)"

            available_models.append({"id": mid, "label": label})

        template_params["available_models"] = available_models
        template_params["ui_config"] = {"pipeline": MODEL_PIPELINE, "env": env_map}

        template_params["api_key"] = {
            "required": True,
            "service": "OpenAI",
            "expected_key": "OPEN_AI_SECRET",
        }
        return template_params

    def generate_image(self, settings, device_config):

        final_prompt = build_final_prompt(settings, device_config)

        model_id, entry = _get_pipeline_entry(settings)
        provider = ((entry.get("image") or {}).get("provider") or "").strip().lower()

        if provider == "openai":
            return generate_openai_image(settings, device_config, final_prompt)
        
        if provider == "gemini":
            return generate_gemini_image(settings, device_config, final_prompt)

        if provider == "horde":
            return generate_horde_image(settings, device_config, final_prompt)

        raise RuntimeError(f"Provider '{provider}' not supported yet in this module.")
