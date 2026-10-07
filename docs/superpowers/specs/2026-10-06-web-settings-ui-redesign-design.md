# 网页设置界面重构设计

**日期**：2026-10-06
**状态**：用户已确认方向，待实现
**范围**：InkyPi 设备网页界面（首页、播放列表、插件库、插件设置、API 密钥、设备设置、运行状态、登录类页面）

## 1. 目标

把"首页 + 返回按钮"的星形结构，改成有常驻导航的现代设置系统。每个页面的操作更直接，播放列表直接展示每个实例的当前画面。

## 2. 已确认的选择

- 设备：手机和电脑都常用。宽屏用左侧栏，窄屏（< 900px）用底部标签栏。
- 视觉：A 精致深色为默认，提供同结构的浅色模式。
- 技术：服务端 Jinja 模板 + 统一外壳 + 原生 JS，不引入新依赖，设备上不需要构建步骤。
- 播放列表：每个实例的预览图放大，作为卡片主体（用户明确要求，"不用一个个点开"）。
- 不做：实例拖拽排序（现有数据模型不支持）、"下一项预告"（没有可靠数据来源）。

## 3. 信息架构

| 版块 | 路由 | 内容 |
| --- | --- | --- |
| 正在播放（首页） | `/`，`/playlist` 渲染同一页面 | 正在显示的大卡片、播放列表切换、实例大预览卡片网格 |
| 插件 | `/plugins`（新增）、`/plugin/<id>` | 插件库（搜索、排序、网格/列表）；插件设置页 |
| 密钥 | `/api-keys` | 按服务组织的密钥；其他变量 |
| 设备 | `/settings`、`/status` | 分组设置；运行状态；维护；危险区 |

- 所有现有路由和接口保持不变，书签继续有效。
- 登录、初始化、找回页使用不带导航的精简外壳。
- 页头：版块标题；右侧是语言切换（复用 `#languageToggle`）、深浅色切换、登录状态。去掉所有"← Back"按钮。

## 4. 设计系统

### 4.1 颜色变量（`static/styles/tokens.css`）

深色（默认，`html[data-theme="dark"]` 以及未设置时）：背景 `#111316`，卡片 `#1b1e23`，浮层 `#23272d`，分隔线 `#2a2f36`，正文 `#e8eaed`，次要文字 `#8a9099`，主色 `#1abc9c`（与现有 `--accent-primary` 相同），正常 `#5fd4a0`，警告 `#f2b84b`，危险 `#ff7b72`。

旧变量名（`--bg-primary`、`--bg-secondary`、`--text-primary`、`--text-secondary`、`--border-color`、`--accent-primary`、`--accent-warn`、`--input-bg`、`--modal-bg` 等 `main.css` 定义过的变量）全部保留为新变量的别名。插件模板的内联样式用到了 `--text-primary`、`--border-color`、`--bg-secondary`、`--accent-warn`。
浅色（`html[data-theme="light"]`）：背景 `#f4f5f7`，卡片 `#ffffff`，浮层 `#f0f1f4`，分隔线 `#e3e5e9`，正文 `#14161a`，次要文字 `#5f6670`，主色 `#14a385`，状态色同色系加深。

深色为默认：未保存偏好时使用深色。用户切换后存入 `localStorage['inkypi-theme']`，与现有键名一致。

### 4.2 尺寸

字号 12/14/16/20/24；圆角 14（卡片）、10（按钮和输入框）；间距 4 的倍数；触控目标不小于 44px。

### 4.3 组件（`static/styles/components.css`）

