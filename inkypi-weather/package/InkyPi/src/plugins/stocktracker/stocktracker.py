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
from plugins.base_plugin.refresh_on_display_presentation import RefreshOnDisplayPresentationMixin
from plugins.base_plugin.render_provenance import SourceProvenance, attach_source_provenance
from plugins.context_cache import write_context

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

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from utils.fonts import load_font
from utils.app_utils import get_base_ui_font, get_font
from utils.image_utils import text_width
from utils.massive_market_data import (
	MassiveMarketData,
	MassiveMarketDataError,
	load_massive_api_key,
	massive_ticker_candidates,
)
from utils.robinhood_mcp import RobinhoodMCPClient
from utils.theme_utils import get_theme_palette
from plugins.stocktracker import trend_chart
import csv
import hashlib
import json
import logging
import math
import re
from functools import lru_cache
from datetime import datetime, timedelta

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
# The portfolio card, in a pixel-cut gold frame SUMMARY_FRAME px deep: between SUMMARY_CHART rows
# under its top, the total (SUMMARY_FIGURE_SIZE type at most, its digits' middle
# SUMMARY_FIGURE_FOOT rows above the chart's foot) by the generated gold dollar coin (COIN_ICON_PATH, SUMMARY_COIN px; drawn in COIN_GOLD rimmed
# in COIN_RIM without the file) in today's colour: deep green up and deep red down on the day
# page (GAIN_INK, LOSS_INK; the night page's own green and red), as is today's change under it.
# Behind it the account's value over the window shows only as a trend, out to the frame on the
# left, right and foot: a hill on a fine grid (SUMMARY_GRID px) resting on the chart's foot, its
# low SUMMARY_HILL_GAP above it, tinted in today's colour and blurred by SUMMARY_BLUR, its top
# SUMMARY_FADE rows fading into the card, a sparkle on today's value.
SUMMARY_FRAME, SUMMARY_CHART, SUMMARY_GRID, SUMMARY_COIN, SUMMARY_FIGURE_SIZE = 7, (33, 92), 12, 40, 38
SUMMARY_FIGURE_FOOT, SUMMARY_HILL_GAP, SUMMARY_BLUR, SUMMARY_FADE = 17, 6, 1.2, 10
COIN_ICON_PATH = os.path.join(os.path.dirname(__file__), "assets", "coin_dollar.png")
COIN_GOLD, COIN_RIM = (232, 186, 62), (8, 38, 22)
GAIN_INK, LOSS_INK = (0, 92, 50), (176, 32, 30)
ACCENT_ORANGE = (245, 122, 38)  # 100Y-50R PANTONE ORANGE 021 family
CINNABAR = (222, 45, 38)  # 100Y-100R PANTONE RED 032 family
MALACHITE = (0, 152, 82)  # 100Y-100B PANTONE 354 family
CHART_MARKER_GREEN = (0, 92, 50)  # 100Y-100B-50R PANTONE 350 family
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
# The holdings' marks: each scaled to the same visual area (LOGO_AREA_SHARE of the slot's
# height, squared) in a slot LOGO_SLOT_RATIO times as wide as tall (the holdings rows' slot is
# HOLDING_LOGO_SLOT_W); at night, parts darker than LOGO_NIGHT_INK_BELOW turn to the ink.
LOGO_AREA_SHARE, LOGO_SLOT_RATIO, HOLDING_LOGO_SLOT_W, LOGO_NIGHT_INK_BELOW = 0.86, 1.2, 22, 56
LOGO_ASSET_BY_SYMBOL = {
	"AAPL": "aapl.png",
	"AMZN": "amzn.png",
	"CASH": "cash.png",
	"FIG": "fig.png",
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
# Saved snapshots are taken after the close, so on a shared day they match the
# official close-based value closely unless deposits or holdings changed.
LOCAL_HISTORY_SEAM_TOLERANCE = 0.01
PORTFOLIO_HISTORY_FILE_ENV = "INKYPI_STOCKTRACKER_HISTORY_FILE"
PORTFOLIO_HISTORY_MAX_DAYS = 180
SOURCE_CACHE_SCHEMA_VERSION = "stocktracker-source-v2"
SOURCE_CACHE_LEAF = "source"
WINDOW_SHORT_LABELS = {
	"1d": "1D",
	"5d": "1W",
	"1mo": "1M",
	"3mo": "3M",
	"6mo": "6M",
	"1y": "1Y",
	"ytd": "YTD",
	"max": "MAX",
}
# Dashboard geometry for the 800x480 panel; cards keep a 16px gutter.
DASHBOARD_MARGIN = 16
HEADER_RULE_Y = 38
# Dollar bills on the wind across the header (GPT Image 2.5; tools/build_flying_header_art.py).
HEADER_MONEY_PATH = os.path.join(os.path.dirname(__file__), "assets", "header_flying_bills.png")
# Taking the art apart: a stroke of at least HEADER_BILL_AREA pixels is a bill, of at least
# HEADER_COIN_AREA a coin; smaller marks (a coin's motion lines) go with the nearest piece, and
# one cut off at the art's edge is left out. Pieces fly at least HEADER_MONEY_MIN_GAP apart.
HEADER_BILL_AREA, HEADER_COIN_AREA, HEADER_MONEY_MIN_GAP = 2500, 300, 4
_HEADER_MONEY_PIECES = {}


@lru_cache(maxsize=4)
def _coin_icon(size):
	"""The generated gold dollar coin (COIN_ICON_PATH) trimmed and ``size`` px on its longer side;
	None without the file."""
	try:
		with Image.open(COIN_ICON_PATH) as source:
			art = source.convert("RGBA")
	except (OSError, ValueError):
		return None
	bounds = art.getchannel("A").getbbox()
	if bounds:
		art = art.crop(bounds)
	art.thumbnail((size, size), Image.Resampling.LANCZOS)
	return art


def _header_money_pieces():
	"""([(bill, top)], [(coin, top)], art height) from the header art, bills and coins left to
	right, each cut to its own strokes; None without the art. Worked out once per process."""
	if "pieces" in _HEADER_MONEY_PIECES:
		return _HEADER_MONEY_PIECES["pieces"]
	pieces = None
	try:
		with Image.open(HEADER_MONEY_PATH) as source:
			art = source.convert("RGBA")
	except (OSError, ValueError) as e:
		logging.warning(f"Could not load the header bills: {type(e).__name__}")
	else:
		# Number each connected stroke by flooding it with its own grey level.
		labels = art.getchannel("A").point(lambda value: 255 if value > 24 else 0)
		strokes = []
		while len(strokes) < 250:
			bounds = labels.point(lambda value: 255 if value == 255 else 0).getbbox()
			if not bounds:
				break
			seed = next(x for x in range(bounds[0], bounds[2]) if labels.getpixel((x, bounds[1])) == 255)
			level = len(strokes) + 1
			ImageDraw.floodfill(labels, (seed, bounds[1]), level)
			mask = labels.point(lambda value, level=level: 255 if value == level else 0)
			strokes.append((mask.histogram()[255], mask.getbbox(), mask))
		big = [stroke for stroke in strokes if stroke[0] >= HEADER_COIN_AREA]
		groups = {id(stroke): [stroke] for stroke in big}
		for stroke in strokes:
			area, (x0, _y0, x1, _y1), _mask = stroke
			if area >= HEADER_COIN_AREA or x0 == 0 or x1 == art.width:
				continue
			middle = (x0 + x1) / 2
			nearest = min(big, key=lambda other: abs((other[1][0] + other[1][2]) / 2 - middle), default=None)
			if nearest is not None:
				groups[id(nearest)].append(stroke)
		bills, coins = [], []
		for stroke in sorted(big, key=lambda stroke: stroke[1][0]):
			mask = Image.new("L", art.size, 0)
			for _area, _box, part in groups[id(stroke)]:
				mask.paste(255, (0, 0), part)
			x0, y0, x1, y1 = mask.getbbox()
			piece = art.crop((x0, y0, x1, y1))
			piece.putalpha(Image.composite(piece.getchannel("A"), Image.new("L", piece.size, 0), mask.crop((x0, y0, x1, y1))))
			(bills if stroke[0] >= HEADER_BILL_AREA else coins).append((piece, y0))
		if bills and coins:
			pieces = (bills, coins, art.height)
	_HEADER_MONEY_PIECES["pieces"] = pieces
	return pieces
TOP_CARDS = (48, 186)
SUMMARY_CARD_WIDTH = 300
HOLDINGS_TOP = 198
HOLDING_ROW_MIN_HEIGHT = 15
HOLDING_ROW_MAX_HEIGHT = 24
# Holdings table columns as fractions of the inner table width.
HOLDING_COLUMNS = {
	"price": 0.205,
	"shares": 0.29,
	"value": 0.43,
	"weight_bar": (0.448, 0.528),
	"weight": 0.597,
	"day": 0.70,
	"day_pl": 0.81,
	"trend": (0.835, 0.915),
	"window": 1.0,
}
# What kind of investment each holding is; the table groups its rows by kind. Unlisted symbols
# are "other", cash is "cash".
HOLDING_CATEGORY_BY_SYMBOL = {
	"AAPL": "tech", "AMZN": "tech", "FIG": "tech", "GOOG": "tech", "GOOGL": "tech", "MSFT": "tech", "NVDA": "tech",
	"NTDOY": "games", "TTWO": "games",
	"SPCX": "mobility", "TSLA": "mobility",
	"SPY": "index", "VTI": "index", "VXUS": "index",
	"VGIT": "bonds",
}
# Each kind: its name on the table's left rail and its colour (a key of the active palette).
HOLDING_CATEGORIES = {
	"tech": ("科技", "accent_blue"),
	"games": ("游戏", "cinnabar"),
	"mobility": ("出行", "accent_orange"),
	"index": ("指数", "malachite"),
	"bonds": ("债券", "accent_gold"),
	"other": ("其他", "grid"),
	"cash": ("现金", "muted"),
}
# The rail left of the rows: a bar in the kind's colour, its name, share of the portfolio and day.
HOLDING_RAIL_W = 46

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
	if mode == "day":
		return _LEGACY_STOCK_COLORS
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
	}


