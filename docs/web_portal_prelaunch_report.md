# Model Y 网页门户：本地上线前验收报告

日期：2026-08-02（America/Los_Angeles）  
状态：本地候选已验收；仍未正式上线，也未触碰 VPS、DNS、供应商密钥或实体屏幕。

## 已完成的本地证据

- 从设备现有只读缓存建立了 1 个播放列表、26 个 current publication。26 个内容寻址 PNG 均为设备原版 `800×480`，逐字节 SHA-256、声明大小和解码尺寸全部匹配。
- 正式 reader/web 在 `127.0.0.1:8789` 返回 `/livez={"status":"ok"}`、`/readyz={"status":"ready"}`。GET 路径只读 Edition Ledger，不调用 provider、renderer、刷新队列或实体屏幕。
- Chromium 实际登录并逐页播放 26/26：每张图都真实加载、`object-fit: contain`、无稳定态裁切，页面无横向或纵向滚动溢出。`1920×1080` 与 `1920×1200` 两档都通过；Fullscreen API 可进入真实 fullscreen。天气外层标题为“当地天气”，右上角时间标签为“当地时间”。
- 26 个受保护 asset 的浏览器响应均为 PNG、`private, no-store`，下载字节的 SHA-256 与 URL 中的 asset id 一致。
- 最终自动化结果：`5238 collected`，`5192 passed`，`46 skipped`，`0 failed`；聚焦 portal/publication/deployment 测试 `202 passed`，其中精确部署合同 `38 passed`。Bash `-n`、Python compileall 与 Ruff 全部通过。
- 官方 Docker Compose `5.3.1` Windows x86_64 二进制的 SHA-256 已核对为 `6d36cc701393c066d67ebc77773b718d8c738bc4ccb350fbf1dc0e6a09f44cb9`；该真实二进制输出的完整 `--profile admin config --format json` 已通过本项目精确 Compose 合同。生产脚本会拒绝其他 Compose 版本。
- Caddy `2.11.4` 的真实 `adapt --validate` 已通过。备份/恢复控制流使用合成 Docker/SQLite 状态完成成功、早期失败、加密失败及清理失败演练；归档本身使用真实 `age` 加解密并通过校验。它不是生产 Docker daemon 或 off-host DR 的替代证据。

最终浏览器证据：

- [带“当地天气/当地时间”标签的 1920×1080 画面](../output/playwright/modely-final-weather-labels-1920x1080.png)
- [隐藏导航后的 1920×1080 原版画面](../output/playwright/modely-final-1920x1080.png)
- [带“当地天气/当地时间”标签的 1920×1200 画面](../output/playwright/modely-final-weather-labels-1920x1200.png)
- [隐藏导航后的 1920×1200 原版画面](../output/playwright/modely-final-1920x1200.png)
- [Fullscreen API 实际启用画面](../output/playwright/modely-final-fullscreen-api-1920x1200.png)

## 尚未执行的外部门禁

以下项目必须保留到目标 Linux VPS、真实域名和真实密钥到位后完成，不能用本机结果代替：

1. 把当前候选的全部必要文件纳入一次干净、已提交的 release checkout；记录完整 Git SHA。当前工作树仍有未提交文件，因此严格生产 preflight 会按设计拒绝它。
2. 在目标 Linux 主机安装并核对固定的 Docker Compose `5.3.1` 官方资产；记录 Docker Engine 版本，再重复真实 `config` 合同。
3. 导入脱敏配置、初始完整账本、独立 session key 与逐文件 provider secrets；配置 worker 出站防火墙。
4. 以同一完整 Git SHA 只构建一次镜像，记录 image ID/digest；运行无网络 admin，然后运行不带公网 URL 的严格 preflight。
5. 启动 staging 长期服务；用真实 container/image inspect 验证运行态合同，等待 worker 完成首轮真实 26/26 发布。
6. 完成 staging HTTPS、外网登录、26/26 播放、告警、加密生产备份与隔离恢复演练。
7. 最后才切生产 DNS，重复严格 preflight、`ready`、登录、播放与回滚检查；再用停驻状态的 Model Y 实车验收。

在上述任一项缺失时，状态都保持为“上线前”，不得宣布上线、切换生产 DNS 或把本地演练称为生产恢复证明。