- 外壳：`.app-shell`、`.app-nav`（宽屏侧栏，窄屏底部栏）、`.page-head`（标题 + 副标题 + 操作区）。
- 卡片 `.card`；预览卡片 `.preview-card`（5:3 图片、状态标签 `.status-pill.ok|warn|err|idle`、名字、元信息、操作按钮）。
- 按钮 `.btn` + `.btn-primary|secondary|ghost|danger`；图标按钮 `.icon-btn`。
- 表单：继续支持插件模板使用的 `.form-group`、`.form-label`、`.form-input`、`.form-control`、`.toggle-*`、`.color-picker`、`.nowrap`、`.form-help`、`.separator`、`.buttons-container`、`.action-button`、`.modal`（插件模板中的现有用法），重新定义其外观。
- 底部弹层 `.sheet`：窄屏从底部滑出，宽屏居中显示。用于编辑、确认、"更多操作"菜单。
- 轻提示 `.toast`：替代 `response_modal`，并保留 `showResponseModal(type, text)` 兼容函数。
- 空状态、加载骨架。

### 4.4 文件组织

- `templates/base.html`：完整外壳（meta、CSRF、主题预设脚本、导航、页头、提示区）。区块包括 `title`、`head`、`page_title`、`page_actions`、`content`、`scripts`。
- `templates/base_minimal.html`：登录类页面使用。
- `templates/components/`：`preview_card.html`、`sheet.html`、`nav.html`。
- `static/styles/`：`tokens.css`、`components.css`，以及各页面自己的 CSS。全部页面迁移后删除 `main.css`。
- `static/scripts/app.js`：主题切换、轻提示、弹层、`apiFetch`（统一处理 JSON 和错误，CSRF 仍由 `inkypi-security.js` 注入）、`pollJson`（页面隐藏时暂停）。
- 每个页面一个独立脚本（`now_playing.js`、`plugins.js`、`apikeys.js`、`device.js`）。插件设置页的内联脚本保持原样（见 5.4）。

## 5. 页面

### 5.1 正在播放（`/`、`/playlist`）

- "正在显示"大卡片：
  - 显示 `/api/current_image` 的画面，每 10 秒按 ETag 检查一次。
  - 标出当前实例名和播放列表；数据来自新增的只读 `/api/now-playing`（`refresh_info`）。
  - 操作：刷新当前（`/refresh_plugin_instance`，随后 `/display_plugin_instance`）、全屏查看。
- 播放列表标签：每个列表一个标签，当前生效的列表加标记。"新建"和当前列表的"设置"（名称、时间段、删除）都用弹层，接口不变。
- 实例卡片网格：
  - 布局：手机 1 列，≥ 640px 2 列，≥ 1100px 3 列，≥ 1500px 4 列。
  - 卡片内容：
    - 预览图来自 `/plugin_instance_image/...`，懒加载，固定 5:3 占位；从未生成过画面的实例显示空状态。
    - 名字和"N 分钟前刷新"。
    - "▶ 立即显示"按钮。
    - "⋯ 更多"按钮，打开的弹层里有：编辑设置（`/plugin/<id>?instance=`）、立即刷新数据、刷新频率（复用 `refresh_settings_form.html` + `refresh_settings_manager.js`）、删除（二次确认）。
  - 状态标签：登录后每 30 秒轮询 `/api/runtime-status`，按实例名和插件 ID 匹配：
    - `issue` 为空 → 正常
    - `retry_wait` → 重试中（带下次重试时间）
    - 其他 → 失败，有缓存时附"正在用缓存"
    - 未登录（接口返回 401）不显示状态标签
  - 当前显示的实例卡片加高亮边框。
- 所有操作改为原地更新加轻提示，不再整页刷新。删除实例和改刷新频率后局部刷新列表。

### 5.2 插件库（`/plugins`）

- 原首页的插件网格移到这里，加搜索框（按显示名过滤）。
- 保留网格/列表切换和拖拽排序；排序仍保存到 `/api/plugin_order`。

### 5.3 API 密钥（`/api-keys`）

- 后端新增纯函数 `build_key_services(entries, registry)`：
  - 返回每个服务的状态（已配置/未配置）、实际命中的变量名（主名或别名）、使用它的插件、获取链接。
  - 返回 `other`：不属于任何服务的变量。
