# Stock Tracker 趋势图重做 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 PORTFOLIO TREND 面板改成"期初基准线 + 涨跌分色 + 单调平滑曲线"的业绩总结图（设计：`docs/superpowers/specs/2026-10-06-stock-trend-chart-design.md`）。

**Architecture:** 纯几何计算放进新模块 `plugins/stocktracker/trend_chart.py`（不依赖 PIL），由架构门禁限制依赖；`StockTracker._draw_sparkline` 保持签名不变，只负责画图。旧的 Catmull-Rom 平滑、固定角标和本地快照打点全部删除。

**Tech Stack:** Python 3.11（测试）/ 3.13（设备），Pillow，pytest 9。

## Global Constraints

- 面板框保持 `(304, 60, width-24, 204)`，标题字样 PORTFOLIO TREND 不变。
- 绘图区 `(left+16, top+40, right-16, bottom-12)`；内边距 `(plot[0]+8, plot[1]+6, plot[2]-10, plot[3]-6)`。
- 纵向余量 14%；数值完全持平时，跨度取 `max(abs(start) * 0.002, 1.0)`。
- 基准线：`muted` 色虚线，实线 5px、间隔 4px、线宽 1。曲线：线宽 3，每段细分 8。填充：与背景按 0.22 混合。
- 颜色只从 `_active_stock_colors()` 取（`malachite` 涨、`cinnabar` 跌、`muted` 基准），保证夜间主题正常。
- `stocktracker.py` 用 Tab 缩进；新模块和测试用 4 个空格。
- 测试命令统一为：`$env:INKYPI_PYTHON311='G:\PersonalProjects\EpaperSystem\.tmp\venvs\inkypi-release-311-secure-20260904\Scripts\python.exe'; tools/run_inkypi_tests.ps1 -PytestArgs @(...)`，在仓库根目录 `G:\PersonalProjects\EpaperSystem\.worktrees\claudeMain` 下运行。全量测试期间不改源码。
- 只提交到本地 main，不推送。

---

### Task 1: 纯几何模块 `trend_chart.py`

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/plugins/stocktracker/trend_chart.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_stocktracker_trend_chart.py`
- Modify: `tools/check_architecture.py`（`BOUNDARIES` 字典，`plugins/sports_dashboard/f1_domain.py` 条目之后）
- Modify: `inkypi-weather/package/InkyPi/tests/test_architecture_gate.py`

**Interfaces:**
- Produces:
  - `value_bounds(values: Sequence[float], padding_ratio: float = 0.14) -> tuple[float, float]`
  - `plot_points(box: tuple[float, float, float, float], values: Sequence[float], vmin: float, vmax: float) -> list[tuple[float, float]]`
  - `monotone_curve(points: Sequence[tuple[float, float]], subdivisions: int = 8) -> list[tuple[float, float]]`
  - `split_at_baseline(curve: Sequence[tuple[float, float]], base_y: float) -> list[tuple[list[tuple[float, float]], bool]]`（`True` 表示该段在基准线上方，即屏幕 y ≤ base_y）

- [ ] **Step 1: 写失败的测试** `tests/test_stocktracker_trend_chart.py`

```python
import pytest

from plugins.stocktracker.trend_chart import monotone_curve, plot_points, split_at_baseline, value_bounds


POINTS = [(0, 50), (10, 20), (20, 35), (30, 35), (40, 5), (50, 60)]


def test_monotone_curve_passes_through_every_point_and_keeps_endpoints():
    curve = monotone_curve(POINTS, subdivisions=8)

    assert len(curve) == (len(POINTS) - 1) * 8 + 1
    assert curve[0] == (0.0, 50.0)
    assert curve[-1] == (50.0, 60.0)
    for index, point in enumerate(POINTS):
        assert curve[index * 8] == pytest.approx(point)


def test_monotone_curve_never_leaves_each_segment_range():
    curve = monotone_curve(POINTS, subdivisions=8)

    for index in range(len(POINTS) - 1):
        low = min(POINTS[index][1], POINTS[index + 1][1])
        high = max(POINTS[index][1], POINTS[index + 1][1])
        for _x, y in curve[index * 8: index * 8 + 9]:
            assert low - 1e-9 <= y <= high + 1e-9


