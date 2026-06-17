from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

import feedparser
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont, ImageOps
import pytz
import requests

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.context_cache import write_context
from utils.app_utils import get_font, get_fonts
from utils.massive_market_data import MassiveMarketData, MassiveMarketDataError, load_massive_api_key
from utils.theme_utils import get_theme_context, get_theme_palette

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-5-nano"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_TITLE = "整点新闻"
DEFAULT_FONT = "Microsoft YaHei"
BACKGROUND_IMAGE = "background_world_news.png"
PLUGIN_DIR = Path(__file__).resolve().parent
TITLE_BACKGROUND_IMAGE = "title_bg_global_radar.png"
TITLE_BACKGROUND_SIZE = (325, 65)
SUMMARY_SCHEMA_VERSION = "fresh-hard-news-rss-only-dedupe-v7"
DEFAULT_FEEDS = """BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml
BBC World|https://feeds.bbci.co.uk/news/world/rss.xml
NPR|https://feeds.npr.org/1001/rss.xml
NYTimes World|https://rss.nytimes.com/services/xml/rss/nyt/World.xml
Guardian World|https://www.theguardian.com/world/rss"""
MARKET_GROUPS = {
    "a_share": [
        ("000001.SS", "上证指数"),
        ("399001.SZ", "深证成指"),
        ("399006.SZ", "创业板指"),
    ],
    "us_stock": [
        ("^GSPC", "标普500"),
        ("^IXIC", "纳斯达克"),
        ("^DJI", "道琼斯"),
    ],
}

SECTION_LABELS = {
    "top": "今日头条",
    "a_share": "A股今日",
    "us_stock": "美股今日",
}

TRADITIONAL_PHRASE_REPLACEMENTS = (
    ("繁體中文", "简体中文"),
    ("臺灣", "台湾"),
    ("台灣", "台湾"),
    ("烏克蘭", "乌克兰"),
    ("俄羅斯", "俄罗斯"),
    ("歐盟", "欧盟"),
    ("美國", "美国"),
    ("英國", "英国"),
    ("日本國會", "日本国会"),
    ("國會", "国会"),
    ("總統", "总统"),
    ("行政院", "行政院"),
    ("立法院", "立法院"),
    ("證券", "证券"),
    ("颱風", "台风"),
)

