#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - macOS Python 3.9+ has zoneinfo.
    ZoneInfo = None  # type: ignore[assignment]


DEFAULT_CONFIG: dict[str, Any] = {
    "output_dir": "~/Pictures/ErgouDaily",
    "brief_timezone": "Asia/Shanghai",
    "schedule_timezone": "Mac local time",
    "weather_location": "Luoyang, Henan",
    "text_model": "gpt-5-mini",
    "image_model": "gpt-image-2",
    "image_size": "1024x1536",
    "image_quality": "high",
    "image_gen_cli": "~/.codex/skills/.system/imagegen/scripts/image_gen.py",
    "notify": True,
    "request_timeout_seconds": 180,
    "rules_path": "rules/ergou_daily_rules.md",
}


WEEKDAYS_CN = "一二三四五六日"


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def expand_path(value: str | Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(value))))


def load_config(config_path: Path | None) -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if config_path and config_path.exists():
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"Config must be a JSON object: {config_path}")
        config.update(loaded)
    validate_config(config)
    return config


def validate_config(config: dict[str, Any]) -> None:
    if str(config.get("image_model")) != "gpt-image-2":
        raise ValueError("This automation is locked to img-2 / gpt-image-2.")


def resolve_rules_path(config: dict[str, Any], config_path: Path | None) -> Path:
    raw_path = Path(str(config.get("rules_path") or DEFAULT_CONFIG["rules_path"]))
    if raw_path.is_absolute():
        return raw_path
    if config_path:
        candidate = config_path.resolve().parent / raw_path
        if candidate.exists():
            return candidate
    return script_dir() / raw_path


def resolve_image_gen_cli(config: dict[str, Any]) -> Path:
    raw = os.environ.get("ERGOU_IMAGE_GEN_CLI") or str(config.get("image_gen_cli"))
    path = expand_path(raw)
    if not path.exists():
        raise RuntimeError(
            "img-2 CLI not found. Install/sync the Codex imagegen skill on this Mac, "
            "or set ERGOU_IMAGE_GEN_CLI in ~/.ergou-daily/.env to the local "
            "image_gen.py path."
        )
    return path