def _active_stock_colors():
	return _ACTIVE_STOCK_COLORS.get() or _LEGACY_STOCK_COLORS

_yf = None


def _load_yfinance():
	global _yf
	if _yf is None:
		import yfinance as yf
		_yf = yf
	return _yf


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


class StockTracker(RefreshOnDisplayPresentationMixin, BasePlugin):

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

		data_provider = self._data_provider(settings)
		portfolio_meta = self._portfolio_meta_from_settings(settings)
		if data_provider == "robinhood_mcp":
			period = settings.get("period", "1mo")
			account_hash = str(settings.get("robinhood_account_hash") or "").strip().lower()
			if not account_hash:
				raise RuntimeError("Robinhood official MCP requires robinhood_account_hash")
			holdings = [(f"ROBINHOOD-MCP-{account_hash}", 0.0)]
		else:
			period, holdings = self._portfolio_holdings_from_settings(settings)
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
			portfolio_meta = cached.get("portfolio_meta") or portfolio_meta
		else:
			if data_provider == "robinhood_mcp":
				try:
					stock_data, portfolio_meta = self._fetch_robinhood_mcp_data(settings)
				except Exception as e:
					raise RuntimeError(f"Robinhood official MCP refresh failed: {str(e)}") from e
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
			self._publish_holdings_context(stock_data)
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
				portfolio_meta,
			)

		account_value_override = portfolio_meta.get("account_value")
		image = self._create_dashboard(
			stock_data,
			dimensions,
			history_points,
			tracking_window_label=self._tracking_window_label(period),
			tracking_period=period,
			holdings_pin_symbols=self._symbols_setting(settings.get("holdings_pin_symbols")),
			holdings_sink_symbols=self._symbols_setting(settings.get("holdings_sink_symbols")),
			account_value_override=account_value_override,
			header_brand=self._header_brand_from_settings(settings, portfolio_meta),
			ticker_theme=settings.get("ticker_theme", "auto"),
			theme_context=theme_context,
			updated_at=updated_at,
			portfolio_meta=portfolio_meta,
			holdings_order=settings.get("holdings_order") or "smart",
		)
		if hasattr(image, "info"):
			provenance = SourceProvenance.FRESH_CACHE if theme_render_only else SourceProvenance.LIVE
			return attach_source_provenance(image, provenance)
		return image

	def _publish_holdings_context(self, stock_data):
		"""Share only the held ticker symbols (largest first) so Money Radar can follow the portfolio."""
		equities = [data for data in stock_data if not self._is_cash_holding(data)]
		equities.sort(key=lambda data: -self._finite_float(data.get("total_value")))
		symbols = [str(data.get("symbol") or "").strip().upper() for data in equities]
		symbols = [symbol for symbol in dict.fromkeys(symbols) if symbol]
		if symbols:
			write_context(
				"stocktracker",
				{"kind": "portfolio_symbols", "source": "Stock Tracker", "symbols": symbols[:20],
				 "summary": f"{len(symbols)} holdings"},
				ttl_seconds=7 * 86400,
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

	def _write_source_cache(self, source_key, stock_data, history_points, generated_at, portfolio_meta=None):
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
			"portfolio_meta": {
				key: value
				for key, value in (portfolio_meta or {}).items()
				if value is None or isinstance(value, (str, int, float, bool))
			},
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
			"portfolio_meta": payload.get("portfolio_meta")
			if isinstance(payload.get("portfolio_meta"), dict)
			else {},
		}

	def _portfolio_holdings_from_settings(self, settings):
		period = settings.get('period', '1mo')
		csv_paths = [
			str(value).strip()
			for value in (
				settings.get('portfolio_csv_path'),
				settings.get('portfolio_csv_file'),
			)
			if str(value or '').strip()
		]
		first_error = None
		if csv_paths:
			for csv_path in dict.fromkeys(csv_paths):
				try:
					resolved_path = self._resolve_portfolio_csv_path(csv_path)
				except RuntimeError as error:
					if first_error is None:
						first_error = error
					continue
				return period, self._load_portfolio_csv(resolved_path)

		try:
			tickers_str = settings.get('tickers', '').strip()
			shares_str = settings.get('shares', '').strip()

			if not tickers_str or not shares_str:
				if first_error is not None:
					raise first_error
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
		return provider if provider in {"auto", "massive", "yfinance", "robinhood_mcp"} else "auto"

	def _massive_client(self, device_config, data_provider):
		if data_provider in {"yfinance", "robinhood_mcp"}:
			return None
		api_key = load_massive_api_key(device_config)
		if not api_key:
			return None
		return MassiveMarketData(api_key)

	def _fetch_robinhood_mcp_data(self, settings):
		account_hash = str(settings.get("robinhood_account_hash") or "").strip().lower()
		token_path = str(settings.get("robinhood_token_path") or "").strip() or None
		period = str(settings.get("period") or "1mo").strip() or "1mo"
		snapshot = RobinhoodMCPClient(token_path=token_path).fetch_snapshot(
			account_hash,
			period=period,
		)
		quotes = snapshot.get("quotes") or {}
		histories = snapshot.get("histories") or {}
		quote_times = [
			str((quotes.get(str(position.get("symbol") or "").strip().upper()) or {}).get("timestamp") or "").strip()
			for position in snapshot.get("positions") or []
		]
		if not quote_times or any(not quote_time for quote_time in quote_times):
			raise RuntimeError("Missing official Robinhood quote timestamp for held position")
		current_history_key = f"current:{max(quote_times)}"
		stock_data = []
		for position in snapshot.get("positions") or []:
			symbol = str(position.get("symbol") or "").strip().upper()
			shares = float(position.get("quantity"))
			quote = quotes.get(symbol)
			if not isinstance(quote, dict):
				raise RuntimeError(f"Missing official Robinhood quote for {symbol}")
			current_price = float(quote["price"])
			previous_close = float(quote["previous_close"])
			quote_time = str(quote.get("timestamp") or "").strip()
			if not quote_time:
				raise RuntimeError(f"Missing official Robinhood quote timestamp for {symbol}")
			change = current_price - previous_close
			change_percent = (change / previous_close) * 100 if previous_close else 0.0
			history_points = histories.get(symbol)
			if not isinstance(history_points, list) or len(history_points) < 2:
				raise RuntimeError(f"Missing official Robinhood history for {symbol}")
			history = _SimpleHistory(
				[(str(timestamp), float(close)) for timestamp, close in history_points]
				+ [(current_history_key, current_price)]
			)
			stock_data.append(
				{
					"symbol": symbol,
					"name": symbol,
					"price": current_price,
					"regular_price": previous_close,
					"change": change,
					"change_percent": change_percent,
					"shares": shares,
					"total_value": current_price * shares,
					"total_change": change * shares,
					"history": history,
					"quote_source": "robinhood_extended"
					if quote.get("extended_hours")
					else "robinhood_realtime",
					"quote_time": quote_time,
					"extended_hours": bool(quote.get("extended_hours")),
					"data_provider": "robinhood_mcp",
				}
			)
		if not stock_data:
			raise RuntimeError("Robinhood official MCP returned no equity positions")
		portfolio_meta = snapshot.get("portfolio_meta")
		if not isinstance(portfolio_meta, dict):
			raise RuntimeError("Robinhood official MCP returned no portfolio metadata")
		portfolio_meta = dict(portfolio_meta)
		portfolio_meta["official_mcp"] = True
		return stock_data, portfolio_meta

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
		font_size = int(size)
		font_name = "Jost-SemiBold.ttf" if bold else "Jost.ttf"
		font_paths = [
			os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "static", "fonts", font_name)),
			"/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
			"/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
		]
		try:
			for font_path in font_paths:
				if os.path.isfile(font_path):
					return load_font(font_path, font_size)
			font = get_font("Jost", font_size, "bold" if bold else "normal")
			if font:
				return font
		except Exception as e:
			logging.warning(f"Falling back to default font: {e}")
		return ImageFont.load_default()

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
		if self._data_provider(settings) == "robinhood_mcp":
			return "robinhood"

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
				"data_provider": "robinhood_mcp"
				if portfolio_meta.get("official_mcp")
				else "robinhood",
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

	def _portfolio_series(self, stock_data, account_value_override=None):
		histories = [
			data.get("history")
			for data in stock_data
			if not getattr(data.get("history"), "empty", True)
		]
		if not histories:
			return []
		common_dates = set(histories[0].index)
		for history in histories[1:]:
			common_dates.intersection_update(history.index)
		dates = [date for date in histories[0].index if date in common_dates]
		series = []
		for date in dates:
			total = 0
			for data in stock_data:
				history = data.get("history")
				if history is not None and date in history.index:
					total += float(history.loc[date, "Close"]) * data["shares"]
			series.append((str(date), total))
		if account_value_override is not None and series:
			series[-1] = (series[-1][0], account_value_override)
		return series

	def _portfolio_values(self, stock_data, account_value_override=None):
		return [
			value
			for _date_key, value in self._portfolio_series(
				stock_data,
				account_value_override=account_value_override,
			)
		]

	@staticmethod
	def _history_cutoff_date(period, now):
		period = str(period or "1mo").strip().lower()
		if period == "ytd":
			return now.date().replace(month=1, day=1)
		days = {
			"1d": 1,
			"5d": 5,
			"1mo": 32,
			"3mo": 95,
			"6mo": 190,
			"1y": 370,
		}.get(period, 32)
		return (now - timedelta(days=days)).date()

	@staticmethod
	def _series_date(date_key):
		match = re.search(r"\d{4}-\d{2}-\d{2}", str(date_key or ""))
		if not match:
			return None
		try:
			return datetime.strptime(match.group(0), "%Y-%m-%d").date()
		except ValueError:
			return None

	def _portfolio_curve(
		self,
		stock_data,
		history_points=None,
		*,
		period="1mo",
		now=None,
		account_value_override=None,
	):
		now = now or datetime.now()
		official_series = self._portfolio_series(
			stock_data,
			account_value_override=account_value_override,
		)
		official_dates = [
			date
			for date in (self._series_date(date_key) for date_key, _value in official_series)
			if date is not None
		]
		cutoff = self._history_cutoff_date(period, now)
		earliest_official = min(official_dates) if official_dates else None
		local_by_date = {}
		local_on_official = {}
		for item in history_points or []:
			point = self._normalize_portfolio_history_entry(item)
			if point is None:
				continue
			point_date = self._series_date(point["date"])
			if point_date is None or point_date < cutoff:
				continue
			if earliest_official is not None and point_date >= earliest_official:
				local_on_official[point_date] = float(point["value"])
				continue
			local_by_date[point_date] = point
		supplemental = [local_by_date[date] for date in sorted(local_by_date)]
		if supplemental and official_series and not self._local_history_meets_official(
			official_series, local_on_official,
		):
			# Saved snapshots record the account as it was, while the official
			# series back-projects today's holdings and cash. Splice them only
			# when both agree on their first shared day, never across a jump.
			supplemental = []
		values = [float(point["value"]) for point in supplemental]
		values.extend(value for _date_key, value in official_series)
		return values, supplemental

	@classmethod
	def _local_history_meets_official(cls, official_series, local_on_official):
		for date_key, official_value in official_series:
			local_value = local_on_official.get(cls._series_date(date_key))
			if local_value is None:
				continue
			scale = max(abs(float(official_value)), 1.0)
			return abs(float(official_value) - local_value) / scale <= LOCAL_HISTORY_SEAM_TOLERANCE
		return False

	def _portfolio_curve_values(self, stock_data, history_points=None, **kwargs):
		values, _supplemental = self._portfolio_curve(stock_data, history_points, **kwargs)
		return values

	def _record_portfolio_snapshot(self, stock_data, now=None, account_value_override=None):
		now = now or datetime.now()
		snapshot = self._portfolio_snapshot(stock_data, now, account_value_override=account_value_override)
		history_path = self._portfolio_history_path(stock_data)
		current_history = self._read_portfolio_history(history_path)
		current_history = self._upsert_portfolio_snapshot(current_history, snapshot)
		try:
			self._write_portfolio_history(history_path, current_history)
		except Exception as e:
			logging.warning(f"Could not write stock portfolio history: {type(e).__name__}: {e}")
		imported_path = os.path.join(os.path.dirname(history_path), "imported-history.json")
		if os.path.abspath(imported_path) == os.path.abspath(history_path):
			return current_history
		combined = self._read_portfolio_history(imported_path)
		for point in current_history:
			combined = self._upsert_portfolio_snapshot(combined, point)
		return combined

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

	@staticmethod
	def _window_short_label(period):
		key = str(period or "").strip().lower()
		return WINDOW_SHORT_LABELS.get(key, key.upper() or "1M")

	@staticmethod
	def _signed_money(value, decimals=2):
		sign = "-" if value < 0 else "+"
		return f"{sign}${abs(value):,.{decimals}f}"

	@staticmethod
	def _compact_money(value):
		abs_value = abs(value)
		if abs_value >= 1000000:
			return f"${value / 1000000:.2f}M"
		if abs_value >= 1000:
			return f"${value / 1000:.1f}K"
		return f"${value:.0f}"

	def _history_closes(self, data):
		history = data.get("history")
		if history is None or getattr(history, "empty", True):
			return []
		closes = []
		for date_key in list(getattr(history, "index", [])):
			try:
				close = float(history.loc[date_key, "Close"])
			except (KeyError, TypeError, ValueError, IndexError):
				continue
			if math.isfinite(close):
				closes.append(close)
		return closes

	def _holding_metrics(self, data, portfolio_total):
		"""Day and window movement for one table row, derived from the fetched quote and history."""
		is_cash = self._is_cash_holding(data)
		value = self._finite_float(data.get("total_value"))
		shares = self._finite_float(data.get("shares"))
		price = self._finite_float(data.get("price"))
		closes = [] if is_cash else self._history_closes(data)
		day_change = None
		day_percent = None
		if not is_cash:
			if data.get("data_provider") != "robinhood_mcp" and len(closes) >= 2 and closes[-2]:
				# Yahoo and Massive report change over the whole window; the bar before the
				# live point is the previous session's close.
				day_change = price - closes[-2]
				day_percent = day_change / closes[-2] * 100
			else:
				day_change = self._finite_float(data.get("change"))
				day_percent = self._finite_float(data.get("change_percent"))
		window_percent = None
		if len(closes) >= 2 and closes[0]:
			window_percent = (closes[-1] - closes[0]) / closes[0] * 100
		return {
			"data": data,
			"is_cash": is_cash,
			"value": value,
			"weight": value / portfolio_total * 100 if portfolio_total else 0.0,
			"day_percent": day_percent,
			"day_pl": day_change * shares if day_change is not None else None,
			"window_percent": window_percent,
			"closes": closes,
		}

	def _summary_stats(self, metrics, portfolio_meta):
		portfolio_meta = portfolio_meta or {}
		stats = []
		equities = [item for item in metrics if not item["is_cash"]]
		stats.append(("INVESTED", self._money(sum(item["value"] for item in equities))))
		cash = portfolio_meta.get("cash_balance")
		if cash is None:
			cash_rows = [item["value"] for item in metrics if item["is_cash"]]
			cash = sum(cash_rows) if cash_rows else None
		if cash is not None:
			stats.append(("CASH", self._money(cash)))
		if portfolio_meta.get("buying_power") is not None:
			stats.append(("BUYING POWER", self._money(portfolio_meta["buying_power"])))
		pending = portfolio_meta.get("pending_deposits")
		if pending is not None and abs(pending) >= 0.005:
			stats.append(("PENDING", self._money(pending)))
		movers = [item for item in equities if item["day_percent"] is not None]
		if movers:
			best = max(movers, key=lambda item: item["day_percent"])
			worst = min(movers, key=lambda item: item["day_percent"])
			stats.append(("BEST TODAY", f"{best['data']['symbol']} {best['day_percent']:+.2f}%"))
			if worst is not best:
				stats.append(("WORST TODAY", f"{worst['data']['symbol']} {worst['day_percent']:+.2f}%"))
		stats.append(("POSITIONS", str(len(equities))))
		return stats[:4]

	def _draw_change_arrow(self, draw, x, center_y, percent, size=8):
		"""Solid up/down triangle: color lives in a filled shape, which survives e-paper dithering."""
		colors = _active_stock_colors()
		half = size / 2
		if percent is None or abs(percent) <= 0.005:
			draw.rectangle((x + 1, round(center_y) - 1, x + size - 1, round(center_y) + 1), fill=colors["ink"])
			return
		color = self._change_color(percent)
		if percent > 0:
			points = [(x, center_y + half * 0.85), (x + size, center_y + half * 0.85), (x + half, center_y - half * 0.95)]
		else:
			points = [(x, center_y - half * 0.85), (x + size, center_y - half * 0.85), (x + half, center_y + half * 0.95)]
		draw.polygon(points, fill=color)

	def _draw_summary(
		self,
		img,
		draw,
		box,
		total_value,
		day_change,
		day_percent,
		day_label="TODAY",
		stats=(),
		curve_values=(),
	):
		"""The portfolio card in its gold frame: the total by its gold coin, in today's colour, standing
		on the floor of a softened chart of the account's value over the window; today's change in that
		colour on the ruled row under it, then the first two balances."""
		colors = _active_stock_colors()
		left, top, right, bottom = (int(edge) for edge in box)
		self._draw_gold_frame(draw, box, colors["panel_gold"])
		if not self._draw_section_wordmark(img, "PORTFOLIO", left + 10, top + 8):
			draw.text((left + 13, top + 11), "PORTFOLIO", fill=colors["ink"], font=self._font(16, True))
		today_ink = self._today_ink(day_percent)
		# The chart runs out to the frame on the left, right and foot; the window's dates are the
		# trend card's to name.
		plot = (left + SUMMARY_FRAME, top + SUMMARY_CHART[0], right - SUMMARY_FRAME + 1, top + SUMMARY_CHART[1])
		chart_bg = self._blend(colors["panel"], colors["panel_gold"], 0.6)
		self._draw_summary_chart(img, draw, plot, curve_values, chart_bg, self._change_color(day_percent),
								 colors["panel_gold"])
		self._draw_total_figure(img, draw, self._total_figure_layout(draw, plot, total_value), today_ink, chart_bg)

		# Today's change, in today's colour by its solid arrow.
		inner_left, inner_right = left + SUMMARY_FRAME, right - SUMMARY_FRAME
		row_top = plot[3]
		draw.line((inner_left, row_top, inner_right, row_top), fill=colors["border"], width=1)
		row_mid = row_top + 11
		self._draw_change_arrow(draw, inner_left + 6, row_mid, day_percent, size=12)
		label_font = self._font(10, True)
		label_width = self._text_width(draw, day_label, label_font)
		draw.text(
			(inner_right - 6 - label_width, self._centered_text_y(draw, day_label, label_font, row_mid)),
			day_label,
			fill=colors["ink"],
			font=label_font,
		)
		change_text = self._change_text(day_change, day_percent)
		change_font = self._fit_font(draw, change_text, inner_right - inner_left - 40 - label_width, 17, True, 11)
		draw.text(
			(inner_left + 24, self._centered_text_y(draw, change_text, change_font, row_mid)),
			change_text,
			fill=today_ink,
			font=change_font,
		)

		# The balances, two cells side by side.
		stats = list(stats)[:2]
		stats_top = row_top + 22
		draw.line((inner_left, stats_top, inner_right, stats_top), fill=colors["border"], width=1)
		if not stats:
			return
		center_y = (stats_top + bottom - SUMMARY_FRAME) / 2 + 0.5
		cell_w = (inner_right - inner_left) / len(stats)
		stat_label_font = self._font(9, True)
		for index, (label, value) in enumerate(stats):
			cell_left = inner_left + index * cell_w
			cell_right = cell_left + cell_w
			label_x = cell_left + 7
			draw.text((label_x, self._centered_text_y(draw, label, stat_label_font, center_y)), label,
					  fill=colors["ink"], font=stat_label_font)
			label_width = self._text_width(draw, label, stat_label_font)
			value_font = self._fit_font(draw, value, cell_right - label_x - label_width - 14, 13, True, 8)
			value_x = cell_right - 7 - self._text_width(draw, value, value_font)
			draw.text((value_x, self._centered_text_y(draw, value, value_font, center_y)), value, fill=colors["ink"],
					  font=value_font)
			if index:
				draw.line((cell_left, stats_top + 4, cell_left, bottom - SUMMARY_FRAME - 3), fill=colors["grid"], width=1)

	def _draw_gold_frame(self, draw, box, fill):
		"""A pixel-cut gold frame: a gold band, lit along its top and left and shaded along its bottom
		and right, between an outer and an inner ink line, its corners notched, over the page's
		orange shadow; ``fill`` inside."""
		colors = _active_stock_colors()
		left, top, right, bottom = (int(edge) for edge in box)
		gold = colors["accent_gold"]
		light = self._blend((255, 255, 255), gold, 0.5)
		shade = self._blend(colors["accent_orange"], gold, 0.65)
		draw.polygon(self._notched(left + 3, top + 4, right + 3, bottom + 4, 3), fill=colors["accent_orange"])
		draw.polygon(self._notched(left, top, right, bottom, 3), fill=colors["border"])
		draw.polygon(self._notched(left + 2, top + 2, right - 2, bottom - 2, 2), fill=gold)
		edge = SUMMARY_FRAME - 1
		draw.line((left + 3, top + 2, right - 3, top + 2), fill=light)
		draw.line((left + 2, top + 3, left + 2, bottom - 3), fill=light)
		draw.line((left + 3, bottom - 2, right - 3, bottom - 2), fill=shade)
		draw.line((right - 2, top + 3, right - 2, bottom - 3), fill=shade)
		draw.rectangle((left + edge, top + edge, right - edge, bottom - edge), fill=colors["border"])
		draw.rectangle((left + SUMMARY_FRAME, top + SUMMARY_FRAME, right - SUMMARY_FRAME, bottom - SUMMARY_FRAME), fill=fill)

	@staticmethod
	def _notched(left, top, right, bottom, notch):
		"""A rectangle's outline with its corners cut ``notch`` px, pixel-art fashion."""
		return [(left + notch, top), (right - notch, top), (right, top + notch), (right, bottom - notch),
				(right - notch, bottom), (left + notch, bottom), (left, bottom - notch), (left, top + notch)]

	@staticmethod
	def _today_ink(percent):
		"""The total's and today's change's colour: deep green up, deep red down (the night page's own
		green and red), ink when flat."""
		colors = _active_stock_colors()
		if percent is None or abs(percent) <= 0.005:
			return colors["ink"]
		if colors["mode"] == "night":
			return colors["malachite"] if percent > 0 else colors["cinnabar"]
		return GAIN_INK if percent > 0 else LOSS_INK

	def _draw_summary_chart(self, img, draw, plot, values, chart_bg, tint, card):
		"""The account's value over the window behind the total, only its trend and softened: a fine
		grid and the values as a hill in ``tint`` from the left edge to the right resting on the foot,
		blurred by SUMMARY_BLUR, its top SUMMARY_FADE rows fading into the ``card`` colour above it,
		with a sparkle on today's value."""
		colors = _active_stock_colors()
		width, height = plot[2] - plot[0], plot[3] - plot[1]
		layer = Image.new("RGB", (width, height), chart_bg)
		pen = ImageDraw.Draw(layer)
		grid = self._blend(colors["grid"], chart_bg, 0.5)
		for x in range(SUMMARY_GRID, width, SUMMARY_GRID):
			pen.line((x, 0, x, height), fill=grid)
		for y in range(SUMMARY_GRID, height, SUMMARY_GRID):
			pen.line((0, y, width, y), fill=grid)
		values = [float(value) for value in values or () if math.isfinite(float(value))]
		end = None
		if len(values) >= 2:
			# Unpadded, the window's low nearly touches the foot and its high the top: the swings show.
			vmin, vmax = trend_chart.value_bounds(values, 0.0)
			points = trend_chart.plot_points((0, 3, width - 9, height - SUMMARY_HILL_GAP), values, vmin, vmax)
			curve = trend_chart.monotone_curve(points)
			# Today's value carries on flat to the right edge, past its sparkle.
			ridge = [*curve, (width, curve[-1][1])]
			pen.polygon([(0, height), *ridge, (width, height)], fill=self._blend(tint, chart_bg, 0.26))
			pen.line([(round(x), round(y)) for x, y in ridge], fill=tint, width=2, joint="curve")
			end = (plot[0] + round(points[-1][0]), plot[1] + round(points[-1][1]))
		layer = layer.filter(ImageFilter.GaussianBlur(SUMMARY_BLUR))
		fade = Image.linear_gradient("L").resize((width, SUMMARY_FADE)).transpose(Image.Transpose.FLIP_TOP_BOTTOM)
		layer.paste(Image.new("RGB", (width, SUMMARY_FADE), card), (0, 0), fade)
		img.paste(layer, plot[:2])
		if end:
			self._draw_sparkle(draw, end, 6, COIN_GOLD, colors["ink"])
			self._draw_sparkle(draw, (end[0] - 5, end[1] - 10), 2, COIN_GOLD, None)
			self._draw_sparkle(draw, (end[0] + 2, end[1] - 15), 2, COIN_GOLD, None)

	@staticmethod
	def _draw_sparkle(draw, center, size, fill, outline):
		"""A four-point star ``size`` px from its centre to each point."""
		x, y = center
		waist = max(1, size * 0.3)
		draw.polygon([(x, y - size), (x + waist, y - waist), (x + size, y), (x + waist, y + waist), (x, y + size),
					  (x - waist, y + waist), (x - size, y), (x - waist, y - waist)], fill=fill, outline=outline)

	def _total_figure_layout(self, draw, plot, value):
		"""Where the total goes, centred with its coin across the chart's foot: its text and font, the
		text's origin and the coin's top left. Both rise as far as they must for the comma's tail
		(with the halo) and the coin to clear the foot; the coin keeps to the digits' middle."""
		text = f"{value:,.2f}"
		font = self._fit_font(draw, text, plot[2] - plot[0] - SUMMARY_COIN - 25, SUMMARY_FIGURE_SIZE, True, 16)
		text_w = self._text_width(draw, text, font)
		x = plot[0] + (plot[2] - plot[0] - SUMMARY_COIN - 5 - text_w) / 2
		digits = draw.textbbox((0, 0), "0", font=font)
		reach = draw.textbbox((0, 0), text, font=font, stroke_width=2)[3]
		middle = min(plot[3] - SUMMARY_FIGURE_FOOT, plot[3] - 1 - reach + (digits[1] + digits[3]) / 2,
					 plot[3] - 2 - SUMMARY_COIN / 2)
		return {"text": text, "font": font, "origin": (x + SUMMARY_COIN + 5, round(middle - (digits[1] + digits[3]) / 2)),
				"coin": (round(x), round(middle - SUMMARY_COIN / 2))}

	def _draw_total_figure(self, img, draw, figure, ink, halo):
		"""The total by the gold dollar coin, in ``ink``, haloed in the chart's ground so the trend behind
		never cuts its digits."""
		coin_x, coin_y = figure["coin"]
		icon = _coin_icon(SUMMARY_COIN)
		if icon is not None:
			img.paste(icon, (coin_x + (SUMMARY_COIN - icon.width) // 2, coin_y + (SUMMARY_COIN - icon.height) // 2), icon)
		else:
			radius = SUMMARY_COIN / 2
			center = (coin_x + radius, coin_y + radius)
			draw.ellipse((center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius), fill=COIN_GOLD,
						 outline=COIN_RIM, width=2)
			draw.text(center, "$", fill=COIN_RIM, font=self._font(int(SUMMARY_COIN * 0.62), True), anchor="mm")
		draw.text(figure["origin"], figure["text"], fill=ink, font=figure["font"], stroke_width=2, stroke_fill=halo)

	def _draw_sparkline(
		self,
		img,
		draw,
		box,
		values,
		history_points=None,
		history_markers_on_prefix=False,
		*,
		window_label=None,
		date_range=None,
		extremes=False,
	):
		# Local snapshots are already merged into values; they are no longer marked separately.
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
		plot = (left + 16, top + 40, right - 16, bottom - 12)
		if window_label and len(values) >= 2:
			self._draw_trend_return(draw, (right - 14, top + 22), values, window_label)
		if date_range:
			self._draw_trend_dates(draw, plot, bottom, date_range)
		chart_bg = self._blend(colors["panel"], colors["paper"], 0.78)
		draw.rounded_rectangle(
			plot,
			radius=4,
			fill=chart_bg,
			outline=self._blend(colors["grid"], colors["panel_blue"], 0.65),
			width=1,
		)
		values = [float(value) for value in values]
		if len(values) < 2:
			return

		start = values[0]
		vmin, vmax = trend_chart.value_bounds(values)
		inner = (plot[0] + 8, plot[1] + 6, plot[2] - 10, plot[3] - 6)
		points = trend_chart.plot_points(inner, values, vmin, vmax)
		curve = trend_chart.monotone_curve(points)
		base_y = points[0][1]
		gain, loss = colors["malachite"], colors["cinnabar"]

		self._fill_against_baseline(img, plot, curve, base_y, chart_bg)
		self._draw_dashed_line(draw, plot[0] + 4, plot[2] - 4, round(base_y), colors["muted"])
		for run, above in trend_chart.split_at_baseline(curve, base_y):
			draw.line(
				[(round(x), round(y)) for x, y in run],
				fill=gain if above else loss,
				width=3,
				joint="curve",
			)
		start_x, start_y = round(points[0][0]), round(base_y)
		draw.ellipse(
			(start_x - 3, start_y - 3, start_x + 3, start_y + 3),
			fill=chart_bg,
			outline=colors["muted"],
			width=2,
		)
		ended_up = values[-1] >= start
		baseline_rect = self._draw_baseline_label(draw, plot, base_y, self._money(start, 0), chart_bg, below=ended_up)
		if extremes:
			self._draw_extreme_tags(draw, plot, values, points, chart_bg, avoid=[baseline_rect])
		self._draw_latest_value_marker(
			draw,
			(round(points[-1][0]), round(points[-1][1])),
			gain if ended_up else loss,
		)

	def _draw_trend_return(self, draw, anchor, values, window_label):
		"""Window return right-aligned in the trend card's title row."""
		colors = _active_stock_colors()
		change = values[-1] - values[0]
		percent = change / values[0] * 100 if values[0] else 0.0
		right, center_y = anchor
		label_font = self._font(11, True)
		label = f"{window_label} RETURN"
		value_text = f"{self._signed_money(change)} ({percent:+.2f}%)"
		value_font = self._font(15, True)
		label_width = self._text_width(draw, label, label_font)
		value_width = self._text_width(draw, value_text, value_font)
		value_x = right - label_width - 8 - value_width
		draw.text(
			(right - label_width, self._centered_text_y(draw, label, label_font, center_y)),
			label,
			fill=colors["ink"],
			font=label_font,
		)
		draw.text(
			(value_x, self._centered_text_y(draw, value_text, value_font, center_y)),
			value_text,
			fill=colors["ink"],
			font=value_font,
		)
		self._draw_change_arrow(draw, value_x - 13, center_y, percent, size=9)

	def _draw_trend_dates(self, draw, plot, bottom, date_range):
		colors = _active_stock_colors()
		font = self._font(9, True)
		center_y = (plot[3] + bottom) / 2 + 0.5
		start_text, end_text = (value.strftime("%b %d").upper() for value in date_range)
		draw.text((plot[0] + 2, self._centered_text_y(draw, start_text, font, center_y)), start_text, fill=colors["ink"], font=font)
		end_width = self._text_width(draw, end_text, font)
		draw.text((plot[2] - 2 - end_width, self._centered_text_y(draw, end_text, font, center_y)), end_text, fill=colors["ink"], font=font)

	def _draw_extreme_tags(self, draw, plot, values, points, chart_bg, avoid=()):
		"""Label the window high above its point and the low below it, skipping any that collide."""
		colors = _active_stock_colors()
		if max(values) - min(values) < 0.005:
			return
		font = self._font(10, True)
		placed = [rect for rect in avoid if rect]
		latest = points[-1]
		placed.append((latest[0] - 7, latest[1] - 7, latest[0] + 7, latest[1] + 7))
		for prefix, index, above in (
			("HIGH", max(range(len(values)), key=lambda i: values[i]), True),
			("LOW", min(range(len(values)), key=lambda i: values[i]), False),
		):
			if index in (0, len(values) - 1):
				continue
			text = f"{prefix} {self._compact_money(values[index])}"
			width = self._text_width(draw, text, font)
			x_point, y_point = points[index]
			x = min(max(x_point - width / 2, plot[0] + 4), plot[2] - width - 4)
			y = y_point - 17 if above else y_point + 5
			y = min(max(y, plot[1] + 2), plot[3] - 15)
			rect = (x - 3, y - 1, x + width + 3, y + 13)
			if any(self._rects_overlap(rect, other) for other in placed):
				continue
			draw.rounded_rectangle(rect, radius=3, fill=chart_bg)
			draw.text((x, y), text, fill=colors["ink"], font=font)
			placed.append(rect)

	@staticmethod
	def _rects_overlap(first, second):
		return not (
			first[2] <= second[0]
			or second[2] <= first[0]
			or first[3] <= second[1]
			or second[3] <= first[1]
		)

	def _fill_against_baseline(self, img, plot, curve, base_y, chart_bg):
		"""Tint the area between the curve and the start baseline: gain above, loss below."""
		colors = _active_stock_colors()
		# Close the area on the whole baseline row; that row belongs to the dashed line, so a
		# fractional baseline never spills one tint into the other side.
		split = int(round(base_y))
		area = Image.new("L", img.size, 0)
		ImageDraw.Draw(area).polygon(
			[(curve[0][0], split), *curve, (curve[-1][0], split)],
			fill=255,
		)
		for color, region in (
			(colors["malachite"], (plot[0], plot[1], plot[2], split)),
			(colors["cinnabar"], (plot[0], split + 1, plot[2], plot[3])),
		):
			if region[3] <= region[1]:
				continue
			tint = Image.new(
				"RGB",
				(region[2] - region[0], region[3] - region[1]),
				self._blend(color, chart_bg, 0.22),
			)
			img.paste(tint, region[:2], area.crop(region))

	@staticmethod
	def _draw_dashed_line(draw, x0, x1, y, fill, dash=5, gap=4):
		x = x0
		while x < x1:
			draw.line((x, y, min(x + dash, x1), y), fill=fill, width=1)
			x += dash + gap

	def _draw_baseline_label(self, draw, plot, base_y, text, chart_bg, below):
		"""Start value on the side of the baseline away from the latest point."""
		colors = _active_stock_colors()
		font = self._font(11, True)
		width = self._text_width(draw, text, font)
		x = plot[2] - width - 14
		y = round(base_y) + 4 if below else round(base_y) - 18
		y = min(max(y, plot[1] + 2), plot[3] - 16)
		rect = (x - 3, y - 1, x + width + 3, y + 14)
		draw.rounded_rectangle(rect, radius=3, fill=chart_bg)
		draw.text((x, y), text, fill=colors["ink"], font=font)
		return rect

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

	def _ordered_holdings(self, stock_data, pin_symbols=None, sink_symbols=None):
		configured_pins = self._symbols_setting(pin_symbols)
		pin_order = ("SPCX",) + tuple(
			symbol for symbol in configured_pins if symbol != "SPCX"
		)
		pin_rank = {
			symbol: index
			for index, symbol in enumerate(pin_order)
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

	def _smart_order(self, metrics, portfolio_total, pin_symbols=None, sink_symbols=None):
		"""Rows by how much each matters today: its share of the day's P/L, raised when the move is unusual.

		A move counts as unusual against the holding's own daily swings over the window, so a 4%
		day for a steady fund ranks above the same 4% for a volatile stock. Lower-priority symbols
		follow the rest and cash comes last; pinned symbols are only kept on the table (never
		pushed into the overflow ticker), not forced to the top.
		"""
		pins = set(self._symbols_setting(pin_symbols))
		sinks = set(self._symbols_setting(sink_symbols))

		def sort_key(item):
			symbol = str(item["data"].get("symbol") or "").upper()
			bucket = 3 if item["is_cash"] else 2 if symbol in sinks and symbol not in pins else 1
			return (bucket, -self._attention(item, portfolio_total), -item["value"], symbol)

		return sorted(metrics, key=sort_key)

	@classmethod
	def _attention(cls, item, portfolio_total):
		if item["is_cash"] or item["day_pl"] is None or not portfolio_total:
			return 0.0
		impact = abs(item["day_pl"]) / portfolio_total * 100
		swing = cls._daily_swing(item["closes"])
		surprise = abs(item["day_percent"] or 0.0) / swing if swing else 1.0
		return impact * (1.0 + 0.5 * max(0.0, surprise - 1.0))

	@staticmethod
	def _daily_swing(closes):
		"""The typical size of one day's move (%) over the window, without today's live point."""
		returns = [abs(b - a) / a * 100 for a, b in zip(closes[:-2], closes[1:-1]) if a]
		if len(returns) < 5:
			return None
		return max(0.25, sum(returns) / len(returns))

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

	def _holding_logo_image(self, symbol, size, width=None):
		"""The holding's official mark centred in a ``size``-tall slot ``width`` wide.

		Every mark is scaled to the same visual area (a wide one such as Nintendo's runs wider,
		a tall one stands taller) within the slot; at night its near-black parts (Apple,
		SpaceX, Take-Two) turn to the page's ink.
		"""
		asset_name = self._holding_logo_asset(symbol)
		if not asset_name:
			return None
		size = int(size)
		width = int(width or round(size * LOGO_SLOT_RATIO))
		colors = _active_stock_colors()
		night = colors["mode"] == "night"
		cache_key = (asset_name, size, width, night)
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
			area = (size * LOGO_AREA_SHARE) ** 2
			scale = min(math.sqrt(area / (logo.width * logo.height)), width / logo.width, size / logo.height)
			logo = logo.resize((max(1, round(logo.width * scale)), max(1, round(logo.height * scale))), resample)
			if night:
				ink = colors["ink"]
				pixels = logo.load()
				for y in range(logo.height):
					for x in range(logo.width):
						r, g, b, a = pixels[x, y]
						if a and max(r, g, b) < LOGO_NIGHT_INK_BELOW:
							pixels[x, y] = (*ink[:3], a)
			canvas = Image.new("RGBA", (width, size), (0, 0, 0, 0))
			canvas.alpha_composite(logo, ((width - logo.width) // 2, (size - logo.height) // 2))
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

	def _draw_header_money(self, img, box, turn=0):
		"""Dollar bills on the wind filling the header's free stretch: the art's bills and coins
		taken apart and flown again, bill and coin by turns from ``turn`` on, at the stretch's
		height and spaced out to reach both its ends."""
		left, top, right, bottom = [int(value) for value in box]
		pieces = _header_money_pieces()
		if right - left < 60 or bottom - top < 12 or not pieces:
			return
		bills, coins, art_height = pieces
		scale = (bottom - top) / art_height
		flight, used = [], 0
		for index in range(64):
			piece, piece_top = bills[(turn + index // 2) % len(bills)] if index % 2 == 0 else coins[(turn + index // 2) % len(coins)]
			width = max(1, round(piece.width * scale))
			if used + width + HEADER_MONEY_MIN_GAP * len(flight) > right - left:
				break
			flight.append((piece, piece_top, width))
			used += width
		if len(flight) > 1 and len(flight) % 2 == 0:
			# The flight ends on a bill, not a coin.
			used -= flight.pop()[2]
		if not flight:
			return
		gap = (right - left - used) / max(1, len(flight) - 1)
		x = left if len(flight) > 1 else left + (right - left - used) / 2
		for piece, piece_top, width in flight:
			height = max(1, round(piece.height * scale))
			art = piece.resize((width, height), Image.Resampling.LANCZOS)
			img.paste(art, (round(x), top + round(piece_top * scale)), art)
			x += width + gap

	def _draw_header(self, img, draw, width, stock_data, header_brand=None, updated_at=None):
		colors = _active_stock_colors()
		margin = DASHBOARD_MARGIN
		draw.rectangle((0, 0, width, HEADER_RULE_Y), fill=colors["paper"])
		header_logo = self._header_logo_image(header_brand, 190, 28)
		if header_logo and colors["mode"] == "night":
			# The wordmark is black artwork; on a dark ground draw its silhouette in ink.
			tinted = Image.new("RGBA", header_logo.size, (*colors["ink"], 255))
			tinted.putalpha(header_logo.getchannel("A"))
			header_logo = tinted
		if header_logo:
			img.paste(header_logo, (margin + 4, (HEADER_RULE_Y - header_logo.height) // 2), header_logo)
			title_right = margin + 4 + header_logo.width
		else:
			draw.text((margin + 4, 7), "STOCK TRACKER", fill=colors["ink"], font=self._font(22, True))
			title_right = margin + 4 + self._text_width(draw, "STOCK TRACKER", self._font(22, True))

		center_y = HEADER_RULE_Y / 2
		chip_font = self._font(11, True)
		right = width - margin
		chips = [(f"AS OF {(updated_at or datetime.now()).strftime('%H:%M')}", colors["ink"])]
		if any(data.get("extended_hours") for data in stock_data):
			chips.append(("EXTENDED HOURS", colors["accent_blue"]))
		for text, outline in chips:
			text_width = self._text_width(draw, text, chip_font)
			chip = (right - text_width - 16, center_y - 10, right, center_y + 10)
			draw.rounded_rectangle(chip, radius=10, fill=colors["panel"], outline=outline, width=2)
			draw.text(
				(chip[0] + 8, self._centered_text_y(draw, text, chip_font, center_y)),
				text,
				fill=colors["ink"],
				font=chip_font,
			)
			right = chip[0] - 8
		source_label = self._source_label(stock_data).upper()
		source_font = self._font(12, True)
		source_left = right - 4 - self._text_width(draw, source_label, source_font)
		draw.text(
			(source_left, self._centered_text_y(draw, source_label, source_font, center_y)),
			source_label,
			fill=colors["ink"],
			font=source_font,
		)
		# The flight starts from another bill each hour.
		self._draw_header_money(img, (title_right + 14, 4, source_left - 14, HEADER_RULE_Y - 5),
								turn=(updated_at or datetime.now()).hour)
		draw.line((margin, HEADER_RULE_Y, width - margin, HEADER_RULE_Y), fill=colors["accent_gold"], width=3)

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
			value_text = self._compact_money(float(data.get("total_value") or 0))
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
		metrics,
		tracking_window_label=None,
		ticker_theme=None,
		theme_context=None,
		window_short="1M",
		sorted_by_day=False,
		keep_symbols=None,
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
		self._draw_holdings_status(draw, (right - 14, top + 22), metrics, tracking_window_label)

		table_left, table_right = left + 12, right - 12
		rows_left = table_left + HOLDING_RAIL_W
		columns = self._holding_column_positions(rows_left + 16, table_right)
		header_font = self._font(10, True)
		header_center = top + 41
		draw.text((table_left, self._centered_text_y(draw, "TYPE", header_font, header_center)), "TYPE", fill=colors["ink"],
				  font=header_font)
		for label, key, align in (
			("SYMBOL", "symbol", "left"),
			("PRICE", "price", "right"),
			("SHARES", "shares", "right"),
			("VALUE", "value", "right"),
			("WEIGHT", "weight_bar", "left"),
			("DAY", "day", "right"),
			("DAY P/L", "day_pl", "right"),
			(f"{window_short} TREND", "trend", "left"),
		):
			x = columns[key][0] if isinstance(columns[key], tuple) else columns[key]
			if align == "right":
				x -= self._text_width(draw, label, header_font)
			draw.text((x, self._centered_text_y(draw, label, header_font, header_center)), label, fill=colors["ink"], font=header_font)
			if sorted_by_day and key == "day_pl":
				# The rows follow today's impact: a small sort mark before the column's name.
				draw.polygon(((x - 10, header_center - 2), (x - 4, header_center - 2), (x - 7, header_center + 2)),
							 fill=colors["ink"])
		rows_top = top + 51
		self._draw_allocation_rule(draw, (table_left, rows_top - 4, table_right, rows_top - 1), metrics)

		rows_bottom = bottom - 6
		max_rows = int((rows_bottom - rows_top) // HOLDING_ROW_MIN_HEIGHT)
		visible = list(metrics)
		hidden = []
		if len(visible) > max_rows:
			rows_bottom = bottom - 40
			max_rows = max(int((rows_bottom - rows_top) // HOLDING_ROW_MIN_HEIGHT), 1)
			# Pinned symbols stay on the table: they take the places of the lowest-ranked others.
			keep = set(keep_symbols or ())
			pinned = {id(item) for item in visible if str(item["data"].get("symbol") or "").upper() in keep}
			others = [item for item in visible if id(item) not in pinned]
			room = max(0, max_rows - len(pinned))
			on_table = pinned | {id(item) for item in others[:room]}
			ranked = visible
			visible = [item for item in ranked if id(item) in on_table][:max_rows]
			shown = {id(item) for item in visible}
			hidden = [item for item in ranked if id(item) not in shown]
		if visible:
			row_height = min((rows_bottom - rows_top) / len(visible), HOLDING_ROW_MAX_HEIGHT)
			max_weight = max((item["weight"] for item in visible), default=0.0)
			groups = self._holding_groups(visible, metrics)
			index = 0
			for group_index, (category, items, weight, day_percent) in enumerate(groups):
				group_top = round(rows_top + index * row_height)
				for item in items:
					row_top = rows_top + index * row_height
					self._draw_holding_row(
						img,
						draw,
						(rows_left, round(row_top), table_right, round(row_top + row_height)),
						item,
						columns,
						max_weight,
					)
					index += 1
				group_bottom = round(rows_top + index * row_height)
				self._draw_holding_rail(draw, (table_left, group_top, rows_left - 4, group_bottom), category, weight,
										day_percent)
				if group_index < len(groups) - 1:
					draw.line((table_left, group_bottom - 1, table_right, group_bottom - 1), fill=colors["ink"], width=1)

		if hidden:
			self._draw_hidden_holdings_ticker(
				img,
				draw,
				box,
				[item["data"] for item in hidden],
				ticker_theme=ticker_theme,
				theme_context=theme_context,
			)

	@staticmethod
	def _holding_category(item):
		if item["is_cash"]:
			return "cash"
		return HOLDING_CATEGORY_BY_SYMBOL.get(str(item["data"].get("symbol") or "").upper(), "other")

	@classmethod
	def _holding_groups(cls, visible, metrics):
		"""The shown rows grouped by kind of investment: (kind, rows in their given order, the
		kind's share of the portfolio %, its day change % or None), the largest kind first and
		other then cash last. Shares and day count every holding of the kind, shown or not."""
		rows, totals = {}, {}
		for item in visible:
			rows.setdefault(cls._holding_category(item), []).append(item)
		for item in metrics:
			total = totals.setdefault(cls._holding_category(item), {"weight": 0.0, "pl": 0.0, "base": 0.0, "known": False})
			total["weight"] += item["weight"]
			if item["day_pl"] is not None and not item["is_cash"]:
				total["pl"] += item["day_pl"]
				total["base"] += item["value"] - item["day_pl"]
				total["known"] = True
		order = sorted(rows, key=lambda kind: (kind == "cash", kind == "other", -totals[kind]["weight"]))
		groups = []
		for kind in order:
			total = totals[kind]
			day = total["pl"] / total["base"] * 100 if total["known"] and total["base"] else None
			groups.append((kind, rows[kind], total["weight"], day))
		return groups

	def _draw_allocation_rule(self, draw, box, metrics):
		"""The rule under the column names, as the portfolio's mix: one segment per kind of
		investment in its colour, as wide as its share, in the table's group order."""
		colors = _active_stock_colors()
		left, top, right, bottom = box
		groups = self._holding_groups(metrics, metrics)
		total = sum(weight for _kind, _rows, weight, _day in groups)
		if total <= 0:
			draw.line((left, bottom, right, bottom), fill=colors["ink"], width=1)
			return
		x = float(left)
		for kind, _rows, weight, _day in groups:
			end = x + (right - left) * weight / total
			colour_key = HOLDING_CATEGORIES.get(kind, HOLDING_CATEGORIES["other"])[1]
			draw.rectangle((round(x), top, max(round(x), round(end) - 1), bottom), fill=colors[colour_key])
			x = end
		draw.line((left, bottom, right, bottom), fill=colors["ink"], width=1)

	def _draw_holding_rail(self, draw, box, category, weight, day_percent):
		"""A group's rail: a bar in its kind's colour beside the kind's name, then (room allowing)
		its share of the portfolio and its day change."""
		colors = _active_stock_colors()
		left, top, right, bottom = box
		label, colour_key = HOLDING_CATEGORIES.get(category, HOLDING_CATEGORIES["other"])
		draw.rectangle((left, top + 2, left + 4, bottom - 3), fill=colors[colour_key])
		text_left = left + 9
		name_font, figure_font = get_base_ui_font(11, bold=True), self._font(10)
		lines = [(label, name_font, None)]
		height = bottom - top
		if height >= 28:
			lines.append((f"{weight:.0f}%" if weight >= 9.5 else f"{weight:.1f}%", figure_font, None))
		if height >= 44 and day_percent is not None:
			lines.append((f"{abs(day_percent):.1f}%", figure_font, day_percent))
		line_h = 13
		y = (top + bottom) / 2 - line_h * (len(lines) - 1) / 2
		for text, font, change in lines:
			x = text_left
			if change is not None:
				self._draw_change_arrow(draw, x, y, change, size=6)
				x += 8
			text = text if self._text_width(draw, text, font) <= right - x else text[:2]
			draw.text((x, self._centered_text_y(draw, text, font, y)), text, fill=colors["ink"], font=font)
			y += line_h

	@staticmethod
	def _holding_column_positions(left, right):
		width = right - left
		positions = {"symbol": left + 18}
		for key, fraction in HOLDING_COLUMNS.items():
			if isinstance(fraction, tuple):
				positions[key] = (round(left + width * fraction[0]), round(left + width * fraction[1]))
			else:
				positions[key] = round(left + width * fraction)
		return positions

	def _draw_holdings_status(self, draw, anchor, metrics, tracking_window_label):
		"""Right side of the HOLDINGS title row: position count, today's breadth, window."""
		colors = _active_stock_colors()
		right, center_y = anchor
		font = self._font(11, True)
		equities = [item for item in metrics if not item["is_cash"]]
		up = sum(1 for item in equities if (item["day_percent"] or 0) > 0.005)
		down = sum(1 for item in equities if (item["day_percent"] or 0) < -0.005)
		parts = [(f"{len(equities)} POSITIONS", None), (f"{up} UP", 1.0), (f"{down} DOWN", -1.0)]
		if tracking_window_label:
			parts.append((tracking_window_label, None))
		x = right
		for index, (text, arrow) in enumerate(reversed(parts)):
			width = self._text_width(draw, text, font)
			x -= width
			draw.text((x, self._centered_text_y(draw, text, font, center_y)), text, fill=colors["ink"], font=font)
			if arrow is not None:
				x -= 12
				self._draw_change_arrow(draw, x, center_y, arrow, size=9)
			if index < len(parts) - 1:
				x -= 16
				draw.ellipse((x + 6, center_y - 1.5, x + 9, center_y + 1.5), fill=colors["ink"])

	def _draw_holding_row(self, img, draw, row_box, item, columns, max_weight):
		colors = _active_stock_colors()
		left, top, right, bottom = row_box
		data = item["data"]
		center_y = (top + bottom) / 2
		draw.line((left + 6, bottom - 1, right, bottom - 1), fill=colors["grid"], width=1)
		day_percent = item["day_percent"]
		indicator = colors["accent_blue"] if item["is_cash"] else self._change_color(day_percent or 0)
		draw.rectangle((left, top + 1, left + 3, bottom - 2), fill=indicator)

		logo_size = max(min(bottom - top - 2, 16), 10)
		# The marks share one slot between the day's colour bar and the symbol.
		logo = self._holding_logo_image(data.get("symbol"), logo_size, width=HOLDING_LOGO_SLOT_W)
		if logo:
			img.paste(logo, (left + 7, round(center_y - logo.height / 2)), logo)

		row_font = self._font(12)
		bold_font = self._font(12, True)
		symbol = str(data.get("symbol") or "")
		symbol_font = self._fit_font(draw, symbol, columns["price"] - columns["symbol"] - 62, 13, True, 9)

		def text_at(x, text, font, align="right"):
			if align == "right":
				x -= self._text_width(draw, text, font)
			draw.text((x, self._centered_text_y(draw, text, font, center_y)), text, fill=colors["ink"], font=font)

		text_at(columns["symbol"], symbol, symbol_font, align="left")
		if item["is_cash"]:
			text_at(columns["price"], data.get("price_text") or "USD", row_font)
			text_at(columns["shares"], "-", row_font)
		else:
			text_at(columns["price"], self._money(self._finite_float(data.get("price"))), row_font)
			text_at(columns["shares"], self._shares(self._finite_float(data.get("shares"))), row_font)
		text_at(columns["value"], self._money(item["value"]), bold_font)

		bar_left, bar_right = columns["weight_bar"]
		bar_top, bar_bottom = round(center_y - 3), round(center_y + 3)
		draw.rectangle((bar_left, bar_top, bar_right, bar_bottom), outline=colors["grid"], width=1)
		if max_weight > 0 and item["weight"] > 0:
			fill_right = bar_left + max(2, round((bar_right - bar_left) * item["weight"] / max_weight))
			draw.rectangle((bar_left, bar_top, fill_right, bar_bottom), fill=colors["ink"])
		text_at(columns["weight"], f"{item['weight']:.1f}%", row_font)

		if day_percent is None:
			text_at(columns["day"], "-", row_font)
			text_at(columns["day_pl"], "-", row_font)
		else:
			day_text = f"{day_percent:+.2f}%"
			day_width = self._text_width(draw, day_text, row_font)
			self._draw_change_arrow(draw, columns["day"] - day_width - 11, center_y, day_percent)
			text_at(columns["day"], day_text, row_font)
			text_at(columns["day_pl"], self._signed_money(item["day_pl"] or 0.0), row_font)

		trend_left, trend_right = columns["trend"]
		window_percent = item["window_percent"]
		if item["is_cash"] or window_percent is None:
			text_at(columns["window"], "-", row_font)
			return
		self._draw_row_sparkline(
			draw,
			(trend_left, top + 3, trend_right, bottom - 4),
			item["closes"],
			self._change_color(window_percent),
		)
		text_at(columns["window"], f"{window_percent:+.1f}%", bold_font)

	@staticmethod
	def _draw_row_sparkline(draw, box, closes, color):
		left, top, right, bottom = box
		if len(closes) < 2 or right <= left or bottom <= top:
			return
		low, high = min(closes), max(closes)
		span = high - low
		last = len(closes) - 1
		points = [
			(
				left + (right - left) * index / last,
				(top + bottom) / 2 if span < 1e-9 else bottom - (bottom - top) * (close - low) / span,
			)
			for index, close in enumerate(closes)
		]
		draw.line(points, fill=color, width=2, joint="curve")
		end_x, end_y = points[-1]
		draw.ellipse((end_x - 2, end_y - 2, end_x + 2, end_y + 2), fill=color)

	def _create_dashboard(
		self,
		stock_data,
		dimensions,
		history_points=None,
		tracking_window_label=None,
		tracking_period="1mo",
		holdings_pin_symbols=None,
		holdings_sink_symbols=None,
		account_value_override=None,
		header_brand=None,
		ticker_theme=None,
		theme_context=None,
		updated_at=None,
		portfolio_meta=None,
		holdings_order="smart",
	):
		"""Account summary and trend on top, every holding with its day and window movement below.

		``holdings_order`` "smart" ranks the rows by today's impact (see _smart_order); "value"
		keeps the pinned symbols first and the rest by value.
		"""
		colors = _stock_render_colors(theme_context)
		token = _ACTIVE_STOCK_COLORS.set(colors)
		try:
			width, height = dimensions
			margin = DASHBOARD_MARGIN
			img = Image.new("RGB", (width, height), colors["paper"])
			draw = ImageDraw.Draw(img)
			window_short = self._window_short_label(tracking_period)

			self._draw_header(img, draw, width, stock_data, header_brand=header_brand, updated_at=updated_at)

			total_value, _window_change, _window_percent = self._portfolio_totals(
				stock_data,
				account_value_override=account_value_override,
			)
			smart = str(holdings_order or "smart").strip().lower() != "value"
			if smart:
				metrics = self._smart_order([self._holding_metrics(data, total_value) for data in stock_data],
											total_value, holdings_pin_symbols, holdings_sink_symbols)
			else:
				ordered = self._ordered_holdings(stock_data, holdings_pin_symbols, holdings_sink_symbols)
				metrics = [self._holding_metrics(data, total_value) for data in ordered]
			day_change = sum(item["day_pl"] or 0.0 for item in metrics)
			day_base = total_value - day_change
			day_percent = self._finite_float(day_change / day_base * 100 if day_base else 0.0)
			all_live = all(
				item["data"].get("data_provider") == "robinhood_mcp"
				for item in metrics
				if not item["is_cash"]
			)

			card_top, card_bottom = TOP_CARDS
			summary_right = margin + SUMMARY_CARD_WIDTH
			curve_values, supplemental_history = self._portfolio_curve(
				stock_data,
				history_points,
				period=tracking_period,
				now=updated_at,
				account_value_override=account_value_override,
			)
			date_range = self._curve_date_range(
				stock_data,
				supplemental_history,
				account_value_override=account_value_override,
			)
			self._draw_summary(
				img,
				draw,
				(margin, card_top, summary_right, card_bottom),
				total_value,
				day_change,
				day_percent,
				day_label="TODAY" if all_live else "LAST SESSION",
				stats=self._summary_stats(metrics, portfolio_meta),
				curve_values=curve_values,
			)
			self._draw_sparkline(
				img,
				draw,
				(summary_right + 12, card_top, width - margin, card_bottom),
				curve_values,
				supplemental_history,
				history_markers_on_prefix=True,
				window_label=window_short,
				date_range=date_range,
				extremes=True,
			)
			self._draw_holdings(
				img,
				draw,
				(margin, HOLDINGS_TOP, width - margin, height - 10),
				metrics,
				tracking_window_label,
				ticker_theme,
				theme_context,
				window_short=window_short,
				sorted_by_day=smart,
				keep_symbols=self._symbols_setting(holdings_pin_symbols) if smart else None,
			)

			return img
		finally:
			_ACTIVE_STOCK_COLORS.reset(token)

	def _curve_date_range(self, stock_data, supplemental_history, account_value_override=None):
		series = self._portfolio_series(stock_data, account_value_override=account_value_override)
		dates = [self._series_date(point["date"]) for point in supplemental_history or []]
		dates.extend(self._series_date(date_key) for date_key, _value in series)
		dates = [value for value in dates if value is not None]
		if len(dates) < 2 or dates[0] == dates[-1]:
			return None
		return dates[0], dates[-1]

	@staticmethod
	def _source_label(stock_data):
		providers = {str(data.get("data_provider") or "yfinance") for data in stock_data}
		if "robinhood_mcp" in providers:
			return "Robinhood official real-time"
		if any(data.get("extended_hours") for data in stock_data):
			return "Yahoo realtime + extended hours"
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
			('robinhood_mcp', 'Robinhood official MCP (real-time account)'),
			('auto', 'Auto: Massive first, yfinance fallback'),
			('massive', 'Massive only'),
			('yfinance', 'yfinance only'),
		]

		# Ensure settings dict is included
		if 'settings' not in template_params:
			template_params['settings'] = {}

		return template_params
