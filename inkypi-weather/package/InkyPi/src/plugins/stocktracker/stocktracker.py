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
import io
import logging

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

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

		# Parse tickers and shares from settings
		try:
			tickers_str = settings.get('tickers', '').strip()
			shares_str = settings.get('shares', '').strip()
			period = settings.get('period', '1mo')

			if not tickers_str or not shares_str:
				raise RuntimeError("Please provide both tickers and shares")

			# Parse comma-separated values
			tickers = [t.strip().upper() for t in tickers_str.split(',')]
			shares = [float(s.strip()) for s in shares_str.split(',')]

			if len(tickers) != len(shares):
				raise RuntimeError("Number of tickers and shares must match")

		except ValueError as e:
			raise RuntimeError(f"Invalid input format: {str(e)}")

		# Fetch stock data
		stock_data = []

		for ticker, share_count in zip(tickers, shares):
			try:
				data = self._fetch_stock_data(ticker, share_count, period)
				if data:
					stock_data.append(data)
			except Exception as e:
				raise RuntimeError(f"Error fetching {ticker}: {str(e)}")

		if not stock_data:
			raise RuntimeError("No valid stock data retrieved")

		# Create dashboard
		return self._create_dashboard(stock_data, dimensions)

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

			current_price = hist['Close'].iloc[-1]
			prev_price = hist['Close'].iloc[0]
			change = current_price - prev_price
			change_percent = (change / prev_price) * 100 if prev_price != 0 else 0

			return {
				'symbol': ticker,
				'name': info.get('shortName', ticker),
				'price': current_price,
				'change': change,
				'change_percent': change_percent,
				'shares': shares,
				'total_value': current_price * shares,
				'total_change': change * shares,
				'history': hist
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

	def _draw_box(self, draw, box, title=None):
		draw.rectangle(box, outline=WHITE, width=2)
		if title:
			draw.text((box[0] + 12, box[1] + 8), title, fill=WHITE, font=self._font(16, True))

	def _draw_sparkline(self, draw, box, values):
		left, top, right, bottom = box
		self._draw_box(draw, box, "PORTFOLIO TREND")
		plot = (left + 14, top + 42, right - 14, bottom - 16)
		draw.rectangle(plot, outline=WHITE, width=1)
		if len(values) < 2:
			return

		vmin = min(values)
		vmax = max(values)
		if abs(vmax - vmin) < 0.0001:
			vmax = vmin + 1

		points = []
		for idx, value in enumerate(values):
			x = plot[0] + (plot[2] - plot[0]) * idx / max(len(values) - 1, 1)
			y = plot[3] - (plot[3] - plot[1]) * (value - vmin) / (vmax - vmin)
			points.append((int(x), int(y)))

		if len(points) >= 2:
			draw.line(points, fill=WHITE, width=3)
		for point in points[-6:]:
			draw.ellipse((point[0] - 2, point[1] - 2, point[0] + 2, point[1] + 2), fill=WHITE)

		label_font = self._font(12)
		draw.text((plot[0] + 4, plot[1] + 4), self._money(vmax, 0), fill=WHITE, font=label_font)
		draw.text((plot[0] + 4, plot[3] - 16), self._money(vmin, 0), fill=WHITE, font=label_font)

	def _draw_summary(self, draw, box, stock_data):
		left, top, right, bottom = box
		self._draw_box(draw, box, "PORTFOLIO")
		total_value = sum(data["total_value"] for data in stock_data)
		total_change = sum(data["total_change"] for data in stock_data)
		base_value = total_value - total_change
		total_change_percent = (total_change / base_value) * 100 if base_value else 0

		value_text = self._money(total_value)
		value_font = self._fit_font(draw, value_text, right - left - 28, 36, True, 20)
		draw.text((left + 14, top + 44), value_text, fill=WHITE, font=value_font)

		change_text = self._change_text(total_change, total_change_percent)
		change_font = self._fit_font(draw, change_text, right - left - 28, 18, True, 12)
		draw.text((left + 14, top + 100), change_text, fill=WHITE, font=change_font)
		draw.text((left + 14, bottom - 28), "Yahoo Finance data", fill=WHITE, font=self._font(13))

	def _draw_holdings(self, draw, box, stock_data):
		left, top, right, bottom = box
		self._draw_box(draw, box, "HOLDINGS")
		header_font = self._font(13, True)
		row_font = self._font(15)
		symbol_font = self._font(17, True)
		y = top + 42
		cols = {
			"symbol": left + 14,
			"price": left + 150,
			"shares": left + 280,
			"value": left + 400,
			"change": left + 600,
		}
		for label, x in [("SYMBOL", cols["symbol"]), ("PRICE", cols["price"]), ("SHARES", cols["shares"]), ("VALUE", cols["value"]), ("CHANGE", cols["change"])]:
			draw.text((x, y), label, fill=WHITE, font=header_font)
		y += 24
		draw.line((left + 12, y, right - 12, y), fill=WHITE, width=1)
		y += 10

		total_value = sum(data["total_value"] for data in stock_data) or 1
		max_rows = min(len(stock_data), 6)
		for data in sorted(stock_data, key=lambda item: item["total_value"], reverse=True)[:max_rows]:
			if y + 24 > bottom - 12:
				break
			row_items = [
				(cols["symbol"], data["symbol"], symbol_font),
				(cols["price"], self._money(data["price"]), row_font),
				(cols["shares"], self._shares(data["shares"]), row_font),
				(cols["value"], self._money(data["total_value"]), row_font),
				(cols["change"], f"{data['change_percent']:+.2f}%", row_font),
			]
			for x, text, font in row_items:
				draw.text((x, y), text, fill=WHITE, font=font)
			y += 27
			draw.line((left + 12, y, right - 12, y), fill=WHITE, width=1)
			y += 7

		if len(stock_data) > max_rows:
			remaining = len(stock_data) - max_rows
			draw.text((left + 14, bottom - 26), f"+{remaining} more holdings", fill=WHITE, font=self._font(13, True))

	def _create_dashboard(self, stock_data, dimensions):
		"""Create a monochrome dashboard optimized for 800x480 e-paper."""
		width, height = dimensions
		img = Image.new("RGB", (width, height), BLACK)
		draw = ImageDraw.Draw(img)

		draw.text((24, 12), "STOCK TRACKER", fill=WHITE, font=self._font(24, True))
		draw.text((width - 24, 18), "B/W E-PAPER MODE", fill=WHITE, font=self._font(13, True), anchor="ra")
		draw.line((24, 46, width - 24, 46), fill=WHITE, width=2)

		self._draw_summary(draw, (24, 60, 284, 204), stock_data)
		self._draw_sparkline(draw, (304, 60, width - 24, 204), self._portfolio_values(stock_data))
		self._draw_holdings(draw, (24, 224, width - 24, height - 24), stock_data)

		return self._threshold_image(img)

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