def beijing_now(config: dict[str, Any], now: dt.datetime | None = None) -> dt.datetime:
    if ZoneInfo is None:
        raise RuntimeError("Python 3.9+ is required for zoneinfo timezone support.")
    timezone_name = str(config["brief_timezone"])
    try:
        tz = ZoneInfo(timezone_name)
    except Exception as exc:
        if timezone_name == "Asia/Shanghai":
            tz = dt.timezone(dt.timedelta(hours=8), name="Asia/Shanghai")
        else:
            raise RuntimeError(f"Could not load timezone: {timezone_name}") from exc
    if now is None:
        return dt.datetime.now(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def date_label(date_value: dt.date) -> str:
    weekday = WEEKDAYS_CN[date_value.weekday()]
    return f"{date_value.year}年{date_value.month}月{date_value.day}日 星期{weekday}"


def slug_for_date(date_value: dt.date) -> str:
    return date_value.strftime("%Y%m%d")


def build_content_prompt(
    config: dict[str, Any],
    rules_text: str,
    target_dt: dt.datetime,
) -> str:
    label = date_label(target_dt.date())
    return f"""
你正在为用户生成固定版式的《二狗新闻早报》内容。目标北京日期：{label}。

请联网核查并刷新以下内容：
1. 5条以上今日要闻，必须是具体事件级新闻；每条输出 title + detail，detail 必须包含时间、地点/主体、数字、动作、结果或影响。
2. 8条国内已验证事故/突发/官方通报/主流媒体后续，按最新到最旧排序；每条输出 title + detail，detail 必须包含更新日期、地区、事件类型、处置状态和可核查数字。
3. 河南洛阳天气，摄氏度。
4. A股、美股和必要的休市/最近收盘说明。
5. 三只买入观察，写成观察理由，不构成投资建议。

固定规则如下：
{rules_text}

只返回合法 JSON。不要 Markdown，不要代码围栏，不要解释。
JSON 字段必须包含：date_label, headlines, incidents, weather, markets, watchlist, sources。
headlines 和 incidents 优先使用对象数组：{{"title": "...", "detail": "..."}}。
不要返回只有笼统标题、没有数字和详情的新闻条目。
date_label 必须等于：{label}
weather.location 必须是洛阳或河南洛阳。
sources 至少 3 条，尽量给出真实 URL。
""".strip()


def post_json(url: str, payload: dict[str, Any], api_key: str, timeout: int) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API error {exc.code}: {body}") from exc


def extract_response_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    parts: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            if isinstance(content.get("text"), str):
                parts.append(content["text"])
            elif isinstance(content.get("content"), str):
                parts.append(content["content"])
    return "\n".join(parts).strip()


def parse_json_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("Could not find a JSON object in model output.")
        cleaned = cleaned[start : end + 1]
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("Model output JSON must be an object.")
    return value


def validate_brief(brief: dict[str, Any]) -> None:
    required = ["date_label", "headlines", "incidents", "weather", "markets", "watchlist", "sources"]
    missing = [key for key in required if key not in brief]
    if missing:
        raise ValueError(f"Brief JSON missing required keys: {', '.join(missing)}")
    if not isinstance(brief["date_label"], str) or not brief["date_label"].strip():
        raise ValueError("date_label must be a non-empty string.")
    if not isinstance(brief["headlines"], list) or len(brief["headlines"]) < 5:
        raise ValueError("headlines must contain at least 5 items.")
    if not isinstance(brief["incidents"], list) or len(brief["incidents"]) != 8:
        raise ValueError("incidents must contain exactly 8 items.")
    if not isinstance(brief["weather"], dict):
        raise ValueError("weather must be an object.")
    if not isinstance(brief["markets"], list) or len(brief["markets"]) < 2:
        raise ValueError("markets must contain at least 2 items.")
    if not isinstance(brief["watchlist"], list) or len(brief["watchlist"]) != 3:
        raise ValueError("watchlist must contain exactly 3 items.")
    if not isinstance(brief["sources"], list) or len(brief["sources"]) < 3:
        raise ValueError("sources must contain at least 3 items.")
    for section in ("headlines", "incidents"):
        for item in brief[section]:
            if isinstance(item, str):
                if len(item.strip()) < 16:
                    raise ValueError(f"{section} string items must be descriptive.")
            elif isinstance(item, dict):
                title = str(item.get("title") or "").strip()
                detail = str(item.get("detail") or item.get("details") or "").strip()
                if len(title) < 6 or len(detail) < 18:
                    raise ValueError(f"{section} object items must include title and detailed facts.")
            else:
                raise ValueError(f"{section} items must be strings or objects.")


def fetch_brief(config: dict[str, Any], rules_text: str, target_dt: dt.datetime, raw_output_path: Path) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing. Add it to ~/.ergou-daily/.env or your shell environment.")

    prompt = build_content_prompt(config, rules_text, target_dt)
    payload = {
        "model": str(config["text_model"]),
        "tools": [{"type": "web_search"}],
        "input": prompt,
    }
    response = post_json(
        "https://api.openai.com/v1/responses",
        payload,
        api_key,
        int(config["request_timeout_seconds"]),
    )
    raw_output_path.write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")
    text = extract_response_text(response)
    brief = parse_json_text(text)
    validate_brief(brief)
    return brief


def sample_brief(target_dt: dt.datetime) -> dict[str, Any]:
    label = date_label(target_dt.date())
    return {
        "date_label": label,
        "headlines": [
            {"title": "高考报名1290万人", "detail": "教育部披露2026年全国统一高考6月7日开考，并为1.4万余名残障考生提供合理便利。"},
            {"title": "物流景气重回扩张", "detail": "中物联6月3日发布5月物流业景气指数50.3%，环比回升0.6个百分点，东中西部均回升。"},
            {"title": "网络餐饮专项抽检", "detail": "市场监管总局覆盖14个平台、24个城市、875家外卖单位，不合格率2.3%。"},
            {"title": "强对流黄色预警", "detail": "中央气象台提示华北等地8级以上雷暴大风，部分地区可能达到10级以上。"},
            {"title": "暑运准备提前启动", "detail": "铁路部门围绕热门线路加开列车、优化候补规则，并同步强化高温和强降雨应急预案。"},
        ],
        "incidents": [
            {"title": "强降雨风险升级", "detail": "6月4日至5日多地有强降雨，局地小时雨量可超60毫米，需防山洪和城市内涝。"},
            {"title": "地灾防御响应启动", "detail": "自然资源部对江西、湖南、贵州启动地质灾害防御IV级响应，重点防范滑坡和泥石流。"},
            {"title": "安全隐患单位被点名", "detail": "近期7家单位因事故隐患排查不力被通报，监管要求限期整改并压实主体责任。"},
            {"title": "外卖抽检不合格处置", "detail": "网络餐饮专项抽检发现不合格样品后，市场监管总局督促属地核查处置。"},
            {"title": "红河客车事故后续", "detail": "云南红河州4月30日客车侧翻相关企业进入安全通报名单，后续整改被跟踪。"},
            {"title": "烟花爆炸后续整治", "detail": "浏阳烟花厂爆炸事故后，烟花爆竹行业继续推进风险排查和安全整治。"},
            {"title": "京津冀雷暴影响出行", "detail": "强对流天气影响交通、农业和城市运行，局地雷暴大风可达10级以上。"},
            {"title": "暴雨区次生灾害风险", "detail": "黑吉湘桂滇局部有暴雨到大暴雨，气象部门提示关注短时强降水和积涝。"},
        ],
        "weather": {
            "location": "洛阳",
            "summary": "多云间晴，午后注意防晒和补水。",
            "temperature": "22-33°C",
            "air": "东南风2-3级，空气质量以良为主",
        },
        "markets": [
            "A股：以上一交易日收盘为准，关注成交量和北向资金变化。",
            "美股：以上一交易日收盘为准，科技股和利率预期仍是主线。",
            "汇率/商品：关注美元指数、人民币中间价和原油价格波动。",
        ],
        "watchlist": [
            {"name": "沪深300ETF", "reason": "宽基回撤后观察成交放量和资金净流入。"},
            {"name": "宁德时代", "reason": "新能源链修复时作为龙头观察，不追高。"},
            {"name": "微软", "reason": "AI 基建和云业务仍是中长期观察主线。"},
        ],
        "sources": [
            {"title": "dry-run sample", "url": "local"},
            {"title": "dry-run sample", "url": "local"},
            {"title": "dry-run sample", "url": "local"},
        ],
    }


def brief_to_img2_prompt(brief: dict[str, Any], rules_text: str) -> str:
    weather = brief["weather"]
    watchlist = brief["watchlist"]
    sources = brief["sources"]

    def news_title_detail(item: Any) -> tuple[str, str]:
        if isinstance(item, dict):
            title = str(item.get("title") or item.get("headline") or "").strip()
            detail = str(item.get("detail") or item.get("details") or item.get("summary") or "").strip()
            return title, detail
        return str(item).strip(), ""

    def numbered(items: list[Any]) -> str:
        rows: list[str] = []
        for idx, item in enumerate(items, 1):
            title, detail = news_title_detail(item)
            if detail:
                rows.append(f"{idx}. {title}\n   细节：{detail}")
            else:
                rows.append(f"{idx}. {title}")
        return "\n".join(rows)

    watch_lines = []
    for item in watchlist:
        if isinstance(item, dict):
            watch_lines.append(f"- {item.get('name', '')}: {item.get('reason', '')}")
        else:
            watch_lines.append(f"- {item}")

    source_lines = []
    for source in sources[:6]:
        if isinstance(source, dict):
            source_lines.append(f"- {source.get('title', '')}: {source.get('url', '')}")
        else:
            source_lines.append(f"- {source}")

    layout_rules = """
Layout repair requirements:
- Use a clear 96px outer safe margin on left and right; no card, title, badge, or text may touch the side edges.
- Use a centered content column about 832px wide.
- Give every card at least 32px internal padding and 24px spacing between cards.
- Keep the main flow single-column: title block, 今日要闻 card, 今日意外 card, then weather/market, then gold watchlist.
- The bottom weather and market blocks may sit in two columns, but each column must have visible breathing room and a 28px gap.
- Keep all list text shorter visually: each item should be one compact line or two short wrapped lines.
- Render news as short title plus smaller detail line. Detail lines must include the specific numbers and context supplied below.
- Do not make the footer tiny or edge-to-edge; keep it inside the same safe margin.
- Do not render the JSON schema or any prompt instructions in the image.
""".strip()

    return f"""
Use img-2 / gpt-image-2 to generate one finished portrait Chinese daily-news image.

Hard requirements:
- Final image size and composition should fit a 1024x1536 vertical phone wallpaper/news card.
- Use the exact Chinese text supplied below as much as possible.
- Preserve the approved visual identity: large red title, subtitle, Beijing-date pill, newspaper/city background, white rounded content cards, red numbered badges, sections for 今日要闻 / 今日意外 / 洛阳天气 / 市场快照, a gold 三只买入观察 table, and a source/footer line.
- Do not redesign into a marketing poster. Do not add unrelated icons, logos, QR codes, watermarks, or English filler text.
- Avoid garbled characters. Keep typography clean, high-contrast, and readable.
- This is a daily news image, not investment advice.

{layout_rules}

Exact content to render:
标题：二狗新闻早报
副标题：1分钟知天下事
日期：{brief["date_label"]}

今日要闻：
{numbered(list(brief["headlines"])[:5])}

今日意外：
{numbered(list(brief["incidents"])[:8])}

洛阳天气：
地点：{weather.get("location", "洛阳")}
摘要：{weather.get("summary", "")}
温度：{weather.get("temperature", "")}
空气/风力：{weather.get("air", "")}

市场快照：
{numbered(list(brief["markets"])[:4])}

三只买入观察：
{chr(10).join(watch_lines[:3])}

页脚：
来源：{" / ".join(source_lines[:4])}
内容由自动任务生成，请以原始来源为准。
""".strip()


def notify(title: str, message: str) -> None:
    if platform.system() != "Darwin":
        return
    script = f'display notification {json.dumps(message)} with title {json.dumps(title)}'
    try:
        subprocess.run(["osascript", "-e", script], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return


def write_output_paths(output_dir: Path, target_dt: dt.datetime, dry_run: bool) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = "dry-run" if dry_run else "daily"
    stem = f"ergou-{slug_for_date(target_dt.date())}-{suffix}"
    return output_dir / f"{stem}.json", output_dir / f"{stem}.prompt.txt", output_dir / f"{stem}.png"


def run_img2_cli(config: dict[str, Any], prompt_path: Path, png_path: Path, dry_run: bool) -> None:
    image_gen_cli = resolve_image_gen_cli(config)
    command = [
        sys.executable,
        str(image_gen_cli),
        "generate",
        "--model",
        "gpt-image-2",
        "--prompt-file",
        str(prompt_path),
        "--size",
        str(config["image_size"]),
        "--quality",
        str(config["image_quality"]),
        "--output-format",
        "png",
        "--out",
        str(png_path),
        "--force",
        "--no-augment",
    ]
    if dry_run:
        command.append("--dry-run")
    subprocess.run(command, check=True)


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the Ergou daily-news brief with img-2.")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.json.")
    parser.add_argument("--env", type=Path, default=None, help="Path to .env file.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Override configured output_dir.")
    parser.add_argument("--dry-run", action="store_true", help="Use sample content and run img-2 CLI dry-run.")
    parser.add_argument("--no-notify", action="store_true", help="Disable macOS notification for this run.")
    args = parser.parse_args(argv)

    default_env = script_dir() / ".env"
    load_env_file(args.env or default_env)
    config = load_config(args.config)
    if args.output_dir is not None:
        config["output_dir"] = str(args.output_dir)
    target_dt = beijing_now(config)
    output_dir = expand_path(str(config["output_dir"]))
    output_dir.mkdir(parents=True, exist_ok=True)

    rules_path = resolve_rules_path(config, args.config)
    rules_text = rules_path.read_text(encoding="utf-8")
    json_path, prompt_path, png_path = write_output_paths(output_dir, target_dt, args.dry_run)
    raw_output_path = output_dir / f"ergou-{slug_for_date(target_dt.date())}-raw-response.json"

    if args.dry_run:
        brief = sample_brief(target_dt)
    else:
        brief = fetch_brief(config, rules_text, target_dt, raw_output_path)
    validate_brief(brief)

    img2_prompt = brief_to_img2_prompt(brief, rules_text)
    json_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
    prompt_path.write_text(img2_prompt, encoding="utf-8")

    run_img2_cli(config, prompt_path, png_path, args.dry_run)

    latest_path = output_dir / "latest.png"
    if png_path.exists():
        shutil.copyfile(png_path, latest_path)

    print(f"Wrote JSON:   {json_path}")
    print(f"Wrote prompt: {prompt_path}")
    if png_path.exists():
        print(f"Wrote PNG:    {png_path}")
        print(f"Latest:       {latest_path}")
    else:
        print(f"Dry-run only: img-2 payload was checked, no PNG was generated.")

    if png_path.exists() and bool(config.get("notify", True)) and not args.no_notify:
        notify("二狗新闻早报", f"已用 img-2 生成：{png_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