TRADITIONAL_TO_SIMPLIFIED = str.maketrans({
    "與": "与",
    "專": "专",
    "業": "业",
    "東": "东",
    "絲": "丝",
    "兩": "两",
    "嚴": "严",
    "喪": "丧",
    "個": "个",
    "臨": "临",
    "為": "为",
    "麗": "丽",
    "舉": "举",
    "義": "义",
    "烏": "乌",
    "樂": "乐",
    "喬": "乔",
    "習": "习",
    "鄉": "乡",
    "書": "书",
    "買": "买",
    "亂": "乱",
    "爭": "争",
    "於": "于",
    "雲": "云",
    "亞": "亚",
    "產": "产",
    "畝": "亩",
    "親": "亲",
    "褻": "亵",
    "億": "亿",
    "僅": "仅",
    "從": "从",
    "倉": "仓",
    "儀": "仪",
    "價": "价",
    "眾": "众",
    "優": "优",
    "會": "会",
    "傘": "伞",
    "偉": "伟",
    "傳": "传",
    "傷": "伤",
    "倫": "伦",
    "偽": "伪",
    "儲": "储",
    "兒": "儿",
    "黨": "党",
    "內": "内",
    "兩": "两",
    "蘭": "兰",
    "關": "关",
    "興": "兴",
    "養": "养",
    "獸": "兽",
    "冊": "册",
    "軍": "军",
    "農": "农",
    "衝": "冲",
    "決": "决",
    "況": "况",
    "凍": "冻",
    "劃": "划",
    "劉": "刘",
    "則": "则",
    "剛": "刚",
    "創": "创",
    "別": "别",
    "刪": "删",
    "劑": "剂",
    "辦": "办",
    "務": "务",
    "動": "动",
    "勢": "势",
    "勛": "勋",
    "勝": "胜",
    "勞": "劳",
    "區": "区",
    "協": "协",
    "單": "单",
    "賣": "卖",
    "衛": "卫",
    "卻": "却",
    "廠": "厂",
    "歷": "历",
    "厲": "厉",
    "壓": "压",
    "縣": "县",
    "參": "参",
    "雙": "双",
    "發": "发",
    "變": "变",
    "敘": "叙",
    "葉": "叶",
    "號": "号",
    "嘆": "叹",
    "聽": "听",
    "啟": "启",
    "吳": "吴",
    "員": "员",
    "問": "问",
    "單": "单",
    "喚": "唤",
    "營": "营",
    "嘗": "尝",
    "嚇": "吓",
    "國": "国",
    "圖": "图",
    "圓": "圆",
    "團": "团",
    "園": "园",
    "壞": "坏",
    "堅": "坚",
    "報": "报",
    "場": "场",
    "塊": "块",
    "塵": "尘",
    "墊": "垫",
    "塢": "坞",
    "墳": "坟",
    "墜": "坠",
    "壘": "垒",
    "壟": "垄",
    "壯": "壮",
    "聲": "声",
    "壺": "壶",
    "處": "处",
    "備": "备",
    "複": "复",
    "夢": "梦",
    "夥": "伙",
    "夾": "夹",
    "奪": "夺",
    "奮": "奋",
    "奧": "奥",
    "婦": "妇",
    "媽": "妈",
    "妝": "妆",
    "姍": "姗",
    "娛": "娱",
    "婁": "娄",
    "婦": "妇",
    "嬰": "婴",
    "學": "学",
    "寧": "宁",
    "寶": "宝",
    "實": "实",
    "審": "审",
    "寫": "写",
    "寬": "宽",
    "寵": "宠",
    "對": "对",
    "尋": "寻",
    "導": "导",
    "將": "将",
    "專": "专",
    "尷": "尴",
    "屆": "届",
    "屬": "属",
    "歲": "岁",
    "島": "岛",
    "峽": "峡",
    "崗": "岗",
    "嶺": "岭",
    "嶼": "屿",
    "川": "川",
    "幣": "币",
    "帥": "帅",
    "師": "师",
    "帳": "帐",
    "帶": "带",
    "幀": "帧",
    "幫": "帮",
    "幹": "干",
    "庫": "库",
    "廁": "厕",
    "廂": "厢",
    "廈": "厦",
    "廣": "广",
    "廟": "庙",
    "廢": "废",
    "廳": "厅",
    "異": "异",
    "彈": "弹",
    "彙": "汇",
    "彎": "弯",
    "張": "张",
    "強": "强",
    "歸": "归",
    "錄": "录",
    "徵": "征",
    "後": "后",
    "徑": "径",
    "徹": "彻",
    "恆": "恒",
    "惡": "恶",
    "愛": "爱",
    "慮": "虑",
    "慶": "庆",
    "憂": "忧",
    "憑": "凭",
    "應": "应",
    "懷": "怀",
    "態": "态",
    "戲": "戏",
    "戶": "户",
    "戰": "战",
    "才": "才",
    "撲": "扑",
    "執": "执",
    "擴": "扩",
    "掃": "扫",
    "揚": "扬",
    "擾": "扰",
    "撫": "抚",
    "拋": "抛",
    "搶": "抢",
    "護": "护",
    "報": "报",
    "擔": "担",
    "擬": "拟",
    "攏": "拢",
    "擇": "择",
    "掛": "挂",
    "採": "采",
    "擺": "摆",
    "攜": "携",
    "攝": "摄",
    "攤": "摊",
    "擊": "击",
    "據": "据",
    "擠": "挤",
    "擴": "扩",
    "敵": "敌",
    "數": "数",
    "斂": "敛",
    "斃": "毙",
    "斕": "斓",
    "斷": "断",
    "無": "无",
    "舊": "旧",
    "時": "时",
    "晉": "晋",
    "暫": "暂",
    "曆": "历",
    "術": "术",
    "樸": "朴",
    "機": "机",
    "殺": "杀",
    "雜": "杂",
    "權": "权",
    "條": "条",
    "來": "来",
    "楊": "杨",
    "極": "极",
    "構": "构",
    "標": "标",
    "樣": "样",
    "樹": "树",
    "檢": "检",
    "樓": "楼",
    "歡": "欢",
    "歐": "欧",
    "步": "步",
    "殘": "残",
    "毆": "殴",
    "殼": "壳",
    "氣": "气",
    "沒": "没",
    "沖": "冲",
    "澤": "泽",
    "潔": "洁",
    "濟": "济",
    "漲": "涨",
    "災": "灾",
    "為": "为",
    "烴": "烃",
    "煉": "炼",
    "煙": "烟",
    "熱": "热",
    "燈": "灯",
    "燒": "烧",
    "爾": "尔",
    "牆": "墙",
    "獨": "独",
    "獲": "获",
    "獵": "猎",
    "環": "环",
    "現": "现",
    "瑪": "玛",
    "畫": "画",
    "異": "异",
    "當": "当",
    "疇": "畴",
    "癥": "症",
    "發": "发",
    "皺": "皱",
    "盜": "盗",
    "監": "监",
    "盤": "盘",
    "盧": "卢",
    "眾": "众",
    "著": "着",
    "矚": "瞩",
    "礎": "础",
    "禮": "礼",
    "禍": "祸",
    "種": "种",
    "稱": "称",
    "穩": "稳",
    "窮": "穷",
    "竊": "窃",
    "競": "竞",
    "筆": "笔",
    "築": "筑",
    "簡": "简",
    "簽": "签",
    "糧": "粮",
    "糾": "纠",
    "紅": "红",
    "紋": "纹",
    "納": "纳",
    "紐": "纽",
    "純": "纯",
    "紙": "纸",
    "級": "级",
    "紛": "纷",
    "組": "组",
    "結": "结",
    "絕": "绝",
    "絲": "丝",
    "經": "经",
    "綁": "绑",
    "綠": "绿",
    "網": "网",
    "綱": "纲",
    "綜": "综",
    "綫": "线",
    "維": "维",
    "緊": "紧",
    "緒": "绪",
    "線": "线",
    "緩": "缓",
    "編": "编",
    "緣": "缘",
    "縣": "县",
    "縱": "纵",
    "總": "总",
    "績": "绩",
    "織": "织",
    "繼": "继",
    "續": "续",
    "纖": "纤",
    "罰": "罚",
    "羅": "罗",
    "羈": "羁",
    "義": "义",
    "習": "习",
    "翹": "翘",
    "聯": "联",
    "聖": "圣",
    "聞": "闻",
    "職": "职",
    "聰": "聪",
    "聯": "联",
    "肅": "肃",
    "脅": "胁",
    "脈": "脉",
    "腦": "脑",
    "臟": "脏",
    "臨": "临",
    "與": "与",
    "興": "兴",
    "舉": "举",
    "艦": "舰",
    "艙": "舱",
    "藝": "艺",
    "節": "节",
    "蘇": "苏",
    "藍": "蓝",
    "虛": "虚",
    "號": "号",
    "蟲": "虫",
    "蠟": "蜡",
    "補": "补",
    "裝": "装",
    "製": "制",
    "複": "复",
    "規": "规",
    "視": "视",
    "覽": "览",
    "覺": "觉",
    "觸": "触",
    "訂": "订",
    "計": "计",
    "訊": "讯",
    "訓": "训",
    "記": "记",
    "訟": "讼",
    "訪": "访",
    "設": "设",
    "許": "许",
    "訴": "诉",
    "診": "诊",
    "詐": "诈",
    "評": "评",
    "詞": "词",
    "試": "试",
    "詩": "诗",
    "誠": "诚",
    "話": "话",
    "誕": "诞",
    "誘": "诱",
    "語": "语",
    "誤": "误",
    "說": "说",
    "課": "课",
    "誰": "谁",
    "調": "调",
    "談": "谈",
    "請": "请",
    "諾": "诺",
    "謀": "谋",
    "謂": "谓",
    "謊": "谎",
    "謝": "谢",
    "謠": "谣",
    "證": "证",
    "識": "识",
    "譯": "译",
    "議": "议",
    "護": "护",
    "讀": "读",
    "變": "变",
    "讓": "让",
    "豐": "丰",
    "豬": "猪",
    "貝": "贝",
    "負": "负",
    "財": "财",
    "責": "责",
    "賢": "贤",
    "敗": "败",
    "賬": "账",
    "貨": "货",
    "質": "质",
    "販": "贩",
    "貪": "贪",
    "貧": "贫",
    "貿": "贸",
    "賀": "贺",
    "資": "资",
    "賓": "宾",
    "賠": "赔",
    "賴": "赖",
    "賺": "赚",
    "購": "购",
    "賽": "赛",
    "贊": "赞",
    "趙": "赵",
    "趕": "赶",
    "趨": "趋",
    "跡": "迹",
    "踐": "践",
    "蹤": "踪",
    "車": "车",
    "軌": "轨",
    "軟": "软",
    "載": "载",
    "較": "较",
    "輔": "辅",
    "輛": "辆",
    "輝": "辉",
    "輩": "辈",
    "輪": "轮",
    "輯": "辑",
    "輸": "输",
    "轉": "转",
    "轟": "轰",
    "辦": "办",
    "辭": "辞",
    "農": "农",
    "邊": "边",
    "遙": "遥",
    "遜": "逊",
    "遞": "递",
    "遠": "远",
    "適": "适",
    "遷": "迁",
    "選": "选",
    "遺": "遗",
    "醫": "医",
    "釋": "释",
    "釐": "厘",
    "重": "重",
    "鈴": "铃",
    "銀": "银",
    "銷": "销",
    "銳": "锐",
    "鋪": "铺",
    "錄": "录",
    "錢": "钱",
    "錦": "锦",
    "錯": "错",
    "鍊": "链",
    "鍵": "键",
    "鍾": "钟",
    "鎖": "锁",
    "鎮": "镇",
    "鏡": "镜",
    "鐘": "钟",
    "鐵": "铁",
    "鐵": "铁",
    "鑑": "鉴",
    "鑒": "鉴",
    "長": "长",
    "門": "门",
    "閃": "闪",
    "閉": "闭",
    "開": "开",
    "閒": "闲",
    "間": "间",
    "閣": "阁",
    "隊": "队",
    "階": "阶",
    "際": "际",
    "陽": "阳",
    "險": "险",
    "隱": "隐",
    "雜": "杂",
    "離": "离",
    "難": "难",
    "電": "电",
    "點": "点",
    "靈": "灵",
    "響": "响",
    "頁": "页",
    "項": "项",
    "順": "顺",
    "須": "须",
    "預": "预",
    "領": "领",
    "頭": "头",
    "頒": "颁",
    "頻": "频",
    "題": "题",
    "額": "额",
    "顏": "颜",
    "願": "愿",
    "類": "类",
    "風": "风",
    "飛": "飞",
    "飯": "饭",
    "飲": "饮",
    "館": "馆",
    "馬": "马",
    "駐": "驻",
    "驅": "驱",
    "驗": "验",
    "驚": "惊",
    "體": "体",
    "髮": "发",
    "鬥": "斗",
    "魚": "鱼",
    "鮮": "鲜",
    "鳥": "鸟",
    "鳴": "鸣",
    "麥": "麦",
    "黃": "黄",
    "齊": "齐",
    "齡": "龄",
    "龍": "龙",
})


