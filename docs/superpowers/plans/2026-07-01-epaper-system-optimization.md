# EpaperSystem 项目优化计划书

> 生成日期：2026-07-01 · 基于对整个仓库的代码审查
> 目标：让系统在树莓派上运行更流畅（更少内存压力、更快刷新、更少挂起），让代码更易维护（消除重复、拆分巨型文件、可跑的测试）。

---

## 一、执行摘要

EpaperSystem 整体架构是健康的：核心调度器（`refresh_task.py`）有缓存优先、后台刷新、资源压力保护等成熟设计；Web 层用异步任务队列（202 + 轮询）而非阻塞请求；测试文件多达 54 个、3.3 万行。

但审查发现 **4 个直接影响运行流畅度的问题** 和 **3 个显著拖慢开发效率的问题**：

| # | 问题 | 影响 |
|---|------|------|
| 1 | 共享 HTTP session 无默认超时，74 处调用约半数未传 `timeout` | 一次网络挂起可**永久卡死刷新线程**，屏幕不再更新 |
| 2 | 每次 HTML 渲染冷启动一个 Chromium 进程（全新 profile） | Pi 上每次渲染慢 2-5 秒、内存峰值高——这正是内存看门狗 `os._exit(75)` 频繁重启的根源 |
| 3 | `Config.load_env_key` 每次调用都重新读取多个 `.env` 文件 | 插件每次取 key 都产生磁盘 IO，且反复覆写全局环境变量 |
| 4 | 启动时一次性 import 全部 61 个插件模块 | 启动慢、常驻内存高（与 #2 叠加造成内存压力） |
| 5 | `sports_dashboard.py` 单文件 **19,799 行、1014 个函数**；且约 500 行 sports 专属逻辑硬编码进核心调度器 | 任何改动都难以定位和验证；核心与插件耦合 |
| 6 | 20+ 插件各自实现 JSON 缓存/TTL/每日限额；53 处字体/文本工具函数重复 | 每个新插件都在重造轮子，bug 修一处漏 N 处 |
| 7 | 无 CI；本机 4 个 venv 均无 pytest（测试跑不起来）；`.git` 已达 325MB；81 个未提交变更 | 回归无法及时发现；仓库越来越重 |

建议分三期执行：**P0 稳定性（1-2 天）→ P1 架构治理（1-2 周）→ P2 仓库卫生与工程化（穿插进行）**。P0 的每一项都很小，但对"运作流畅"的收益最大。

---

## 二、现状盘点（数据）

- 运行时代码：`inkypi-weather/package/InkyPi/src` 共 **79,016 行 Python**，61 个插件
- 测试代码：`tests/` 共 **33,647 行**、54 个测试文件——但无 CI，本机 `.venv`/`.venv-codex`/`.venv-local`/`.venv-test` 四个虚拟环境**均未安装 pytest**
- 仓库：`.git` 325MB；git 跟踪着 587 个 PNG + 34 个 BMP；工作区有 81 个未提交变更（+4452/−602）
- 遗留子项目：`dashboard-7in5/`（1,665 行）与 InkyPi **零引用关系**，内含硬编码坐标、打印机 IP 占位符，README 已声明"可运行的应用在 InkyPi"
- 根目录杂物：`.tmp/`（几十个 stage 目录）、`.codex_tmp/`、`tmp/`、`output/`、`pytest-cache-files-*`、空文件 `__sports_flag_smoke.py`、`tmp_scp_test.txt`（多数已 ignore 但仍占磁盘、干扰导航）
- `inkypi-weather/dist/` 内有 3 份完整的包拷贝（已 ignore）

---

## 三、P0：稳定性修复（优先做，每项 0.5-2 小时）

### 任务 P0-1：给共享 HTTP session 加默认超时

**问题**：`src/utils/http_client.py` 的 `get_http_session()` 返回原生 `requests.Session`，无默认超时。30 个插件使用它，74 处 `session.get/post(` 调用中只有约 33 处显式传 `timeout`。任何一处无超时调用在网络异常时会永久阻塞——如果发生在刷新线程里，**整块屏幕停止更新**，只能靠内存看门狗误打误撞地重启。

**方案**：在 `http_client.py` 中用带默认超时的 Session 子类，调用方显式传的 `timeout` 仍优先生效：

```python
DEFAULT_TIMEOUT_SECONDS = 30

class TimeoutSession(requests.Session):
    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", DEFAULT_TIMEOUT_SECONDS)
        return super().request(method, url, **kwargs)
```

把 `_HTTP_SESSION = requests.Session()` 改为 `TimeoutSession()` 即可，插件零改动。