def test_monotone_curve_returns_short_input_unchanged():
    assert monotone_curve([(0, 1), (5, 2)]) == [(0.0, 1.0), (5.0, 2.0)]
    assert monotone_curve([]) == []


def test_split_at_baseline_cuts_exactly_on_the_baseline():
    runs = split_at_baseline([(0, 40), (10, 60), (20, 30), (30, 70)], 50)

    assert [above for _run, above in runs] == [True, False, True, False]
    for (first, _), (second, _) in zip(runs, runs[1:]):
        assert first[-1] == second[0]
        assert first[-1][1] == 50.0
    assert runs[0][0][0] == (0, 40)
    assert runs[0][0][-1] == pytest.approx((5.0, 50.0))
    assert runs[-1][0][-1] == (30, 70)


def test_split_at_baseline_drops_a_zero_length_run_at_the_start():
    assert split_at_baseline([(0, 50), (10, 70), (20, 80)], 50) == [([(0, 50), (10, 70), (20, 80)], False)]


def test_value_bounds_pads_the_range_and_handles_flat_series():
    low, high = value_bounds([100.0, 110.0])
    flat_low, flat_high = value_bounds([250000.0, 250000.0])
    single_low, single_high = value_bounds([5.0])

    assert (low, high) == (pytest.approx(98.6), pytest.approx(111.4))
    assert flat_low < 250000.0 < flat_high
    assert flat_high - flat_low == pytest.approx(250000 * 0.002 * 0.28)
    assert single_low < 5.0 < single_high


def test_plot_points_maps_values_into_the_box():
    assert plot_points((10, 20, 110, 120), [0.0, 50.0, 100.0], 0.0, 100.0) == [
        (10.0, 120.0),
        (60.0, 70.0),
        (110.0, 20.0),
    ]
```

- [ ] **Step 2: 运行，确认失败**

Run: `tools/run_inkypi_tests.ps1 -PytestArgs @('tests/test_stocktracker_trend_chart.py','-q')`
Expected: 收集错误 `ModuleNotFoundError: No module named 'plugins.stocktracker.trend_chart'`

- [ ] **Step 3: 实现** `src/plugins/stocktracker/trend_chart.py`

```python
"""Pure geometry for the Stock Tracker portfolio trend chart: no drawing, no plugin state."""

from __future__ import annotations

from collections.abc import Sequence

Point = tuple[float, float]


def value_bounds(values: Sequence[float], padding_ratio: float = 0.14) -> tuple[float, float]:
    """Pad the observed range so the line never touches the plot edge."""

    low = min(values)
    high = max(values)
    span = high - low
    if span < 1e-9:
        span = max(abs(values[0]) * 0.002, 1.0)
    return low - span * padding_ratio, high + span * padding_ratio


def plot_points(box: tuple[float, float, float, float], values: Sequence[float], vmin: float, vmax: float) -> list[Point]:
    left, top, right, bottom = box
    last = max(len(values) - 1, 1)
    return [
        (left + (right - left) * index / last, bottom - (bottom - top) * (float(value) - vmin) / (vmax - vmin))
        for index, value in enumerate(values)
    ]


def monotone_curve(points: Sequence[Point], subdivisions: int = 8) -> list[Point]:
    """Fritsch-Carlson monotone cubic through every point; no segment leaves its endpoints' range."""

    if len(points) < 3:
        return [(float(x), float(y)) for x, y in points]
    xs = [float(x) for x, _y in points]
    ys = [float(y) for _x, y in points]
    widths = [xs[index + 1] - xs[index] for index in range(len(xs) - 1)]
    slopes = [(ys[index + 1] - ys[index]) / width if width else 0.0 for index, width in enumerate(widths)]
    tangents = [slopes[0]] + [0.0] * (len(xs) - 2) + [slopes[-1]]
    for index in range(1, len(xs) - 1):
        before, after = slopes[index - 1], slopes[index]
        if before * after > 0:
            # Weighted harmonic mean keeps both Hermite tangents within 3x the secant.
            w1 = 2 * widths[index] + widths[index - 1]
            w2 = widths[index] + 2 * widths[index - 1]
            tangents[index] = (w1 + w2) / (w1 / before + w2 / after)
    curve = [(xs[0], ys[0])]
    for index, width in enumerate(widths):
        for step in range(1, subdivisions + 1):
            t = step / subdivisions
            y = ((1 + 2 * t) * (1 - t) ** 2 * ys[index]
                 + t * (1 - t) ** 2 * width * tangents[index]
                 + t * t * (3 - 2 * t) * ys[index + 1]
                 + t * t * (t - 1) * width * tangents[index + 1])
            curve.append((xs[index] + width * t, y))
    curve[-1] = (xs[-1], ys[-1])
    return curve