def _enabled(value: Any) -> bool:
    return value is True or str(value).lower() in {"1", "true", "on", "yes"}


def _clean_text(value: str, max_len: int = 260) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:max_len]


def _parse_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(low, min(high, parsed))


def _safe_json_load(path: Path, default: Any) -> Any:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read JSON cache %s: %s", path, exc)
    return default


def _safe_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        path.write_text(text, encoding="utf-8")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


class DailyAINews(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["api_key"] = {
            "required": True,
            "service": "OpenAI or Groq",
            "expected_key": "OPEN_AI_SECRET or GROQ_API_KEY",
        }
        params["available_fonts"] = sorted({
            f.get("name") or f.get("font_family")
            for f in get_fonts()
            if f.get("name") or f.get("font_family")
        })
        if DEFAULT_FONT not in params["available_fonts"]:
            params["available_fonts"].append(DEFAULT_FONT)
        return params

    def generate_image(self, settings, device_config):
        dimensions = self.get_dimensions(device_config)

        tz_name = device_config.get_config("timezone") or "America/Los_Angeles"
        now = datetime.now(pytz.timezone(tz_name))

        try:
            brief = self._get_brief(settings, device_config, now)
        except Exception as exc:
            logger.exception("Daily AI news failed")
            brief = self._fallback_brief(settings, now, str(exc))

        brief = self._simplify_chinese_payload(brief)
        self._write_news_context(brief, now)
        theme_context = get_theme_context(device_config, now=now)
        return self._render(dimensions, settings, brief, now, theme_context)

    def _write_news_context(self, brief: dict[str, Any], now: datetime) -> None:
        payload = brief.get("brief") if isinstance(brief, dict) else {}
        if not isinstance(payload, dict):
            return

        items = []
        for item in (payload.get("top") or [])[:7]:
            if isinstance(item, dict):
                title = str(item.get("title") or "").strip()
                why = str(item.get("why") or "").strip()
            else:
                title = str(item or "").strip()
                why = ""
            if title:
                items.append({"title": title[:120], "why": why[:140]})

        write_context(
            "daily_ai_news",
            {
                "kind": "news",
                "source": "Daily AI News",
                "summary": str(payload.get("lede") or "").strip()[:160],
                "items": items,
                "sources": payload.get("sources") or [],
                "from_cache": bool(brief.get("from_cache")),
            },
            generated_at=brief.get("generated_at") or now,
            ttl_seconds=24 * 60 * 60,
        )

    def _get_brief(self, settings, device_config, now: datetime) -> dict[str, Any]:
        model = (settings.get("model") or DEFAULT_MODEL).strip()
        feeds_text = settings.get("feed_urls") or DEFAULT_FEEDS
        max_items = _parse_int(settings.get("max_items"), 22, 6, 40)
        force_refresh = _enabled(settings.get("force_refresh"))
        date_key = now.strftime("%Y-%m-%d")
        cache_key = self._cache_key(date_key, model, feeds_text, max_items, settings.get("region_focus"))

        cache_file = self._cache_dir() / "brief.json"
        cached = _safe_json_load(cache_file, {})
        if cached.get("cache_key") == cache_key and not force_refresh:
            cached["from_cache"] = True
            return cached

        items = self._fetch_items(feeds_text, max_items)
        items = self._rank_news_items(items, now)[:max_items]
        if not items:
            stale = cached if cached.get("brief") else None
            if stale:
                stale["from_cache"] = True
                stale["warning"] = "新闻源暂不可用，显示旧缓存。"
                return stale
            raise RuntimeError("No RSS items could be fetched.")

        stale = cached if cached.get("brief") else None
        market_snapshot = self._fetch_market_snapshot(now, device_config)
        openai_key = device_config.load_env_key("OPEN_AI_SECRET") or device_config.load_env_key("OPENAI_API_KEY")
        groq_key = device_config.load_env_key("GROQ_API_KEY")
        can_call_ai = self._allow_api_call(settings, date_key)

        if openai_key or groq_key:
            if not can_call_ai:
                if stale:
                    stale["from_cache"] = True
                    stale["warning"] = "已达到今日 API 调用上限，显示旧缓存。"
                    return stale
                brief = self._rss_only_brief(items, "已达到今日 API 调用上限，使用 RSS 兜底。")
            else:
                try:
                    brief = self._summarize_with_ai(openai_key, groq_key, model, settings, items, market_snapshot, now)
                except Exception as exc:
                    logger.warning("AI summary failed; using RSS fallback: %s", exc)
                    if stale:
                        stale["from_cache"] = True
                        stale["warning"] = f"AI 摘要失败，显示旧缓存：{str(exc)[:80]}"
                        return stale
                    brief = self._rss_only_brief(items, f"AI 摘要失败，使用 RSS 兜底：{str(exc)[:60]}")
        else:
            brief = self._rss_only_brief(items, "未配置 AI 密钥，使用 RSS 兜底。")

        payload_model = brief.pop("_model", model)
        used_ai = bool(brief.pop("_used_ai", False))
        payload_warning = brief.pop("_fallback_warning", "")
        payload = {
            "cache_key": cache_key,
            "date": date_key,
            "generated_at": now.isoformat(),
            "model": payload_model,
            "items": items[:max_items],
            "market_snapshot": market_snapshot,
            "brief": brief,
            "from_cache": False,
        }
        if payload_warning:
            payload["warning"] = payload_warning
        payload = self._simplify_chinese_payload(payload)
        _safe_json_write(cache_file, payload)
        if used_ai:
            self._record_api_call(date_key)
        return payload

    def _cache_dir(self) -> Path:
        path = Path(self.get_plugin_dir("cache"))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _cache_key(self, date_key: str, model: str, feeds_text: str, max_items: int, region_focus: Any) -> str:
        raw = "\n".join([SUMMARY_SCHEMA_VERSION, date_key, model, feeds_text, str(max_items), str(region_focus or "")])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _allow_api_call(self, settings, date_key: str) -> bool:
        limit = _parse_int(settings.get("daily_api_limit"), 1, 1, 5)
        state = _safe_json_load(self._cache_dir() / "state.json", {})
        if state.get("date") != date_key:
            return True
        return int(state.get("calls") or 0) < limit

    def _record_api_call(self, date_key: str) -> None:
        state_file = self._cache_dir() / "state.json"
        state = _safe_json_load(state_file, {})
        if state.get("date") != date_key:
            state = {"date": date_key, "calls": 0}
        state["calls"] = int(state.get("calls") or 0) + 1
        _safe_json_write(state_file, state)

    def _parse_feeds(self, feeds_text: str) -> list[tuple[str, str]]:
        feeds = []
        for line in (feeds_text or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "|" in line:
                name, url = [part.strip() for part in line.split("|", 1)]
            else:
                url = line
                name = re.sub(r"^https?://", "", url).split("/")[0]
            if url.startswith("http"):
                feeds.append((name or url, url))
        return feeds

    def _fetch_items(self, feeds_text: str, max_items: int) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        seen = set()
        for source, url in self._parse_feeds(feeds_text):
            try:
                resp = requests.get(
                    url,
                    timeout=12,
                    headers={"User-Agent": "InkyPi Daily AI News/1.0"},
                )
                resp.raise_for_status()
                feed = feedparser.parse(resp.content)
            except Exception as exc:
                logger.warning("RSS fetch failed for %s: %s", url, exc)
                continue

            per_feed = 0
            for entry in feed.entries[:12]:
                title = _clean_text(entry.get("title", ""), 150)
                if not title:
                    continue
                key = re.sub(r"\W+", "", title.lower())
                if key in seen:
                    continue
                seen.add(key)
                items.append({
                    "source": source,
                    "title": title,
                    "summary": _clean_text(
                        entry.get("summary", "") or entry.get("description", ""),
                        280,
                    ),
                    "published": _clean_text(entry.get("published", "") or entry.get("updated", ""), 80),
                    "link": _clean_text(entry.get("link", ""), 220),
                })
                per_feed += 1
                if len(items) >= max_items or per_feed >= 5:
                    break
            if len(items) >= max_items:
                break
        return items

    def _rank_news_items(self, items: list[dict[str, str]], now: datetime) -> list[dict[str, str]]:
        def score(index: int, item: dict[str, str]) -> float:
            title = item.get("title", "")
            summary = item.get("summary", "")
            text = f"{title} {summary}".lower()
            value = 100 - index * 0.05

            published = self._parse_published(item.get("published", ""))
            if published:
                age_hours = max(0.0, (now - published.astimezone(now.tzinfo)).total_seconds() / 3600)
                if age_hours <= 30:
                    value += 35
                elif age_hours <= 72:
                    value += 18
                elif age_hours > 168:
                    value -= 35

            hard_terms = [
                "空袭", "袭击", "打击", "冲突", "谈判", "协议", "制裁", "关税",
                "事故", "调查", "追责", "死亡", "警告", "宣布", "发布", "通过",
                "选举", "法院", "央行", "利率", "通胀", "油价", "股价", "上涨",
                "下跌", "收盘", "升级", "停火", "导弹", "军", "政府", "总统",
            ]
            broad_terms = [
                "人生", "心理健康", "遗产", "生活", "旅行", "文化", "观点",
                "专访", "研究显示", "为什么", "如何", "可能对你有益", "意义",
            ]
            value += sum(10 for term in hard_terms if term in text)
            value -= sum(16 for term in broad_terms if term in text)
            if not self._has_cjk(title):
                value -= 5
            return value

        return [item for _score, item in sorted(
            ((score(index, item), item) for index, item in enumerate(items)),
            key=lambda pair: pair[0],
            reverse=True,
        )]

    def _parse_published(self, value: str) -> datetime | None:
        if not value:
            return None
        try:
            parsed = parsedate_to_datetime(value)
        except Exception:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except Exception:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=pytz.UTC)
        return parsed

    def _fetch_market_snapshot(self, now: datetime, device_config=None) -> dict[str, Any]:
        snapshot: dict[str, Any] = {"generated_at": now.isoformat(), "groups": {}}
        massive_client = None
        massive_key = load_massive_api_key(device_config)
        if massive_key:
            massive_client = MassiveMarketData(massive_key)
            snapshot["macro"] = self._fetch_massive_macro(massive_client)
        for group, symbols in MARKET_GROUPS.items():
            rows = []
            for symbol, name in symbols:
                row = self._fetch_massive_quote(massive_client, symbol, name)
                if not row:
                    row = self._fetch_yahoo_quote(symbol, name)
                if row:
                    rows.append(row)
            snapshot["groups"][group] = rows
        return snapshot

    def _fetch_massive_quote(self, massive_client, symbol: str, name: str) -> dict[str, Any] | None:
        if massive_client is None:
            return None
        try:
            return massive_client.fetch_quote(symbol, name)
        except MassiveMarketDataError as exc:
            logger.warning("Massive market quote failed for %s: %s", symbol, exc)
            return None

    def _fetch_massive_macro(self, massive_client) -> dict[str, Any]:
        try:
            treasury_yields = massive_client.fetch_treasury_yields(limit=1)
        except MassiveMarketDataError as exc:
            logger.warning("Massive macro fetch failed: %s", exc)
            treasury_yields = []
        return {
            "source": "massive",
            "treasury_yields": treasury_yields[:1],
        }

    def _fetch_yahoo_quote(self, symbol: str, name: str) -> dict[str, Any] | None:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}"
        try:
            resp = requests.get(
                url,
                params={"range": "5d", "interval": "1d"},
                timeout=10,
                headers={"User-Agent": "InkyPi Daily AI News/1.0"},
            )
            resp.raise_for_status()
            result = resp.json()["chart"]["result"][0]
        except Exception as exc:
            logger.warning("Market quote fetch failed for %s: %s", symbol, exc)
            return None

        meta = result.get("meta") or {}
        timestamps = result.get("timestamp") or []
        quote_data = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        closes = [value for value in quote_data.get("close", []) if isinstance(value, (int, float))]

        price = meta.get("regularMarketPrice")
        if not isinstance(price, (int, float)) and closes:
            price = closes[-1]
        previous = meta.get("chartPreviousClose") or meta.get("previousClose")
        if not isinstance(previous, (int, float)) and len(closes) >= 2:
            previous = closes[-2]
        if not isinstance(price, (int, float)):
            return None

        change = None
        change_pct = None
        if isinstance(previous, (int, float)) and previous:
            change = price - previous
            change_pct = change / previous * 100

        as_of = ""
        if timestamps:
            try:
                as_of = datetime.fromtimestamp(timestamps[-1], tz=pytz.UTC).isoformat()
            except Exception:
                as_of = ""

        return {
            "symbol": symbol,
            "name": name,
            "price": round(float(price), 2),
            "change": round(float(change), 2) if isinstance(change, (int, float)) else None,
            "change_pct": round(float(change_pct), 2) if isinstance(change_pct, (int, float)) else None,
            "as_of": as_of,
            "currency": meta.get("currency") or "",
            "exchange": meta.get("exchangeName") or meta.get("fullExchangeName") or "",
            "source": "yahoo",
        }

    def _summarize_with_ai(
        self,
        openai_key: str,
        groq_key: str,
        model: str,
        settings,
        items: list[dict[str, str]],
        market_snapshot: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        backends = []
        if openai_key:
            backends.append({
                "provider": "openai",
                "api_key": openai_key,
                "model": model,
                "base_url": None,
            })
        if groq_key:
            backends.append({
                "provider": "groq",
                "api_key": groq_key,
                "model": settings.get("groq_model") or settings.get("ai_groq_model") or DEFAULT_GROQ_MODEL,
                "base_url": "https://api.groq.com/openai/v1",
            })

        errors = []
        for backend in backends:
            try:
                brief = self._summarize_with_openai(
                    backend["api_key"],
                    backend["model"],
                    settings,
                    items,
                    market_snapshot,
                    now,
                    provider=backend["provider"],
                    base_url=backend["base_url"],
                )
                brief["_model"] = backend["model"] if backend["provider"] == "openai" else f"groq:{backend['model']}"
                brief["_used_ai"] = True
                return brief
            except Exception as exc:
                reason = self._ai_error_reason(exc)
                errors.append(f"{backend['provider']}: {reason}")
                logger.warning("Daily AI news provider failed: %s", errors[-1])

        raise RuntimeError("; ".join(errors) or "No AI provider is configured.")

    def _summarize_with_openai(
        self,
        api_key: str,
        model: str,
        settings,
        items: list[dict[str, str]],
        market_snapshot: dict[str, Any],
        now: datetime,
        provider: str = "openai",
        base_url: str | None = None,
    ) -> dict[str, Any]:
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        client = OpenAI(**client_kwargs)
        system = (
            "你是中文新闻编辑。只根据用户提供的 RSS 条目写简体中文每日简报。"
            "所有中文必须使用简体中文，不得使用繁体中文或港澳台繁体词形。"
            "top 新闻只能来自 RSS 条目，不能使用市场行情、常识或背景知识补充。"
            "新闻必须强调今天或最近一次更新的具体变化，标题要具体，避免宏观空话。"
            "输出必须是一个 JSON object，不要 Markdown。"
        )
        user = {
            "date": now.strftime("%Y-%m-%d"),
            "style": settings.get("region_focus") or "china_global",
            "output_schema": {
                "lede": "不超过32字的总览",
                "top": [{"title": "包含对象、动作、结果的具体标题", "why": "为什么重要"}],
                "sources": ["来源名"],
            },
            "rules": [
                "所有输出字段只使用简体中文；如果素材是繁体中文，必须转换为简体中文再写入 JSON",
                "top 给 7 条，每条 title 18到28字，why 16到26字",
                "top 只选最近 24-48 小时内有明确新进展的硬新闻；优先冲突、政策、事故、市场、外交、法律、重大科技治理",
                "top 不允许使用 market_snapshot、股票指数或你自己的背景知识生成新闻",
                "不得写 RSS items 中不存在的人名、机构、政策或市场事件",
                "top 中同一事件只能出现一次，不要用不同标题重复同一军事行动、谈判或事故",
                "title 必须包含具体人物/机构/地点/事件动作/结果，禁止只写宏观分类",
                "避免使用“引发讨论”“风险升级”“议题焦点”“全球媒体聚焦”这类宽泛标题，除非同时写清具体事件",
                "不要把人生、心理健康、生活方式、科普解释稿、旧背景稿放进 top，除非它们是当天重大政策或公共事件",
                "lede 必须概括今天最重要的新变化，不要写“今日新闻简报已生成”",
                "尽量保留素材里的数字、地点、人物、机构和动作",
                "sources 只列实际用到的来源名，最多5个",
            ],
            "items": items,
        }

        user_text = json.dumps(user, ensure_ascii=False)
        try:
            response_kwargs = {
                "model": model,
                "instructions": system,
                "input": user_text,
                "max_output_tokens": 3000,
            }
            if model.startswith("gpt-5"):
                response_kwargs["reasoning"] = {"effort": "minimal"}
                response_kwargs["text"] = {"verbosity": "low"}
            if provider == "openai":
                response = client.responses.create(**response_kwargs)
                content = (getattr(response, "output_text", "") or "").strip()
            else:
                raise RuntimeError(f"{provider} does not support Responses API in this plugin")
        except Exception as exc:
            logger.warning("Responses API unavailable for %s/%s, falling back to chat completions: %s", provider, model, exc)
            chat_kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_text},
                ],
            }
            try:
                response = client.chat.completions.create(max_completion_tokens=3000, **chat_kwargs)
            except TypeError:
                response = client.chat.completions.create(max_tokens=3000, **chat_kwargs)
            content = (response.choices[0].message.content or "").strip()

        if not content:
            raise RuntimeError("OpenAI returned an empty summary.")
        brief = self._parse_brief_json(content)
        brief["top"] = self._dedupe_top_items(brief.get("top") or [], items)
        if not str(brief.get("lede") or "").strip():
            brief["lede"] = self._fallback_lede(brief["top"])
        return self._simplify_chinese_payload(brief)

    def _parse_brief_json(self, content: str) -> dict[str, Any]:
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, re.S)
            if not match:
                raise
            data = json.loads(match.group(0))

        top = self._as_list(data.get("top"), 7)
        lede = str(data.get("lede") or "").strip()
        if not lede or lede == "今日新闻简报已生成。":
            lede = self._fallback_lede(top)
        return self._simplify_chinese_payload({
            "lede": lede[:48],
            "top": top,
            "a_share": self._as_market_block(data.get("a_share")),
            "us_stock": self._as_market_block(data.get("us_stock")),
            "sources": self._as_list(data.get("sources"), 5),
        })

    def _as_list(self, value: Any, limit: int) -> list[Any]:
        if isinstance(value, list):
            return value[:limit]
        if value:
            return [value]
        return []

    def _as_text_list(self, value: Any, limit: int) -> list[str]:
        return [self._module_text(item) for item in self._as_list(value, limit) if self._module_text(item)]

    def _as_market_block(self, value: Any) -> dict[str, str]:
        if isinstance(value, dict):
            return {
                "summary": str(value.get("summary") or value.get("title") or value.get("text") or "")[:48],
                "analysis": str(value.get("analysis") or value.get("why") or value.get("reason") or "")[:56],
            }
        items = self._as_text_list(value, 2)
        return {
            "summary": items[0] if items else "",
            "analysis": items[1] if len(items) > 1 else "",
        }

    def _rss_only_brief(self, items: list[dict[str, str]], warning: str) -> dict[str, Any]:
        top = []
        sources = []
        for item in items:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            source = str(item.get("source") or "").strip()
            summary = str(item.get("summary") or "").strip()
            why = summary[:36] if summary else (source[:36] or "来自 RSS 源。")
            top.append({"title": title[:32], "why": why})
            if source and source not in sources:
                sources.append(source)
            if len(top) >= 7:
                break

        top = self._dedupe_top_items(top, items)
        return {
            "lede": self._fallback_lede(top),
            "top": top,
            "a_share": {"summary": "A股行情由行情接口补充", "analysis": "AI 不可用时仍显示可抓取数据。"},
            "us_stock": {"summary": "美股行情由行情接口补充", "analysis": "AI 不可用时仍显示可抓取数据。"},
            "sources": sources[:5],
            "_model": "rss-fallback",
            "_used_ai": False,
            "_fallback_warning": warning,
        }

    def _fallback_lede(self, top: list[Any]) -> str:
        if top:
            headline, _why = self._news_text(top[0])
            if headline:
                return headline[:32]
        return "今日硬新闻更新"

    def _dedupe_top_items(self, top: list[Any], source_items: list[dict[str, str]]) -> list[Any]:
        result = []
        for item in top:
            headline, _why = self._news_text(item)
            if not headline:
                continue
            if any(self._similar_news_title(headline, self._news_text(existing)[0]) for existing in result):
                continue
            result.append(item)
            if len(result) >= 7:
                return result

        for source in source_items:
            title = str(source.get("title") or "").strip()
            if not title or not self._has_cjk(title):
                continue
            if any(self._similar_news_title(title, self._news_text(existing)[0]) for existing in result):
                continue
            summary = str(source.get("summary") or source.get("source") or "").strip()
            result.append({"title": title[:32], "why": summary[:36]})
            if len(result) >= 7:
                break
        return result

    def _ai_error_reason(self, exc: Exception) -> str:
        status = getattr(exc, "status_code", None)
        response = getattr(exc, "response", None)
        if status is None and response is not None:
            status = getattr(response, "status_code", None)
        text = re.sub(r"\s+", " ", str(exc or "")).strip()
        if len(text) > 180:
            text = text[:180].rstrip()
        return f"HTTP {status}: {text}" if status else (text or type(exc).__name__)

    def _similar_news_title(self, left: str, right: str) -> bool:
        left_chars = {char for char in left if "\u4e00" <= char <= "\u9fff"}
        right_chars = {char for char in right if "\u4e00" <= char <= "\u9fff"}
        if not left_chars or not right_chars:
            return False
        overlap = len(left_chars & right_chars) / max(1, min(len(left_chars), len(right_chars)))
        shared_hot_terms = any(term in left and term in right for term in ("伊朗", "空袭", "煤矿", "太空人", "黎巴嫩", "基辅", "教宗"))
        return overlap >= 0.56 or (shared_hot_terms and overlap >= 0.38)

    def _fallback_brief(self, settings, now: datetime, error: str) -> dict[str, Any]:
        return {
            "date": now.strftime("%Y-%m-%d"),
            "generated_at": now.isoformat(),
            "model": settings.get("model") or DEFAULT_MODEL,
            "brief": {
                "lede": "AI新闻暂不可用，等待下次刷新。",
                "top": [{"title": "新闻简报生成失败", "why": error[:48]}],
                "a_share": {"summary": "A股行情暂不可用", "analysis": "等待下一次刷新。"},
                "us_stock": {"summary": "美股行情暂不可用", "analysis": "等待下一次刷新。"},
                "sources": [],
            },
            "from_cache": False,
            "warning": error,
        }

    def _simplify_chinese_payload(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._simplify_chinese_text(value)
        if isinstance(value, list):
            return [self._simplify_chinese_payload(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._simplify_chinese_payload(item) for item in value)
        if isinstance(value, dict):
            return {key: self._simplify_chinese_payload(item) for key, item in value.items()}
        return value

    def _simplify_chinese_text(self, text: str) -> str:
        simplified = str(text)
        if simplified.startswith(("http://", "https://")):
            return simplified
        for traditional, replacement in TRADITIONAL_PHRASE_REPLACEMENTS:
            simplified = simplified.replace(traditional, replacement)
        return simplified.translate(TRADITIONAL_TO_SIMPLIFIED)

    def _render(self, dimensions, settings, payload: dict[str, Any], now: datetime, theme_context=None) -> Image.Image:
        width, height = dimensions
        raw_title = str(settings.get("brief_title") or "").strip()
        title = DEFAULT_TITLE if not raw_title or raw_title == "二狗新闻" else raw_title
        title = self._simplify_chinese_text(title)
        payload = self._simplify_chinese_payload(payload)
        brief = payload.get("brief") or {}

        palette = get_theme_palette(theme_context)
        bg = palette["background"]
        header_bg = palette["header"]
        ink = palette["ink"]
        muted = palette["muted"]
        dim = palette["dim"]
        rule = palette["rule"]
        red = palette["red"]
        gold = palette["gold"]
        cyan = palette["cyan"]
        green = palette["green"]

        img = self._base_background(dimensions, bg, (theme_context or {}).get("mode", "day"))
        draw = ImageDraw.Draw(img)

        font_family = DEFAULT_FONT
        title_font = self._font(font_family, 44, "bold")
        meta_font = self._font(font_family, 14)
        lede_font = self._font(font_family, 25, "bold")
        section_font = self._font(font_family, 18, "bold")
        headline_font = self._font(font_family, 19, "bold")
        side_font = self._font(font_family, 18, "bold")
        body_font = self._font(font_family, 17)
        small_font = self._font(font_family, 16)
        footer_font = self._font(font_family, 13)

        margin = 24
        draw.rectangle((0, 0, width, 74), fill=header_bg)
        date_label = self._date_label(payload, now)
        meta = f"{date_label}  |  {payload.get('model', DEFAULT_MODEL)}"
        if payload.get("from_cache"):
            meta += "  |  cache"
        theme_label = "MIDNIGHT BRIEF" if (theme_context or {}).get("mode") == "night" else "DAY BRIEF"
        meta_left = width - margin - max(self._tw(draw, meta, meta_font), self._tw(draw, theme_label, meta_font))

        draw.text((margin, 17), title, font=title_font, fill=ink)
        draw.line((margin, 64, margin + min(210, self._tw(draw, title, title_font)), 64), fill=red, width=3)
        title_right = margin + self._tw(draw, title, title_font)
        self._draw_title_background(
            img,
            (
                int(title_right + 12),
                8,
                int(meta_left - 12),
                73,
            ),
        )
        draw.text((width - margin - self._tw(draw, meta, meta_font), 20), meta, font=meta_font, fill=muted)
        draw.text((width - margin - self._tw(draw, theme_label, meta_font), 45), theme_label, font=meta_font, fill=cyan)

        lede = str(brief.get("lede") or "")
        draw.line((margin, 88, margin, 124), fill=gold, width=4)
        lede_end = self._draw_wrapped_full(draw, lede, margin + 14, 86, width - margin * 2 - 14, lede_font, ink, 30)

        top_items = list(brief.get("top") or [])
        main_gap = 14
        side_w = 350
        main_w = width - margin * 2 - main_gap - side_w
        top_x = margin
        side_x = top_x + main_w + main_gap
        y = max(136, lede_end + 8)
        top_limit_y = module_y = 360

        self._section_header(draw, "◆ " + SECTION_LABELS["top"], top_x, y, main_w, section_font, red, rule)
        self._section_header(draw, "◇ 快讯补充", side_x, y, side_w, section_font, cyan, rule)
        self._draw_news_items(
            draw,
            top_items[:3],
            top_x,
            y + 28,
            main_w,
            headline_font,
            small_font,
            gold,
            ink,
            muted,
            max_y=top_limit_y,
            start_index=1,
            force_all=True,
            fit_family=font_family,
        )

        side_items = top_items[3:6]
        if len(side_items) < 3:
            side_items.extend(self._rss_sidebar_items(payload, len(top_items), 3 - len(side_items)))
        self._draw_news_items(
            draw,
            side_items,
            side_x,
            y + 28,
            side_w,
            side_font,
            small_font,
            cyan,
            ink,
            muted,
            max_y=top_limit_y,
            start_index=4,
            compact=True,
            force_all=True,
            fit_family=font_family,
            drop_why_if_needed=True,
        )

        module_h = 96
        gap = 20
        col_w = (width - margin * 2 - gap) // 2
        modules = [
            ("▣ " + SECTION_LABELS["a_share"], "a_share", red),
            ("◎ " + SECTION_LABELS["us_stock"], "us_stock", green),
        ]
        for i, (label, key, color) in enumerate(modules):
            x = margin + i * (col_w + gap)
            self._draw_market_module(
                draw,
                label,
                brief,
                payload,
                key,
                x,
                module_y + 6,
                col_w,
                section_font,
                body_font,
                color,
                ink,
                dim,
                green,
                red,
                max_y=module_y + module_h,
            )

        footer = self._footer_text(payload, brief)
        draw.text((margin, height - 20), footer, font=footer_font, fill=dim)
        return img

    def _base_background(self, dimensions, bg, theme_mode="day") -> Image.Image:
        return Image.new("RGB", dimensions, bg)

    def _draw_title_background(self, image: Image.Image, box) -> bool:
        left, top, right, bottom = [int(round(value)) for value in box]
        target_w = max(0, right - left)
        target_h = max(0, bottom - top)
        if target_w < 80 or target_h < 24:
            return False

        source = self._load_title_background()
        if source is None:
            return False

        try:
            resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
            art = ImageOps.contain(source.copy(), (target_w, target_h), method=resample)
            layer = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
            paste_x = int((target_w - art.width) / 2)
            paste_y = int((target_h - art.height) / 2)
            layer.alpha_composite(art, (paste_x, paste_y))
            image.paste(layer.convert("RGB"), (left, top), layer.getchannel("A"))
            return True
        except Exception as exc:
            logger.warning("Daily AI News title background unavailable: %s", exc)
            return False

    @staticmethod
    @lru_cache(maxsize=1)
    def _load_title_background():
        path = PLUGIN_DIR / TITLE_BACKGROUND_IMAGE
        if not path.is_file():
            return None
        try:
            return Image.open(path).convert("RGBA")
        except Exception as exc:
            logger.warning("Could not load Daily AI News title background %s: %s", path, exc)
            return None

    def _font(self, family: str, size: int, weight: str = "normal"):
        if str(family or "").strip().lower() == DEFAULT_FONT.lower():
            font = self._microsoft_yahei_font(size, weight)
            if font:
                return font

        seen = set()
        for candidate in (family, DEFAULT_FONT, "方正新楷近似", "FandolKai"):
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            try:
                font = get_font(candidate, size, weight)
                if font:
                    return font
            except OSError:
                continue
            except Exception:
                continue
        return ImageFont.load_default()

    def _microsoft_yahei_font(self, size: int, weight: str = "normal"):
        plugin_dir = Path(self.get_plugin_dir())
        shared_fonts = plugin_dir.parent / "sports_dashboard" / "fonts"
        local_fonts = plugin_dir / "fonts"
        weight = str(weight or "normal").lower()
        candidates = []
        if weight == "bold":
            candidates.extend([
                local_fonts / "msyhbd.ttc",
                shared_fonts / "msyhbd.ttc",
                Path("C:/Windows/Fonts/msyhbd.ttc"),
                Path("C:/Windows/Fonts/msyhbd.ttf"),
                Path("/usr/share/fonts/opentype/microsoft/msyhbd.ttc"),
                Path("/usr/share/fonts/truetype/microsoft/msyhbd.ttc"),
                Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
                Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"),
            ])
        candidates.extend([
            local_fonts / "msyh.ttc",
            shared_fonts / "msyh.ttc",
            local_fonts / "msyhl.ttc",
            shared_fonts / "msyhl.ttc",
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/msyh.ttf"),
            Path("C:/Windows/Fonts/msyhl.ttc"),
            Path("/usr/share/fonts/opentype/microsoft/msyh.ttc"),
            Path("/usr/share/fonts/truetype/microsoft/msyh.ttc"),
            Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ])
        for path in candidates:
            if not path.exists():
                continue
            try:
                return ImageFont.truetype(str(path), size=int(size))
            except Exception:
                continue
        return None

    def _date_label(self, payload: dict[str, Any], now: datetime) -> str:
        date = payload.get("date") or now.strftime("%Y-%m-%d")
        try:
            return datetime.fromisoformat(date).strftime("%Y.%m.%d")
        except ValueError:
            return now.strftime("%Y.%m.%d")

    def _footer_text(self, payload: dict[str, Any], brief: dict[str, Any]) -> str:
        sources = brief.get("sources") or []
        if sources:
            source_text = " / ".join(str(s) for s in sources[:2])
        else:
            source_text = "RSS + OpenAI"
        warning = payload.get("warning")
        if warning:
            return f"来源: {source_text}  |  {warning[:54]}"
        generated_at = str(payload.get("generated_at") or "")[:16].replace("T", " ")
        return f"来源: {source_text}  |  生成: {generated_at}"

    def _section_header(self, draw, label: str, x: int, y: int, width: int, font, accent, rule) -> None:
        draw.text((x, y), label, font=font, fill=accent)
        underline_w = min(width, max(48, self._tw(draw, label, font) + 8))
        draw.line((x, y + 22, x + underline_w, y + 22), fill=accent, width=2)

    def _market_rows(self, payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
        rows = (((payload.get("market_snapshot") or {}).get("groups") or {}).get(key) or [])
        return rows if isinstance(rows, list) else []

    def _market_lines(self, brief: dict[str, Any], payload: dict[str, Any], key: str) -> list[str]:
        block = brief.get(key) if isinstance(brief.get(key), dict) else {}
        rows = self._market_rows(payload, key)
        if rows:
            summary = self._market_summary(key, rows, str(payload.get("date") or ""))
            analysis = self._market_tone(rows)
            return [summary, analysis]

        lines = [
            str(block.get("summary") or "").strip(),
            str(block.get("analysis") or "").strip(),
        ]
        lines = [line for line in lines if line]
        if lines:
            return lines[:2]

        label = "A股" if key == "a_share" else "美股"
        return [f"{label}行情暂不可用", "等待下一次刷新。"]

    def _market_summary(self, key: str, rows: list[dict[str, Any]], date_key: str) -> str:
        prefix, parts = self._market_summary_parts(key, rows, date_key)
        if not parts:
            return "行情数据暂不可用"
        return prefix + " ".join(f"{name}{self._market_pct(pct)}" for name, pct in parts)

    def _market_summary_parts(self, key: str, rows: list[dict[str, Any]], date_key: str) -> tuple[str, list[tuple[str, float]]]:
        names = {
            "上证指数": "上证",
            "深证成指": "深成",
            "创业板指": "创业板",
            "标普500": "标普",
            "纳斯达克": "纳指",
            "道琼斯": "道指",
        }
        parts = []
        latest_date = ""
        for row in rows[:3]:
            name = names.get(str(row.get("name") or ""), str(row.get("name") or row.get("symbol") or "指数"))
            pct = row.get("change_pct")
            if not isinstance(pct, (int, float)):
                continue
            parts.append((name, float(pct)))
            as_of = str(row.get("as_of") or "")[:10]
            if as_of > latest_date:
                latest_date = as_of
        prefix = "上日 " if key == "us_stock" and date_key and latest_date and latest_date < date_key else ""
        return prefix, parts

    def _market_pct(self, pct: float) -> str:
        text = f"{pct:+.2f}%"
        return text.replace("+0.", "+.").replace("-0.", "-.")

    def _market_change_color(self, pct: float, up_color, down_color, neutral_color):
        if pct > 0:
            return up_color
        if pct < 0:
            return down_color
        return neutral_color

    def _market_tone(self, rows: list[dict[str, Any]]) -> str:
        pct_values = [row.get("change_pct") for row in rows if isinstance(row.get("change_pct"), (int, float))]
        if not pct_values:
            return "指数方向不明，等待更多数据。"
        positive = sum(1 for pct in pct_values if pct > 0)
        negative = sum(1 for pct in pct_values if pct < 0)
        if positive == len(pct_values):
            return "主要指数同步走强，风险偏好改善。"
        if negative == len(pct_values):
            return "主要指数同步走弱，避险情绪升温。"
        if positive > negative:
            return "指数分化偏强，资金仍在选择方向。"
        if negative > positive:
            return "指数分化偏弱，市场情绪较谨慎。"
        return "涨跌互现，市场缺少一致主线。"

    def _draw_market_module(
        self,
        draw,
        label,
        brief,
        payload,
        key,
        x,
        y,
        width,
        section_font,
        body_font,
        accent,
        ink,
        rule,
        up_color,
        down_color,
        max_y=None,
    ) -> int:
        self._section_header(draw, label, x, y, width, section_font, accent, rule)
        y += 31
        rows = self._market_rows(payload, key)
        prefix, parts = self._market_summary_parts(key, rows, str(payload.get("date") or "")) if rows else ("", [])
        if parts:
            y = self._draw_market_change_line(draw, prefix, parts, x, y, width, body_font, ink, up_color, down_color, max_y=max_y)
            return self._draw_module_lines(draw, [self._market_tone(rows)], x, y, width, body_font, ink, max_y=max_y)
        return self._draw_module_lines(draw, self._market_lines(brief, payload, key), x, y, width, body_font, ink, max_y=max_y)

    def _draw_market_change_line(self, draw, prefix, parts, x, y, width, body_font, ink, up_color, down_color, max_y=None) -> int:
        groups = [[("— ", ink)]]
        if prefix:
            groups[0].append((prefix, ink))
        for index, (name, pct) in enumerate(parts):
            group = [
                (name, ink),
                (self._market_pct(pct), self._market_change_color(pct, up_color, down_color, ink)),
            ]
            if index < len(parts) - 1:
                group.append((" ", ink))
            groups.append(group)

        line_h = 20
        if max_y is not None and y + line_h > max_y:
            return y

        cursor_x = x
        limit_x = x + width
        for group in groups:
            group_w = sum(self._tw(draw, text, body_font) for text, _color in group)
            if cursor_x > x and cursor_x + group_w > limit_x:
                y += line_h
                if max_y is not None and y + line_h > max_y:
                    return y
                cursor_x = x
            for text, color in group:
                draw.text((cursor_x, y), text, font=body_font, fill=color)
                cursor_x += self._tw(draw, text, body_font)
        return y + line_h + 3

    def _draw_module(self, draw, label, items, x, y, width, section_font, body_font, accent, ink, rule, max_y=None) -> int:
        self._section_header(draw, label, x, y, width, section_font, accent, rule)
        y += 31
        return self._draw_module_lines(draw, items, x, y, width, body_font, ink, max_y=max_y)

    def _draw_module_lines(self, draw, items, x, y, width, body_font, ink, max_y=None) -> int:
        for item in list(items)[:2]:
            text = self._module_text(item)
            if not text:
                continue
            lines = self._wrap(draw, f"— {text}", body_font, width)
            needed = len(lines) * 20 + 3
            if max_y is not None and y + needed > max_y:
                break
            for line in lines:
                draw.text((x, y), line, font=body_font, fill=ink)
                y += 20
            y += 3
        return y

    def _draw_wrapped_full(self, draw, text, x, y, max_width, font, fill, line_height) -> int:
        for line in self._wrap(draw, text, font, max_width):
            draw.text((x, y), line, font=font, fill=fill)
            y += line_height
        return y

    def _draw_news_items(
        self,
        draw,
        items,
        x,
        y,
        width,
        headline_font,
        why_font,
        marker_color,
        ink,
        muted,
        max_y,
        start_index=1,
        compact=False,
        force_all=False,
        fit_family=None,
        drop_why_if_needed=False,
    ) -> int:
        if force_all:
            return self._draw_news_items_fit(
                draw,
                items,
                x,
                y,
                width,
                marker_color,
                ink,
                muted,
                max_y,
                start_index,
                fit_family or DEFAULT_FONT,
                drop_why_if_needed,
            )

        markers = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨"]
        marker_w = 34 if compact else 40
        title_h = 22 if compact else 24
        why_h = 19 if compact else 20
        gap = 4 if compact else 7
        for offset, item in enumerate(items):
            headline, why = self._news_text(item)
            if not headline and not why:
                continue
            title_lines = self._wrap(draw, headline, headline_font, width - marker_w)
            why_lines = self._wrap(draw, why, why_font, width - marker_w) if why else []
            needed = len(title_lines) * title_h + len(why_lines) * why_h + gap
            if y + needed > max_y:
                break
            number = start_index + offset
            marker = markers[number - 1] if 0 < number <= len(markers) else f"{number}."
            draw.text((x, y - 1), marker, font=headline_font, fill=marker_color)
            text_x = x + marker_w
            for line in title_lines:
                draw.text((text_x, y), line, font=headline_font, fill=ink)
                y += title_h
            for line in why_lines:
                draw.text((text_x, y), line, font=why_font, fill=muted)
                y += why_h
            y += gap
        return y

    def _draw_news_items_fit(
        self,
        draw,
        items,
        x,
        y,
        width,
        marker_color,
        ink,
        muted,
        max_y,
        start_index,
        font_family,
        drop_why_if_needed=False,
    ) -> int:
        styles = [
            {"title": 18, "why": 16, "marker_w": 38, "title_h": 21, "why_h": 18, "gap": 3},
            {"title": 17, "why": 15, "marker_w": 36, "title_h": 20, "why_h": 17, "gap": 2},
            {"title": 16, "why": 14, "marker_w": 34, "title_h": 19, "why_h": 16, "gap": 1},
            {"title": 15, "why": 13, "marker_w": 32, "title_h": 18, "why_h": 15, "gap": 0},
            {"title": 14, "why": 12, "marker_w": 30, "title_h": 17, "why_h": 14, "gap": 0},
            {"title": 13, "why": 11, "marker_w": 28, "title_h": 16, "why_h": 13, "gap": 0},
            {"title": 12, "why": 10, "marker_w": 26, "title_h": 15, "why_h": 12, "gap": 0},
        ]
        prepared = []
        available = max_y - y
        why_modes = (False, True) if drop_why_if_needed else (False,)
        for omit_why in why_modes:
            prepared = []
            for style in styles:
                headline_font = self._font(font_family, style["title"], "bold")
                why_font = self._font(font_family, style["why"])
                rows = []
                needed_total = 0
                for item in items:
                    headline, why = self._news_text(item)
                    if not headline and not why:
                        continue
                    if omit_why:
                        why = ""
                    text_w = width - style["marker_w"]
                    title_lines = self._wrap(draw, headline, headline_font, text_w)
                    why_lines = self._wrap(draw, why, why_font, text_w) if why else []
                    needed = (
                        len(title_lines) * style["title_h"]
                        + len(why_lines) * style["why_h"]
                        + style["gap"]
                    )
                    rows.append((title_lines, why_lines, needed, headline_font, why_font, style))
                    needed_total += needed
                prepared = rows
                if needed_total <= available:
                    break
            if not prepared or needed_total <= available:
                break

        markers = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨"]
        for offset, (title_lines, why_lines, _needed, headline_font, why_font, style) in enumerate(prepared):
            number = start_index + offset
            marker = markers[number - 1] if 0 < number <= len(markers) else f"{number}."
            draw.text((x, y - 1), marker, font=headline_font, fill=marker_color)
            text_x = x + style["marker_w"]
            for line in title_lines:
                draw.text((text_x, y), line, font=headline_font, fill=ink)
                y += style["title_h"]
            for line in why_lines:
                draw.text((text_x, y), line, font=why_font, fill=muted)
                y += style["why_h"]
            y += style["gap"]
        return y

    def _news_text(self, item) -> tuple[str, str]:
        if isinstance(item, dict):
            return str(item.get("title") or ""), str(item.get("why") or "")
        return str(item), ""

    def _module_text(self, item) -> str:
        if isinstance(item, dict):
            primary = (
                item.get("risk")
                or item.get("signal")
                or item.get("watch")
                or item.get("title")
                or item.get("text")
                or item.get("summary")
                or ""
            )
            why = item.get("why") or item.get("reason") or ""
            if primary and why:
                return f"{primary}，{why}"
            return str(primary or why)
        return str(item)

    def _rss_sidebar_items(self, payload: dict[str, Any], start_index: int, limit: int) -> list[dict[str, str]]:
        result = []
        for item in list(payload.get("items") or [])[start_index:]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title or not self._has_cjk(title):
                continue
            source = str(item.get("source") or "").strip()
            result.append({"title": title, "why": source})
            if len(result) >= limit:
                break
        return result

    def _has_cjk(self, text: str) -> bool:
        return bool(re.search(r"[\u3400-\u9fff]", text))

    def _wrap(self, draw, text: str, font, max_width: int) -> list[str]:
        text = re.sub(r"\s+", " ", str(text)).strip()
        lines = []
        current = ""
        for char in text:
            candidate = current + char
            if self._tw(draw, candidate, font) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = char
        if current:
            lines.append(current)
        return lines or [""]

    def _tw(self, draw, text: str, font) -> int:
        box = draw.textbbox((0, 0), text, font=font)
        return box[2] - box[0]
