# Model Y 网页门户：生产上线门禁

本文是生产首次部署与恢复的权威顺序；若旧部署说明与本文冲突，以本文为准。

## 首次部署顺序

首次部署不能先运行严格 preflight：管理员凭据和初始 Edition Ledger 此时尚不存在。必须依次完成：

1. 使用干净、已提交的 release checkout，记录完整 Git SHA，并在 `.env` 中同时设置 `WEB_PORTAL_IMAGE=epapersystem-web:<完整 Git SHA>` 与 `WEB_PORTAL_GIT_REVISION=<同一完整 Git SHA>`。这两个值是 build、admin、up、backup 和 restore 共用的 release 输入；preflight 会拒绝 `:local`、漂移标签、dirty checkout、revision 不一致、环境工厂覆盖和非标准 bind 路径。
2. 创建目录。`security` 必须是 `10001:10001/0700`；`publication` 和 `worker-data` 必须由 UID 10001 拥有并可读写。`admin_credentials.json` 此时不应存在；admin 完成后它必须是 `10001:10001/0600`。
3. 在构建前导入经脱敏的 `config/web_config.json`、`config/required_secrets.json` 与 `config/resources/`，逐项安装清单要求的 provider secret，并生成独立 session key。值不得进入 `.env`、命令行或 Git。
4. 将本地已验证的完整初始账本上传到 `deploy/web/publication/`。它应来自 `tools/bootstrap_web_rasters.py` 的全新输出，并已证明所有配置帧均存在、SHA-256 正确且尺寸等于设备分辨率。不要让公网空站点等待首轮 provider 渲染。
5. 以完整 Git SHA 同时作为镜像标签和 revision build arg 只构建一次，再运行无网络的 admin profile 创建凭据；仍不启动长期服务。
6. 运行不带公网 URL 的严格 preflight。它使用解析后的 Compose 合同拒绝额外 bind、环境漂移与可变镜像；web 只读真实账本，worker 只写隔离 scratch。
7. 主机出站防火墙就绪后启动长期服务，等待 worker 完成第一轮真实发布。只有 100% 配置项都有可验证 current raster 时 `/readyz` 才为 `ready`。部分目录返回 HTTP 200 `degraded/publication_degraded`，不能通过上线门禁。
8. 推荐先把专用 staging hostname 解析到 VPS，以该主机名取得 HTTPS 并完成公网 preflight、登录和播放冒烟。切生产名时必须按“修改 `.env` → 不带 URL 的严格 preflight → `docker compose up -d` 重建 Caddy/web → 指向生产 DNS 并等待证书 → 带生产 HTTPS URL 的 preflight与登录冒烟”的顺序执行。DNS 指向本身不算正式上线，只有最终 `ready`、登录与回滚证据全部通过后才宣布上线。脚本拒绝 HTTP、凭据、查询、片段、错误主机和 `degraded`。