def split_at_baseline(curve: Sequence[Point], base_y: float) -> list[tuple[list[Point], bool]]:
    """Cut the curve where it crosses base_y; above means screen y <= base_y (value >= start)."""

    if not curve:
        return []
    runs: list[tuple[list[Point], bool]] = []
    run = [curve[0]]
    above = curve[0][1] <= base_y
    for start, end in zip(curve, curve[1:]):
        end_above = end[1] <= base_y
        if end_above != above:
            ratio = (base_y - start[1]) / (end[1] - start[1])
            cross = (start[0] + (end[0] - start[0]) * ratio, float(base_y))
            run.append(cross)
            if any(point != run[0] for point in run[1:]):
                runs.append((run, above))
            run = [cross]
            above = end_above
        run.append(end)
    if any(point != run[0] for point in run[1:]):
        runs.append((run, above))
    return runs
```

注意：`curve[index * 8]` 在 Step 1 里要求精确等于原始点，Hermite 公式在 t=1 时得到的就是 `ys[index + 1]`（浮点下可能有 1e-15 误差，测试用 `pytest.approx`；最后一个点强制写回原值，保证 `curve[-1]` 精确相等）。

- [ ] **Step 4: 运行，确认通过**

Run: `tools/run_inkypi_tests.ps1 -PytestArgs @('tests/test_stocktracker_trend_chart.py','-q')`
Expected: `7 passed`

- [ ] **Step 5: 加架构边界并测试**

`tools/check_architecture.py` 的 `BOUNDARIES` 中，在 `"plugins/sports_dashboard/f1_domain.py": {...},` 之后加入：

```python
    "plugins/stocktracker/trend_chart.py": {
        "__future__", "collections.abc",
    },
```

`tests/test_architecture_gate.py` 中 f1_domain 的三条断言之后加入：

```python
    assert gate.check_source("from PIL import Image\n", "plugins/stocktracker/trend_chart.py")
    assert not gate.check_source("from collections.abc import Sequence\n", "plugins/stocktracker/trend_chart.py")
```

Run: `tools/run_inkypi_tests.ps1 -PytestArgs @('tests/test_architecture_gate.py','tests/test_stocktracker_trend_chart.py','-q')` → 全部通过；
Run: `& $env:INKYPI_PYTHON311 tools/check_architecture.py` → `0 violations`

- [ ] **Step 6: 提交**

```bash
git add inkypi-weather/package/InkyPi/src/plugins/stocktracker/trend_chart.py inkypi-weather/package/InkyPi/tests/test_stocktracker_trend_chart.py tools/check_architecture.py inkypi-weather/package/InkyPi/tests/test_architecture_gate.py
git commit -m "feat(stocktracker): add monotone trend geometry with a baseline split"
```

---

### Task 2: 用新几何重画趋势面板

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/plugins/stocktracker/stocktracker.py`（导入区；`_draw_sparkline` 及其后的图表辅助函数，约 1445–1670 行）
- Modify: `inkypi-weather/package/InkyPi/tests/test_stocktracker.py`

**Interfaces:**
- Consumes: Task 1 的 `value_bounds`、`plot_points`、`monotone_curve`、`split_at_baseline`
- Produces: `StockTracker._draw_sparkline(img, draw, box, values, history_points=None, history_markers_on_prefix=False)`（签名不变）；新的私有方法 `_fill_against_baseline`、`_draw_dashed_line`、`_draw_baseline_label`

