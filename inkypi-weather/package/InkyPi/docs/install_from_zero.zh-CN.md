# 从零安装

这份文档从一张空白 microSD 卡开始，一直到 InkyPi Web UI 在局域网里正常打开。

## 你需要准备

- 树莓派，支持 Wi-Fi 或网线。
- microSD 卡，建议 16 GB 或更大。
- 墨水屏。
- 一台和树莓派在同一网络下的电脑。
- Raspberry Pi Imager：<https://www.raspberrypi.com/software/>

默认新手安装脚本按 Waveshare 7.3 英寸彩色墨水屏配置，驱动型号是
`epd7in3e`。其他 Waveshare 和 Pimoroni 屏幕也支持，但需要在命令里指定。

## 1. 写入 Raspberry Pi OS

1. 在电脑上打开 Raspberry Pi Imager。
2. 选择你的树莓派型号。
3. 选择 Raspberry Pi OS Lite 64-bit，找不到时选 Lite 版本即可。
4. 选择 microSD 卡。
5. 点设置齿轮或 `Edit Settings`。
6. 设置：
   - Hostname，例如 `inkypi`。
   - 用户名和密码。
   - Wi-Fi 名称和密码。
   - 地区和时区。
7. 打开 SSH。
8. 写入系统，弹出 microSD 卡，插入树莓派并通电。

第一次启动等待 2-5 分钟。

## 2. 从电脑 SSH 进入树莓派

在电脑终端里运行：

```bash
ssh <用户名>@inkypi.local
```

如果 `.local` 访问失败，到路由器里找到树莓派 IP，然后运行：

```bash
ssh <用户名>@<树莓派IP>
```

Windows 上如果 `ssh` 命令不可用，可以试：

```powershell
C:\Windows\System32\OpenSSH\ssh.exe <用户名>@<树莓派IP>
```

## 3. 安装 Git

```bash
sudo apt-get update
sudo apt-get install -y git
```

## 4. 下载项目

```bash
git clone https://github.com/Feeengyuuu/EpaperSystem.git
cd EpaperSystem/inkypi-weather/package/InkyPi
```

## 5. 运行中文新手安装脚本

默认 Waveshare 7.3 英寸彩色屏：

```bash
sudo bash install/bootstrap.sh --lang zh-CN
```

其他 Waveshare 型号示例：

```bash
sudo bash install/bootstrap.sh --lang zh-CN -W epd7in5_V2
```

Pimoroni Inky 屏幕：

```bash
sudo bash install/bootstrap.sh --lang zh-CN --pimoroni
```

脚本会自动做这些事：

1. 安装 Linux 依赖。
2. 启用 SPI 和 I2C。
3. 创建 `/usr/local/inkypi`。
4. 创建 Python 虚拟环境。
5. 安装并启用 `inkypi` systemd 服务。
6. 如果 `.env` 不存在就创建它。
7. 提示你是否现在添加 API Key。
8. 启动服务并运行健康检查。

API Key 是可选项。安装时直接回车可以跳过，之后也能从网页或命令行补上。

## 6. 重启一次

全新安装建议重启一次，让 SPI/I2C 配置完全生效：

```bash
sudo reboot now
```

等待 1-2 分钟后重新 SSH：

```bash
ssh <用户名>@inkypi.local
cd EpaperSystem/inkypi-weather/package/InkyPi
```

## 7. 验证是否正常

```bash
bash install/healthcheck.sh --lang zh-CN
```

如果健康检查通过，在浏览器打开：

```text
http://inkypi.local
http://<树莓派IP>
```

## 8. 后期添加 API Key

网页方式：

```text
http://<树莓派IP>/api-keys
```

命令行方式：

```bash
python3 install/configure_api_keys.py --env-file .env --lang zh-CN
```

查看所有 Key 的注册地址：

```bash
python3 install/configure_api_keys.py --list --lang zh-CN
```

修改 Key 后重启服务：

```bash
sudo systemctl restart inkypi
```

完整 API Key 表见：[api_keys.zh-CN.md](./api_keys.zh-CN.md)。

## 9. 出问题时复制这些输出

```bash
bash install/healthcheck.sh --lang zh-CN
sudo systemctl status inkypi --no-pager
sudo journalctl -u inkypi -n 120 --no-pager
```

常见处理：

- Web UI 打不开：运行 `sudo systemctl restart inkypi`，再运行 `bash install/healthcheck.sh --lang zh-CN`。
- 屏幕一直空白：运行 `sudo reboot now`。
- 插件提示缺少 Key：打开 `/api-keys`，或运行 `python3 install/configure_api_keys.py --list --lang zh-CN`。
- Waveshare 型号选错：重新运行 `sudo bash install/bootstrap.sh --lang zh-CN -W <型号>`。
