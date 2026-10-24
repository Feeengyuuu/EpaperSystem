"""Money Radar: FX, crypto, Treasury yields and macro, plus news, earnings and filings for holdings."""

from __future__ import annotations

import logging
import os
import re

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.render_provenance import attach_source_provenance
from plugins.base_plugin.source_book import Source, SourceBook, combined_provenance
from plugins.context_cache import read_contexts
from runtime.long_task_executor import (
    current_instance_identity, current_instance_identity_validator, task_context_or_default,
)
from runtime.refresh_contracts import TaskCancelled, TaskContext
from utils.app_utils import coerce_bool, get_available_font_names
from utils.cache_manager import CacheBudget

from . import sources


logger = logging.getLogger(__name__)
PLUGIN_ID = "money_radar"
CACHE_SCHEMA = 1
CACHE_BUDGET = CacheBudget(30 * 86400, 16, 4 * 1024 * 1024)
DEFAULT_FONT = "Microsoft YaHei"
TASK_SECONDS = 45
PORTFOLIO_CONTEXT_MAX_AGE = 30 * 86400
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}$")


def _checkpoint(context):
    context.raise_if_cancelled()
    identity = current_instance_identity()
    validator = current_instance_identity_validator()
    if identity is not None and validator is not None and not validator(identity):
        raise TaskCancelled("Money Radar instance changed during refresh")


class MoneyRadar(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    def _book(self):
        directory = self.cache_dir(env_var="MONEY_RADAR_CACHE_DIR", leaf="sources", create=True, strip=True)
        return SourceBook(directory, CACHE_BUDGET, schema=CACHE_SCHEMA, max_bytes=512 * 1024,
                          checkpoint=_checkpoint)

    @staticmethod
    def _env_key(device_config, key):
        value = ""
        if device_config is not None and hasattr(device_config, "load_env_key"):
            try:
                value = device_config.load_env_key(key) or ""
            except Exception as exc:
                logger.warning("Could not load %s for Money Radar: %s", key, exc.__class__.__name__)
        return str(value or os.getenv(key, "") or "").strip()

    @staticmethod
    def _portfolio_symbols():
        try:
            entries = read_contexts(["stocktracker"], max_age_seconds=PORTFOLIO_CONTEXT_MAX_AGE, include_stale=True)
        except Exception as exc:
            logger.debug("Could not read Stock Tracker holdings context: %s", exc)
            return ()
        for entry in entries:
            symbols = (entry.get("payload") or {}).get("symbols")
            if isinstance(symbols, list):
                return tuple(str(symbol) for symbol in symbols)
        return ()

    def _symbols(self, settings):
        raw = str(settings.get("symbols") or "").strip()
        if raw and raw.lower() != "auto":
            return sources.symbols_setting(raw), False
        portfolio = self._portfolio_symbols()
        if portfolio:
            try:
                return sources.symbols_setting(None, portfolio), True
            except ValueError:
                pass
        return sources.symbols_setting(None), False

    def _payload(self, settings, device_config, now, *, allow_network, context):
        symbols, from_portfolio = self._symbols(settings)
        coins = sources.coins_setting(settings.get("coins"))
        fred_key = self._env_key(device_config, "FRED_API_KEY")
        finnhub_key = self._env_key(device_config, "FINNHUB_API_KEY")
        contact = str(settings.get("secContact") or "").strip()
        contact = contact if EMAIL.fullmatch(contact) else ""
        force = allow_network and coerce_bool(settings.get("forceRefresh"), default=False)
        book = self._book()

        def load(spec):
            return book.load(spec, now, allow_network=allow_network, context=context, force=force)

        results = {
            "markets": load(Source("markets", {"symbols": list(sources.MARKET_SYMBOLS)}, sources.fetch_markets,
                                   fresh_seconds=1800, usable_seconds=2 * 86400, validate=sources.valid_markets,
                                   timeout_seconds=12)),
            "fx": load(Source("fx", {"base": "USD", "to": list(sources.FX_CURRENCIES)},
                              lambda ctx: sources.fetch_fx(now, ctx), fresh_seconds=6 * 3600,
                              usable_seconds=5 * 86400, validate=sources.valid_fx, timeout_seconds=8)),
            "crypto": load(Source("crypto", {"coins": list(coins)}, lambda ctx: sources.fetch_crypto(coins, ctx),
                                  fresh_seconds=1200, usable_seconds=86400, validate=sources.valid_crypto,
                                  timeout_seconds=8)),
            "news": load(Source("news", {"symbols": list(symbols)}, lambda ctx: sources.fetch_news(symbols, ctx),
                                fresh_seconds=1800, usable_seconds=86400, validate=sources.valid_news,
                                timeout_seconds=12)),
            # CPI and unemployment come out once a month: a reading stays current for days, so a
            # BLS outage (503 "website is busy" on 2026-10-24) no longer turns the page stale.
            "macro": load(Source("macro", {"fred": bool(fred_key)},
                                 lambda ctx: sources.fetch_macro(fred_key, ctx), fresh_seconds=3 * 86400,
                                 usable_seconds=30 * 86400, validate=sources.valid_macro, timeout_seconds=10)),
        }
        ciks = load(Source("sec_ciks", {"symbols": list(symbols)},
                           (lambda ctx: sources.fetch_ciks(symbols, contact, ctx)) if contact else None,
                           fresh_seconds=7 * 86400, usable_seconds=30 * 86400, validate=sources.valid_ciks,
                           timeout_seconds=10))
        cik_map = ciks["data"].get("map") or {}
        results["filings"] = load(Source(
            "filings", {"symbols": list(symbols)},
            (lambda ctx: sources.fetch_filings(symbols, cik_map, contact, now, ctx)) if contact and cik_map else None,
            fresh_seconds=4 * 3600, usable_seconds=3 * 86400, validate=sources.valid_filings, timeout_seconds=15))
        if contact and not cik_map:
            results["filings"] = {**results["filings"], "state": ciks["state"], "error": ciks["error"]}
        results["earnings"] = load(Source(
            "earnings", {"symbols": list(symbols)},
            (lambda ctx: sources.fetch_earnings(symbols, finnhub_key, now, ctx)) if finnhub_key else None,
            fresh_seconds=12 * 3600, usable_seconds=3 * 86400, validate=sources.valid_earnings, timeout_seconds=12))
        return {**results, "symbols": list(symbols), "symbols_from_portfolio": from_portfolio,
                "provenance": combined_provenance(results.values())}

    def render_cached_display(self, settings, device_config, *, resolved_theme_context):
        return self.render_themed_image(settings, device_config, theme_render_only=True,
                                        resolved_theme_context=resolved_theme_context)

    def generate_image(self, settings, device_config):
        from .render import render_page

        settings = dict(settings or {})
        settings["_inkypi_theme"] = settings.get("_inkypi_theme") or self.resolve_theme(settings, device_config)
        cache_only = any(coerce_bool(settings.get(key), default=False)
                         for key in ("_theme_render_only", "_cached_render_only", "cached_render_only"))
        parent = task_context_or_default(timeout_seconds=TASK_SECONDS)
        context = TaskContext(parent.cancel_event, min(parent.deadline_monotonic, parent.clock() + TASK_SECONDS),
                              parent.clock)
        now = sources.local_now()
        _checkpoint(context)
        payload = self._payload(settings, device_config, now, allow_network=not cache_only, context=context)
        image = render_page(self.get_dimensions(device_config), payload, settings, now)
        _checkpoint(context)
        return attach_source_provenance(image, payload["provenance"], detail=PLUGIN_ID)