- [ ] **Step 1: 写失败的渲染测试**（加到 `tests/test_stocktracker.py`，并在文件顶部导入区加 `from PIL import Image, ImageDraw  # noqa: E402`）

```python
TREND_BOX = (304, 60, 776, 204)
DIP_THEN_RISE = [100.0, 96.0, 92.0, 95.0, 101.0, 106.0, 104.0, 109.0, 112.0]


def _trend_panel(values, theme_context=None):
    plugin = StockTracker({"id": "stocktracker"})
    colors = stocktracker_module._stock_render_colors(theme_context)
    token = stocktracker_module._ACTIVE_STOCK_COLORS.set(colors)
    try:
        image = Image.new("RGB", (800, 480), colors["paper"])
        plugin._draw_sparkline(image, ImageDraw.Draw(image), TREND_BOX, values)
    finally:
        stocktracker_module._ACTIVE_STOCK_COLORS.reset(token)
    return image, colors


def test_trend_panel_splits_gain_and_loss_around_the_start_baseline():
    image, colors = _trend_panel(DIP_THEN_RISE)
    # Plot inner box (328, 106, 750, 186); the start value 100 sits at y=152.
    dip_below = (332, 155, 520, 188)
    rise_above = (560, 104, 748, 150)

    assert _region_near_color_count(image, colors["cinnabar"], dip_below, tolerance=12) > 80
    assert _region_near_color_count(image, colors["malachite"], dip_below, tolerance=12) == 0
    assert _region_near_color_count(image, colors["malachite"], rise_above, tolerance=12) > 300
    assert _region_near_color_count(image, colors["muted"], (330, 152, 740, 153), tolerance=6) > 120
    assert image.getpixel((750, 115)) == colors["malachite"]


def test_trend_panel_ending_below_start_is_mostly_loss_colored():
    image, colors = _trend_panel([100.0, 103.0, 99.0, 95.0, 92.0, 90.0, 88.0])
    plot = (320, 100, 760, 192)

    assert _region_near_color_count(image, colors["cinnabar"], plot, tolerance=12) > _region_near_color_count(
        image, colors["malachite"], plot, tolerance=12
    )
    assert image.getpixel((750, 177)) == colors["cinnabar"]


def test_trend_panel_handles_two_points_flat_series_and_night_theme():
    _trend_panel([5000.0, 5100.0])
    flat, flat_colors = _trend_panel([5000.0, 5000.0, 5000.0])
    night, night_colors = _trend_panel(DIP_THEN_RISE, _canonical_theme("night"))

    assert flat.getpixel((750, 146)) == flat_colors["malachite"]
    assert night.getpixel((750, 115)) == night_colors["malachite"]
    assert night_colors["malachite"] != stocktracker_module.MALACHITE
```

坐标推导（写给实现者核对）：绘图区 `(320, 100, 760, 192)`，内框 `(328, 106, 750, 186)`。DIP_THEN_RISE 的范围 92–112，余量 2.8 → `vmin=89.2, vmax=114.8`；期初 100 → `y = 186 - 80 × 10.8 / 25.6 = 152.25`；终点 112 → `y = 114.75`，取整 115。下跌序列 88–103：`vmin=85.9, vmax=105.1`，终点 88 → `y = 186 - 80 × 2.1 / 19.2 = 177.25`，取整 177。持平序列：期初、终点都在 `y = 146`。

- [ ] **Step 2: 运行，确认失败**

Run: `tools/run_inkypi_tests.ps1 -PytestArgs @('tests/test_stocktracker.py','-q','-k','trend_panel')`
Expected: 3 个测试失败（旧图没有基准虚线，开头下探部分也是绿色）

- [ ] **Step 3: 实现**

在 `stocktracker.py` 导入区 `from utils.theme_utils import get_theme_palette` 之后加：

```python
from plugins.stocktracker import trend_chart
```

把 `_draw_sparkline` 整个替换为下面的版本，并在它后面加三个辅助方法：

