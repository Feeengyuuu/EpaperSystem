# Ergou Daily Mac Automation

This package moves the recurring `二狗新闻早报` workflow onto macOS. It refreshes daily content with the OpenAI Responses API, then calls the Codex imagegen CLI with `gpt-image-2` / `img-2` for the final PNG.

## What It Does

- Runs every day at 12:00 local Mac time through `launchd`.
- Uses the current Beijing date for the brief.
- Refreshes concrete headlines, 8 newest-first domestic incidents, Luoyang weather, A-share and U.S. market context, and a three-stock watchlist.
- Generates the final portrait PNG with `img-2` / `gpt-image-2`.
- Writes the JSON and final img-2 prompt next to the image for review.
- Keeps a copy at `~/Pictures/ErgouDaily/latest.png`.

## Install On Mac

Copy this whole folder to the Mac, then run:

```bash
cd tools/ergou_daily_mac
./install.sh
```

Add your API key:

```bash
nano ~/.ergou-daily/.env
```

Set:

```bash
OPENAI_API_KEY=sk-...
```

Make sure the imagegen CLI exists on the Mac. By default the automation expects:

```text
~/.codex/skills/.system/imagegen/scripts/image_gen.py
```

If it lives elsewhere, set this in `~/.ergou-daily/.env`:

```bash
ERGOU_IMAGE_GEN_CLI=/path/to/image_gen.py
```

Run a dry test without calling OpenAI image generation:

```bash
~/.ergou-daily/run_now.sh --dry-run
```

For a one-off test into a custom folder:

```bash
~/.ergou-daily/run_now.sh --dry-run --output-dir ~/Desktop/ergou-test
```

Run a real generation with fresh content and img-2 output:

```bash
~/.ergou-daily/run_now.sh
```

## Configuration

The installer creates `~/.ergou-daily/config.json` from `config.example.json` if it does not already exist.

Important fields:

- `output_dir`: where PNG files are saved.
- `schedule_timezone`: only documented in config; the actual trigger time is controlled by launchd on the Mac.
- `brief_timezone`: date used for the brief, default `Asia/Shanghai`.
- `text_model`: OpenAI model for content refresh, default `gpt-5-mini`.
- `image_model`: locked to `gpt-image-2`.
- `image_size`: default `1024x1536`.
- `image_quality`: default `high`.
- `image_gen_cli`: path to Codex imagegen CLI.
- `weather_location`: default `Luoyang, Henan`.
- `notify`: whether to show a macOS notification after generation.

## Schedule

The LaunchAgent is installed as:

```text
~/Library/LaunchAgents/com.ergou.daily-news.plist
```

It runs:

```text
~/.ergou-daily/run_now.sh
```

at 12:00 every day in the Mac's local timezone.

## Uninstall

```bash
~/.ergou-daily/uninstall.sh
```

This unloads the LaunchAgent. It leaves generated images and config files in place.
