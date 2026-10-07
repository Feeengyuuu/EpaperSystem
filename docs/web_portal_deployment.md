# Model Y 网页门户部署

> 生产首次部署、严格 preflight、主机防火墙与灾备边界请以 [web_portal_go_live_gate.md](web_portal_go_live_gate.md) 为准。

这套部署把 EpaperSystem 的已发布信息放到独立的只读网页中。公网入口只有 Caddy 的 80/443 端口；`web` 的 8080 端口只在 Docker 内部网络可见，`worker` 和一次性 `admin` 服务都没有入站端口。它不会暴露 InkyPi 管理站，也不会把 `/api/current_image`、刷新队列、配置写入或实体屏控制路由代理到公网。

## 本地站点预览（先于 VPS）

在准备 VPS、域名或供应商密钥之前，可以先用已有的插件 PNG 启动完整的本地站点结构。预览会走正式的发布账本、内容寻址资源、登录、CSRF、页面模板和同源资源路由；每张 PNG 都作为插件 renderer 的原版画面保存，不会被重绘、裁切或转码。它不会加载真实插件、访问供应商、启动 Chromium、进入刷新队列或控制实体屏。

首次运行先在仓库的忽略临时目录创建独立 Python 环境；它不会修改系统 Python：

```powershell
py -3.11 -m venv .tmp\portal-preview-venv
.\.tmp\portal-preview-venv\Scripts\python.exe -m pip install `
  --requirement tools\requirements-web-portal-preview.txt
```

然后从仓库根目录运行：

```powershell
.\.tmp\portal-preview-venv\Scripts\python.exe tools\preview_web_portal.py `
  --weather-image "C:\path\to\weather.png" `
  --port 8787 `
  --timezone America/Los_Angeles
```

默认命令只发布 `--weather-image` 指定的 Weather 原版画面。若要审阅其他插件，加入 `--showcase`，并为每个希望出现的插件重复传入 `--plugin-image PLUGIN_ID=PNG`：

```powershell
.\.tmp\portal-preview-venv\Scripts\python.exe tools\preview_web_portal.py `
  --weather-image "C:\path\to\weather.png" `
  --plugin-image "sports_dashboard=C:\path\to\sports-dashboard.png" `
  --plugin-image "live_radar=C:\path\to\live-radar.png" `
  --plugin-image "steam_charts=C:\path\to\steam-charts.png" `
  --port 8787 `
  --timezone America/Los_Angeles `
  --showcase
```

`--plugin-image` 可重复使用，当前接受 `sports_dashboard`、`live_radar`、`steam_charts`、`stocktracker` 和 `ticketmaster_events`。同一插件不能重复传入；所有文件都必须是可验证的真实 PNG。展厅只为显式传入的图片创建页面和播放列表条目，不再生成结构化 HTML 样例，也不会伪造缺少的插件。特别是，没有传入 Mini Weather 原版 PNG 时，展厅不会显示一个虚构的 Mini Weather 页面。只加 `--showcase` 而不加任何 `--plugin-image` 时，仍只有 Weather；未加 `--showcase` 时传入 `--plugin-image` 会被拒绝。

命令会隐藏输入并二次确认一个至少 12 个字符的本地管理员密码。随后访问 `http://127.0.0.1:8787`，用户名为 `admin`。Weather 的门户标题显示“当地天气”，其他页面保留各自的插件标题；外层时间标签统一显示“当地时间”。PNG 内部已有的地点、标题和数据保持原样，门户外层只增加鉴权、导航、来源、新鲜度和当地时间信息。

