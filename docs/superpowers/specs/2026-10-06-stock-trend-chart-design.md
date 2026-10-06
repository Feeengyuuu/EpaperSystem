# Stock Tracker 组合趋势图重做设计

**日期**：2026-10-06
**状态**：用户已确认设计，待实现
**范围**：`plugins/stocktracker` 的 PORTFOLIO TREND 面板（800×480 仪表盘右上角，面板框 `(304, 60, width-24, 204)`）

## 1. 目标

把趋势图从"装饰性的平滑曲线"改成能一眼看出这段时间是赚是亏的业绩总结图，同时在 Spectra 6 六色墨水屏上保持清晰。

## 2. 现状问题

1. 用 Catmull-Rom 插值连接每天的数据点，会在相邻两天之间画出实际不存在的起伏，曲线还可能超出真实的最高、最低值。
2. 最高、最低价标签固定贴在左上角和左下角，和它们出现的位置无关，还会盖住曲线（2026-10-06 截图中，`$258,851` 遮住了曲线的开头一段）。
3. 没有参照线，看不出现在比期初高还是低；面积填充是一种统一的浅色，和涨跌无关。
4. 网格线是浅色，抖动后在墨水屏上变成杂点。

## 3. 已确认的设计选择

用户在三个选项中只选了"期初基准线 + 涨跌分色"，不要总收益文字、最高/最低点标注、日期轴和刻度。曲线画法选了 A（平滑曲线），没选 B（折线）。预览对比图见 `outputs/stock-trend-design-20261006/trend-comparison.png`（不入库）。

### 3.1 画面

- 面板框、标题字样 PORTFOLIO TREND、强调色保持不变。
- 绘图区 `(left+16, top+40, right-16, bottom-12)`，背景保持奶油色，**不画网格线**。
- 纵向范围取所有数值（包含期初值）的最小、最大值，上下各留 14% 余量。如果数值完全持平，最小跨度取期初值的 0.2% 或 $1 中较大的一个，避免除零。
- **基准线**：在期初值的高度画灰色（`muted`）虚线，实线 5px、间隔 4px，线宽 1px。
- **曲线**：用 Fritsch–Carlson 单调三次插值，每两个数据点之间细分 8 段，线宽 3px。曲线严格经过每个真实数据点，任何一段都不会超出这一段两端的数值。
- **分色**：曲线高于基准线（屏幕 y 坐标更小）的部分用 `malachite`，低于的部分用 `cinnabar`。曲线跨过基准线时，在精确交点处切开，两段在交点处首尾相接。
- **填充**：曲线和基准线之间的区域，高于基准的部分填 `malachite` 与背景 22% 混合的浅绿，低于的部分填 `cinnabar` 的 22% 浅红。
- **起点**：画一个半径 3 的空心圆，描边用 `muted`。
- **终点**：沿用 `_draw_latest_value_marker`，颜色在最终值 ≥ 期初值时为绿色，否则为红色。
- **期初金额标签**：用 `_money(start, 0)`，11px 粗体，`muted` 色，靠右放置。最终值 ≥ 期初值时放在基准线下方（终点在上方），否则放在上方。标签纵向位置限制在绘图区以内，底下铺一块背景色防止和虚线重叠。
- 夜间主题：所有颜色都从 `_active_stock_colors()` 取，自动使用主题调色板。

### 3.2 删除的内容

- 原来固定贴边的最高、最低价标签（`_draw_chart_label`）。
- 网格线。
- 本地快照打点（`_draw_history_markers`、`_history_marker_points`、`_sample_curve_points`）。这些点只出现在官方数据覆盖不到的日子，会和红绿分色冲突。快照数据仍然由 `_portfolio_curve` 并入曲线的数值，只是不再单独打点。
- Catmull-Rom 平滑函数 `_smooth_curve_points`，以及被新模块取代的 `_chart_value_bounds`、`_plot_series_points`。

## 4. 代码结构

`stocktracker.py` 已有 2125 行。新建 `plugins/stocktracker/trend_chart.py`，只放纯几何计算，不依赖 PIL，也不依赖插件状态：

| 函数 | 作用 |
| --- | --- |
| `value_bounds(values, padding_ratio=0.14)` | 返回 `(vmin, vmax)`，处理持平数据 |
| `plot_points(box, values, vmin, vmax)` | 数值 → 屏幕坐标（浮点） |
| `monotone_curve(points, subdivisions=8)` | Fritsch–Carlson 单调三次插值，少于 3 个点时原样返回 |
| `split_at_baseline(curve, base_y)` | 返回 `[(points, above)]`，在交点处切开 |

`StockTracker._draw_sparkline` 的签名保持不变，`_create_dashboard` 不需要改；它用上面这些函数计算几何形状，自己负责画图。`history_points` 和 `history_markers_on_prefix` 参数保留但不再使用，避免改动调用方。

## 5. 测试

- `tests/test_stocktracker_trend_chart.py`（纯几何）：
  - 单调插值经过每个输入点，首尾点保持不变。
  - 每一段的 y 值都在该段两端点的 y 值之间（不越界）。
  - 少于 3 个点时原样返回。
  - 切分时交点的 y 正好等于基准线，`above` 标记交替出现，所有切段首尾相接。
  - `value_bounds` 能处理持平数据和单个值。
- `tests/test_stocktracker.py`（渲染）：
  - 先探底再上涨的数据：开头一段的基准线下方有红色像素，后段的基准线上方以绿色为主。
  - 一路下跌的数据：以红色为主，终点标记是红色。
  - 两个点和完全持平的数据都能正常渲染，不报错。
  - 夜间主题正常渲染。
  - 删除对 `_smooth_curve_points` 和 `_history_marker_points` 的旧测试。
- 门禁：ruff、`tools/check_architecture.py`、全量测试。

## 6. 上线

沿用现有流程：全量测试 → 打包部署到 `ColoredEpaperFrame` → 用设备上真实持仓数据渲染一次 Stock Tracker，确认实际画面 → 3 次自然写屏的部署验收。48 小时验收仍然等用户下令。