```python
	def _draw_sparkline(
		self,
		img,
		draw,
		box,
		values,
		history_points=None,
		history_markers_on_prefix=False,
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
		self._draw_latest_value_marker(
			draw,
			(round(points[-1][0]), round(points[-1][1])),
			gain if ended_up else loss,
		)
		self._draw_baseline_label(draw, plot, base_y, self._money(start, 0), chart_bg, below=ended_up)

	def _fill_against_baseline(self, img, plot, curve, base_y, chart_bg):
		"""Tint the area between the curve and the start baseline: gain above, loss below."""
		colors = _active_stock_colors()
		area = Image.new("L", img.size, 0)
		ImageDraw.Draw(area).polygon(
			[(curve[0][0], base_y), *curve, (curve[-1][0], base_y)],
			fill=255,
		)
		split = int(round(base_y))
		for color, region in (
			(colors["malachite"], (plot[0], plot[1], plot[2], split)),
			(colors["cinnabar"], (plot[0], split, plot[2], plot[3])),
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
		draw.rounded_rectangle((x - 3, y - 1, x + width + 3, y + 14), radius=3, fill=chart_bg)
		draw.text((x, y), text, fill=colors["muted"], font=font)
```

然后删除不再使用的方法：`_chart_value_bounds`、`_smooth_curve_points`、`_draw_chart_label`、`_plot_series_points`、`_sample_curve_points`、`_history_marker_points`、`_draw_history_markers`。保留 `_draw_latest_value_marker`。`math` 仍被其他函数使用，不删导入。

在 `tests/test_stocktracker.py` 删除三个只测旧实现的测试：`test_stock_tracker_chart_bounds_and_smoothing_keep_endpoints`、`test_stock_tracker_history_markers_decorate_portfolio_curve_coordinates`、`test_stock_tracker_supplemental_history_markers_stay_on_curve_prefix`；如果 `ACCENT_ORANGE` 因此不再被使用，从导入列表中去掉。

- [ ] **Step 4: 运行，确认通过**

Run: `tools/run_inkypi_tests.ps1 -PytestArgs @('tests/test_stocktracker.py','tests/test_stocktracker_trend_chart.py','-q')`
Expected: 全部通过。若 `test_stock_dashboard_uses_color_theme_and_us_change_colors` 的红色像素数因不再打点而低于 500，检查实际数量：该测试的组合曲线 `[4500, 4900, 4700, 4760, 4736, 4735]` 全程高于期初，图里本就不该有红色，红色只来自持仓表里 TSLA 的行；按实际数量把阈值改为该数量的一半，并在断言旁注明红色来自持仓表。

- [ ] **Step 5: 渲染完整仪表盘，人工看一眼**

Run（仓库根目录下）：

```powershell
& $env:INKYPI_PYTHON311 G:\PersonalProjects\EpaperSystem\outputs\stock-trend-design-20261006\mockup.py
```

`mockup.py` 中 `"current"` 变体此时就是新实现：打开 `trend-comparison.png`，`current/up` 那一行应和 `A_monotone/up` 一致（基准虚线、红绿分色、右侧期初金额）。

- [ ] **Step 6: 门禁并提交**

Run: `& $env:INKYPI_PYTHON311 -m ruff check inkypi-weather/package/InkyPi/src/plugins/stocktracker inkypi-weather/package/InkyPi/tests/test_stocktracker.py inkypi-weather/package/InkyPi/tests/test_stocktracker_trend_chart.py` → `All checks passed!`
Run: `& $env:INKYPI_PYTHON311 tools/check_architecture.py` → `0 violations`

```bash
git add inkypi-weather/package/InkyPi/src/plugins/stocktracker/stocktracker.py inkypi-weather/package/InkyPi/tests/test_stocktracker.py
git commit -m "feat(stocktracker): draw the trend against a start baseline in gain/loss colors"
```

---

### Task 3: 全量测试、部署和设备验证

**Files:**
- Create: `outputs/stock-trend-20261006/`（从 `outputs/steam-btd-20261006/` 复制 `release_prepare.py`、`preflight.py`、`deploy.ps1`、`acceptance.py`、`backup_rotate.ps1`）