详情页和自动播放页按浏览器实际可用视口动态排版，并以 `object-fit: contain` 完整显示原版画面。播放导航是悬浮层，可在闲置后自动隐藏，也可由用户显式隐藏和恢复。浏览器支持且允许时可以进入 Fullscreen API；不支持、被拒绝或退出全屏时，普通动态视口布局仍必须可用。Tesla 官方没有为 Model Y 网页浏览器公开一个固定不变的 CSS viewport，且[官方手册](https://www.tesla.com/ownersmanual/modely/en_us/GUID-59E1B254-DB74-4178-B727-CE12EA84A2F7.html)说明触摸屏呈现可能随车辆配置、软件版本和市场区域变化，因此不要按网络传闻硬编码 Tesla 分辨率。

本地入口固定监听 `127.0.0.1`，没有可配置的 `--host` 参数，也不信任代理请求头。账本、管理员凭据和会话密钥都位于进程专用的临时目录；按 `Ctrl+C` 退出后自动清理。`--password-file` 只用于自动化验收，普通预览不要把密码写入命令行、环境变量或仓库文件。

## 从设备当前缓存建立全量本地账本

需要核对全部已配置插件时，可以只读访问设备现有的 `/playlist` 与同源 `plugin_instance_image` 缓存路由。采集器不会调用 provider、renderer、刷新队列或屏幕写入，也不会保存原始管理页 HTML、CSRF token 或其他管理页内容。输出目录必须是新的隔离目录：

```powershell
python tools/capture_device_portal.py `
  --device-url http://ColoredEpaperFrame.local `
  --output .tmp\device-portal-capture `
  --timezone America/Los_Angeles
```

采集完成后，把经过校验的缓存 PNG 接入正式 Edition Ledger。`--publication-root` 必须指向全新且不存在的 publication 目录；bootstrap 会先在同盘 staging 目录完整发布和验证，再原子切换目标，绝不在已有账本上做半量覆盖：

```powershell
python tools/bootstrap_web_rasters.py `
  --bundle .tmp\device-portal-capture\bundle `
  --snapshot .tmp\device-portal-capture\snapshot `
  --publication-root .tmp\device-portal-publication
```

这条受支持的只读页面路由不公开受保护配置里的真实实例 UUID，因此本地验收包会根据播放列表、插件和名称生成稳定的合成实例 UUID。插件标题、播放顺序、时间窗以及 PNG 字节保持设备当前状态；合成身份只用于本地 bootstrap。将来让独立 cloud worker 长期接管时，仍应使用脱敏配置导出中的真实 UUID、设置修订和结构修订。无论使用哪条路径，门户的 GET 请求都只读 Edition Ledger，不能反向触发设备或插件执行。

## 服务器与 DNS

建议从 2 vCPU、4 GB RAM、40 GB SSD 的 Debian/Ubuntu VPS 起步，并安装 Docker Engine 与固定版本 Docker Compose `5.3.1`。该版本是当前精确 Compose JSON 合同已经实际验证的工具链；安装来源、架构和官方 SHA-256 必须按[生产上线门禁](web_portal_go_live_gate.md)记录。为服务器设置静态公网地址，在 DNS 中创建 `epaper.example.com` 的 DNS A/AAAA 记录；只有服务器确实配置了 IPv6 时才发布 AAAA 记录。开放 TCP 80/443 和 UDP 443，SSH 仅允许管理地址访问。不要在防火墙或云安全组中开放 8080。

将仓库放在 VPS，例如 `/opt/epapersystem-web/app`，然后准备部署目录：

```sh
cd /opt/epapersystem-web/app/deploy/web
cp .env.example .env
GIT_SHA="$(git rev-parse HEAD)"
sed -i 's/epaper.example.com/你的实际域名/' .env
sed -i "s/REPLACE_WITH_GIT_SHA/$GIT_SHA/g" .env
sudo install -d -o 10001 -g 10001 -m 0750 config publication worker-data worker-data/health provider-secrets session-key
sudo install -d -o 10001 -g 10001 -m 0700 security
sudo install -d -o root -g root -m 0700 backups /opt/epapersystem-web/restore-drills
```

这些目录对应不同信任域：

- `web` 只能以只读方式访问 `publication`、`security`、单独挂载的 `session-key/web_portal_secret_key`，以及 `worker-data/health` 这个不含供应商值的状态子目录。
- `worker` 只能读取 `config` 与 `provider-secrets`，并写入 `publication` 和 `worker-data`；它看不到管理员哈希和会话密钥。
- `admin` 只在显式启用 `admin` profile 时运行，只能写 `security`，并使用 `network_mode: none`。

Compose 使用同一个非 root Python 镜像运行这三个 Python 服务；Python 基础镜像和 Caddy 都固定到 SHA-256 digest。Caddy 只加入 `edge` 与内部 `portal` 网络，`web` 只加入内部 `portal`，持有供应商密钥的 `worker` 只加入独立的 `worker_egress`，三者不会再落入默认共享网络。Caddy 自动申请并续期 HTTPS 证书。更新镜像 digest 必须作为代码变更审阅；不要把 `.env`、上述目录或备份提交到 Git。

## 一次性脱敏导入

在能够只读访问现有 `device.json` 和上传目录的机器上运行导出器。该命令不刷新插件、不访问供应商、不控制树莓派屏幕：

```sh
python3 tools/export_web_config.py \
  --config /var/lib/inkypi/config/device.json \
  --uploads-root /var/lib/inkypi/data/uploads \
  --output /tmp/epaper-web-export
```

产物包括 `web_config.json`、`manifest.json`、`required_secrets.json` 和内容寻址的 `resources/`。导出器保留播放列表顺序、时间窗、实例 UUID、设置修订与刷新设置；明文凭据（包括 URL userinfo）会变为确定性的 `${CANONICAL_NAME}` 或 `${URL_USERINFO_*}`。缺失或不可信的本地文件会在 manifest 中标为 `unavailable`，而不是静默消失。

把整个导出目录传到 VPS 的临时位置，核对 manifest 后再导入。以下 `--delete` 只针对专用的 `deploy/web/config` 目录，因此应先确认当前目录无误：

```sh
cd /opt/epapersystem-web/app/deploy/web
sudo rsync -a --delete --chown=10001:10001 /tmp/epaper-web-export/ ./config/
jq -r '.secrets[].name' config/required_secrets.json
```

`jq` 只显示所需的规范密钥名称。每个真实值都应来自独立的安全文件，并以占位符名称安装到 `provider-secrets`；不要把值写入 `.env`、命令参数、聊天记录或 shell 历史。URL userinfo 文件的内容是原始的 `username:password` 段：

```sh
sudo install -o 10001 -g 10001 -m 0600 /安全来源/TICKETMASTER_API_KEY \
  ./provider-secrets/TICKETMASTER_API_KEY
```

用独立文件生成 Flask 会话密钥，命令不会把密钥打印到终端：

```sh
sudo sh -c 'umask 077; openssl rand -base64 48 > ./session-key/web_portal_secret_key'
sudo chown 10001:10001 ./session-key/web_portal_secret_key
```

导入完成后，云端独立使用这份配置、资源和服务端密钥；它不再依赖树莓派在线。独立 worker 调用与 Epaper 相同的插件 renderer，把每个实例的原版 raster 作为不可变 edition 发布到内容寻址账本。网页读取只访问已发布账本和同源鉴权资源，不会因打开页面而调用 provider、renderer、调度器或实体屏。

## 构建、bootstrap 密码与启动

首次安装必须先准备完整初始 Edition Ledger，并先构建以完整 Git SHA 为标签的镜像；否则严格 preflight 会因账本或管理员凭据缺失而按设计失败。随后运行无网络、仅挂载 `security` 的一次性管理员服务。密码至少 12 个字符，输入不会回显，也不会进入环境变量。管理员凭据建立后，先运行不带公网 URL 的严格 preflight，成功后才启动长期服务；完整顺序与主机级前置条件见 [生产上线门禁](web_portal_go_live_gate.md)：

```sh
docker compose --project-name epapersystem-web --env-file .env -f compose.yaml \
  pull caddy
WEB_PORTAL_GIT_REVISION="$GIT_SHA" docker compose \
  --project-name epapersystem-web --env-file .env -f compose.yaml build
docker compose --project-name epapersystem-web --env-file .env -f compose.yaml \
  --profile admin run --rm admin
sudo bash ./ops/preflight.sh .env
docker compose --project-name epapersystem-web --env-file .env -f compose.yaml up -d
sudo bash ./ops/preflight.sh .env "https://你的实际域名"
```

默认的 `up` 不会启动 profile 中的 `admin`。worker 每个周期会原子写入 `worker-data/health/worker-state.json`：循环完成时间用于 liveness，`last_successful_publication_at` 只在真正发布新 edition 时前进；全量 unavailable 或异常周期会标为 degraded/failed，不能伪装成成功。worker healthcheck 只确认循环仍在推进，供应商暂时失败不会触发无意义的重启循环。

账本默认每个实例保留 32 个不可变版本，目录版本保留 64 个；超过上限且不再被 current 指针引用的记录与 CAS 资源会被安全清理。可在 `.env` 中调高 `WEB_EDITION_RETENTION_PER_INSTANCE` 或 `WEB_CATALOG_RETENTION`，但应先按实例数量、图片大小和磁盘容量重新估算空间。

检查三个长期运行容器的 healthcheck 和有限日志：

```sh
docker compose --env-file .env -f compose.yaml ps
docker compose --env-file .env -f compose.yaml logs --tail=100 web worker caddy
curl --fail --silent --show-error https://你的实际域名/livez
curl --fail --silent --show-error https://你的实际域名/readyz
```

`/healthz` 与 `/livez` 都只证明 Web 进程能响应，并保持固定的 `{"status":"ok"}` 兼容格式。`/readyz` 还会检查管理员凭据、只读账本、完整配置目录，以及每个当前 raster 的真实大小和 SHA-256；发布物的重型检查默认只缓存 10 秒并进行线程内合并，管理员凭据与 worker 状态仍逐次检查。任一配置项缺少可显示 current 时返回 HTTP 200 `degraded/publication_degraded`；已有完整 last-good 画面但 worker 过旧或最近失败时返回 HTTP 200 `degraded`。这些状态都不能通过生产上线门禁。无法登录、账本不可读、无任何可显示发布物或当前资源损坏时返回 HTTP 503 `not_ready`。公开响应只含固定状态码，不返回路径、异常文本、供应商 URL 或密钥。

随后在普通浏览器登录并核对播放列表、时间、fresh/stale 状态和资源，再在 Model Y 停车状态下访问同一 HTTPS 地址、登录、收藏并测试自动轮播。车辆固件和地区策略可能影响浏览器在行驶中的可用性，不要在驾驶中操作页面。

## 备份与回滚

发布账本和 worker 数据可能正被写入。备份本身包含秘密，必须加密存放并限制访问。服务器需要安装 `age`，并在 `/root/epaper-backup-recipients.txt` 中只保存一个或多个公开 recipient；对应 identity 必须离线保管，不得留在 VPS。使用仓库内的 root 脚本，避免跨 `sudo` 权限、管道假成功和截断文件问题：

```sh
cd /opt/epapersystem-web/app/deploy/web
sudo bash ./ops/backup.sh /root/epaper-backup-recipients.txt "$PWD/backups"
```

脚本使用 `set -Eeuo pipefail`，只暂停实际正在运行的 worker；`web` 仍可读取一致的已发布账本。Compose 给 worker 3 分钟优雅停止窗口，备份脚本也显式等待最多 180 秒并核对容器以退出码 0 停止，避免在 120 秒渲染预算内强杀进程。内部是 `tar --numeric-owner -czf - ... | age -R ...`，所有产物先写入同盘 `.partial` 目录，root 计算校验和并把 Git revision、web image ID 与 Caddy image ID 同时写入加密包及外部索引；随后用 GNU `sync -f` 落盘文件与 stage 目录，原子改名为 `backups/web-时间戳/` 后再次同步备份根目录，才报告 committed。备份根必须保持 `root:root`、`0700`，UID 10001 不能替换版本目录；任一步失败都会优先按原状态恢复 worker，再尽力删除 partial。加密包名为 `state.tar.gz.age`，校验命令为 `sha256sum -c state.tar.gz.age.sha256`。

恢复演练不会停止、移动或覆盖现役服务。它先把密文、固定格式校验和、identity 与 release metadata 绑定到一个全新的 root-only 临时目录，只解密一次；解包前拒绝符号链接、硬链接、设备和 FIFO，并限制所有路径。随后从备份记录的 Git revision 导出当时的 Compose/Caddyfile，核对 web 镜像 revision label、SQLite、管理员凭据、会话密钥和每个 required provider secret；正式 runtime 会逐张读取当前 raster，worker factory 则只使用隔离 scratch 目录启动验证。演练结束即清除解密目录：

```sh
cd /opt/epapersystem-web/app/deploy/web
sudo bash ./ops/restore-drill.sh \
  "$PWD/backups/web-YYYYMMDDTHHMMSSZ" \
  /安全离线介质/epaper-backup-identity.txt \
  /opt/epapersystem-web/restore-drills
```

只有 restore drill 成功、目标 Git commit 与两个已记录镜像仍在本机、且新的 sibling release 已完成同样的 preflight 后，才进入真正切换。这一步属于上线/回滚授权边界：短暂停止 worker，保留现役目录不动，通过版本化 `current` 指针切到完整的新 release，运行 `/livez`、`/readyz` 与登录播放冒烟；失败则把指针切回旧 release，而不是现场 `git switch` 或重新构建可变镜像。不要先搬走现役数据再尝试 checkout/build，也不要用 `up -d --build` 充当回滚。

## 上线前门禁与停止边界

在获取 VPS、真实域名和供应商密钥之前，可以完成并应保留的证据包括：全部单元/安全测试、26 张当前设备 PNG 的字节哈希、本地正式 reader/web 浏览器回归、Compose schema 静态校验、Caddyfile 规则审阅、Bash `-n`、真实 `age` 加解密 round trip，以及备份/恢复脚本的隔离演练。真正的 `docker compose config/build/up`、Caddy `adapt --validate`、provider worker 首轮实际发布、HTTPS/DNS、外网登录、Model Y 实车和生产备份恢复，必须在目标 Linux VPS 与真实材料到位后执行，不能用本机预览替代。

最终上线前按顺序留存：Git SHA；固定 image digest 与本机 image ID；`ops/preflight.sh` 输出；`/livez` 与 `/readyz` JSON；26 个当前 asset 的 SHA-256；worker-state 中的首次真实成功时间；一次加密 backup 路径；一次无触碰现役服务的 restore-drill 结果。任一项缺失都保持在“上线前”，不要切换 DNS 或 `current`。

Caddy 的证书状态保存在独立 named volumes 中，应用回滚不会删除它。只有明确需要重新签发证书时才考虑处理这些 volumes；常规回滚不要执行 `docker compose down -v`。
