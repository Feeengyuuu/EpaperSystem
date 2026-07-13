#!/usr/bin/env python3

"""
Stock Tracker Plugin for InkyPi – Enhanced Dashboard

File: src/plugins/stocktracker/stocktracker.py
Author: MEAN-GAIN
Description:
    A portfolio tracking plugin for InkyPi that retrieves market data using
    yfinance and renders a visual dashboard showing stock performance,
    portfolio value, and historical trends.

DISCLAIMER:
    This software is provided for informational and educational purposes only.
    It is NOT intended to be financial, investment, trading, or legal advice.
    Market data is obtained from third-party sources and may be delayed,
    inaccurate, or incomplete.

    The authors and contributors make no representations or warranties of any
    kind regarding the accuracy, reliability, or suitability of the information
    displayed and accept no liability for any losses or damages arising from
    its use. Use this software entirely at your own risk.

Dependencies:
    - yfinance
    - matplotlib
    - numpy
    - Pillow (PIL)

Compatible with:
    InkyPi plugin architecture

Note:
    Logging is used for debugging and error reporting. The InkyPi framework
    is responsible for configuring the logging system.
"""

from plugins.base_plugin.base_plugin import BasePlugin

import os
import sys
from collections.abc import Mapping
from contextvars import ContextVar

VENDOR_DIR = os.path.join(os.path.dirname(__file__), "_vendor")
if os.path.isdir(VENDOR_DIR) and VENDOR_DIR not in sys.path:
	sys.path.insert(0, VENDOR_DIR)

GOOGLE_VENDOR_DIR = os.path.join(VENDOR_DIR, "google")
if os.path.isdir(GOOGLE_VENDOR_DIR):
	try:
		import google
		google_path = getattr(google, "__path__", None)
		if google_path is not None and GOOGLE_VENDOR_DIR not in list(google_path):
			google_path.append(GOOGLE_VENDOR_DIR)
	except Exception:
		pass

def _default_mpl_config_dir():
	cache_root = os.getenv("INKYPI_CACHE_DIR", "").strip()
	if cache_root:
		return os.path.join(
			os.path.expanduser(cache_root),
			"plugins",
			"stocktracker",
			"matplotlib",
		)
	return os.path.join(os.path.dirname(__file__), "_mplconfig")


MPLCONFIGDIR = _default_mpl_config_dir()
os.environ.setdefault("MPLCONFIGDIR", MPLCONFIGDIR)
os.makedirs(os.environ["MPLCONFIGDIR"], exist_ok=True)

from PIL import Image, ImageDraw, ImageFont, ImageOps
from utils.app_utils import get_base_ui_font
from utils.image_utils import text_width
from utils.massive_market_data import (
	MassiveMarketData,
	MassiveMarketDataError,
	load_massive_api_key,
	massive_ticker_candidates,
)
from utils.theme_utils import get_theme_palette
import csv
import hashlib
import io
import json
import logging
import math
import re
from datetime import datetime

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
PAPER = (255, 248, 220)  # 25Y PANTONE 100, vintage comic paper ground
PANEL = (255, 253, 240)
PANEL_BLUE = (235, 246, 255)  # 25B PANTONE 304 family, paper-tinted
PANEL_GOLD = (255, 239, 176)  # 50Y PANTONE 101 family, paper-tinted
INK = (8, 8, 8)  # PROCESS BLACK
MUTED = (126, 112, 82)  # 50Y-25R-25B PANTONE 465 family
GRID = (190, 177, 134)
BORDER = INK
ACCENT_BLUE = (0, 92, 185)  # 100B-25R PANTONE 285 family
ACCENT_GOLD = (255, 196, 30)  # 100Y-25R PANTONE 123 family
ACCENT_ORANGE = (245, 122, 38)  # 100Y-50R PANTONE ORANGE 021 family
CINNABAR = (222, 45, 38)  # 100Y-100R PANTONE RED 032 family
MALACHITE = (0, 152, 82)  # 100Y-100B PANTONE 354 family
CHART_MARKER_GREEN = (0, 92, 50)  # 100Y-100B-50R PANTONE 350 family
ROW_COLORS = [
	PANEL,
	(240, 247, 255),
]
TICKER_PALETTES = {
	"day": {
		"fill": PANEL_GOLD,
		"border": ACCENT_GOLD,
		"top_line": ACCENT_ORANGE,
		"separator": GRID,
		"symbol": INK,
		"price": ACCENT_BLUE,
		"value": MUTED,
	},
	"night": {
		"fill": INK,
		"border": ACCENT_GOLD,
		"top_line": ACCENT_ORANGE,
		"separator": ACCENT_GOLD,
		"symbol": WHITE,
		"price": ACCENT_GOLD,
		"value": WHITE,
	},
}
LOGO_ASSET_BY_SYMBOL = {
	"AAPL": "aapl.png",
	"AMZN": "amzn.png",
	"CASH": "cash.png",
	"GOOG": "goog.png",
	"GOOGL": "googl.png",
	"MSFT": "msft.png",
	"NTDOY": "ntdoy.png",
	"NVDA": "nvda.png",
	"SPCX": "spcx.png",
	"SPY": "spy.png",
	"TSLA": "tsla.png",
	"TTWO": "ttwo.png",
	"VGIT": "vgit.png",
	"VTI": "vti.png",
	"VXUS": "vxus.png",
}
SECTION_WORDMARK_IMAGES = {
	"PORTFOLIO": ("subtitle_portfolio.png", (118, 24)),
	"PORTFOLIO TREND": ("subtitle_portfolio_trend.png", (184, 24)),
	"HOLDINGS": ("subtitle_holdings.png", (116, 24)),
}
CSV_SYMBOL_FIELDS = ("symbol", "ticker", "tickersymbol", "instrument", "securitysymbol")
CSV_SHARE_FIELDS = ("shares", "share", "quantity", "qty", "currentquantity", "position")
CSV_ACTION_FIELDS = ("action", "type", "activitytype", "transactiontype", "transcode", "description")
CSV_NEGATIVE_ACTION_HINTS = ("sell", "sold", "transfer out", "outgoing", "journal out", "removed")
CSV_POSITIVE_ACTION_HINTS = ("buy", "bought", "reinvest", "transfer in", "incoming", "journal in", "received")
CASH_SYMBOLS = ("cash", "usd", "us dollar", "money market")
EXTENDED_HISTORY_PERIOD = "1d"
EXTENDED_HISTORY_INTERVAL = "1m"
PORTFOLIO_HISTORY_DIR_ENV = "INKYPI_STOCKTRACKER_HISTORY_DIR"
PORTFOLIO_HISTORY_FILE_ENV = "INKYPI_STOCKTRACKER_HISTORY_FILE"
PORTFOLIO_HISTORY_MAX_DAYS = 180
SOURCE_CACHE_SCHEMA_VERSION = "stocktracker-source-v1"
SOURCE_CACHE_LEAF = "source"

_LEGACY_STOCK_COLORS = {
	"mode": "day",
	"white": WHITE,
	"paper": PAPER,
	"panel": PANEL,
	"panel_blue": PANEL_BLUE,
	"panel_gold": PANEL_GOLD,
	"ink": INK,
	"muted": MUTED,
	"grid": GRID,
	"border": BORDER,
	"accent_blue": ACCENT_BLUE,
	"accent_gold": ACCENT_GOLD,
	"accent_orange": ACCENT_ORANGE,
	"cinnabar": CINNABAR,
	"malachite": MALACHITE,
	"chart_marker_green": CHART_MARKER_GREEN,
	"row_colors": ROW_COLORS,
}
_ACTIVE_STOCK_COLORS = ContextVar("stocktracker_render_colors", default=None)


def _rgb(value, fallback):
	if isinstance(value, (list, tuple)) and len(value) == 3:
		try:
			channels = tuple(int(channel) for channel in value)
		except (TypeError, ValueError):
			return fallback
		if all(0 <= channel <= 255 for channel in channels):
			return channels
	return fallback


def _blend_rgb(foreground, background, amount):
	amount = min(max(float(amount), 0.0), 1.0)
	return tuple(
		int(round(foreground[index] * amount + background[index] * (1.0 - amount)))
		for index in range(3)
	)