- [ ] **Step 1: 全量测试**

Run: `tools/run_inkypi_tests.ps1 -PytestArgs @('tests','-q','--no-header','-p','no:warnings','-o','faulthandler_timeout=300')`（约 13 分钟，期间不改源码）
Expected: 0 failed。`test_install_update` 若出现 WinError 5，单独重跑该文件确认。

- [ ] **Step 2: 准备部署脚本**

- `release_prepare.py`：`BASE_COMMIT = '5c273f4fcca0e31d5ce95d2fc2c898c610c9b629'`，`BASE_RELEASE = 'deploy-20261006T180329Z-steam-btd-5c273f4f'`，`BASE_MANIFEST = OUT / 'base-5c273f4f-manifest.json'`（复制 `outputs/steam-btd-20261006/current-manifest.json`）；`EXPECTED_CHANGES` 设为 `git diff --name-only 5c273f4f HEAD -- inkypi-weather/package/InkyPi` 的结果（去掉包前缀），应包含 `src/plugins/stocktracker/stocktracker.py`、`src/plugins/stocktracker/trend_chart.py`、`tests/conftest.py`、`tests/tmp_cleanup.py`、`tests/test_tmp_cleanup.py`、`tests/test_stocktracker.py`、`tests/test_stocktracker_trend_chart.py`、`tests/test_architecture_gate.py`。
- `deploy.ps1`：release 后缀改为 `'-stock-trend-'`。
- 备份：Windows 备份要换成 5c273f4f。上一轮的 transport zip 已删除，复制 `outputs/audit-fixes-20261006/backup_prepare.py`（它从提交重新打包并逐成员校验），改这些常量：`COMMIT = '5c273f4fcca0e31d5ce95d2fc2c898c610c9b629'`、`RELEASE = 'deploy-20261006T180329Z-steam-btd-5c273f4f'`、`EXPECTED_ARCHIVE_SHA = 'd738ab229f52cd05b901ae306e2f8fde1d4ce1dfa41fb6f84ffa8d172c7ceba4'`、`BASE_MANIFEST = OUT / 'base-5c273f4f-manifest.json'`、`ARTIFACT = OUT / 'baseline-5c273f4f.zip'`，文件数断言 `== 1556` 改为 `== 1557`；`backup_identity()` 里期望的现有备份提交改为 `'d7c591155fac80bcf7c672c6c968dc6893cafa90'`；草稿清单的 `artifact` 改为 `PREVIOUS / 'inkypi-5c273f4f.zip'`，`activation_condition` 改为 `'Only after the stock-trend release passes deployment acceptance'`。运行后 `reproduces_original_zip_sha256` 必须为 `true`。

- [ ] **Step 3: 基线、构建、预检、部署**

```powershell
& $env:INKYPI_PYTHON311 outputs/stock-trend-20261006/release_prepare.py baseline
& $env:INKYPI_PYTHON311 outputs/stock-trend-20261006/release_prepare.py build
& $env:INKYPI_PYTHON311 outputs/stock-trend-20261006/preflight.py
& outputs/stock-trend-20261006/deploy.ps1 -Execute
```

Expected: baseline 1557 个文件哈希一致；build 的 `changed_files` 等于 `EXPECTED_CHANGES`；deploy 输出 `InkyPi release committed`。

- [ ] **Step 4: 用设备上的真实数据看实际画面**

部署后跑 `acceptance.py --label postdeploy`（readyz=ready、源码哈希一致）。然后用 3 次自然写屏的验收：`acceptance.py --watch 3 --timeout 2400`，看 `acceptance-journal.json` 里 `app_errors_by_plugin` 有没有 `stocktracker`。Stock Tracker 轮播到时，`acceptance-latest.png` 会记录真实画面；如果 3 次写屏里没轮到它，延长观察到它出现一次为止（只读，不强制刷新），截取趋势面板发给用户。

- [ ] **Step 5: 收尾**

验收通过后：运行 `backup_rotate.ps1 -Execute`（备份换成 5c273f4f），删除本轮 transport zip，只保留清单、日志和截图；更新记忆文件 `claudemain-branch.md`。不推送。