- 页面分三组：
  - **缺少的密钥**：未配置的服务，在最前面。
  - **已配置**。
  - **其他变量**：可折叠，内容即原始变量表。
- 操作：设置、更换、删除，都在弹层里完成，保存仍走 `/api-keys/save` 的整表协议：其余变量 `keepExisting`，删除即不提交。
- 值永远不回显，只显示"已设置"。

### 5.4 插件设置页（`/plugin/<id>`）

- 换成新外壳，导航停在"插件"。页头显示插件图标、名字、所需密钥状态（链接到密钥页）。
- **保持不变**：内联脚本的全局变量和函数（`pluginSettings`、`uploadedFiles`、`handleAction`、`openModal`、`closeModal`、`toggleCollapsible`、`selectedFrame` 等），以及元素 ID（`settingsForm`、`scheduleForm`、`loadingIndicator`、`refreshSettingsModal`、`scheduleModal`）。插件模板依赖这些名字。jQuery/Select2 照常加载。
- 底部操作改为吸底操作栏："立即更新"/"保存"为主按钮，"加入播放列表"/"另存为"为次按钮。
- 两个弹窗沿用 `.modal` 结构，由新样式渲染成弹层外观。

### 5.5 设备（`/settings`）与运行状态（`/status`）

- 分组卡片：
  - **显示**：方向、反色、图像设置（滑块显示数值）。
  - **时间**：时区、时间格式。
  - **轮播**：间隔，单位用分段选择器。
  - **系统**：设备名、记录系统状态。
- 一个"保存更改"主按钮，字段名和 `/save_settings` 保持一致。
- 运行状态卡：readyz 状态和最近恢复事件，来自 `/api/runtime-status`，未登录时提示登录；"查看详情"链接到 `/status`。
- 维护：下载日志（24 小时）。
- 危险区：重启、关机，必须二次确认。
- `/status` 换成新外壳，导航停在"设备"。

### 5.6 登录、初始化、找回

使用 `base_minimal.html`：居中卡片，深色外观，表单字段和属性（如 `minlength="9"`、"Connection is not encrypted"提示）保持不变。

## 6. 兼容与约束

- CSRF：所有页面保留 `<meta name="inkypi-csrf-token">` 和 `inkypi-security.js`。
- 国际化：模板继续用英文源字符串，新文案的中文写进 `i18n.js` 的 `zh` 字典。动态插入的内容由现有 MutationObserver 自动翻译。
- 权限：页面可匿名浏览，修改需登录。接口返回 401/403 时轻提示"请先登录"并给出登录链接。
- 测试中已断言的标记要保留，例如首页的 `<img src="/api/current_image" alt="Current Image">`、插件页主题选择器结构、登录页属性。

## 7. 后端改动（最小）

- `main.py`：`/` 渲染 `now_playing.html`；新增 `/plugins`、`/api/now-playing`。
- `playlist.py`：`/playlist` 渲染同一模板（复用同一个视图函数）。
- `apikeys.py`：新增 `build_key_services`，并把结果传给模板。
- 所有现有 POST/PUT/DELETE 接口不变。

## 8. 分阶段实施

1. 设计系统 + 外壳 + 正在播放 + 插件库。
2. 插件设置页。
3. API 密钥页。
4. 设备、运行状态、登录类页面；删除 `main.css` 和不再使用的旧脚本、图标。

每个阶段完成后都跑相关测试；全部完成后跑全量测试，再部署到设备验证。

## 9. 验证

- Python 测试：新路由、`/api/now-playing`、`build_key_services`、各页面渲染 200 和关键标记、旧路由仍然可用。
- 浏览器验证：本机开发实例（`.tmp/ui-dev`，localhost:8080，测试管理员账号）。覆盖手机 375px 和桌面 1280px、深色和浅色、中文和英文，逐页截图检查；所有操作走一遍。
- 设备验证：部署后用设备上的真实数据看"正在播放"页，只读浏览。