**验收**：`tests/test_http_client.py` 增加用例——不传 timeout 时 `request` 收到默认值；显式传 `timeout=5` 时不被覆盖。

### 任务 P0-2：`load_env_key` 缓存 .env 读取

**问题**：`src/config.py:183-193`，每次 `load_env_key` 都遍历 5+ 个候选路径并 `load_dotenv(override=True)`。插件在每次 `generate_image` 里可能取多个 key，等于每轮刷新反复做磁盘 IO 并覆写进程环境变量。

**方案**：首次调用时加载一次并记录各 `.env` 文件的 `mtime`；后续调用只在 mtime 变化时重新加载（保留"改了 .env 不用重启"的现有行为）：

```python
def load_env_key(self, key):
    self._reload_env_if_changed()   # 比较 mtime，变了才 load_dotenv
    for candidate in self._env_key_candidates(key):
        value = os.getenv(candidate)
        if value:
            return value
    return ""
```

**验收**：`tests/test_config_env_key_aliases.py` 现有用例全绿；新增用例——修改 .env 文件后新值可读到。

### 任务 P0-3：Waitress 线程数从 1 提到 4

**问题**：`src/inkypi.py:124` `serve(app, host="0.0.0.0", port=PORT, threads=1)`。单线程意味着任何一个慢端点（如插件设置页触发的插件代码、图片传输）都会让整个 Web UI 无响应。

**方案**：`threads=4`，并允许 `device.json` 里用 `web_server_threads` 覆盖（Pi Zero 用户可调回 2）。注意：多线程后 `Config.update_value` 的并发写已有 `_write_lock` 保护，风险低。

**验收**：Web UI 在一个手动刷新排队期间仍能打开其他页面。

### 任务 P0-4：修复弱 secret_key

**问题**：`src/inkypi.py:110` `app.secret_key = str(random.randint(100000,999999))`——只有 90 万种可能，且每次重启使所有会话失效。

**方案**：首次启动生成 `secrets.token_hex(32)` 持久化到 `config/` 下（0600 权限），之后复用。

### 任务 P0-5：消除 `refresh_task._run` 中的重复块

**问题**：`src/refresh_task.py:299-334`，`if refresh_action:` 分支尾部与 `elif background_cache_refresh:` 分支是两份几乎相同的 30 行代码。

**方案**：提取 `_maybe_start_background_cache_refresh(playlist, displayed, current_dt, force)` 私有方法，两处调用。行为不变，`tests/test_refresh_task.py` 全绿即验收。

---

## 四、P1：性能与架构治理（1-2 周，可拆散执行）

### 任务 P1-1：持久化浏览器渲染进程（最大性能收益）

**问题**：`src/utils/image_utils.py:133-230` `take_screenshot` 每次渲染都：新建临时 profile 目录 → 冷启动 Chromium → 截图 → 杀进程。在 Pi 上单次冷启动 2-5 秒、内存峰值 150-300MB。这是内存看门狗（`refresh_task.py:786` `os._exit(75)` 重启进程）存在的根本原因——**当前是在用重启掩盖渲染管线的资源消耗**。

**方案**（按投入递增，三选一，建议先做方案 A 观测效果）：

- **方案 A（低投入）**：复用同一个 `--user-data-dir`（首次创建后保留），避免每次生成新 profile；给 Chromium 加 `--disk-cache-size=1` 防止缓存膨胀。改动仅限 `take_screenshot`。
- **方案 B（中投入）**：引入 Playwright（或直接用 CDP over websocket 控制常驻 headless Chromium），浏览器进程常驻，渲染变为"打开页面→截图"，单次渲染从秒级降至百毫秒级。需要处理常驻进程的健康检查与崩溃重启。
- **方案 C（长期）**：为简单文本/表格类插件提供纯 Pillow 渲染路径（很多插件已经是纯 Pillow 的），逐步减少依赖 HTML 渲染的插件数量。

**验收**：连续渲染 10 个 HTML 插件，记录耗时与 `available_mb`（日志里已有内存维护统计）；对比优化前后。目标：内存看门狗触发频率显著下降。

### 任务 P1-2：插件懒加载

**问题**：`src/plugins/plugin_registry.py:13-41` 启动时 import 全部 61 个插件模块。部分插件带重依赖（openai、yfinance、Telethon、numpy），全部常驻内存。

**方案**：`load_plugins` 只登记元数据（plugin-info.json 已在 `Config.read_plugins_list` 读过），`get_plugin_instance` 首次被调用时才 `importlib.import_module` 并缓存实例。Web UI 插件列表页只需要元数据，不需要模块本体。

