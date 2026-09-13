# 从零安装与个人搭建

这份指南从一张空白 microSD 卡开始，直到你能在局域网中管理自己的墨水屏。安装命令在**树莓派的 SSH 终端**中运行。

## 1. 准备设备与系统

- 树莓派、合适的电源、网络，以及匹配的 Waveshare 或 Pimoroni Inky 墨水屏。
- microSD 卡建议 16 GB 或更大；安装前 `/opt` 至少留 2 GiB，`/var` 至少留 512 MiB。
- 同一局域网中的电脑或手机，用于访问管理页面。
- [Raspberry Pi Imager](https://www.raspberrypi.com/software/)。

在 Imager 中选择你的树莓派、Raspberry Pi OS Lite（优先 64 位）和目标卡。安装器需要 Python 3.11 或更新版本；旧系统请先换用较新的 Raspberry Pi OS。

在系统自定义设置中配置主机名（例如 `inkypi`）、自己的用户名和密码、Wi-Fi、地区与时区，并在远程访问设置中启用 SSH。按 [Raspberry Pi 官方准备指南](https://www.raspberrypi.com/documentation/computers/getting-started.html) 完成写卡。断电连接墨水屏，再插卡通电。

## 2. SSH 登录

在电脑终端或 Windows PowerShell 运行，替换成你在 Imager 里设置的用户名：

```bash
ssh <用户名>@inkypi.local
```

若 `.local` 找不到设备，用路由器里查到的 IP 地址替代 `inkypi.local`。这一步登录成功后，后面的 Linux 命令才是在树莓派上执行。

## 3. 一条命令安装

```bash
curl -fsSL https://raw.githubusercontent.com/Feeengyuuu/EpaperSystem/main/install.sh | sudo bash -s -- --lang zh-CN
```

如系统缺少 curl，先运行 `sudo apt-get update && sudo apt-get install -y curl ca-certificates`。

安装器将下载项目到 `/opt/EpaperSystem`，然后：

1. 引导选择屏幕。默认是 Waveshare 7.3 英寸彩色 HAT E，驱动 `epd7in3e`。
2. 检查树莓派硬件、Python、systemd、磁盘空间和内置驱动。
3. 安装依赖、启用 SPI/I2C、创建服务账户与独立 Python 环境。
4. 安装应用并启用开机启动。
5. 询问是否添加 API Key；直接回车即可全部跳过。
6. 等待服务就绪，显示管理地址、首次登录步骤和诊断命令。

依赖下载速度和设备性能会影响耗时。请等待终端完成；出现错误时，按提示处理后重跑同一命令。脚本不会自动重启设备。

首次安装完成后运行 `sudo reboot`，等待设备重新联网，使 SPI/I2C 设置生效。

### 其他屏幕与自动安装

已知型号时可以直接指定，避免选择菜单：

```bash
curl -fsSL https://raw.githubusercontent.com/Feeengyuuu/EpaperSystem/main/install.sh | sudo bash -s -- --lang zh-CN -W epd7in5_V2
```

Pimoroni 使用 `--pimoroni`，由 Inky 驱动自动识别。通过无交互远程任务执行时加 `--non-interactive --skip-keys`，并明确指定适合自己的屏幕；省略型号会使用 `epd7in3e`。无终端的交互安装会明确停止，不会猜测输入。

内置驱动不代表所有硬件组合都已经实测；请选择与屏幕型号和版本一致的驱动。

### 先查看源码或仅检查环境

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/Feeengyuuu/EpaperSystem.git
cd EpaperSystem
bash install.sh --list-displays
bash install.sh --lang zh-CN --check
sudo bash install.sh --lang zh-CN
```

`--check` 不安装软件、不修改服务或配置，也不驱动屏幕；默认检查 `epd7in3e`，其他屏幕请同时加上 `-W <型号>` 或 `--pimoroni`。完整安装仍需要联网下载依赖。

## 4. 创建自己的管理员密码

浏览器打开安装器打印的地址，如 `http://inkypi.local` 或 `http://<树莓派IP>`。首次访问会引导到 `/auth/setup`。

在 SSH 终端读取一次性配对码：

```bash
sudo cat /var/lib/inkypi/data/security/bootstrap_admin.token
```

把配对码填入网页，设置并确认管理员密码。以后在 `/auth/login` 登录；管理员密码由你自己设置。配对码过期时运行 `sudo inkypi admin bootstrap`，再读取新的配对码。不要把配对码或 API Key 发给别人。

## 5. 搭建自己的信息屏

1. 在设置页填写设备名称、时区、屏幕方向等。
2. 先添加一个不需要密钥的页面，例如图片或使用 Open-Meteo 的天气；填写自己的城市/坐标、单位等参数。
3. 预览并显示该页面，检查内容和实际屏幕方向。
4. 再逐步添加日历、资讯、体育等内容，按插件提示配置来源。
5. 创建自己的播放列表，添加页面并设置轮播间隔。

应用安装成功与某个第三方数据源是否可用是两件事。第一次运行还没有显示过页面时，“没有已提交的当前图片”属于提示；实际显示一张页面后再验收屏幕。

API Key 按需在网页 `/api-keys` 配置，也可从任意目录运行：

```bash
sudo python3 /opt/inkypi/current/install/configure_api_keys.py --env-file /etc/inkypi/inkypi.env --lang zh-CN
sudo systemctl restart inkypi
```

注册地址和各项用途见 [API Key 配置指南](api_keys.zh-CN.md)。运行中的密钥位于 `/etc/inkypi/inkypi.env`，不要在源码目录新建另一份 `.env` 当作运行配置。

## 6. 更新与数据位置

使用在线安装命令的用户，可重新运行同一命令更新。下载目录必须是对应仓库且没有未提交修改；检测到个人源码修改时，安装器会停止自动更新，请先提交或妥善保存修改。

手动克隆的用户在自己的目录运行：

```bash
git pull --ff-only
sudo bash install.sh --lang zh-CN
```

重复安装保留已有设备配置、屏幕型号、管理员账户和 API Key。重跑时的屏幕参数不会覆盖已有配置。如果首次选错屏幕，先停止服务，用 `sudoedit /var/lib/inkypi/config/device.json` 更正 `display_type`，删除旧的 `resolution` 让驱动重新识别，再启动服务；新型号若需要启用 SPI，重新运行安装器并重启设备。

| 内容 | 路径 |
| --- | --- |
| 当前与上一版应用 | `/opt/inkypi/current`、`/opt/inkypi/previous` |
| 实际发布版本 | `/opt/inkypi/releases`，完成更新后只保留当前与上一版 |
| 设备与播放列表配置 | `/var/lib/inkypi/config` |
| 图片、账户等持久数据 | `/var/lib/inkypi/data` |
| 可重新获取的缓存 | `/var/cache/inkypi` |
| 可选服务密钥 | `/etc/inkypi/inkypi.env` |

安装临时 ZIP 放在 `/opt/inkypi/.tmp`，退出时清除。管理自己的 fork 时，可用 `EPAPERSYSTEM_REPO_URL` 指定仓库，并用 `EPAPERSYSTEM_CHECKOUT_DIR` 指定独立下载目录；本地克隆执行则直接使用当前源码。

## 7. 故障排查

下面的命令可以在任意目录执行：

```bash
sudo bash /opt/inkypi/current/install/healthcheck.sh --lang zh-CN --wait 120
sudo systemctl status inkypi --no-pager
sudo journalctl -u inkypi -n 120 --no-pager
```

- 网页打不开：确认电脑和树莓派在同一局域网，用 IP 替代 `.local`；再检查服务。
- 配对码失效：运行 `sudo inkypi admin bootstrap` 后重新读取。
- 忘记管理员密码：运行 `sudo inkypi admin recover`，按提示读取恢复码，在 `/auth/recover` 设置新密码。
- 屏幕空白：确认型号和接线，首次安装后重启，并从网页实际显示一个页面。
- 插件缺少 Key：在 `/api-keys` 按需添加；不需要使用的服务可以保持空白。
- 下载失败：检查树莓派的网络和系统时间，确认能访问 GitHub 与软件源，再重新运行安装命令。
- 健康检查显示 `degraded`：应用已启动，但某项功能或数据源降级，应查看相关日志；这不等于全部插件已正常取数。

默认管理页面用于可信局域网。需要从外网访问时，另行配置私人 VPN 或带 HTTPS 的访问入口。
