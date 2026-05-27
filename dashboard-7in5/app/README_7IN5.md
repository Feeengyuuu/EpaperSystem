# 7.5-inch Waveshare e-Paper adaptation

这个目录是从 `czuryk/Waveshare-ePaper-10.85-dashboard` 复制出来的 7.5 寸适配版。

当前阶段使用 `800x480` 原生重排布局。图标、圆形仪表、天气图标等元素不会被横向硬压缩。

流程是：

1. 读取原项目的数据结构和资源。
2. 直接渲染成你的 7.5 寸黑白屏 `800x480`。
3. Windows 上先生成预览图。
4. 同一份目录放到 Raspberry Pi Zero 2 W 后，用 `epd7in5_V2` 驱动实屏显示。

## Windows preview

在电脑上运行：

```powershell
cd G:\PersonalProjects\EpaperSystem\dashboard-7in5\app
python preview_7in5.py --mode layout
```

输出文件在：

```text
G:\PersonalProjects\EpaperSystem\dashboard-7in5\app\output\dashboard-7in5-preview.png
G:\PersonalProjects\EpaperSystem\dashboard-7in5\app\output\dashboard-7in5-display.bmp
G:\PersonalProjects\EpaperSystem\dashboard-7in5\app\output\dashboard-10in85-source.png
```

`dashboard-7in5-preview.png` 就是 7.5 寸屏幕要看到的 `800x480` 预览。

## Live 1:1 PC preview

运行本地预览服务器：

```powershell
cd G:\PersonalProjects\EpaperSystem\dashboard-7in5\app
python preview_server.py --port 8765
```

浏览器打开：

```text
http://127.0.0.1:8765/
```

页面里的图片固定是 `800px x 480px`，不做 CSS 缩放。浏览器缩放保持 `100%` 时，就是一比一查看。

## Adapt modes

默认模式是 `layout`：

```powershell
python preview_7in5.py --mode layout
```

可选模式：

```text
layout       直接重新布局成 800x480，不硬性压缩，推荐
squash       保留全部内容，高度不变，横向压缩到 800px
fit          保留比例和全部内容，但上下会留白，内容更小
crop-left    保留原始大小，只看左侧 800px
crop-center  保留原始大小，只看中间 800px
crop-right   保留原始大小，只看右侧 800px
```

正常开发使用 `layout`。其他模式只保留给对比和排查用。

## Raspberry Pi setup

在 Raspberry Pi OS 上启用 SPI：

```bash
sudo raspi-config
```

进入 `Interface Options`，启用 `SPI`，然后重启。

安装系统依赖：

```bash
sudo apt update
sudo apt install -y python3-pip python3-pil python3-numpy git tmux
```

把本目录复制到树莓派，例如：

```bash
/home/pi/dashboard-7in5
```

然后安装 Python 依赖：

```bash
cd /home/pi/dashboard-7in5
python3 -m pip install -r requirements-pi.txt
```

先生成一张本地预览图，确认字体和图片资源正常：

```bash
python3 preview_7in5.py --mode layout
```

再测试实屏刷新一帧：

```bash
python3 run_7in5.py --once --mode layout
```

如果单帧可以显示，再持续运行：

```bash
tmux new -s epaper
python3 run_7in5.py --mode layout
```

按 `Ctrl+B`，松开后按 `D`，可以让程序留在后台。

## Hardware note

当前脚本按你的截图默认使用 Waveshare 7.5 寸黑白二色 `800x480` 屏，也就是 Python 驱动 `epd7in5_V2`。

如果你实际拿到的是红黑白三色、四色、HD、V1 或其他变体，驱动名可能不同，需要把 `run_7in5.py` 里的驱动导入改成对应型号。