def _stock_render_colors(theme_context):
	if not isinstance(theme_context, Mapping):
		return _LEGACY_STOCK_COLORS
	mode = "night" if str(theme_context.get("mode") or "").strip().lower() == "night" else "day"
	canonical = get_theme_palette(mode)
	supplied = theme_context.get("palette")
	if isinstance(supplied, Mapping):
		for role in ("background", "panel", "ink", "muted", "rule", "accent"):
			canonical[role] = _rgb(supplied.get(role), canonical[role])
	background = canonical["background"]
	panel = canonical["panel"]
	ink = canonical["ink"]
	muted = canonical["muted"]
	rule = canonical["rule"]
	accent = canonical["accent"]
	gold = canonical["gold"]
	green = canonical["green"]
	red = canonical["red"]
	return {
		"mode": mode,
		"white": panel,
		"paper": background,
		"panel": panel,
		"panel_blue": _blend_rgb(accent, panel, 0.11 if mode == "day" else 0.18),
		"panel_gold": _blend_rgb(gold, panel, 0.16 if mode == "day" else 0.20),
		"ink": ink,
		"muted": muted,
		"grid": rule,
		"border": ink,
		"accent_blue": accent,
		"accent_gold": gold,
		"accent_orange": gold,
		"cinnabar": red,
		"malachite": green,
		"chart_marker_green": green,
		"row_colors": [panel, _blend_rgb(accent, panel, 0.055 if mode == "day" else 0.10)],
	}


def _active_stock_colors():
	return _ACTIVE_STOCK_COLORS.get() or _LEGACY_STOCK_COLORS

_yf = None
_plt = None
_np = None


def _load_yfinance():
	global _yf
	if _yf is None:
		import yfinance as yf
		_yf = yf
	return _yf


def _load_plot_libs():
	global _plt, _np
	if _plt is None:
		import matplotlib
		matplotlib.use("Agg", force=True)
		import matplotlib.pyplot as plt
		_plt = plt
	if _np is None:
		import numpy as np
		_np = np
	return _plt, _np


class _SimpleIloc:
	def __init__(self, values):
		self.values = values

	def __getitem__(self, index):
		return self.values[index]


class _SimpleCloseSeries:
	def __init__(self, values, index):
		self.values = list(values)
		self.index = list(index)
		self.iloc = _SimpleIloc(self.values)
		self.empty = len(self.values) == 0

	def dropna(self):
		pairs = [(index, value) for index, value in zip(self.index, self.values) if value is not None]
		return _SimpleCloseSeries([value for _, value in pairs], [index for index, _ in pairs])


class _SimpleLoc:
	def __init__(self, values):
		self.values = values

	def __getitem__(self, key):
		date_key, column = key
		if column != "Close":
			raise KeyError(column)
		return self.values[date_key]

	def __setitem__(self, key, value):
		date_key, column = key
		if column != "Close":
			raise KeyError(column)
		self.values[date_key] = value


class _SimpleHistory:
	def __init__(self, points):
		self.index = [date_key for date_key, _close in points]
		self.loc = _SimpleLoc({date_key: close for date_key, close in points})
		self.columns = ["Close"]
		self.empty = len(points) == 0

	def __getitem__(self, key):
		if key != "Close":
			raise KeyError(key)
		return _SimpleCloseSeries([self.loc.values[index] for index in self.index], self.index)

	def copy(self):
		return _SimpleHistory([(index, self.loc.values[index]) for index in self.index])


