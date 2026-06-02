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

MPLCONFIGDIR = os.path.join(os.path.dirname(__file__), "_mplconfig")
os.environ.setdefault("MPLCONFIGDIR", MPLCONFIGDIR)
os.makedirs(os.environ["MPLCONFIGDIR"], exist_ok=True)

from PIL import Image, ImageDraw, ImageFont
from utils.app_utils import get_font
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


class StockTracker(BasePlugin):

	"""Stock portfolio tracker plugin for InkyPi"""

	# Constants for improved code readability
	CARD_HEIGHT_RATIO = 0.22
	CARD_WIDTH_RATIO = 0.42
	CHART_MARGIN_RATIO = 0.15
	DEFAULT_TICKER_NAME_MAX_LENGTH = 25

	def generate_image(self, settings, device_config):

		"""Generate stock portfolio dashboard"""
		dimensions = device_config.get_resolution()
		if device_config.get_config("orientation") == "vertical":
			dimensions = dimensions[::-1]

		period, holdings = self._portfolio_holdings_from_settings(settings)

		# Fetch stock data
		stock_data = []

		for ticker, share_count in holdings:
			try:
				data = self._fetch_stock_data(ticker, share_count, period)
				if data:
					stock_data.append(data)
			except Exception as e:
				raise RuntimeError(f"Error fetching {ticker}: {str(e)}")

		if not stock_data:
			raise RuntimeError("No valid stock data retrieved")

		# Create dashboard
		history_points = self._record_portfolio_snapshot(stock_data)
		return self._create_dashboard(stock_data, dimensions, history_points)

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

	def _fetch_stock_data(self, ticker, shares, period):

		"""Fetch stock data using yfinance with proper error handling"""

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
			}
		except Exception as e:
			# Log detailed error information for debugging
			logging.error(f"Failed to fetch data for {ticker}: {type(e).__name__}: {e}", exc_info=True)
			# Re-raise the original exception so that callers can handle or wrap it as needed
			raise

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
					return ImageFont.truetype(font_path, font_size)
			font = get_font("Jost", font_size, "bold" if bold else "normal")
			if font:
				return font
		except Exception as e:
			logging.warning(f"Falling back to default font: {e}")
		return ImageFont.load_default()

	@staticmethod
	def _text_width(draw, text, font):
		bbox = draw.textbbox((0, 0), str(text), font=font)
		return bbox[2] - bbox[0]

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
	def _threshold_image(img):
		return img.convert("L").point(lambda p: 255 if p >= 128 else 0, mode="1").convert("RGB")

	def _portfolio_values(self, stock_data):
		dates = list(stock_data[0]["history"].index)
		values = []
		for date in dates:
			total = 0
			for data in stock_data:
				if date in data["history"].index:
					total += float(data["history"].loc[date, "Close"]) * data["shares"]
			values.append(total)
		return values

	def _record_portfolio_snapshot(self, stock_data, now=None):
		now = now or datetime.now()
		snapshot = self._portfolio_snapshot(stock_data, now)
		history_path = self._portfolio_history_path(stock_data)
		history = self._read_portfolio_history(history_path)
		history = self._upsert_portfolio_snapshot(history, snapshot)
		try:
			self._write_portfolio_history(history_path, history)
		except Exception as e:
			logging.warning(f"Could not write stock portfolio history: {type(e).__name__}: {e}")
		return history

	def _portfolio_totals(self, stock_data):
		total_value = sum(self._finite_float(data.get("total_value")) for data in stock_data)
		total_change = sum(self._finite_float(data.get("total_change")) for data in stock_data)
		base_value = total_value - total_change
		total_change_percent = (total_change / base_value) * 100 if base_value else 0
		total_change_percent = self._finite_float(total_change_percent)
		return total_value, total_change, total_change_percent

	def _portfolio_snapshot(self, stock_data, now):
		total_value, total_change, total_change_percent = self._portfolio_totals(stock_data)
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
			return os.path.abspath(explicit_file)

		history_dir = os.getenv(PORTFOLIO_HISTORY_DIR_ENV)
		if not history_dir:
			history_dir = os.path.join(os.path.dirname(__file__), ".stocktracker_history")
		history_key = self._portfolio_history_key(stock_data)
		return os.path.abspath(os.path.join(history_dir, f"{history_key}.json"))

	@staticmethod
	def _portfolio_history_key(stock_data):
		holdings = [
			{
				"symbol": str(data.get("symbol", "")).upper(),
				"shares": round(float(data.get("shares", 0)), 6),
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
		if percent > 0.005:
			return MALACHITE
		if percent < -0.005:
			return CINNABAR
		return MUTED

	def _draw_box(self, draw, box, title=None, accent=ACCENT_BLUE, fill=PANEL):
		left, top, right, bottom = [int(v) for v in box]
		draw.rectangle((left + 3, top + 4, right + 3, bottom + 4), fill=ACCENT_ORANGE)
		draw.rectangle((left, top, right, bottom), fill=fill)
		draw.rectangle((left, top, right, bottom), outline=BORDER, width=2)
		draw.rectangle((left, top, right, top + 6), fill=accent)
		if title:
			draw.text((left + 12, top + 12), title, fill=INK, font=self._font(16, True))

	def _draw_summary(self, draw, box, stock_data):
		left, top, right, bottom = box
		self._draw_box(draw, box, "PORTFOLIO", accent=ACCENT_GOLD, fill=PANEL_GOLD)
		total_value, total_change, total_change_percent = self._portfolio_totals(stock_data)
		change_color = self._change_color(total_change_percent)

		value_text = self._money(total_value)
		value_font = self._fit_font(draw, value_text, right - left - 28, 36, True, 20)
		draw.text((left + 14, top + 44), value_text, fill=INK, font=value_font)

		change_text = self._change_text(total_change, total_change_percent)
		change_font = self._fit_font(draw, change_text, right - left - 32, 18, True, 12)
		pill = (left + 12, top + 96, right - 12, top + 124)
		draw.rounded_rectangle(
			pill,
			radius=8,
			fill=self._blend(change_color, PANEL_GOLD, 0.12),
			outline=change_color,
			width=2,
		)
		draw.text((left + 24, top + 100), change_text, fill=change_color, font=change_font)
		draw.text((left + 14, bottom - 18), datetime.now().strftime("Updated %H:%M"), fill=MUTED, font=self._font(10))

	def _draw_sparkline(self, draw, box, values, history_points=None):
		left, top, right, bottom = box
		self._draw_box(draw, box, "PORTFOLIO TREND", accent=ACCENT_BLUE, fill=PANEL_BLUE)
		plot = (left + 14, top + 42, right - 14, bottom - 16)
		draw.rectangle(plot, fill=(244, 250, 255), outline=GRID, width=1)
		for i in range(1, 4):
			y = plot[1] + (plot[3] - plot[1]) * i / 4
			draw.line((plot[0] + 1, y, plot[2] - 1, y), fill=self._blend(GRID, PANEL_BLUE, 0.55), width=1)
		history_points = [
			point
			for point in (self._normalize_portfolio_history_entry(item) for item in (history_points or []))
			if point
		]
		history_values = [float(point["value"]) for point in history_points]
		if len(values) < 2 and len(history_values) < 1:
			return

		all_values = [float(value) for value in values] + history_values
		vmin = min(all_values)
		vmax = max(all_values)
		if abs(vmax - vmin) < 0.0001:
			vmax = vmin + 1

		points = []
		for idx, value in enumerate(values):
			x = plot[0] + (plot[2] - plot[0]) * idx / max(len(values) - 1, 1)
			y = plot[3] - (plot[3] - plot[1]) * (value - vmin) / (vmax - vmin)
			points.append((int(x), int(y)))

		area = [(plot[0], plot[3])] + points + [(plot[2], plot[3])]
		if len(points) >= 2:
			draw.polygon(area, fill=self._blend(ACCENT_BLUE, (244, 250, 255), 0.16))
			draw.line(points, fill=ACCENT_BLUE, width=3)
		self._draw_history_markers(draw, plot, history_points, vmin, vmax)
		if len(points) >= 2:
			self._draw_latest_value_marker(draw, points[-1])

		label_font = self._font(12)
		draw.text((plot[0] + 4, plot[1] + 4), self._money(vmax, 0), fill=INK, font=label_font)
		draw.text((plot[0] + 4, plot[3] - 16), self._money(vmin, 0), fill=MUTED, font=label_font)

	def _draw_latest_value_marker(self, draw, point):
		draw.ellipse((point[0] - 3, point[1] - 3, point[0] + 3, point[1] + 3), fill=INK)
		draw.ellipse((point[0] - 2, point[1] - 2, point[0] + 2, point[1] + 2), fill=ACCENT_BLUE)

	def _draw_history_markers(self, draw, plot, history_points, vmin, vmax):
		if not history_points:
			return
		radius = 4 if len(history_points) <= 36 else 3
		ordered_points = sorted(history_points, key=lambda point: point["date"])
		previous_value = None
		for idx, point in enumerate(ordered_points):
			value = float(point["value"])
			if len(ordered_points) == 1:
				x = plot[2]
			else:
				x = plot[0] + (plot[2] - plot[0]) * idx / (len(ordered_points) - 1)
			y = plot[3] - (plot[3] - plot[1]) * (value - vmin) / (vmax - vmin)
			if previous_value is None:
				fill = ACCENT_ORANGE
			else:
				fill = MALACHITE if value >= previous_value else CINNABAR
			previous_value = value
			x = int(round(x))
			y = int(round(y))
			draw.ellipse((x - radius - 1, y - radius - 1, x + radius + 1, y + radius + 1), fill=INK)
			draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)

	def _draw_holdings(self, draw, box, stock_data):
		left, top, right, bottom = box
		self._draw_box(draw, box, "HOLDINGS", accent=MALACHITE, fill=PANEL)
		header_font = self._font(12, True)
		row_font = self._font(13)
		symbol_font = self._font(15, True)
		y = top + 40
		cols = {
			"symbol": left + 28,
			"price": left + 150,
			"shares": left + 280,
			"value": left + 400,
			"change": left + 600,
		}
		for label, x in [("SYMBOL", cols["symbol"]), ("PRICE", cols["price"]), ("SHARES", cols["shares"]), ("VALUE", cols["value"]), ("CHANGE", cols["change"])]:
			draw.text((x, y), label, fill=MUTED, font=header_font)
		y += 22
		draw.line((left + 12, y, right - 12, y), fill=GRID, width=1)
		y += 8

		max_rows = min(len(stock_data), 6)
		displayed_rows = 0
		for idx, data in enumerate(sorted(stock_data, key=lambda item: item["total_value"], reverse=True)[:max_rows]):
			if y + 18 > bottom - 26:
				break
			row_bg = ROW_COLORS[idx % len(ROW_COLORS)]
			draw.rounded_rectangle((left + 10, y - 4, right - 10, y + 18), radius=5, fill=row_bg)
			change_color = self._change_color(data["change_percent"])
			draw.rounded_rectangle((left + 5, y - 3, left + 10, y + 17), radius=3, fill=change_color)
			row_items = [
				(cols["symbol"], data["symbol"], symbol_font, INK),
				(cols["price"], self._money(data["price"]), row_font, INK),
				(cols["shares"], self._shares(data["shares"]), row_font, INK),
				(cols["value"], self._money(data["total_value"]), row_font, INK),
				(cols["change"], f"{data['change_percent']:+.2f}%", row_font, change_color),
			]
			for x, text, font, fill in row_items:
				draw.text((x, y), text, fill=fill, font=font)
			y += 20
			draw.line((left + 12, y, right - 12, y), fill=GRID, width=1)
			y += 2
			displayed_rows += 1

		if len(stock_data) > displayed_rows:
			remaining = len(stock_data) - displayed_rows
			draw.text((left + 14, bottom - 26), f"+{remaining} more holdings", fill=MUTED, font=self._font(13, True))

	def _create_dashboard(self, stock_data, dimensions, history_points=None):
		"""Create a color dashboard that preserves the original stock layout."""
		width, height = dimensions
		img = Image.new("RGB", (width, height), PAPER)
		draw = ImageDraw.Draw(img)

		draw.rectangle((0, 0, width, 54), fill=PAPER)
		draw.text((24, 12), "STOCK TRACKER", fill=INK, font=self._font(24, True))
		source_label = "Yahoo realtime + extended hours" if any(data.get("extended_hours") for data in stock_data) else "Yahoo Finance data"
		draw.text((width - 24, 18), f"{source_label}  |  COLOR E-PAPER MODE", fill=MUTED, font=self._font(13, True), anchor="ra")
		draw.line((24, 46, width - 24, 46), fill=ACCENT_GOLD, width=3)

		self._draw_summary(draw, (24, 60, 284, 204), stock_data)
		self._draw_sparkline(draw, (304, 60, width - 24, 204), self._portfolio_values(stock_data), history_points)
		self._draw_holdings(draw, (24, 224, width - 24, height - 24), stock_data)

		return img

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

		# Ensure settings dict is included
		if 'settings' not in template_params:
			template_params['settings'] = {}

		return template_params