**验收**：启动时间与启动后 RSS 内存对比；`tests/` 全绿。

### 任务 P1-3：把 sports_dashboard 专属逻辑从核心调度器抽离

**问题**：`src/refresh_task.py` 有约 500 行 `_sports_dashboard_*` 方法：5 份几乎相同的 live-state 路径/激活判断（`:1192-1291`）、每个赛事源的 enabled/interval 读取（`:1124-1190`）、硬编码常量（`:30-39`）。另有 `REFRESH_ON_DISPLAY_PLUGIN_IDS` 硬编码 17 个插件 ID（`:40-58`）。核心调度器不应该知道任何具体插件。

**方案**：定义插件声明式能力接口：

1. `plugin-info.json` 增加 `"refresh_on_display": true` 字段，替代硬编码集合；`newspaper` 的特殊逻辑移入其插件类的 `wants_refresh_on_display(settings)` 钩子。
2. 定义 `BasePlugin.get_live_refresh_state(settings, current_dt) -> {active: bool, interval_seconds: int} | None` 钩子；sports_dashboard 实现它（读取自己的 5 个 live-state 文件）；调度器只调用钩子，不再知道 worldcup/lpl/msi/nba/offseason_hub 的存在。
3. 5 份 `_sports_dashboard_*_live_state_path/active` 合并成 sports_dashboard 包内一个以 source 为参数的函数。

**验收**：`refresh_task.py` 中不再出现 "sports" 字样；`tests/test_refresh_task.py`、`tests/test_sports_dashboard.py` 全绿。

### 任务 P1-4：拆分 sports_dashboard.py（19,799 行 → 包）

**方案**：按已有的天然边界拆为包（该文件已用状态版本常量清晰划分了各数据源）：

```
sports_dashboard/
├── __init__.py            # 导出 SportsDashboard
├── sports_dashboard.py    # 只保留插件类：generate_image、布局编排（目标 < 2000 行）
├── cache_io.py            # 已存在
├── common.py              # 共享：ESPN scoreboard 客户端、每日限额、状态文件读写、字体/文本工具
├── worldcup.py            # 世界杯：赛程/积分/赔率/阵容/直播
├── nba.py
├── esports.py             # LPL/MSI/LCK/Valve/EWC
├── f1.py
└── offseason_hub.py       # MLB/WNBA/PGA/NFL/NCAA
```

**执行要点**：
- 纯搬移不改逻辑，每搬一个源跑一次 `tests/test_sports_dashboard.py`（现有 214+ 行测试是安全网），每个源一个 commit。
- 77 处 `except Exception` 在搬移时顺手收窄：网络调用捕 `requests.RequestException`，JSON 解析捕 `(ValueError, KeyError)`，真正未知的才保留宽捕获并 `logger.exception`。

### 任务 P1-5：抽取跨插件公共库

**问题**：20+ 插件各自实现 JSON 状态缓存（TTL、每日 API 限额、原子写）；29 个插件文件里散布 53 个 `_load_font/_fit_text/_draw_centered/_truncate_text` 类工具函数；`refresh_task.py:1521-1545` 里还有第 54 份。

**方案**：新建两个共享模块，**新插件必须使用，旧插件搬迁时顺手替换**（不要求一次性全量替换）：

- `src/utils/plugin_cache.py`：`CachedState(path, version, ttl, daily_limit)`——统一封装 sports_dashboard/cache_io.py 与 context_cache.py 已验证的模式（原子写、版本校验、TTL、每日限额计数）。
- `src/utils/draw_utils.py`：`load_font(size, bold, lang)`（含字体路径解析和缓存）、`fit_text`、`draw_centered`、`ellipsize`。

**验收**：至少 5 个最常改动的插件（sports_dashboard、daily_ai_news、steam_charts、weather、mini_weather）迁移完成；每个被替换的私有 helper 删除。

### 任务 P1-6：统一超时与重试策略清理

P0-1 落地后，逐插件删除与默认值相同的显式 `timeout=30`，只保留确有理由的特殊值（如 `steam_charts` 的 `STEAMCHARTS_CHART_TIMEOUT = 30` 可直接删掉，`timeout=15` 的保留）。低优先，可与 P1-5 搬迁同步做。

---

## 五、P2：仓库卫生与工程化（穿插进行）

### 任务 P2-1：处理 81 个未提交变更

当前工作区混着功能修改（sports_dashboard、steam_charts 测试等 +4452 行）与清理性删除（output 产物、日志）。**先按主题拆成 2-3 个 commit 提交掉**，再开始上面的重构——否则重构 diff 会和现有变更搅在一起。