主机必须提供 Docker Engine，并固定使用本项目已经过真实规范化 JSON 验证的 Docker Compose `5.3.1`。不要把“任意 v2/v5”视为兼容：精确合同依赖 Compose 展开的字段形状。Compose 应从 [Docker Compose 官方 v5.3.1 release](https://github.com/docker/compose/releases/tag/v5.3.1) 安装并核对官方校验和；Linux x86_64 资产 `docker-compose-linux-x86_64` 的 SHA-256 是 `f9ebc6ebdb19d769b793c245a736caaeb198c62587f13b25c660c13b4987f959`。其他架构必须使用同一 release 对应的官方资产和校验和。还需要 `git`、`curl`、`jq`、`rsync`、`openssl`、`age`、`sqlite3`、GNU `tar` 与 coreutils。正式操作前逐项做能力探测：

```sh
docker version
docker compose version
test "$(docker compose version --short | sed 's/^v//')" = "5.3.1"
docker compose config --help | grep -q -- '--format'
docker compose config --help | grep -q -- '--images'
docker compose config --help | grep -q -- '--hash'
docker compose ps --help | grep -q -- '--status'
command -v git curl jq rsync openssl age sqlite3 awk grep sed sync tar sha256sum stat install
sync --help | grep -q -- '--file-system'
tar --help | grep -q -- '--numeric-owner'
```

```sh
cd /opt/epapersystem-web/app/deploy/web
cp .env.example .env
GIT_SHA="$(git rev-parse HEAD)"
sed -i 's/epaper.example.com/你的实际域名/' .env
# 一次替换同时固定 WEB_PORTAL_IMAGE 与 WEB_PORTAL_GIT_REVISION。
sed -i "s/REPLACE_WITH_GIT_SHA/$GIT_SHA/g" .env

sudo install -d -o 10001 -g 10001 -m 0750 \
  config publication worker-data worker-data/health provider-secrets session-key
sudo install -d -o 10001 -g 10001 -m 0700 security
sudo install -d -o root -g root -m 0700 \
  backups /opt/epapersystem-web/restore-drills

sudo rsync -a --delete --chown=10001:10001 \
  /tmp/epaper-web-export/ ./config/
jq -r '.secrets[].name' config/required_secrets.json
sudo install -o 10001 -g 10001 -m 0600 \
  /安全来源/TICKETMASTER_API_KEY ./provider-secrets/TICKETMASTER_API_KEY
# 对 required_secrets.json 中的每个名称重复上一步，不在终端打印值。
sudo sh -c 'umask 077; openssl rand -base64 48 > ./session-key/web_portal_secret_key'
sudo chown 10001:10001 ./session-key/web_portal_secret_key

sudo rsync -a --delete --chown=10001:10001 \
  /tmp/validated-device-portal-publication/ ./publication/

docker compose --project-name epapersystem-web --env-file .env -f compose.yaml \
  pull caddy
WEB_PORTAL_GIT_REVISION="$GIT_SHA" docker compose \
  --project-name epapersystem-web --env-file .env -f compose.yaml build
docker compose --project-name epapersystem-web --env-file .env -f compose.yaml \
  --profile admin run --rm admin
sudo chown 10001:10001 security/admin_credentials.json
sudo chmod 0600 security/admin_credentials.json

sudo bash ./ops/preflight.sh .env
docker compose --project-name epapersystem-web --env-file .env -f compose.yaml up -d
sudo bash ./ops/preflight.sh .env "https://你的预验收或生产域名"
```

若先使用 staging hostname，完成其验收后再执行生产名切换；不要只改 `.env` 后直接探测旧容器：

```sh
sed -i 's/预验收域名/生产域名/' .env
sudo bash ./ops/preflight.sh .env
docker compose --project-name epapersystem-web --env-file .env -f compose.yaml up -d
# 将生产 DNS 指向 VPS，等待 Caddy 为生产名取得证书。
sudo bash ./ops/preflight.sh .env "https://生产域名"
# 最后再做生产域名的登录、26/26 播放与回滚冒烟。
```

## 主机级控制

- worker 出站规则必须拒绝 link-local、云 metadata、Docker host/bridge、RFC1918、ULA 与 loopback，只允许 DNS 和确有需要的公网 provider。VPS 不应挂载云 IAM 凭据。
- 配置外部 watchdog/告警监控 worker health。Docker 的 `unless-stopped` 不会自动重启仍在运行但 unhealthy 的容器。在 watchdog 上线前，至少需要告警和经审核的人工 `docker compose --project-name epapersystem-web ... restart worker` 手册。
- 公网只开放 Caddy 的 80/443；8080、InkyPi 管理站、刷新/渲染/显示写入接口均不得暴露。

## 备份与灾备边界

`backup.sh` 是同一 Docker daemon 上的加密状态备份与回滚证据，不是 VPS 丢失后的完整灾备。它记录本地 image ID，并要求这些镜像仍在本机。上线前必须二选一：

- 把不可变 web 镜像推送到私有 registry 并记录 digest；或
- 启用供应商加密磁盘快照并完成一次恢复验证。

否则不得把当前备份称为 off-host DR。`/root/epaper-backup-recipients.txt` 应由 root 拥有且禁止组/其他用户写入。

恢复演练必须使用专用 root-only 父目录：

```sh
sudo install -d -o root -g root -m 0700 /opt/epapersystem-web/restore-drills
sudo bash ./ops/restore-drill.sh \
  "$PWD/backups/web-YYYYMMDDTHHMMSSZ" \
  /安全离线介质/epaper-backup-identity.txt \
  /opt/epapersystem-web/restore-drills
```

恢复演练只验证加密状态、记录的 Git/Compose/Caddy 合同、同机镜像、管理员凭据、完整 raster 账本、provider secret 可读性和隔离 worker 构造；它不会替换或重启线上服务。
