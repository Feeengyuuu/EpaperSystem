# EpaperSystem 复审报告与优化路线 v2

> 生成日期：2026-07-01 晚 · 对象：当前 main 分支（HEAD = `79acf9be`）
> 背景：v1 计划书发布后，P0/P1/P2 的绝大部分任务已在 9 个新提交中落地。本报告是对落地质量的全面复审 + 下一步路线。

---

## 一、v1 计划完成情况对照

| v1 任务 | 状态 | 证据 |
|---------|------|------|
| P0-1 HTTP 默认超时 | ✅ | `http_client.py` TimeoutSession（30s 默认，显式传参优先） |
| P0-2 .env mtime 缓存 | ✅ | `config.py _reload_env_if_changed`，未变更时零磁盘解析 |
| P0-3 waitress 线程池 | ✅ | 默认 4 线程，`web_server_threads` 可配，钳制 1..16 |
| P0-4 持久化 secret_key | ✅ | `utils/secret_key.py`，token_hex(32)，文件 0600 |
| P0-5 调度器重复块合并 | ✅ | `_dispatch_background_cache_refresh`，行为等价性已验证 |
| P1-1A 浏览器 profile 复用 | ✅ | `image_utils.py` 持久 profile 目录（`INKYPI_BROWSER_PROFILE_DIR` 可覆盖） |
| P1-2 插件懒加载 | ✅ | `plugin_registry.py` 只登记元数据，首次使用才 import |
| P1-3 调度器与 sports 解耦 | ✅ | `refresh_task.py` 中 "sports" 仅剩 1 处引用；`BasePlugin` 新增 `wants_refresh_on_display` / `get_live_refresh_state` 钩子 |
| P1-4 拆分 sports_dashboard | ✅ | 19,799 行单文件 → 12 个模块（最大 esports.py 3,500 行） |
| P1-5 公共缓存/绘图库 | ✅ | `utils/plugin_cache.py` + `utils/draw_utils.py`，已有 5 个插件采用 |
| P1-6 超时常量去重 | ✅ | 提交 `12ea6f34` |
| P2-1 分主题提交 | ✅ | 7 个主题提交 |
| P2-2 CI | ✅⚠️ | `.github/workflows/test.yml`（pytest + ruff fatal）——**但当前配置首跑必红，见问题 #1** |
| P2-3 requirements 去重 | ✅⚠️ | base/prod/dev 三层结构——**base 裁剪引入回归，见问题 #1/#5** |
| P2-5 归档 dashboard-7in5 | ✅ | 提交 `10ef2f74`，README 已注明 |
| P1-1B 常驻浏览器进程 | ⏳ 未做 | 待 Pi 实测数据决定是否需要 |
| P2-4 git 瘦身（325MB） | ⏳ 未做 | 建议公开发布时新仓库首发 |
| P2-6 ruff 配置固化 | ⏳ 部分 | CI 已跑 ruff 但无配置文件（用默认规则集） |

## 二、复审验证结果（本机实测）

| 检查项 | 结果 |
|--------|------|
| ruff check src tests | ✅ All checks passed |
| 全量测试（完整依赖环境） | ✅ 1275 passed（30.7s） |
| **CI 模拟**（严格按 requirements-dev.txt 的干净环境） | ❌ **1271 passed + 1 收集错误**：`test_mini_weather_backgrounds.py` → `ModuleNotFoundError: No module named 'astral'` |
| P0 修复对抗审查 | 并发视角完成（发现 1 major + 3 minor，见下）；正确性/兼容性两个审查智能体因会话限额中断，已由本人补审 |

> 注：本机 `.venv` 现已严格对齐 requirements-dev.txt（卸载了 astral/yfinance/google-genai/Telethon/pi-heif），用于精确模拟 CI。在 astral 决策落地前，本地跑全量测试会看到同一个收集错误——这是有意保留的信号，不是环境坏了。

## 三、新发现问题清单（按优先级）

### 🔴 #1 astral 被移出 requirements，但 weather 是模块级硬依赖
`src/plugins/weather/weather.py:8` `from astral import moon` 是无条件导入，mini_weather 测试链也会触发。后果：**CI 首跑必红**；全新树莓派安装后 weather/mini_weather 插件直接崩溃（requirements.txt 也不再含 astral）。
**修复（二选一）**：把 `astral>=3.1` 加回 `install/requirements-base.txt`（推荐，包很小且 weather 是招牌插件）；或改为懒导入 + 缺失时渲染占位卡。

### 🔴 #2 model.py 播放列表完全无锁，threads=4 下有真实竞态
`PlaylistManager`/`Playlist` 没有任何锁。`blueprints/playlist.py:43-68` 先 `find_plugin()` 再 `add_plugin_to_playlist()`，`:108-117` 先 `get_playlist()` 再 `add_playlist()`（后者自身不查重）——两个并发请求可产生重复条目或丢失更新。此前 threads=1 掩盖了问题，现在暴露。
**修复**：在 `PlaylistManager` 上加一把 `threading.RLock`，所有 mutation 方法（add/remove/update playlist 和 plugin instance）内部加锁；蓝图层的 check-then-act 序列改为调用 manager 上的原子方法（如 `add_plugin_if_absent`）。