### 任务 P2-2：建立 CI（GitHub Actions）

**问题**：54 个测试文件、3.3 万行测试代码，但没有任何 CI，本机也没有能跑测试的环境——测试的价值目前接近于零。

**方案**：新建 `.github/workflows/test.yml`：

```yaml
name: tests
on: [push, pull_request]
jobs:
  pytest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }   # 与 Raspberry Pi OS Bookworm 一致
      - run: pip install -r inkypi-weather/package/InkyPi/install/requirements-dev.txt
      - run: cd inkypi-weather/package/InkyPi && python -m pytest tests -q
```

同时：本机重建一个可用的开发环境（删掉 4 个 venv，保留一个 `.venv`，装 requirements-dev.txt），把"如何在开发机跑测试"写进 README 或 docs。

### 任务 P2-3：requirements 去重

`install/requirements.txt` 与 `requirements-dev.txt` 有 18 行重复且已出现漂移（dev 缺 `inky/cysystemd/yfinance/google-genai/numpy` 版本差异风险）。改为 dev 文件只写：

```
-r requirements.txt
pytest==8.4.2
```

硬件专属包（inky、cysystemd、pi-heif）如在 CI 上装不动，则反向拆分：`requirements-base.txt`（共享）+ `requirements.txt`（base + 硬件）+ `requirements-dev.txt`（base + pytest）。

### 任务 P2-4：git 仓库瘦身（325MB）

- 短期：确认 `docs/images/readme/` 与 `marketing_assets/` 的 PNG 是否需要留在 git；后续新增截图建议压缩到合理尺寸（当前 README 图片有 800x480 原图多份）。
- 公开发布前（README 已有此计划）：**以当前状态新建干净仓库首发**（squash 历史），而不是带着 325MB 历史和历史中可能的敏感信息发布。这同时解决 README "Before Publishing" 一节担心的 secrets-in-history 问题，比 `git filter-repo` 更省事、更保险。
- 顺手删除磁盘上的历史残留：`inkypi-weather/dist/` 三份包拷贝、根目录 `.tmp/` 几十个 stage 目录、`pytest-cache-files-*`、空文件 `__sports_flag_smoke.py`、`tmp_scp_test.txt`（均已 ignore，纯磁盘清理）。

### 任务 P2-5：归档 dashboard-7in5

与 InkyPi 零引用、README 已declare唯一可运行应用是 InkyPi、内含硬编码个人坐标。建议移到单独的归档分支（`git branch archive/dashboard-7in5` 后从 main 删除），根 README 加一行说明去处。若仍在使用它，则至少把 `LOCATION_LAT/LON`、`PRINTER_CONF` 等硬编码配置抽到 `.env`。

### 任务 P2-6：加入 ruff（lint + format）

79k 行代码没有任何 lint 配置。新建 `pyproject.toml`，从最小规则集起步（`E9,F63,F7,F82` 仅致命错误），进 CI；随重构逐步收紧。不做一次性全量 format（会污染 blame）。

---

## 六、执行顺序与依赖

```
P2-1 提交现有变更 ──► P0-1..P0-5（互相独立，可并行）──► P2-2 CI 建立
                                                          │
                     P1-2 懒加载 ◄──────────────────────┤
                     P1-1 渲染进程复用（A→B 递进）◄──────┤
                     P1-3 调度器解耦 ──► P1-4 拆分 sports_dashboard ──► P1-5 公共库搬迁 ──► P1-6 超时清理
                     P2-3..P2-6 穿插任意空档
```

原则：**每个任务独立成 commit、测试全绿再进下一个**。P1-3/P1-4 动的是最大的文件，务必在 CI（P2-2）建好之后进行。

## 七、成效度量

| 指标 | 现状 | 目标 | 测量方式 |
|------|------|------|----------|
| 单次 HTML 插件渲染耗时 | 冷启动 Chromium，秒级 | 方案 A 降 30%+；方案 B 亚秒 | 日志时间戳 |
| 内存看门狗重启频率 | 存在（os._exit(75)） | 接近 0 | `journalctl` 中 exit 75 计数 |
| 刷新线程挂起风险 | 无超时调用可永久挂起 | 任何网络调用 ≤30s 必返回 | 代码保证（P0-1） |
| 启动内存 RSS | 61 插件全量导入 | 降 20%+（懒加载） | `psutil` 日志已有 |
| 最大单文件行数 | 19,799 | < 3,000 | `wc -l` |
| CI | 无 | 每次 push 全量 pytest | GitHub Actions |
| `.git` 体积 | 325MB | 公开仓库 < 50MB | `du -sh .git` |