class StockTracker(BasePlugin):

	"""Stock portfolio tracker plugin for InkyPi"""

	# Constants for improved code readability
	CARD_HEIGHT_RATIO = 0.22
	CARD_WIDTH_RATIO = 0.42
	CHART_MARGIN_RATIO = 0.15
	DEFAULT_TICKER_NAME_MAX_LENGTH = 25
	_holding_logo_cache = {}
	_header_logo_cache = {}
	_section_wordmark_cache = {}

	def generate_image(self, settings, device_config):

		"""Generate stock portfolio dashboard"""
		settings = settings or {}
		dimensions = self.get_dimensions(device_config)
		theme_context = settings.get("_inkypi_theme")
		if not isinstance(theme_context, Mapping):
			theme_context = self.resolve_theme(settings, device_config)

		period, holdings = self._portfolio_holdings_from_settings(settings)
		portfolio_meta = self._portfolio_meta_from_settings(settings)
		data_provider = self._data_provider(settings)
		source_key = self._source_cache_key(period, holdings, portfolio_meta, data_provider)
		theme_render_only = self._enabled(settings.get("_theme_render_only"))

		if theme_render_only:
			cached = self._read_source_cache(source_key)
			if cached is None:
				raise RuntimeError(
					"Theme-only redraw requires a matching StockTracker source cache."
				)
			stock_data = cached["stock_data"]
			history_points = cached["history_points"]
			updated_at = cached["generated_at"]
		else:
			massive_client = self._massive_client(device_config, data_provider)
			stock_data = []
			for ticker, share_count in holdings:
				try:
					data = self._fetch_stock_data(
						ticker,
						share_count,
						period,
						data_provider=data_provider,
						massive_client=massive_client,
					)
					if data:
						stock_data.append(data)
				except Exception as e:
					raise RuntimeError(f"Error fetching {ticker}: {str(e)}")

			stock_data.extend(self._portfolio_meta_rows(portfolio_meta, stock_data))
			if not stock_data:
				raise RuntimeError("No valid stock data retrieved")
			updated_at = datetime.now()
			history_points = self._record_portfolio_snapshot(
				stock_data,
				now=updated_at,
				account_value_override=portfolio_meta.get("account_value"),
			)
			self._write_source_cache(
				source_key,
				stock_data,
				history_points,
				updated_at,
			)

		account_value_override = portfolio_meta.get("account_value")
		return self._create_dashboard(
			stock_data,
			dimensions,
			history_points,
			tracking_window_label=self._tracking_window_label(period),
			holdings_pin_symbols=self._symbols_setting(settings.get("holdings_pin_symbols")),
			holdings_sink_symbols=self._symbols_setting(settings.get("holdings_sink_symbols")),
			account_value_override=account_value_override,
			header_brand=self._header_brand_from_settings(settings, portfolio_meta),
			ticker_theme=settings.get("ticker_theme", "auto"),
			theme_context=theme_context,
			updated_at=updated_at,
		)

	@staticmethod
	def _enabled(value):
		if isinstance(value, bool):
			return value
		return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

	@staticmethod
	def _source_cache_key(period, holdings, portfolio_meta, data_provider):
		payload = {
			"schema": SOURCE_CACHE_SCHEMA_VERSION,
			"period": str(period or ""),
			"holdings": [
				[str(symbol or "").upper(), round(float(shares), 8)]
				for symbol, shares in holdings
			],
			"portfolio_meta": {
				**{
					key: portfolio_meta.get(key)
					for key in ("account_value", "cash_balance", "buying_power")
					if portfolio_meta.get(key) is not None
				},
				"currency": str(portfolio_meta.get("currency") or "USD").strip().upper() or "USD",
			},
			"data_provider": str(data_provider or "auto"),
		}
		encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
		return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]

	def _source_cache_file(self, source_key, *, create):
		return self.cache_dir(leaf=SOURCE_CACHE_LEAF, create=create) / f"{source_key}.json"

	@staticmethod
	def _serialized_history(history):
		if history is None or getattr(history, "empty", True):
			return []
		points = []
		for date_key in list(getattr(history, "index", [])):
			try:
				close = float(history.loc[date_key, "Close"])
			except (KeyError, TypeError, ValueError, IndexError):
				continue
			if math.isfinite(close):
				points.append([str(date_key), close])
		return points

	@classmethod
	def _serialized_stock_data(cls, stock_data):
		serialized = []
		for item in stock_data:
			record = {}
			for key, value in item.items():
				if key == "history":
					record[key] = cls._serialized_history(value)
				elif value is None or isinstance(value, (str, int, float, bool)):
					record[key] = value
				elif isinstance(value, (list, tuple)) and len(value) == 3:
					record[key] = list(value)
				else:
					record[key] = str(value)
			serialized.append(record)
		return serialized

	@staticmethod
	def _rehydrated_stock_data(raw_stock_data):
		if not isinstance(raw_stock_data, list) or not raw_stock_data:
			return None
		stock_data = []
		for raw in raw_stock_data:
			if not isinstance(raw, dict) or not str(raw.get("symbol") or "").strip():
				return None
			record = dict(raw)
			points = record.get("history")
			if not isinstance(points, list):
				return None
			clean_points = []
			for point in points:
				if not isinstance(point, (list, tuple)) or len(point) != 2:
					continue
				try:
					close = float(point[1])
				except (TypeError, ValueError):
					continue
				if math.isfinite(close):
					clean_points.append((str(point[0]), close))
			if not clean_points:
				return None
			record["history"] = _SimpleHistory(clean_points)
			for color_key in ("change_text_color", "indicator_color"):
				if isinstance(record.get(color_key), list):
					record[color_key] = _rgb(record[color_key], MUTED)
			stock_data.append(record)
		return stock_data

	def _write_source_cache(self, source_key, stock_data, history_points, generated_at):
		path = self._source_cache_file(source_key, create=True)
		payload = {
			"schema": SOURCE_CACHE_SCHEMA_VERSION,
			"cache_key": source_key,
			"generated_at": generated_at.isoformat(),
			"stock_data": self._serialized_stock_data(stock_data),
			"history_points": [
				point
				for point in (
					self._normalize_portfolio_history_entry(item)
					for item in (history_points or [])
				)
				if point
			],
		}
		temp = path.with_suffix(path.suffix + ".tmp")
		try:
			temp.write_text(
				json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")),
				encoding="utf-8",
			)
			os.replace(temp, path)
		except Exception as e:
			logging.warning(f"Could not write StockTracker source cache: {type(e).__name__}: {e}")
			try:
				temp.unlink(missing_ok=True)
			except Exception:
				pass

	def _read_source_cache(self, source_key):
		path = self._source_cache_file(source_key, create=False)
		try:
			payload = json.loads(path.read_text(encoding="utf-8"))
		except Exception:
			return None
		if (
			payload.get("schema") != SOURCE_CACHE_SCHEMA_VERSION
			or payload.get("cache_key") != source_key
		):
			return None
		stock_data = self._rehydrated_stock_data(payload.get("stock_data"))
		if not stock_data:
			return None
		history_points = [
			point
			for point in (
				self._normalize_portfolio_history_entry(item)
				for item in (payload.get("history_points") or [])
			)
			if point
		]
		try:
			generated_at = datetime.fromisoformat(str(payload.get("generated_at") or ""))
		except ValueError:
			return None
		return {
			"stock_data": stock_data,
			"history_points": history_points,
			"generated_at": generated_at,
		}

	def _portfolio_holdings_from_settings(self, settings):
		period = settings.get('period', '1mo')
		csv_path = (settings.get('portfolio_csv_file') or settings.get('portfolio_csv_path') or '').strip()
		if csv_path:
			resolved_path = self._resolve_portfolio_csv_path(csv_path)
			return period, self._load_portfolio_csv(resolved_path)

		try:
			tickers_str = settings.get('tickers', '').strip()
			shares_str = settings.get('shares', '').strip()

			if not tickers_str or not shares_str:
				raise RuntimeError("Please provide both tickers and shares, or upload a portfolio CSV")

			tickers = [t.strip().upper() for t in tickers_str.split(',') if t.strip()]
			shares = [float(s.strip()) for s in shares_str.split(',') if s.strip()]

			if len(tickers) != len(shares):
				raise RuntimeError("Number of tickers and shares must match")

			return period, list(zip(tickers, shares))
		except ValueError as e:
			raise RuntimeError(f"Invalid input format: {str(e)}")

	@staticmethod
	def _data_provider(settings):
		provider = str((settings or {}).get("data_provider") or "auto").strip().lower()
		return provider if provider in {"auto", "massive", "yfinance"} else "auto"

	def _massive_client(self, device_config, data_provider):
		if data_provider == "yfinance":
			return None
		api_key = load_massive_api_key(device_config)
		if not api_key:
			return None
		return MassiveMarketData(api_key)

	def _resolve_portfolio_csv_path(self, csv_path):
		csv_path = os.path.expanduser(str(csv_path).strip())
		if os.path.isabs(csv_path):
			resolved_path = csv_path
		else:
			candidates = [
				os.path.abspath(csv_path),
				os.path.abspath(os.path.join(os.path.dirname(__file__), csv_path)),
				os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", csv_path)),
			]
			resolved_path = next((path for path in candidates if os.path.isfile(path)), candidates[0])

		if not os.path.isfile(resolved_path):
			raise RuntimeError(f"Portfolio CSV not found: {resolved_path}")
		return resolved_path

	@staticmethod
	def _csv_key(value):
		return ''.join(ch for ch in str(value).lower() if ch.isalnum())

	@staticmethod
	def _csv_value(row, field_names):
		for field_name in field_names:
			value = row.get(field_name)
			if value not in (None, ''):
				return str(value).strip()
		return ''

	@staticmethod
	def _parse_csv_number(value):
		text = str(value or '').strip()
		if not text:
			return None
		negative = text.startswith('(') and text.endswith(')')
		text = text.strip('()').replace('$', '').replace(',', '').replace('%', '').strip()
		try:
			number = float(text)
		except ValueError:
			return None
		return -number if negative else number

	@staticmethod
	def _clean_csv_symbol(value):
		symbol = str(value or '').strip().upper()
		if not symbol or symbol.lower() in CASH_SYMBOLS:
			return ''
		return symbol

	def _signed_csv_quantity(self, row, quantity):
		action_text = ' '.join(
			self._csv_value(row, (field_name,)).lower()
			for field_name in CSV_ACTION_FIELDS
			if self._csv_value(row, (field_name,))
		)
		if any(hint in action_text for hint in CSV_NEGATIVE_ACTION_HINTS):
			return -abs(quantity)
		if any(hint in action_text for hint in CSV_POSITIVE_ACTION_HINTS):
			return abs(quantity)
		if self._csv_value(row, ("transcode",)).upper() == "SPL":
			return abs(quantity)
		return quantity

	def _load_portfolio_csv(self, csv_path):
		holdings = {}
		order = []
		with open(csv_path, newline='', encoding='utf-8-sig') as csv_file:
			reader = csv.DictReader(csv_file)
			if not reader.fieldnames:
				raise RuntimeError("Portfolio CSV is missing a header row")

			for raw_row in reader:
				row = {self._csv_key(key): value for key, value in raw_row.items()}
				symbol = self._clean_csv_symbol(self._csv_value(row, CSV_SYMBOL_FIELDS))
				quantity = self._parse_csv_number(self._csv_value(row, CSV_SHARE_FIELDS))
				if not symbol or quantity is None:
					continue

				quantity = self._signed_csv_quantity(row, quantity)
				if symbol not in holdings:
					holdings[symbol] = 0.0
					order.append(symbol)
				holdings[symbol] += quantity

		result = [(symbol, holdings[symbol]) for symbol in order if holdings[symbol] > 0.0001]
		if not result:
			raise RuntimeError("No positive stock holdings found in portfolio CSV")
		return result

	def _fetch_extended_quote(self, stock, ticker):
		try:
			extended_hist = stock.history(
				period=EXTENDED_HISTORY_PERIOD,
				interval=EXTENDED_HISTORY_INTERVAL,
				prepost=True,
			)
		except Exception as e:
			logging.warning(f"Extended-hours quote unavailable for {ticker}: {type(e).__name__}: {e}")
			return None

		if getattr(extended_hist, "empty", True):
			return None
		if "Close" not in getattr(extended_hist, "columns", []):
			return None

		closes = extended_hist["Close"].dropna()
		if getattr(closes, "empty", True):
			return None

		return {
			"price": float(closes.iloc[-1]),
			"timestamp": closes.index[-1] if len(closes.index) else None,
		}

	@staticmethod
	def _history_with_latest_close(hist, latest_price):
		try:
			updated_hist = hist.copy()
			updated_hist.loc[updated_hist.index[-1], "Close"] = float(latest_price)
			return updated_hist
		except Exception as e:
			logging.warning(f"Unable to apply latest quote to trend history: {type(e).__name__}: {e}")
			return hist

	def _fetch_stock_data(self, ticker, shares, period, data_provider="yfinance", massive_client=None):

		"""Fetch stock data using yfinance with proper error handling"""

		if data_provider in {"auto", "massive"}:
			massive_data = self._fetch_massive_stock_data(ticker, shares, period, massive_client)
			if massive_data:
				return massive_data
			if data_provider == "massive":
				return None

		try:
			yf = _load_yfinance()
			stock = yf.Ticker(ticker)
			hist = stock.history(period=period)

			if hist.empty:
				# Intraday periods (e.g. '1d') return no data outside market hours.
				# Fall back to 5 trading days of daily candles which are always available.
				logging.warning(
					f"No data for {ticker} with period={period}, retrying with period='5d', interval='1d'"
				)
				hist = stock.history(period='5d', interval='1d')

			if hist.empty:
				logging.warning(f"No historical data available for ticker: {ticker}")
				return None

			# Safely access stock info with explicit error handling
			# yfinance may fail when fetching info due to network issues or invalid responses
			try:
				info = stock.info
				# Validate that info is a dictionary
				if not isinstance(info, dict):
					logging.warning(f"Invalid info response for {ticker}: expected dict, got {type(info)}")
					info = {}
			except Exception as e:
				# Catch any error from yfinance when accessing info
				# This includes JSONDecodeError wrapped by yfinance, network errors, etc.
				logging.error(f"Error fetching info for {ticker}: {type(e).__name__}: {e}", exc_info=True)
				info = {}

			regular_price = float(hist['Close'].iloc[-1])
			current_price = regular_price
			quote_source = "historical_close"
			quote_time = hist.index[-1] if len(hist.index) else None
			extended_quote = self._fetch_extended_quote(stock, ticker)
			if extended_quote:
				current_price = extended_quote["price"]
				quote_source = "extended_1m"
				quote_time = extended_quote["timestamp"]

			prev_price = float(hist['Close'].iloc[0])
			change = current_price - prev_price
			change_percent = (change / prev_price) * 100 if prev_price != 0 else 0
			history = self._history_with_latest_close(hist, current_price)

			return {
				'symbol': ticker,
				'name': info.get('shortName', ticker),
				'price': current_price,
				'regular_price': regular_price,
				'change': change,
				'change_percent': change_percent,
				'shares': shares,
				'total_value': current_price * shares,
				'total_change': change * shares,
				'history': history,
				'quote_source': quote_source,
				'quote_time': quote_time,
				'extended_hours': quote_source == "extended_1m",
				'data_provider': "yfinance",
			}
		except Exception as e:
			# Log detailed error information for debugging
			logging.error(f"Failed to fetch data for {ticker}: {type(e).__name__}: {e}", exc_info=True)
			# Re-raise the original exception so that callers can handle or wrap it as needed
			raise

	def _fetch_massive_stock_data(self, ticker, shares, period, massive_client=None):
		if massive_client is None:
			return None
		for massive_symbol in massive_ticker_candidates(ticker):
			try:
				bars = massive_client.fetch_daily_bars(massive_symbol, period=period)
			except MassiveMarketDataError as e:
				logging.warning(f"Massive data unavailable for {ticker}/{massive_symbol}: {type(e).__name__}: {e}")
				continue
			if not bars:
				continue

			points = [(bar.date or str(index), float(bar.close)) for index, bar in enumerate(bars)]
			hist = _SimpleHistory(points)
			if hist.empty:
				continue

			info = {}
			try:
				info = massive_client.fetch_ticker_details(massive_symbol)
			except MassiveMarketDataError as e:
				logging.warning(f"Massive ticker details unavailable for {ticker}: {type(e).__name__}: {e}")

			regular_price = float(hist['Close'].iloc[-1])
			current_price = regular_price
			prev_price = float(hist['Close'].iloc[0])
			change = current_price - prev_price
			change_percent = (change / prev_price) * 100 if prev_price != 0 else 0

			return {
				'symbol': ticker,
				'name': info.get('name') or info.get('ticker') or ticker,
				'price': current_price,
				'regular_price': regular_price,
				'change': change,
				'change_percent': change_percent,
				'shares': shares,
				'total_value': current_price * shares,
				'total_change': change * shares,
				'history': hist,
				'quote_source': "massive_daily",
				'quote_time': hist.index[-1] if len(hist.index) else None,
				'extended_hours': False,
				'data_provider': "massive",
				'massive_symbol': massive_symbol,
			}
		return None

	def _font(self, size, bold=False):
		return get_base_ui_font(int(size), bold=bool(bold))

	@staticmethod
	def _text_width(draw, text, font):
		return text_width(draw, str(text), font)

	@staticmethod
	def _centered_text_y(draw, text, font, center_y):
		bbox = draw.textbbox((0, 0), str(text), font=font)
		return int(round(center_y - (bbox[3] - bbox[1]) / 2 - bbox[1]))

	def _fit_font(self, draw, text, max_width, start_size, bold=False, min_size=10):
		size = int(start_size)
		while size > min_size:
			font = self._font(size, bold)
			if self._text_width(draw, text, font) <= max_width:
				return font
			size -= 1
		return self._font(min_size, bold)

	@staticmethod
	def _money(value, decimals=2):
		return f"${value:,.{decimals}f}"

	@staticmethod
	def _shares(value):
		if abs(value - round(value)) < 0.0001:
			return str(int(round(value)))
		return f"{value:.2f}".rstrip("0").rstrip(".")

	@staticmethod
	def _change_text(value, percent):
		return f"{value:+,.2f} ({percent:+.2f}%)"

	@staticmethod
	def _tracking_window_label(period):
		period_labels = {
			"1d": "TODAY",
			"5d": "LAST WEEK",
			"1mo": "LAST MONTH",
			"3mo": "3 MONTHS",
			"6mo": "6 MONTHS",
			"1y": "1 YEAR",
			"ytd": "YTD",
			"max": "MAX",
		}
		period_key = str(period or "").strip()
		if not period_key:
			return None
		window = period_labels.get(period_key, period_key.upper())
		return f"WINDOW: {window}"

	@staticmethod
	def _symbols_setting(value):
		if value is None:
			return []
		if isinstance(value, (list, tuple, set)):
			candidates = value
		else:
			candidates = str(value).replace("\n", ",").replace(";", ",").split(",")
		return [str(symbol).strip().upper() for symbol in candidates if str(symbol).strip()]

	def _portfolio_meta_from_settings(self, settings):
		settings = settings or {}
		return {
			"cash_balance": self._number_setting(settings, "cash_balance", "cash"),
			"buying_power": self._number_setting(settings, "buying_power"),
			"pending_deposits": self._number_setting(settings, "pending_deposits"),
			"account_value": self._number_setting(settings, "account_value", "portfolio_value", "total_value"),
			"currency": str(settings.get("currency") or "USD").strip().upper() or "USD",
		}

	def _number_setting(self, settings, *keys):
		for key in keys:
			number = self._parse_csv_number(settings.get(key))
			if number is not None and math.isfinite(number):
				return number
		return None

	def _header_brand_from_settings(self, settings, portfolio_meta=None):
		settings = settings or {}
		portfolio_meta = portfolio_meta or {}
		explicit = str(settings.get("header_brand") or settings.get("brokerage_brand") or "").strip().lower()
		if explicit in ("robinhood", "rh"):
			return "robinhood"
		if explicit in ("none", "off", "stocktracker"):
			return None

		csv_path = str(settings.get("portfolio_csv_file") or settings.get("portfolio_csv_path") or "").lower()
		if "robinhood" in csv_path:
			return "robinhood"
		if portfolio_meta.get("buying_power") is not None or portfolio_meta.get("pending_deposits") is not None:
			return "robinhood"
		return None

	def _portfolio_meta_rows(self, portfolio_meta, stock_data):
		cash_balance = portfolio_meta.get("cash_balance")
		if cash_balance is None or abs(cash_balance) < 0.0001:
			return []

		buying_power = portfolio_meta.get("buying_power")
		currency = portfolio_meta.get("currency") or "USD"
		change_text = ""
		if buying_power is not None:
			change_text = f"BP {self._money(buying_power)}"

		return [
			{
				"symbol": "CASH",
				"name": "Cash",
				"price": 1.0,
				"regular_price": 1.0,
				"change": 0.0,
				"change_percent": 0.0,
				"shares": cash_balance,
				"total_value": cash_balance,
				"total_change": 0.0,
				"history": self._constant_value_history(stock_data),
				"quote_source": "account_cash",
				"extended_hours": False,
				"data_provider": "robinhood",
				"is_cash": True,
				"price_text": currency,
				"shares_text": "Cash",
				"change_text": change_text,
				"change_text_color": MUTED,
				"indicator_color": ACCENT_BLUE,
			}
		]

	def _constant_value_history(self, stock_data):
		for data in stock_data:
			history = data.get("history")
			if getattr(history, "empty", True):
				continue
			return _SimpleHistory([(date_key, 1.0) for date_key in history.index])

		today = datetime.now().strftime("%Y-%m-%d")
		return _SimpleHistory([(f"{today}-start", 1.0), (today, 1.0)])

	@staticmethod
	def _threshold_image(img):
		return img.convert("L").point(lambda p: 255 if p >= 128 else 0, mode="1").convert("RGB")

	def _portfolio_values(self, stock_data, account_value_override=None):
		dates = list(stock_data[0]["history"].index)
		values = []
		for date in dates:
			total = 0
			for data in stock_data:
				if date in data["history"].index:
					total += float(data["history"].loc[date, "Close"]) * data["shares"]
			values.append(total)
		if account_value_override is not None and values:
			values[-1] = account_value_override
		return values

	def _record_portfolio_snapshot(self, stock_data, now=None, account_value_override=None):
		now = now or datetime.now()
		snapshot = self._portfolio_snapshot(stock_data, now, account_value_override=account_value_override)
		history_path = self._portfolio_history_path(stock_data)
		history = self._read_portfolio_history(history_path)
		history = self._upsert_portfolio_snapshot(history, snapshot)
		try:
			self._write_portfolio_history(history_path, history)
		except Exception as e:
			logging.warning(f"Could not write stock portfolio history: {type(e).__name__}: {e}")
		return history

	def _portfolio_totals(self, stock_data, account_value_override=None):
		calculated_value = sum(self._finite_float(data.get("total_value")) for data in stock_data)
		total_value = account_value_override if account_value_override is not None else calculated_value
		total_change = sum(self._finite_float(data.get("total_change")) for data in stock_data)
		base_value = total_value - total_change
		total_change_percent = (total_change / base_value) * 100 if base_value else 0
		total_change_percent = self._finite_float(total_change_percent)
		return total_value, total_change, total_change_percent

	def _portfolio_snapshot(self, stock_data, now, account_value_override=None):
		total_value, total_change, total_change_percent = self._portfolio_totals(
			stock_data,
			account_value_override=account_value_override,
		)
		return {
			"date": now.strftime("%Y-%m-%d"),
			"timestamp": now.isoformat(),
			"value": round(float(total_value), 4),
			"change": round(float(total_change), 4),
			"change_percent": round(float(total_change_percent), 4),
		}

	@staticmethod
	def _finite_float(value, default=0.0):
		try:
			number = float(value)
		except (TypeError, ValueError):
			return default
		return number if math.isfinite(number) else default

	def _portfolio_history_path(self, stock_data):
		explicit_file = os.getenv(PORTFOLIO_HISTORY_FILE_ENV)
		if explicit_file:
			explicit_file = os.path.expanduser(explicit_file)
			if not os.path.isabs(explicit_file) and os.getenv("INKYPI_DATA_DIR", "").strip():
				explicit_file = os.path.join(str(self.data_dir()), explicit_file)
			return os.path.abspath(explicit_file)

		history_dir = self.data_dir(
			env_var=PORTFOLIO_HISTORY_DIR_ENV,
			leaf="history",
			legacy_leaf=".stocktracker_history",
		)
		history_key = self._portfolio_history_key(stock_data)
		return os.path.abspath(os.path.join(str(history_dir), f"{history_key}.json"))

	@staticmethod
	def _portfolio_history_key(stock_data):
		holdings = [
			{
				"symbol": str(data.get("symbol", "")).upper(),
				"shares": 0.0 if data.get("is_cash") else round(float(data.get("shares", 0)), 6),
			}
			for data in stock_data
		]
		holdings.sort(key=lambda item: item["symbol"])
		payload = json.dumps(holdings, sort_keys=True, separators=(",", ":"))
		return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]

	def _read_portfolio_history(self, history_path):
		try:
			if not os.path.isfile(history_path):
				return []
			with open(history_path, "r", encoding="utf-8") as history_file:
				raw_history = json.load(history_file)
		except Exception as e:
			logging.warning(f"Could not read stock portfolio history: {type(e).__name__}: {e}")
			return []

		if not isinstance(raw_history, list):
			return []
		return [entry for entry in (self._normalize_portfolio_history_entry(item) for item in raw_history) if entry]

	@staticmethod
	def _normalize_portfolio_history_entry(item):
		if not isinstance(item, dict):
			return None
		date_text = str(item.get("date") or "").strip()
		if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_text):
			return None
		try:
			value = float(item.get("value"))
		except (TypeError, ValueError):
			return None
		if not math.isfinite(value):
			return None
		normalized = {
			"date": date_text,
			"timestamp": str(item.get("timestamp") or date_text),
			"value": value,
		}
		for key in ("change", "change_percent"):
			try:
				number = float(item.get(key))
				if math.isfinite(number):
					normalized[key] = number
			except (TypeError, ValueError):
				pass
		return normalized

	def _upsert_portfolio_snapshot(self, history, snapshot):
		by_date = {}
		for item in history:
			normalized = self._normalize_portfolio_history_entry(item)
			if normalized:
				by_date[normalized["date"]] = normalized
		by_date[snapshot["date"]] = snapshot
		return [
			by_date[date_key]
			for date_key in sorted(by_date.keys())[-PORTFOLIO_HISTORY_MAX_DAYS:]
		]

	@staticmethod
	def _write_portfolio_history(history_path, history):
		os.makedirs(os.path.dirname(history_path), exist_ok=True)
		temp_path = f"{history_path}.tmp"
		with open(temp_path, "w", encoding="utf-8") as history_file:
			json.dump(history, history_file, ensure_ascii=True, indent=2, allow_nan=False)
		os.replace(temp_path, history_path)

	@staticmethod
	def _blend(fg, bg, amount):
		amount = min(max(amount, 0.0), 1.0)
		return tuple(int(round(f * amount + b * (1.0 - amount))) for f, b in zip(fg, bg))

	@staticmethod
	def _change_color(percent):
		colors = _active_stock_colors()
		if percent > 0.005:
			return colors["malachite"]
		if percent < -0.005:
			return colors["cinnabar"]
		return colors["muted"]

	def _draw_box(self, draw, box, title=None, accent=None, fill=None, canvas=None):
		colors = _active_stock_colors()
		accent = colors["accent_blue"] if accent is None else accent
		fill = colors["panel"] if fill is None else fill
		left, top, right, bottom = [int(v) for v in box]
		draw.rectangle((left + 3, top + 4, right + 3, bottom + 4), fill=colors["accent_orange"])
		draw.rectangle((left, top, right, bottom), fill=fill)
		draw.rectangle((left, top, right, bottom), outline=colors["border"], width=2)
		draw.rectangle((left, top, right, top + 6), fill=accent)
		if title:
			if canvas is not None and self._draw_section_wordmark(canvas, title, left + 10, top + 8):
				return
			draw.text((left + 12, top + 12), title, fill=colors["ink"], font=self._font(16, True))

	def _draw_summary(self, img, draw, box, stock_data, account_value_override=None, updated_at=None):
		colors = _active_stock_colors()
		left, top, right, bottom = box
		self._draw_box(
			draw,
			box,
			"PORTFOLIO",
			accent=colors["accent_gold"],
			fill=colors["panel_gold"],
			canvas=img,
		)
		total_value, total_change, total_change_percent = self._portfolio_totals(
			stock_data,
			account_value_override=account_value_override,
		)
		change_color = self._change_color(total_change_percent)

		value_text = self._money(total_value)
		value_font = self._fit_font(draw, value_text, right - left - 28, 36, True, 20)
		draw.text((left + 14, top + 44), value_text, fill=colors["ink"], font=value_font)

		change_text = self._change_text(total_change, total_change_percent)
		change_font = self._fit_font(draw, change_text, right - left - 32, 18, True, 12)
		pill = (left + 12, top + 96, right - 12, top + 124)
		draw.rounded_rectangle(
			pill,
			radius=8,
			fill=self._blend(change_color, colors["panel_gold"], 0.12),
			outline=change_color,
			width=2,
		)
		draw.text((left + 24, top + 100), change_text, fill=change_color, font=change_font)
		updated_at = updated_at or datetime.now()
		draw.text(
			(left + 14, bottom - 18),
			updated_at.strftime("Updated %H:%M"),
			fill=colors["muted"],
			font=self._font(10),
		)

	def _draw_sparkline(self, img, draw, box, values, history_points=None):
		colors = _active_stock_colors()
		left, top, right, bottom = box
		self._draw_box(
			draw,
			box,
			"PORTFOLIO TREND",
			accent=colors["accent_blue"],
			fill=colors["panel_blue"],
			canvas=img,
		)
		plot = (left + 14, top + 42, right - 14, bottom - 16)
		chart_bg = self._blend(colors["panel"], colors["paper"], 0.78)
		draw.rounded_rectangle(
			plot,
			radius=4,
			fill=chart_bg,
			outline=self._blend(colors["grid"], colors["panel_blue"], 0.65),
			width=1,
		)
		for i in range(1, 5):
			y = plot[1] + (plot[3] - plot[1]) * i / 5
			draw.line(
				(plot[0] + 8, y, plot[2] - 8, y),
				fill=self._blend(colors["grid"], chart_bg, 0.38),
				width=1,
			)
		for i in range(1, 5):
			x = plot[0] + (plot[2] - plot[0]) * i / 5
			draw.line(
				(x, plot[1] + 4, x, plot[3] - 4),
				fill=self._blend(colors["grid"], chart_bg, 0.18),
				width=1,
			)
		history_points = [
			point
			for point in (self._normalize_portfolio_history_entry(item) for item in (history_points or []))
			if point
		]
		values = [float(value) for value in values]
		if len(values) < 2:
			return

		raw_vmin = min(values)
		raw_vmax = max(values)
		vmin, vmax = self._chart_value_bounds(values)
		line_color = self._change_color(values[-1] - values[0])
		if line_color == colors["muted"]:
			line_color = colors["accent_blue"]

		points = self._plot_series_points(plot, values, vmin, vmax)
		curve_points = self._smooth_curve_points(points)

		area = [(plot[0], plot[3])] + curve_points + [(plot[2], plot[3])]
		if len(curve_points) >= 2:
			draw.polygon(area, fill=self._blend(line_color, chart_bg, 0.11))
			draw.line(curve_points, fill=self._blend(line_color, chart_bg, 0.30), width=5)
			draw.line(curve_points, fill=line_color, width=3)
			draw.ellipse((points[0][0] - 3, points[0][1] - 3, points[0][0] + 3, points[0][1] + 3), fill=chart_bg, outline=line_color, width=2)
		if len(points) >= 2:
			self._draw_latest_value_marker(draw, points[-1], line_color)
		self._draw_history_markers(draw, history_points, points)

		self._draw_chart_label(
			draw,
			(plot[0] + 6, plot[1] + 6),
			self._money(raw_vmax, 0),
			colors["ink"],
			chart_bg,
		)
		self._draw_chart_label(
			draw,
			(plot[0] + 6, plot[3] - 20),
			self._money(raw_vmin, 0),
			colors["muted"],
			chart_bg,
		)

	@staticmethod
	def _chart_value_bounds(values):
		vmin = min(values)
		vmax = max(values)
		span = vmax - vmin
		if abs(span) < 0.0001:
			padding = max(abs(vmax) * 0.01, 1.0)
		else:
			padding = span * 0.12
		return vmin - padding, vmax + padding

	@staticmethod
	def _smooth_curve_points(points, subdivisions=8):
		if len(points) < 3:
			return points
		curve = [points[0]]
		last_index = len(points) - 1
		for idx in range(last_index):
			p0 = points[max(idx - 1, 0)]
			p1 = points[idx]
			p2 = points[idx + 1]
			p3 = points[min(idx + 2, last_index)]
			for step in range(1, subdivisions + 1):
				t = step / subdivisions
				t2 = t * t
				t3 = t2 * t
				x = 0.5 * (
					(2 * p1[0])
					+ (-p0[0] + p2[0]) * t
					+ (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
					+ (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
				)
				y = 0.5 * (
					(2 * p1[1])
					+ (-p0[1] + p2[1]) * t
					+ (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
					+ (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
				)
				curve.append((int(round(x)), int(round(y))))
		return curve

	def _draw_chart_label(self, draw, position, text, fill, chart_bg):
		colors = _active_stock_colors()
		x, y = [int(v) for v in position]
		font = self._font(11, True)
		width = self._text_width(draw, text, font)
		draw.rounded_rectangle(
			(x - 2, y - 1, x + width + 6, y + 14),
			radius=3,
			fill=self._blend(chart_bg, colors["white"], 0.72),
			outline=self._blend(colors["grid"], chart_bg, 0.45),
			width=1,
		)
		draw.text((x + 2, y), text, fill=fill, font=font)

	def _draw_latest_value_marker(self, draw, point, color=None):
		colors = _active_stock_colors()
		color = colors["accent_blue"] if color is None else color
		draw.ellipse(
			(point[0] - 5, point[1] - 5, point[0] + 5, point[1] + 5),
			fill=colors["white"],
			outline=colors["ink"],
			width=1,
		)
		draw.ellipse((point[0] - 3, point[1] - 3, point[0] + 3, point[1] + 3), fill=color)

	@staticmethod
	def _plot_series_points(plot, values, vmin, vmax):
		points = []
		for idx, value in enumerate(values):
			x = plot[0] + (plot[2] - plot[0]) * idx / max(len(values) - 1, 1)
			y = plot[3] - (plot[3] - plot[1]) * (float(value) - vmin) / (vmax - vmin)
			points.append((int(round(x)), int(round(y))))
		return points

	@staticmethod
	def _sample_curve_points(curve_points, count):
		if not curve_points or count <= 0:
			return []
		if count == 1:
			return [curve_points[-1]]
		max_index = len(curve_points) - 1
		sampled_points = []
		for idx in range(count):
			position = max_index * idx / (count - 1)
			left_index = int(math.floor(position))
			right_index = min(left_index + 1, max_index)
			ratio = position - left_index
			left_point = curve_points[left_index]
			right_point = curve_points[right_index]
			x = left_point[0] + (right_point[0] - left_point[0]) * ratio
			y = left_point[1] + (right_point[1] - left_point[1]) * ratio
			sampled_points.append((int(round(x)), int(round(y))))
		return sampled_points

	def _history_marker_points(self, curve_points, history_points):
		colors = _active_stock_colors()
		if not history_points:
			return []
		ordered_points = sorted(history_points, key=lambda point: str(point.get("timestamp") or point["date"]))
		coords = self._sample_curve_points(curve_points, len(ordered_points))
		previous_value = None
		marker_points = []
		for idx, point in enumerate(ordered_points):
			value = float(point["value"])
			if previous_value is None:
				fill = colors["accent_orange"]
			else:
				fill = colors["malachite"] if value >= previous_value else colors["cinnabar"]
			previous_value = value
			marker_points.append({"point": coords[idx], "fill": fill, "history": point})
		return marker_points

	def _draw_history_markers(self, draw, history_points, curve_points):
		colors = _active_stock_colors()
		marker_points = self._history_marker_points(curve_points, history_points)
		if not marker_points:
			return
		radius = 4 if len(marker_points) <= 36 else 3
		for marker in marker_points:
			x, y = marker["point"]
			fill = marker["fill"]
			draw.ellipse(
				(x - radius - 1, y - radius - 1, x + radius + 1, y + radius + 1),
				fill=colors["ink"],
			)
			draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)

	def _ordered_holdings(self, stock_data, pin_symbols=None, sink_symbols=None):
		pin_rank = {
			symbol: index
			for index, symbol in enumerate(self._symbols_setting(pin_symbols))
		}
		sink_set = set(self._symbols_setting(sink_symbols))

		def sort_key(item):
			symbol = str(item.get("symbol") or "").upper()
			value_rank = -self._finite_float(item.get("total_value"))
			if self._is_cash_holding(item):
				return (3, 0, value_rank, symbol)
			if symbol in pin_rank:
				return (0, pin_rank[symbol], value_rank, symbol)
			if symbol in sink_set:
				return (2, 0, value_rank, symbol)
			return (1, 0, value_rank, symbol)

		return sorted(stock_data, key=sort_key)

	@staticmethod
	def _is_cash_holding(item):
		symbol = str(item.get("symbol") or "").strip().lower()
		return bool(item.get("is_cash")) or symbol in CASH_SYMBOLS

	@staticmethod
	def _holding_logo_asset(symbol):
		return LOGO_ASSET_BY_SYMBOL.get(str(symbol or "").strip().upper())

	@staticmethod
	def _trim_transparent_border(image):
		if image.mode != "RGBA":
			image = image.convert("RGBA")
		alpha = image.getchannel("A")
		threshold_bbox = alpha.point(lambda value: 255 if value > 8 else 0).getbbox()
		return image.crop(threshold_bbox or alpha.getbbox()) if alpha.getbbox() else image

	def _draw_section_wordmark(self, canvas, title, x, y):
		if _active_stock_colors()["mode"] == "night":
			return False
		config = SECTION_WORDMARK_IMAGES.get(title)
		if not config:
			return False
		source = self._load_section_wordmark(title)
		if source is None:
			return False

		try:
			_, size = config
			target_w, target_h = [int(value) for value in size]
			source = self._trim_transparent_border(source.copy())
			resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
			art = ImageOps.contain(source, (target_w, target_h), method=resample)
			layer = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
			layer.alpha_composite(art, ((target_w - art.width) // 2, (target_h - art.height) // 2))
			canvas.paste(layer.convert("RGB"), (int(x), int(y)), layer.getchannel("A"))
			return True
		except Exception as e:
			logging.warning(f"Could not render stock section wordmark {title}: {type(e).__name__}: {e}")
			return False

	def _section_wordmark_file(self, title):
		config = SECTION_WORDMARK_IMAGES.get(title)
		if not config:
			return None
		asset_name, _ = config
		return os.path.join(os.path.dirname(__file__), "assets", "subtitles", asset_name)

	def _load_section_wordmark(self, title):
		path = self._section_wordmark_file(title)
		if not path or not os.path.exists(path):
			return None
		cache_key = (title, path)
		if cache_key in self._section_wordmark_cache:
			return self._section_wordmark_cache[cache_key]
		try:
			wordmark = Image.open(path).convert("RGBA")
		except Exception as e:
			logging.warning(f"Could not load stock section wordmark {title}: {type(e).__name__}: {e}")
			return None
		self._section_wordmark_cache[cache_key] = wordmark
		return wordmark

	def _holding_logo_image(self, symbol, size):
		asset_name = self._holding_logo_asset(symbol)
		if not asset_name:
			return None
		cache_key = (asset_name, int(size))
		if cache_key in self._holding_logo_cache:
			return self._holding_logo_cache[cache_key]

		path = os.path.join(os.path.dirname(__file__), "assets", "logos", asset_name)
		if not os.path.exists(path):
			return None
		try:
			logo = self._trim_transparent_border(Image.open(path).convert("RGBA"))
			try:
				resample = Image.Resampling.LANCZOS
			except AttributeError:
				resample = Image.LANCZOS
			logo.thumbnail((size, size), resample)
			canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
			canvas.alpha_composite(logo, ((size - logo.width) // 2, (size - logo.height) // 2))
		except Exception as e:
			logging.warning(f"Could not load holding logo for {symbol}: {type(e).__name__}: {e}")
			return None

		self._holding_logo_cache[cache_key] = canvas
		return canvas

	def _header_logo_image(self, brand, max_width, max_height):
		if brand != "robinhood":
			return None
		cache_key = (brand, int(max_width), int(max_height))
		if cache_key in self._header_logo_cache:
			return self._header_logo_cache[cache_key]

		path = os.path.join(os.path.dirname(__file__), "assets", "robinhood_logo.png")
		if not os.path.exists(path):
			return None
		try:
			logo = Image.open(path).convert("RGBA")
			try:
				resample = Image.Resampling.LANCZOS
			except AttributeError:
				resample = Image.LANCZOS
			logo.thumbnail((max_width, max_height), resample)
		except Exception as e:
			logging.warning(f"Could not load Robinhood header logo: {type(e).__name__}: {e}")
			return None

		self._header_logo_cache[cache_key] = logo
		return logo

	def _draw_header(self, img, draw, width, stock_data, header_brand=None):
		colors = _active_stock_colors()
		draw.rectangle((0, 0, width, 54), fill=colors["paper"])
		header_logo = self._header_logo_image(header_brand, 214, 34)
		if header_logo:
			img.paste(header_logo, (24, 9), header_logo)
		else:
			draw.text((24, 12), "STOCK TRACKER", fill=colors["ink"], font=self._font(24, True))

		source_label = self._source_label(stock_data)
		draw.text(
			(width - 24, 18),
			f"{source_label}  |  COLOR E-PAPER MODE",
			fill=colors["muted"],
			font=self._font(13, True),
			anchor="ra",
		)
		draw.line((24, 46, width - 24, 46), fill=colors["accent_gold"], width=3)

	@staticmethod
	def _ticker_theme_key(theme=None, theme_context=None):
		requested = str(theme or "auto").strip().lower()
		if requested in ("day", "light"):
			return "day"
		if requested in ("night", "dark"):
			return "night"
		if isinstance(theme_context, Mapping):
			return "night" if str(theme_context.get("mode") or "").strip().lower() == "night" else "day"
		return _active_stock_colors()["mode"]

	def _ticker_palette(self, theme=None, theme_context=None):
		mode = self._ticker_theme_key(theme, theme_context)
		if (
			not isinstance(theme_context, Mapping)
			and _ACTIVE_STOCK_COLORS.get() is _LEGACY_STOCK_COLORS
		):
			return TICKER_PALETTES[mode]
		colors = _active_stock_colors()
		return {
			"fill": colors["paper"] if mode == colors["mode"] else TICKER_PALETTES[mode]["fill"],
			"border": colors["accent_gold"],
			"top_line": colors["accent_orange"],
			"separator": colors["grid"],
			"symbol": colors["ink"],
			"price": colors["accent_blue"],
			"value": colors["muted"],
		}

	def _draw_hidden_holdings_ticker(
		self,
		img,
		draw,
		box,
		hidden_rows,
		ticker_theme=None,
		theme_context=None,
	):
		if not hidden_rows:
			return

		left, top, right, bottom = box
		palette = self._ticker_palette(ticker_theme, theme_context)
		ticker_top = max(top, bottom - 36)
		ticker_bottom = bottom - 7
		if ticker_bottom - ticker_top < 24:
			return

		visible_rows = list(hidden_rows[:6])
		strip = (left + 12, ticker_top, right - 12, ticker_bottom)
		draw.rounded_rectangle(strip, radius=4, fill=palette["fill"], outline=palette["border"], width=2)
		draw.line((strip[0] + 2, strip[1] + 3, strip[2] - 2, strip[1] + 3), fill=palette["top_line"], width=1)

		segment_count = len(visible_rows)
		segment_width = max(1, (strip[2] - strip[0]) // segment_count)
		meta_font = self._font(7)
		price_font = self._font(8, True)
		logo_size = 16

		for idx, data in enumerate(visible_rows):
			seg_left = strip[0] + idx * segment_width
			seg_right = strip[2] if idx == segment_count - 1 else seg_left + segment_width
			if idx:
				draw.line((seg_left, strip[1] + 4, seg_left, strip[3] - 4), fill=palette["separator"], width=1)

			logo_x = seg_left + 5
			logo = self._holding_logo_image(data.get("symbol"), logo_size)
			if logo:
				logo_y = int(round((strip[1] + strip[3] - logo.height) / 2))
				img.paste(logo, (logo_x, logo_y), logo)

			text_left = seg_left + 25
			text_right = seg_right - 5
			if text_right <= text_left:
				continue
			symbol = str(data.get("symbol") or "")[:5]
			price = data.get("price_text", self._money(float(data.get("price") or 0)))
			change = data.get("change_text", f"{float(data.get('change_percent') or 0):+.2f}%")
			change_color = data.get("change_text_color", self._change_color(float(data.get("change_percent") or 0)))
			value = float(data.get("total_value") or 0)
			abs_value = abs(value)
			if abs_value >= 1000000:
				value_text = f"${value / 1000000:.1f}M"
			elif abs_value >= 1000:
				value_text = f"${value / 1000:.1f}K"
			else:
				value_text = f"${value:.0f}"
			price_width = self._text_width(draw, price, price_font)
			max_symbol_width = max(22, text_right - text_left - price_width - 3)
			symbol_font = self._fit_font(draw, symbol, max_symbol_width, 10, True, min_size=8)
			draw.text((text_left, strip[1] + 6), symbol, fill=palette["symbol"], font=symbol_font)
			draw.text((text_right, strip[1] + 6), price, fill=palette["price"], font=price_font, anchor="ra")
			draw.text((text_left, strip[1] + 18), change, fill=change_color, font=meta_font)
			if self._text_width(draw, change, meta_font) + self._text_width(draw, value_text, meta_font) + 6 < text_right - text_left:
				draw.text((text_right, strip[1] + 18), value_text, fill=palette["value"], font=meta_font, anchor="ra")

	def _draw_holdings(
		self,
		img,
		draw,
		box,
		stock_data,
		tracking_window_label=None,
		holdings_pin_symbols=None,
		holdings_sink_symbols=None,
		ticker_theme=None,
		theme_context=None,
	):
		colors = _active_stock_colors()
		left, top, right, bottom = box
		self._draw_box(
			draw,
			box,
			"HOLDINGS",
			accent=colors["malachite"],
			fill=colors["panel"],
			canvas=img,
		)
		if tracking_window_label:
			draw.text(
				(right - 14, top + 14),
				tracking_window_label,
				fill=colors["muted"],
				font=self._font(12, True),
				anchor="ra",
			)
		header_font = self._font(12, True)
		row_font = self._font(13)
		symbol_font = self._font(15, True)
		y = top + 40
		row_slot_height = 20
		logo_size = 18
		logo_x = left + 20
		cols = {
			"symbol": left + 56,
			"price": left + 166,
			"shares": left + 292,
			"value": left + 412,
			"change": left + 604,
		}
		for label, x in [("SYMBOL", cols["symbol"]), ("PRICE", cols["price"]), ("SHARES", cols["shares"]), ("VALUE", cols["value"]), ("CHANGE", cols["change"])]:
			draw.text((x, y), label, fill=colors["muted"], font=header_font)
		y += 22
		draw.line((left + 12, y, right - 12, y), fill=colors["grid"], width=1)
		y += 6

		max_rows = min(len(stock_data), 6)
		displayed_rows = 0
		display_rows = self._ordered_holdings(stock_data, holdings_pin_symbols, holdings_sink_symbols)
		for idx, data in enumerate(display_rows[:max_rows]):
			if y + row_slot_height > bottom - 38:
				break
			row_bg = colors["row_colors"][idx % len(colors["row_colors"])]
			row_top = y - 2
			row_bottom = row_top + row_slot_height
			row_center_y = row_top + row_slot_height / 2
			draw.rounded_rectangle((left + 10, row_top - 1, right - 10, row_bottom), radius=5, fill=row_bg)
			change_color = data.get("change_text_color", self._change_color(data["change_percent"]))
			indicator_color = data.get("indicator_color", change_color)
			draw.rounded_rectangle((left + 5, row_top, left + 10, row_bottom - 1), radius=3, fill=indicator_color)
			logo = self._holding_logo_image(data.get("symbol"), logo_size)
			if logo:
				logo_y = int(round(row_center_y - logo.height / 2))
				img.paste(logo, (logo_x, logo_y), logo)
			row_items = [
				(cols["symbol"], data["symbol"], symbol_font, colors["ink"]),
				(cols["price"], data.get("price_text", self._money(data["price"])), row_font, colors["ink"]),
				(cols["shares"], data.get("shares_text", self._shares(data["shares"])), row_font, colors["ink"]),
				(cols["value"], data.get("value_text", self._money(data["total_value"])), row_font, colors["ink"]),
				(cols["change"], data.get("change_text", f"{data['change_percent']:+.2f}%"), row_font, change_color),
			]
			for x, text, font, fill in row_items:
				text_y = self._centered_text_y(draw, text, font, row_center_y)
				draw.text((x, text_y), text, fill=fill, font=font)
			y += row_slot_height
			draw.line((left + 12, y, right - 12, y), fill=colors["grid"], width=1)
			displayed_rows += 1

		hidden_rows = display_rows[displayed_rows:]
		if hidden_rows:
			self._draw_hidden_holdings_ticker(
				img,
				draw,
				box,
				hidden_rows,
				ticker_theme=ticker_theme,
				theme_context=theme_context,
			)

	def _create_dashboard(
		self,
		stock_data,
		dimensions,
		history_points=None,
		tracking_window_label=None,
		holdings_pin_symbols=None,
		holdings_sink_symbols=None,
		account_value_override=None,
		header_brand=None,
		ticker_theme=None,
		theme_context=None,
		updated_at=None,
	):
		"""Create a color dashboard that preserves the original stock layout."""
		colors = _stock_render_colors(theme_context)
		token = _ACTIVE_STOCK_COLORS.set(colors)
		try:
			width, height = dimensions
			img = Image.new("RGB", (width, height), colors["paper"])
			draw = ImageDraw.Draw(img)

			self._draw_header(img, draw, width, stock_data, header_brand=header_brand)

			self._draw_summary(
				img,
				draw,
				(24, 60, 284, 204),
				stock_data,
				account_value_override=account_value_override,
				updated_at=updated_at,
			)
			self._draw_sparkline(
				img,
				draw,
				(304, 60, width - 24, 204),
				self._portfolio_values(stock_data, account_value_override=account_value_override),
				history_points,
			)
			self._draw_holdings(
				img,
				draw,
				(24, 224, width - 24, height - 24),
				stock_data,
				tracking_window_label,
				holdings_pin_symbols,
				holdings_sink_symbols,
				ticker_theme,
				theme_context,
			)

			return img
		finally:
			_ACTIVE_STOCK_COLORS.reset(token)

	@staticmethod
	def _source_label(stock_data):
		if any(data.get("extended_hours") for data in stock_data):
			return "Yahoo realtime + extended hours"
		providers = {str(data.get("data_provider") or "yfinance") for data in stock_data}
		if "robinhood" in providers:
			providers.discard("robinhood")
			if not providers:
				return "Robinhood account data"
			return "Yahoo + Robinhood account data"
		if providers == {"massive"}:
			return "Massive market data"
		if "massive" in providers:
			return "Massive + Yahoo fallback"
		return "Yahoo Finance data"

	def generate_settings_template(self):

		"""Generate template variables for settings form"""

		template_params = super().generate_settings_template()

		template_params['period_options'] = [
			('1d', '1 Day'),
			('5d', '5 Days'),
			('1mo', '1 Month'),
			('3mo', '3 Months'),
			('6mo', '6 Months'),
			('1y', '1 Year'),
			('ytd', 'Year to Date')
		]
		template_params['data_provider_options'] = [
			('auto', 'Auto: Massive first, yfinance fallback'),
			('massive', 'Massive only'),
			('yfinance', 'yfinance only'),
		]

		# Ensure settings dict is included
		if 'settings' not in template_params:
			template_params['settings'] = {}

		return template_params