### 🟡 #3 plugin_registry 懒加载 check-then-create 无锁
`get_plugin_instance()`（plugin_registry.py:72-85）两个线程首次同时请求同一插件会构造两个实例、各自持有一份——破坏单例假设，重复初始化（Jinja env、image loader）。
**修复**：模块级 `threading.Lock` 包住"查缓存-构造-写缓存"。

### 🟡 #4 http_client 单例先赋值后配置
`get_http_session()` 把裸 `TimeoutSession()` 赋给全局后才挂 UA 头和重试适配器——并发首调用可能拿到半初始化 session；双线程同时进入还会泄漏一个 session。
**修复**：先在局部变量上完整构造，最后一步赋值给 `_HTTP_SESSION`（或同样加锁）。

### 🟡 #5 pi-heif 移到 Pi-only，但 inkypi.py 无条件导入
`src/inkypi.py:6` `from pi_heif import register_heif_opener`。测试不受影响（没有测试导入 inkypi.py），但新开发机装 dev 依赖后跑 `python src/inkypi.py --dev` 直接 ImportError。
**修复**：守卫导入（缺失时跳过 HEIF 注册并 log warning），或把 pi-heif 放回 base（它在 Win/mac/Linux 都有轮子）。

### 🟢 #6 secret_key 跨进程竞态
`load_or_create_secret_key` 是 check-then-write；dev 和 prod 实例共用同一路径同时首启会各自生成不同 key。影响小（会话失效一次）。修复：`open(path, 'x')` 原子创建，失败则重读。

### 🟢 #7 可选依赖缺失时的插件行为不符产品规则
yfinance/google-genai 移除后，stocktracker/ai_image_multiverse 在渲染期抛 ImportError → 错误页。README 的插件作者规则要求"缺凭据/依赖时渲染占位或示例内容"。建议做一个统一的"可选依赖缺失 → 占位卡片"辅助函数（放 base_plugin 或 draw_utils），逐插件接入。

### 🟢 #8 config 的 bare load_dotenv 发现路径不在 mtime 缓存内
非候选路径上的 .env（靠 dotenv 向上搜索发现的）变更后不会触发重载，需等候选文件变化或重启。行为差异极小且已在提交说明中记录，接受即可。

### 🟢 #9 无 ruff 配置文件
CI 目前用 ruff 默认规则集（E4/E7/E9/F）。建议加 `pyproject.toml` 固化规则与目标版本（py311），后续逐步开启 B/UP/SIM 等规则。

### 🟢 #10 遗留清理项
- `esports.py`（3,500 行）与 `common.py`（3,087 行）仍偏大，可再拆 render 层（worldcup/offseason 已拆出 *_render.py，esports 部分完成）。
- 3 个废弃 venv（`.venv-codex/.venv-local/.venv-test`）仍在磁盘。
- 微软雅黑字体：公开发布前换 Noto Sans CJK（OFL 授权），在 `FONT_FAMILIES` 中加 Noto 作为 YaHei 缺失时的回退映射。
- `.git` 325MB：公开发布时以干净历史新仓库首发。
- 约 20 个插件仍用各自的 JSON 缓存实现，可随改动逐步迁移到 `plugin_cache`。

## 四、下一步优化路线 v2

### 第一批：修复回归与并发（半天，全是小改动）
1. astral 决策 + 落地（问题 #1）→ CI 转绿的前提
2. PlaylistManager 加锁（#2）+ 蓝图原子化
3. plugin_registry / http_client 初始化锁（#3/#4）
4. inkypi.py pi-heif 守卫导入（#5）
5. push 触发 CI 首跑，确认绿

### 第二批：观测驱动的性能决策（部署到 Pi 后 1-2 周）
- 在 Pi 上实测并记录：单次 HTML 渲染耗时、`journalctl` 中 exit-75（内存看门狗重启）频率、`available_mb` 走势
- 若 profile 复用后渲染仍是瓶颈 → 实施 P1-1B 常驻浏览器（CDP 控制常驻 headless Chromium，渲染降至亚秒）
- 若 exit-75 归零 → 把内存看门狗默认阈值调保守或默认关闭

### 第三批：产品一致性与公开发布准备
- "可选依赖缺失 → 占位卡"统一模式（#7），先接入 stocktracker / ai_image_multiverse / telegram_digest
- Noto Sans CJK 替换方案（授权合规）
- pyproject.toml + ruff 规则固化
- 删除废弃 venv；README 补"本机开发与测试"一节（.venv + requirements-dev + pytest）
- 公开发布：干净历史新仓库 + 按 docs/open_source_release_checklist.md 走查

## 五、度量表更新

| 指标 | v1 基线 | 当前 | 目标 |
|------|---------|------|------|
| 最大单文件行数 | 19,799 | 3,500 | < 3,000（拆 esports render 后可达） |
| 刷新线程挂起风险 | 无超时可永久挂起 | 默认 30s 超时兜底 | ✅ 已达成 |
| 启动插件导入 | 61 个全量 | 按需懒加载 | ✅ 已达成（Pi 上量化内存收益待测） |
| 测试 | 本机跑不了 | 1275 个用例 31s 全绿（完整依赖） | CI 常绿（待 astral 修复） |
| CI | 无 | pytest + ruff 已配置 | 首跑绿 |
| Chromium 冷启动 | 每次渲染 | profile 复用 | Pi 实测后决定是否上常驻进程 |
| `.git` 体积 | 325MB | 325MB | 公开仓库 < 50MB |
