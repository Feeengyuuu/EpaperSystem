from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

import feedparser
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont, ImageOps
import pytz

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.base_plugin.presentation import PresentationMode
from plugins.base_plugin.render_provenance import (
    SourceProvenance,
    attach_source_provenance,
)
from plugins.context_cache import write_context
from utils.app_utils import (
    bounded_int,
    get_available_font_names,
    get_base_ui_font,
    get_font,
)
from utils.image_utils import text_width
from utils.http_client import get_http_client
from utils.massive_market_data import MassiveMarketData, MassiveMarketDataError, load_massive_api_key
from utils.plugin_cache import read_json, write_json
from utils.theme_utils import get_theme_palette

from .news_layout import NewsPage, date_text as news_date_text

logger = logging.getLogger(__name__)

MAX_PROVIDER_IO_WORKERS = 4

DEFAULT_MODEL = "gpt-5-nano"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_TITLE = "整点新闻"
DEFAULT_FONT = "Microsoft YaHei"
PLUGIN_DIR = Path(__file__).resolve().parent
TITLE_BACKGROUND_IMAGE = "title_bg_global_radar.png"
TITLE_BACKGROUND_SIZE = (325, 65)
TITLE_WORDMARK_IMAGE = "title_wordmark.png"
TITLE_WORDMARK_SIZE = (220, 58)
SUMMARY_SCHEMA_VERSION = "mainland-world-lead-tags-v17"
DEFAULT_FEED_FETCH_TIMEOUT_SECONDS = 8
DEFAULT_AI_TIMEOUT_SECONDS = 30
DEFAULT_MAX_FEEDS = 16
RECENT_NEWS_HISTORY_DAYS = 4
RECENT_NEWS_HISTORY_LIMIT = 80
MAX_NEWS_ITEM_AGE_DAYS = 10
LEGACY_DEFAULT_FEEDS = """BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml
BBC World|https://feeds.bbci.co.uk/news/world/rss.xml
NPR|https://feeds.npr.org/1001/rss.xml
NYTimes World|https://rss.nytimes.com/services/xml/rss/nyt/World.xml
Guardian World|https://www.theguardian.com/world/rss"""
LEGACY_WORLD_ONLY_DEFAULT_FEEDS = """BBC中文|https://feeds.bbci.co.uk/zhongwen/simp/rss.xml
BBC世界|https://feeds.bbci.co.uk/news/world/rss.xml
NPR新闻|https://feeds.npr.org/1001/rss.xml
纽约时报国际|https://rss.nytimes.com/services/xml/rss/nyt/World.xml
卫报国际|https://www.theguardian.com/world/rss
半岛电视台|https://www.aljazeera.com/xml/rss/all.xml
法国24|https://www.france24.com/en/rss
德国之声|https://rss.dw.com/rdf/rss-en-all
PBS新闻一小时|https://www.pbs.org/newshour/feeds/rss/headlines
ABC国际|https://abcnews.go.com/abcnews/internationalheadlines"""
# 新华网时政 xml 停更于 2022 年、人民网时政 xml 停更于 2025-06，二者会永远返回同一批
# 旧条目并导致大陆新闻反复出现，因此默认源只保留仍在按日更新的中新网各频道。
LEGACY_MAINLAND_FEEDS_V1 = """大陆新闻:新华网时政|https://www.news.cn/politics/news_politics.xml
大陆新闻:人民网时政|https://www.people.com.cn/rss/politics.xml
大陆新闻:中国新闻网国内|https://www.chinanews.com.cn/rss/china.xml
大陆新闻:中国新闻网即时|https://www.chinanews.com.cn/rss/scroll-news.xml"""
DEAD_DEFAULT_FEED_URLS = {
    "https://www.news.cn/politics/news_politics.xml",
    "https://www.people.com.cn/rss/politics.xml",
}
DEFAULT_MAINLAND_FEEDS = """大陆新闻:中国新闻网要闻|https://www.chinanews.com.cn/rss/importnews.xml
大陆新闻:中国新闻网国内|https://www.chinanews.com.cn/rss/china.xml
大陆新闻:中国新闻网财经|https://www.chinanews.com.cn/rss/finance.xml
大陆新闻:中国新闻网社会|https://www.chinanews.com.cn/rss/society.xml
大陆新闻:中国新闻网即时|https://www.chinanews.com.cn/rss/scroll-news.xml"""
DEFAULT_WORLD_FEEDS = """世界新闻:BBC世界|https://feeds.bbci.co.uk/news/world/rss.xml
世界新闻:NPR新闻|https://feeds.npr.org/1001/rss.xml
世界新闻:纽约时报国际|https://rss.nytimes.com/services/xml/rss/nyt/World.xml
世界新闻:卫报国际|https://www.theguardian.com/world/rss
世界新闻:半岛电视台|https://www.aljazeera.com/xml/rss/all.xml
世界新闻:法国24|https://www.france24.com/en/rss
世界新闻:德国之声|https://rss.dw.com/rdf/rss-en-all
世界新闻:PBS新闻一小时|https://www.pbs.org/newshour/feeds/rss/headlines
世界新闻:ABC国际|https://abcnews.go.com/abcnews/internationalheadlines"""
DEFAULT_FEEDS = f"{DEFAULT_MAINLAND_FEEDS}\n{DEFAULT_WORLD_FEEDS}"
LEGACY_REGIONAL_DEFAULT_FEEDS_V1 = f"{LEGACY_MAINLAND_FEEDS_V1}\n{DEFAULT_WORLD_FEEDS}"
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
MASSIVE_INDEX_TICKERS = {
    "^GSPC": {"I:SPX"},
    "^IXIC": {"I:COMP", "I:NDX"},
    "^DJI": {"I:DJI"},
}
MARKET_SHORT_NAMES = {
    "上证指数": "上证",
    "深证成指": "深成",
    "创业板指": "创业板",
    "标普500": "标普",
    "纳斯达克": "纳指",
    "道琼斯": "道指",
}
# Daily closes kept per index for the market tile sparkline.
MARKET_CLOSES_KEPT = 22
# Each story is a short concrete title, a one-sentence fact and a 2-4 character tag.
NEWS_TITLE_MAX_CHARS = 30
NEWS_FACT_MAX_CHARS = 44
NEWS_TAG_MAX_CHARS = 4
# The fixed tag vocabulary the AI picks from, with the meaning the prompt gives it.
NEWS_TAG_DEFINITIONS = (
    ("时政", "国内政策、政府与人事、重要会议"),
    ("经济", "宏观数据、消费、贸易、物价、口岸"),
    ("产业", "工业、制造、企业经营、产量"),
    ("金融", "银行、央行、货币、股市、债市"),
    ("科技", "科研、人工智能、芯片、航天、互联网"),
    ("社会", "民生、教育、乡村、社会事件"),
    ("两岸", "台湾与两岸关系"),
    ("外交", "国家间往来、会谈、特使、峰会"),
    ("冲突", "战争、袭击、军事行动、停火"),
    ("公卫", "疫情、疾病、医疗卫生"),
    ("灾害", "地震、台风、洪水、火灾等灾害事故"),
    ("司法", "法院、执法、反腐审查、案件"),
    ("航空", "民航、机场、飞机与航班"),
    ("能源", "石油、天然气、电力、煤炭、新能源"),
    ("体育", "赛事与运动员"),
    ("环境", "气候、生态、污染、碳排放"),
)
NEWS_TAGS = tuple(tag for tag, _meaning in NEWS_TAG_DEFINITIONS)
FALLBACK_NEWS_TAG = "要闻"
# Title keywords that place an out-of-vocabulary tag, most specific first.
TAG_TITLE_KEYWORDS = (
    ("两岸", ("两岸", "台湾", "台当局", "国台办", "台胞", "陆生", "民进党")),
    ("公卫", ("疫情", "病毒", "埃博拉", "病例", "公共卫生", "疫苗", "医疗", "流感")),
    ("冲突", ("冲突", "袭击", "空袭", "战争", "导弹", "武装", "停火", "交火", "战机", "以军", "胡塞", "军事")),
    ("灾害", ("地震", "台风", "洪水", "暴雨", "火灾", "山火", "干旱", "灾害", "泥石流", "事故")),
    ("航空", ("航空", "机场", "客机", "航班", "波音", "空客", "飞机", "FAA")),
    ("能源", ("能源", "石油", "原油", "天然气", "电力", "煤电", "煤炭", "核电", "光伏", "电池", "储能", "油价")),
    ("司法", ("法院", "法官", "司法", "判决", "审判", "起诉", "检察", "逮捕", "查处", "查获", "违纪", "审查调查", "警方")),
    ("外交", ("外交", "会谈", "会晤", "对话", "访问", "特使", "大使", "峰会", "外长")),
    ("金融", ("金融", "银行", "央行", "人民币", "利率", "股市", "证券", "汇率", "债券")),
    ("科技", ("科技", "人工智能", "芯片", "航天", "卫星", "火箭", "发射", "半导体", "互联网")),
    ("产业", ("产业", "制造", "钢", "汽车", "工业", "企业", "产量", "工信部")),
    ("环境", ("气候", "环境", "生态", "污染", "排放", "碳")),
    ("经济", ("经济", "消费", "投资", "贸易", "外贸", "出口", "进口", "物价", "价格", "批发价", "就业", "口岸", "GDP")),
    ("体育", ("体育", "比赛", "奥运", "足球", "篮球", "冠军", "联赛")),
    ("社会", ("社会", "乡村", "教育", "民生", "养老", "医保", "学校")),
    ("时政", ("国务院", "中央", "政府", "政策", "人大", "政协", "选举", "总统", "首相", "议会")),
)
# Title keywords for these tags are specific enough to correct even a vocabulary tag the AI
# picked (埃博拉 is 公卫 whatever the AI says); broad ones (时政 经济 产业 社会 环境) are not.
DECISIVE_TITLE_TAGS = frozenset({"两岸", "公卫", "冲突", "灾害", "航空", "能源", "司法", "外交", "金融", "科技", "体育"})
# Out-of-vocabulary tags the AI writes for a vocabulary tag, used when the title says nothing.
TAG_SYNONYMS = {"公共卫生": "公卫", "卫生": "公卫", "医疗": "公卫", "政治": "时政", "财经": "经济", "军事": "冲突",
                "战争": "冲突", "灾难": "灾害", "法治": "司法", "反腐": "司法", "民生": "社会"}
# Events whose headlines vary a lot: titles naming one group are the same story at a
# lower character overlap. A group lists the ways one event is written.
HOT_NEWS_TERM_GROUPS = (
    ("伊朗",), ("空袭",), ("煤矿",), ("太空人",), ("黎巴嫩",), ("基辅",), ("教宗",),
    ("巴以", "以巴", "加沙", "巴勒斯坦", "约旦河西岸"),
)
# Tag for an RSS-only card, from its chinanews.com.cn desk.
SOURCE_DESK_TAGS = {"要闻": "要闻", "国内": "时政", "财经": "经济", "社会": "社会", "即时": "要闻"}
# The page keeps the masthead's two inks: the panel's own red, which it shows solidly
# instead of dithering, is the one accent. Gains are red and losses green, as Chinese
# market quotes show them, but only as small solid marks.
PAGE_ACCENTS = {"lead": (255, 0, 0), "up": (255, 0, 0), "down": (0, 255, 0)}
# The page is laid out for the 800x480 panel; other displays get it scaled to fit.
PAGE_SIZE = (800, 480)
PAGE_DAY_COLOURS = dict(PAGE_ACCENTS)
PAGE_NIGHT_COLOURS = {**PAGE_ACCENTS, "panel": (0, 0, 0)}
FEED_SECTION_PREFIXES = {
    "大陆": "mainland",
    "大陆新闻": "mainland",
    "国内": "mainland",
    "中国": "mainland",
    "世界": "world",
    "世界新闻": "world",
    "世界快报": "world",
    "国际": "world",
    "全球": "world",
}
MAINLAND_FEED_HOST_HINTS = (
    "chinanews.com",
    "news.cn",
    "xinhuanet.com",
    "people.com.cn",
    "cctv.com",
)
MAINLAND_NEWS_MAX = 4
WORLD_NEWS_MAX = 4

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

ENGLISH_NEWS_TERM_REPLACEMENTS = (
    (re.compile(r"\bUnited States\b", re.I), "美国"),
    (re.compile(r"\bU\.S\.", re.I), "美国"),
    (re.compile(r"\bUS\b"), "美国"),
    (re.compile(r"\bUSA\b"), "美国"),
    (re.compile(r"\bMoscow\b", re.I), "莫斯科"),
    (re.compile(r"\bRussia\b", re.I), "俄罗斯"),
    (re.compile(r"\bRussian\b", re.I), "俄罗斯"),
    (re.compile(r"\bUkraine\b", re.I), "乌克兰"),
    (re.compile(r"\bKyiv\b", re.I), "基辅"),
    (re.compile(r"\bKiev\b", re.I), "基辅"),
    (re.compile(r"\bIsrael\b", re.I), "以色列"),
    (re.compile(r"\bIran\b", re.I), "伊朗"),
    (re.compile(r"\bGaza\b", re.I), "加沙"),
    (re.compile(r"\bHamas\b", re.I), "哈马斯"),
    (re.compile(r"\bWashington\b", re.I), "华盛顿"),
    (re.compile(r"\bBeijing\b", re.I), "北京"),
    (re.compile(r"\bChina\b", re.I), "中国"),
    (re.compile(r"\bTaiwan\b", re.I), "台湾"),
    (re.compile(r"\bJapan\b", re.I), "日本"),
    (re.compile(r"\bSouth Korea\b", re.I), "韩国"),
    (re.compile(r"\bNorth Korea\b", re.I), "朝鲜"),
    (re.compile(r"\bHong Kong\b", re.I), "香港"),
    (re.compile(r"\bUnited Nations\b", re.I), "联合国"),
    (re.compile(r"\bU\.N\.", re.I), "联合国"),
    (re.compile(r"\bUN\b"), "联合国"),
    (re.compile(r"\breconstruction\b", re.I), "重建"),
    (re.compile(r"\bceasefire\b", re.I), "停火"),
    (re.compile(r"\bcease-fire\b", re.I), "停火"),
    (re.compile(r"\bsanctions\b", re.I), "制裁"),
    (re.compile(r"\btariffs\b", re.I), "关税"),
)

VISIBLE_ENGLISH_ALLOWLIST = {"ABC", "AI", "API", "BBC", "G7", "G20", "NPR", "PBS", "RSS"}
VISIBLE_ENGLISH_WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z.\-]{1,}\b")

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
    "萬": "万",
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
    "滿": "满",
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
    "礦": "矿",
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
    "劇": "剧",
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
    "腎": "肾",
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
    "贏": "赢",
    "贊": "赞",
    "趙": "赵",
    "趕": "赶",
    "這": "这",
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
    "鉢": "钵",
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
    "闆": "板",
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
    return bounded_int(value, default, low, high)


def _safe_json_load(path: Path, default: Any) -> Any:
    return read_json(path, default=default)


def _safe_json_write(path: Path, payload: Any) -> None:
    write_json(path, payload, ensure_ascii=False, indent=2)


class DailyAINews(BasePlugin):
    def presentation_mode(self, settings):
        return PresentationMode.NO_CHANGE

    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = True
        params["api_key"] = {
            "required": True,
            "service": "OpenAI or Groq",
            "expected_key": "OPEN_AI_SECRET or GROQ_API_KEY",
        }
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        return params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        dimensions = self.get_dimensions(device_config)

        tz_name = device_config.get_config("timezone") or "America/Los_Angeles"
        now = datetime.now(pytz.timezone(tz_name))

        try:
            brief = self._get_brief(settings, device_config, now)
        except Exception as exc:
            if _enabled(settings.get("_theme_render_only")):
                raise
            logger.exception("Daily AI news failed")
            brief = self._fallback_brief(settings, now, str(exc))
            brief = self._mark_source_provenance(
                brief,
                SourceProvenance.LOCAL_FALLBACK,
            )

        brief = self._simplify_chinese_payload(brief)
        if not _enabled(settings.get("_theme_render_only")):
            self._write_news_context(brief, now)
        theme_context = settings.get("_inkypi_theme") or self.resolve_theme(settings, device_config, now=now)
        image = self._render(dimensions, settings, brief, now, theme_context)
        return attach_source_provenance(
            image,
            brief.get("_source_provenance", SourceProvenance.LOCAL_FALLBACK),
            detail="daily_ai_news",
        )

    def _write_news_context(self, brief: dict[str, Any], now: datetime) -> None:
        payload = brief.get("brief") if isinstance(brief, dict) else {}
        if not isinstance(payload, dict):
            return

        context_news = self._as_list(payload.get("top"), MAINLAND_NEWS_MAX + WORLD_NEWS_MAX)
        if not context_news:
            context_news = self._as_list(payload.get("mainland"), MAINLAND_NEWS_MAX) + self._as_list(payload.get("world"), WORLD_NEWS_MAX)
        if isinstance(payload.get("lead"), dict):
            context_news = [payload["lead"], *context_news]

        items = []
        for item in context_news[:MAINLAND_NEWS_MAX + WORLD_NEWS_MAX + 1]:
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
                "source_provenance": brief.get("_source_provenance"),
            },
            generated_at=brief.get("generated_at") or now,
            ttl_seconds=24 * 60 * 60,
        )

    def _get_brief(self, settings, device_config, now: datetime) -> dict[str, Any]:
        model = (settings.get("model") or DEFAULT_MODEL).strip()
        feeds_text = self._effective_feeds_text(settings.get("feed_urls"))
        max_items = _parse_int(settings.get("max_items"), 22, 6, 40)
        theme_render_only = _enabled(settings.get("_theme_render_only"))
        cache_only_render = theme_render_only or _enabled(
            settings.get("_cached_render_only") or settings.get("cached_render_only")
        )
        force_refresh = _enabled(settings.get("force_refresh")) and not cache_only_render
        date_key = now.strftime("%Y-%m-%d")
        cache_key = self._cache_key(
            date_key,
            model,
            feeds_text,
            max_items,
            settings.get("region_focus"),
        )
        cache_dir = self._cache_dir(create=False) if theme_render_only else self._cache_dir()
        cached = _safe_json_load(cache_dir / "brief.json", {})
        cache_matches = cached.get("cache_key") == cache_key
        # A theme redraw only recolors the brief on screen. After local midnight
        # that is still the previous day's brief until the scheduled DATA
        # refresh, but never one built for other settings or another schema.
        if (
            theme_render_only
            and cached.get("brief")
            and not cache_matches
            and not self._brief_built_for_settings(
                cached,
                model,
                feeds_text,
                max_items,
                settings.get("region_focus"),
            )
        ):
            raise RuntimeError(
                "Theme-only redraw requires a matching Daily AI News cache."
            )
        payload = self._get_brief_unclassified(settings, device_config, now)
        if payload.get("from_cache"):
            policy_fresh = bool(payload.pop("_policy_fresh_cache", False))
            provenance = (
                SourceProvenance.FRESH_CACHE
                if policy_fresh or (cache_matches and not force_refresh)
                else SourceProvenance.STALE_CACHE
            )
        else:
            provenance = SourceProvenance.LIVE
        return self._mark_source_provenance(payload, provenance)

    def _get_brief_unclassified(
        self,
        settings,
        device_config,
        now: datetime,
    ) -> dict[str, Any]:
        model = (settings.get("model") or DEFAULT_MODEL).strip()
        feeds_text = self._effective_feeds_text(settings.get("feed_urls"))
        max_items = _parse_int(settings.get("max_items"), 22, 6, 40)
        theme_render_only = _enabled(settings.get("_theme_render_only"))
        cache_only_render = theme_render_only or _enabled(settings.get("_cached_render_only") or settings.get("cached_render_only"))
        force_refresh = _enabled(settings.get("force_refresh")) and not cache_only_render
        date_key = now.strftime("%Y-%m-%d")
        cache_key = self._cache_key(date_key, model, feeds_text, max_items, settings.get("region_focus"))

        cache_dir = self._cache_dir(create=False) if theme_render_only else self._cache_dir()
        cache_file = cache_dir / "brief.json"
        cached = _safe_json_load(cache_file, {})
        cache_matches_current_settings = cached.get("cache_key") == cache_key
        if cached.get("brief") and (cache_only_render or (cache_matches_current_settings and not force_refresh)):
            cached["from_cache"] = True
            if cache_only_render and not (
                cache_matches_current_settings
                or self._brief_built_for_settings(
                    cached,
                    model,
                    feeds_text,
                    max_items,
                    settings.get("region_focus"),
                )
            ):
                # Normal operation, not a failure: the header shows it as plain "· 缓存".
                cached["notice"] = "仅重渲染显示，复用现有新闻缓存。"
            return cached
        if theme_render_only:
            raise RuntimeError("Theme-only redraw requires a warm Daily AI News cache.")

        if (
            force_refresh
            and cache_matches_current_settings
            and cached.get("date") == date_key
            and not self._allow_api_call(settings, date_key)
        ):
            cached["from_cache"] = True
            cached["_policy_fresh_cache"] = True
            cached["notice"] = "已按今日 API 限额复用计划内最新缓存。"
            return cached

        recent_titles = self._load_recent_news_titles(now)
        items = self._drop_stale_items(self._fetch_items(feeds_text, max_items), now)
        items = self._rank_news_items(items, now, recent_titles)
        items = self._drop_recently_shown_items(items, recent_titles)
        items = self._diversify_news_items(items, max_items)
        if not items:
            stale = cached if cached.get("brief") else None
            if stale:
                stale["from_cache"] = True
                stale["warning"] = "新闻源暂不可用，显示旧缓存。"
                return stale
            raise RuntimeError("No RSS items could be fetched.")

        stale = cached if cached.get("brief") else None
        stale_matches_current_settings = bool(stale and cache_matches_current_settings)
        market_snapshot = self._fetch_market_snapshot(now, device_config)
        openai_key = device_config.load_env_key("OPEN_AI_SECRET") or device_config.load_env_key("OPENAI_API_KEY")
        groq_key = device_config.load_env_key("GROQ_API_KEY")
        can_call_ai = self._allow_api_call(settings, date_key)
        rss_needs_translation = self._rss_items_need_translation(items)

        if openai_key or groq_key:
            if not can_call_ai:
                if stale_matches_current_settings or (stale and rss_needs_translation):
                    stale["from_cache"] = True
                    stale["warning"] = "已达到今日 API 调用上限，显示旧中文缓存。"
                    return stale
                brief = self._rss_only_brief(items, "已达到今日 API 调用上限，使用 RSS 兜底。", recent_titles)
            else:
                try:
                    brief = self._summarize_with_ai(openai_key, groq_key, model, settings, items, market_snapshot, now, recent_titles)
                except Exception as exc:
                    logger.warning("AI summary failed; using RSS fallback: %s", exc)
                    if stale_matches_current_settings or (stale and rss_needs_translation):
                        stale["from_cache"] = True
                        stale["warning"] = f"AI 摘要失败，显示旧中文缓存：{str(exc)[:80]}"
                        return stale
                    brief = self._rss_only_brief(items, f"AI 摘要失败，使用 RSS 兜底：{str(exc)[:60]}", recent_titles)
        else:
            if stale and rss_needs_translation:
                stale["from_cache"] = True
                stale["warning"] = "未配置 AI 密钥，显示旧中文缓存。"
                return stale
            brief = self._rss_only_brief(items, "未配置 AI 密钥，使用 RSS 兜底。", recent_titles)

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
        self._record_recent_news_titles(payload, now)
        if used_ai:
            self._record_api_call(date_key)
        return payload

    @staticmethod
    def _mark_source_provenance(payload, provenance):
        result = dict(payload)
        result["_source_provenance"] = SourceProvenance(provenance).value
        return result

    def _cache_dir(self, create=True) -> Path:
        return self.cache_dir(leaf="cache", create=create)

    def _cache_key(self, date_key: str, model: str, feeds_text: str, max_items: int, region_focus: Any) -> str:
        raw = "\n".join([SUMMARY_SCHEMA_VERSION, date_key, model, feeds_text, str(max_items), str(region_focus or "")])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _brief_built_for_settings(
        self,
        cached: dict[str, Any],
        model: str,
        feeds_text: str,
        max_items: int,
        region_focus: Any,
    ) -> bool:
        """Return whether a cached brief matches these settings on its own date."""
        date_key = cached.get("date")
        if not isinstance(date_key, str) or not date_key:
            return False
        return cached.get("cache_key") == self._cache_key(
            date_key,
            model,
            feeds_text,
            max_items,
            region_focus,
        )

    def _effective_feeds_text(self, feeds_text: Any) -> str:
        raw = str(feeds_text or "").strip()
        if not raw:
            return DEFAULT_FEEDS
        feeds = self._parse_feeds(raw)
        if self._should_expand_legacy_feeds(feeds):
            return DEFAULT_FEEDS
        return raw

    def _should_expand_legacy_feeds(self, feeds: list[tuple[str, str]]) -> bool:
        urls = {self._normalize_feed_url(url) for _name, url in feeds if self._normalize_feed_url(url)}
        if not urls:
            return True
        legacy_feed_sets = (LEGACY_DEFAULT_FEEDS, LEGACY_WORLD_ONLY_DEFAULT_FEEDS, LEGACY_REGIONAL_DEFAULT_FEEDS_V1)
        for legacy_text in legacy_feed_sets:
            legacy_urls = {self._normalize_feed_url(url) for _name, url in self._parse_feeds(legacy_text)}
            if urls == legacy_urls:
                return True
        historical_default_urls = {
            self._normalize_feed_url(url)
            for _name, url in self._parse_feeds(f"{DEFAULT_FEEDS}\n{LEGACY_REGIONAL_DEFAULT_FEEDS_V1}")
        }
        historical_default_urls.add("https://feeds.bbci.co.uk/zhongwen/simp/rss.xml")
        required_regional_default_urls = {
            "https://www.chinanews.com.cn/rss/china.xml",
            "https://feeds.bbci.co.uk/news/world/rss.xml",
        }
        if urls <= historical_default_urls and required_regional_default_urls <= urls:
            if urls & DEAD_DEFAULT_FEED_URLS:
                return True
            if "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml" in urls:
                return True
            if "https://www.chinanews.com.cn/rss/scroll-news.xml" not in urls:
                return True
        if urls and all("bbci.co.uk" in url or "bbc.co.uk" in url or "bbc.com" in url for url in urls):
            return True
        return False

    @staticmethod
    def _normalize_feed_url(url: str) -> str:
        return str(url or "").strip().rstrip("/")

    def _feed_source_and_section(self, source: str, url: str) -> tuple[str, str]:
        label = str(source or "").strip()
        section = ""
        for prefix, mapped_section in FEED_SECTION_PREFIXES.items():
            for separator in (":", "："):
                marker = f"{prefix}{separator}"
                if label.startswith(marker):
                    label = label[len(marker):].strip()
                    section = mapped_section
                    break
            if section:
                break

        if not section:
            normalized_url = self._normalize_feed_url(url).lower()
            section = "mainland" if any(hint in normalized_url for hint in MAINLAND_FEED_HOST_HINTS) else "world"

        return label or str(source or url).strip(), section

    def _item_section(self, item: dict[str, Any]) -> str:
        section = str(item.get("section") or "").strip().lower()
        return section if section in {"mainland", "world"} else "world"

    def _allow_api_call(self, settings, date_key: str) -> bool:
        limit = _parse_int(settings.get("daily_api_limit"), 1, 1, 20)
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

    def _recent_news_file(self) -> Path:
        return self._cache_dir() / "recent_news.json"

    def _load_recent_news_titles(self, now: datetime) -> list[str]:
        history = _safe_json_load(self._recent_news_file(), {})
        entries = self._pruned_recent_news_entries(history, now)
        titles = []
        seen = set()
        for entry in entries:
            title = str(entry.get("title") or "").strip() if isinstance(entry, dict) else ""
            key = self._news_history_key(title)
            if title and key not in seen:
                titles.append(title)
                seen.add(key)
        return titles

    def _record_recent_news_titles(self, payload: dict[str, Any], now: datetime) -> None:
        brief = payload.get("brief") if isinstance(payload, dict) else {}
        if not isinstance(brief, dict):
            return

        history_file = self._recent_news_file()
        history = _safe_json_load(history_file, {})
        entries = self._pruned_recent_news_entries(history, now)
        date_key = now.strftime("%Y-%m-%d")
        existing = {
            (str(entry.get("date") or ""), str(entry.get("key") or self._news_history_key(entry.get("title") or "")))
            for entry in entries
            if isinstance(entry, dict)
        }
        additions = []
        lead = brief.get("lead") if isinstance(brief.get("lead"), dict) else None
        stories = [(str(lead.get("section") or "world"), lead)] if lead else []
        for section, limit in (("mainland", MAINLAND_NEWS_MAX), ("world", WORLD_NEWS_MAX)):
            stories.extend((section, item) for item in self._as_list(brief.get(section), limit))
        for section, item in stories:
            title, _why = self._news_text(item)
            title = title.strip()
            key = self._news_history_key(title)
            if not title or not key or (date_key, key) in existing:
                continue
            additions.append({"date": date_key, "section": section, "title": title[:120], "key": key})
            existing.add((date_key, key))

        if additions:
            entries.extend(additions)
            entries = entries[-RECENT_NEWS_HISTORY_LIMIT:]
            _safe_json_write(history_file, {"updated_at": now.isoformat(), "entries": entries})

    def _pruned_recent_news_entries(self, history: Any, now: datetime) -> list[dict[str, Any]]:
        raw_entries = history.get("entries") if isinstance(history, dict) else []
        if not isinstance(raw_entries, list):
            return []
        cutoff = (now - timedelta(days=RECENT_NEWS_HISTORY_DAYS)).date()
        entries = []
        for entry in raw_entries:
            if not isinstance(entry, dict):
                continue
            entry_date = self._history_entry_date(entry)
            if entry_date is not None and entry_date < cutoff:
                continue
            title = str(entry.get("title") or "").strip()
            if title:
                entries.append(entry)
        return entries[-RECENT_NEWS_HISTORY_LIMIT:]

    @staticmethod
    def _history_entry_date(entry: dict[str, Any]):
        raw = str(entry.get("date") or "")[:10]
        try:
            return datetime.fromisoformat(raw).date()
        except Exception:
            return None

    @staticmethod
    def _news_history_key(title: Any) -> str:
        return re.sub(r"[\W_]+", "", str(title or ""), flags=re.UNICODE).casefold()

    def _matches_recent_news_title(self, title: str, recent_titles: list[str] | None) -> bool:
        title_key = self._news_history_key(title)
        if not title_key:
            return False
        for recent_title in recent_titles or []:
            recent_key = self._news_history_key(recent_title)
            if title_key == recent_key or self._similar_news_title(str(title or ""), str(recent_title or "")):
                return True
        return False

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
        feeds = self._parse_feeds(feeds_text)[:DEFAULT_MAX_FEEDS]
        if not feeds:
            return items
        per_feed_limit = max(3, min(8, (max_items * 2 + len(feeds) - 1) // len(feeds)))
        entry_scan_limit = max(12, per_feed_limit * 2)
        for source, url in feeds:
            display_source, section = self._feed_source_and_section(source, url)
            try:
                resp = get_http_client().request_bytes(
                    "GET",
                    url,
                    timeout=DEFAULT_FEED_FETCH_TIMEOUT_SECONDS,
                    headers={"User-Agent": "InkyPi Daily AI News/1.0"},
                    max_bytes=4 * 1024 * 1024,
                )
                feed = feedparser.parse(resp.data)
            except Exception as exc:
                logger.warning("RSS fetch failed for %s: %s", url, exc)
                continue

            per_feed = 0
            for entry in feed.entries[:entry_scan_limit]:
                title = _clean_text(entry.get("title", ""), 150)
                if not title:
                    continue
                key = re.sub(r"\W+", "", title.lower())
                if key in seen:
                    continue
                seen.add(key)
                items.append({
                    "source": display_source,
                    "section": section,
                    "title": title,
                    "summary": _clean_text(
                        entry.get("summary", "") or entry.get("description", ""),
                        280,
                    ),
                    "published": _clean_text(entry.get("published", "") or entry.get("updated", ""), 80),
                    "link": _clean_text(entry.get("link", ""), 220),
                })
                per_feed += 1
                if per_feed >= per_feed_limit:
                    break
        return items

    def _drop_stale_items(self, items: list[dict[str, str]], now: datetime) -> list[dict[str, str]]:
        reference = now if now.tzinfo is not None else now.replace(tzinfo=pytz.UTC)
        max_age = timedelta(days=MAX_NEWS_ITEM_AGE_DAYS)
        kept = []
        for item in items:
            published = self._parse_published(str(item.get("published") or ""))
            if published is not None and reference - published.astimezone(reference.tzinfo) > max_age:
                continue
            kept.append(item)
        return kept

    def _drop_recently_shown_items(
        self,
        items: list[dict[str, str]],
        recent_titles: list[str] | None,
    ) -> list[dict[str, str]]:
        if not recent_titles:
            return list(items)
        fresh: list[dict[str, str]] = []
        repeats: list[dict[str, str]] = []
        fresh_counts = {"mainland": 0, "world": 0}
        for item in items:
            if self._matches_recent_news_title(str(item.get("title") or ""), recent_titles):
                repeats.append(item)
            else:
                fresh.append(item)
                fresh_counts[self._item_section(item)] += 1
        if not repeats:
            return fresh
        # A section keeps recently-shown candidates only while it lacks enough
        # fresh material; otherwise repeats are cut so each day reads new.
        section_minimums = {"mainland": MAINLAND_NEWS_MAX + 2, "world": WORLD_NEWS_MAX + 2}
        kept_repeats = [
            item
            for item in repeats
            if fresh_counts[self._item_section(item)] < section_minimums[self._item_section(item)]
        ]
        return fresh + kept_repeats

    def _rank_news_items(
        self,
        items: list[dict[str, str]],
        now: datetime,
        recent_titles: list[str] | None = None,
    ) -> list[dict[str, str]]:
        recent_titles = list(recent_titles or [])

        def score(index: int, item: dict[str, str]) -> float:
            title = item.get("title", "")
            summary = item.get("summary", "")
            text = f"{title} {summary}".lower()
            value = 100 - index * 0.05

            published = self._parse_published(item.get("published", ""))
            if published:
                age_now = now
                if age_now.tzinfo is None:
                    age_now = age_now.replace(tzinfo=pytz.UTC)
                age_hours = max(0.0, (age_now - published.astimezone(age_now.tzinfo)).total_seconds() / 3600)
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
            if self._matches_recent_news_title(title, recent_titles):
                value -= 70
            if not self._has_cjk(title):
                value -= 5
            return value

        return [item for _score, item in sorted(
            ((score(index, item), item) for index, item in enumerate(items)),
            key=lambda pair: pair[0],
            reverse=True,
        )]

    def _rss_items_need_translation(self, items: list[dict[str, str]]) -> bool:
        titled_items = [item for item in items if str(item.get("title") or "").strip()]
        if not titled_items:
            return False
        non_cjk_count = sum(1 for item in titled_items if not self._has_cjk(str(item.get("title") or "")))
        return non_cjk_count > len(titled_items) / 2

    @staticmethod
    def _diversify_news_items(items: list[dict[str, str]], max_items: int) -> list[dict[str, str]]:
        if not items or max_items <= 0:
            return []
        buckets: dict[str, list[dict[str, str]]] = {}
        for item in items:
            source = str(item.get("source") or "").strip() or "_unknown"
            buckets.setdefault(source, []).append(item)
        if len(buckets) <= 1:
            return items[:max_items]

        selected = []
        sources = list(buckets)
        while len(selected) < max_items:
            made_progress = False
            for source in sources:
                bucket = buckets[source]
                if not bucket:
                    continue
                selected.append(bucket.pop(0))
                made_progress = True
                if len(selected) >= max_items:
                    break
            if not made_progress:
                break
        return selected

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

        quote_tasks = [
            (group, symbol, name)
            for group, symbols in MARKET_GROUPS.items()
            for symbol, name in symbols
        ]
        rows_by_group: dict[str, list[dict[str, Any]]] = {
            group: [] for group in MARKET_GROUPS
        }
        worker_count = max(
            1,
            min(
                MAX_PROVIDER_IO_WORKERS,
                len(quote_tasks) + int(massive_client is not None),
            ),
        )
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            macro_future = (
                executor.submit(self._fetch_massive_macro, massive_client)
                if massive_client is not None
                else None
            )
            quote_futures = [
                (
                    group,
                    symbol,
                    executor.submit(
                        self._fetch_market_quote,
                        massive_client,
                        symbol,
                        name,
                    ),
                )
                for group, symbol, name in quote_tasks
            ]
            for group, symbol, future in quote_futures:
                try:
                    row = future.result()
                except Exception as exc:
                    logger.warning(
                        "Market quote worker failed for %s: %s",
                        symbol,
                        type(exc).__name__,
                    )
                    row = None
                if row:
                    rows_by_group[group].append(row)
            if macro_future is not None:
                try:
                    snapshot["macro"] = macro_future.result()
                except Exception as exc:
                    logger.warning(
                        "Market macro worker failed: %s",
                        type(exc).__name__,
                    )

        snapshot["groups"].update(rows_by_group)
        return snapshot

    def _fetch_market_quote(self, massive_client, symbol: str, name: str):
        row = self._fetch_massive_quote(massive_client, symbol, name)
        if self._is_massive_index_proxy_quote(symbol, row):
            logger.warning(
                "Discarding Massive ETF proxy %s for index %s; using Yahoo fallback",
                row.get("massive_symbol"),
                symbol,
            )
            row = None
        if not row:
            return self._fetch_yahoo_quote(symbol, name)
        if not row.get("closes"):
            history = self._fetch_yahoo_quote(symbol, name)
            if history and str(history.get("as_of") or "")[:10] > str(row.get("as_of") or "")[:10]:
                # Massive can lag a session behind; mixing dates in one strip misleads.
                return history
            if history and history.get("closes"):
                # Otherwise Massive gives the quote and Yahoo's daily closes feed the sparkline.
                row = {**row, "closes": history["closes"]}
        return row

    def _fetch_massive_quote(self, massive_client, symbol: str, name: str) -> dict[str, Any] | None:
        if massive_client is None:
            return None
        try:
            return massive_client.fetch_quote(symbol, name)
        except MassiveMarketDataError as exc:
            logger.warning("Massive market quote failed for %s: %s", symbol, exc)
            return None

    @staticmethod
    def _is_massive_index_proxy_quote(symbol: str, row: dict[str, Any] | None) -> bool:
        accepted_tickers = MASSIVE_INDEX_TICKERS.get(symbol)
        if not accepted_tickers or not row or row.get("source") != "massive":
            return False
        massive_symbol = str(row.get("massive_symbol") or "").upper()
        return massive_symbol not in accepted_tickers

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

    def _yahoo_chart(self, symbol: str, chart_range: str, interval: str) -> dict[str, Any] | None:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}"
        try:
            resp = get_http_client().request_json(
                "GET",
                url,
                params={"range": chart_range, "interval": interval},
                timeout=10,
                headers={"User-Agent": "InkyPi Daily AI News/1.0"},
            )
            return resp.data["chart"]["result"][0]
        except Exception as exc:
            logger.warning("Market quote fetch failed for %s (%s): %s", symbol, interval, exc)
            return None

    @staticmethod
    def _yahoo_series(result: dict[str, Any]) -> list[tuple[float, float]]:
        quote_data = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        return [
            (stamp, float(close))
            for stamp, close in zip(result.get("timestamp") or [], quote_data.get("close") or [])
            if isinstance(stamp, (int, float)) and isinstance(close, (int, float))
        ]

    @staticmethod
    def _daily_closes(series, gmtoffset: Any) -> list[tuple[float, float]]:
        """The last bar of each exchange-local day, from intraday bars."""
        offset = gmtoffset if isinstance(gmtoffset, (int, float)) else 0
        days: dict[Any, tuple[float, float]] = {}
        for stamp, close in series:
            days[datetime.fromtimestamp(stamp + offset, tz=pytz.UTC).date()] = (stamp, close)
        return [days[day] for day in sorted(days)]

    def _fetch_yahoo_quote(self, symbol: str, name: str) -> dict[str, Any] | None:
        result = self._yahoo_chart(symbol, "1mo", "1d")
        if result is None:
            return None

        meta = result.get("meta") or {}
        series = self._yahoo_series(result)
        price = meta.get("regularMarketPrice")
        if not isinstance(price, (int, float)) and series:
            price = series[-1][1]
        if not isinstance(price, (int, float)):
            return None

        previous = None
        if len(series) < 2:
            # Some indexes (创业板指 399006.SZ) have no daily history on Yahoo; hourly bars,
            # grouped by exchange day, give their recent closes. The hourly chart's own
            # previousClose can be a session behind, so it is only a last resort.
            hourly = self._yahoo_chart(symbol, "5d", "60m")
            if hourly is not None:
                hourly_meta = hourly.get("meta") or {}
                daily = self._daily_closes(self._yahoo_series(hourly), hourly_meta.get("gmtoffset"))
                series = daily or series
                previous_close = hourly_meta.get("previousClose")
                if len(daily) < 2 and isinstance(previous_close, (int, float)) and previous_close:
                    previous = float(previous_close)
        if previous is None:
            previous = self._previous_session_close(series, meta.get("regularMarketTime"), meta.get("gmtoffset"))
        change = change_pct = None
        if previous:
            change = price - previous
            change_pct = change / previous * 100

        as_of = ""
        stamps = [series[-1][0]] if series else []
        market_time = meta.get("regularMarketTime")
        if isinstance(market_time, (int, float)):
            # The price can come from a session the daily bars do not show yet.
            stamps.append(market_time)
        if stamps:
            try:
                as_of = datetime.fromtimestamp(max(stamps), tz=pytz.UTC).isoformat()
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
            "closes": [round(close, 2) for _stamp, close in series[-MARKET_CLOSES_KEPT:]],
        }

    @staticmethod
    def _previous_session_close(series, market_time: Any, gmtoffset: Any) -> float | None:
        """The close the latest price changed from: the bar before today's, or the last bar.

        Yahoo stamps each daily bar at its session open; when the last bar is the
        current price's session, the previous session is the bar before it. The
        chart's chartPreviousClose precedes the whole range, so it is never used.
        """
        if not series:
            return None
        offset = gmtoffset if isinstance(gmtoffset, (int, float)) else 0

        def session_day(stamp):
            return datetime.fromtimestamp(stamp + offset, tz=pytz.UTC).date()

        last_is_current = not isinstance(market_time, (int, float)) or session_day(series[-1][0]) == session_day(market_time)
        if last_is_current:
            return series[-2][1] if len(series) >= 2 else None
        return series[-1][1]

    def _summarize_with_ai(
        self,
        openai_key: str,
        groq_key: str,
        model: str,
        settings,
        items: list[dict[str, str]],
        market_snapshot: dict[str, Any],
        now: datetime,
        recent_titles: list[str] | None = None,
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
                    recent_titles=recent_titles,
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
        recent_titles: list[str] | None = None,
        provider: str = "openai",
        base_url: str | None = None,
    ) -> dict[str, Any]:
        client_kwargs = {
            "api_key": api_key,
            "timeout": DEFAULT_AI_TIMEOUT_SECONDS,
            "max_retries": 0,
        }
        if base_url:
            client_kwargs["base_url"] = base_url
        client = OpenAI(**client_kwargs)
        system = (
            "你是中文新闻编辑。只根据用户提供的 RSS 条目写简体中文整点新闻。"
            "所有中文必须使用简体中文，不得使用繁体中文或港澳台繁体词形。"
            "所有用户可见文字必须是简体中文；常见英文地名、人名、组织名必须译成通用简体中文。"
            "mainland 新闻只能来自 section=mainland 的 RSS 条目，world 新闻只能来自 section=world 的 RSS 条目。"
            "优先选择 recent_titles 中没有出现过的新标题；候选不足时才复用近几天出现过的事件。"
            "新闻必须强调今天或最近一次更新的具体变化，标题要具体，避免宏观空话。"
            "输出必须是一个 JSON object，不要 Markdown。"
        )
        user = {
            "date": now.strftime("%Y-%m-%d"),
            "style": settings.get("region_focus") or "china_global",
            "recent_titles": list(recent_titles or [])[:24],
            "output_schema": {
                "lead": {"section": "mainland 或 world", "tag": "固定分类之一", "title": "头条标题，不超过18字", "why": "一句话事实，不超过30字"},
                "mainland": [{"tag": "固定分类之一", "title": "大陆新闻标题，不超过18字", "why": "一句话事实，不超过30字"}],
                "world": [{"tag": "固定分类之一", "title": "世界新闻标题，不超过24字", "why": "一句话事实，不超过30字"}],
                "sources": ["来源名"],
            },
            "rules": [
                "所有输出字段只使用简体中文；如果素材是繁体中文，必须转换为简体中文再写入 JSON",
                "不要留下英文单词或英文地名，例如 Moscow 必须写成莫斯科，United States 必须写成美国，Iran 必须写成伊朗",
                "如果无法确定某个英文术语的标准中文译名，必须改写句子，不要把英文原词放进 title、why 或 lede",
                "中文词内部不要加空格，例如写“美伊谈判”，不要写“美 伊谈判”",
                "lead 是两个分区中今天最重要的一条新变化，必须来自 RSS 条目，section 写它来自 mainland 还是 world；lead 不得再出现在 mainland 或 world 中",
                "lead 选重大硬新闻（重大政策、冲突、灾害、关键经济数据、外交），不要选人物表彰、企业宣传、活动启幕、纪念活动这类软新闻",
                "section=mainland 的素材里如果是外国新闻（例如乌克兰、美国、中东的事件），不要放进 mainland；mainland 只写中国大陆的事件",
                "表彰、荣誉、先进个人评选和企业宣传不进 mainland，除非没有其他素材",
                "mainland 给 2 到 4 条，只能选择 section=mainland 的 RSS 条目，优先大陆时政、经济、社会、公共安全和重大政策更新",
                "world 给 3 到 4 条，只能选择 section=world 的 RSS 条目，优先国际冲突、外交、政策、事故、市场、法律和重大科技治理",
                "tag 只能从以下分类中选一个：" + "；".join(f"{tag}（{meaning}）" for tag, meaning in NEWS_TAG_DEFINITIONS) + "；都不合适时写 要闻",
                "title 中大陆新闻和 lead 不超过18字，世界新闻不超过24字，写清主体和动作，不要标点堆砌",
                "why 写一句话事实：谁、做了什么、关键数字或结果，不超过30字；不要写意义、影响、体现、凸显、彰显之类的评价，也不要写“成为关注焦点”“引发关注”“引发讨论”“持续发酵”“局势紧张”这类空话；why 要补充标题以外的信息，不能复述标题",
                "如果某个分区素材不足，宁可少写，不得跨区挪用，也不得编造新闻",
                "mainland 和 world 不允许使用 market_snapshot、股票指数或你自己的背景知识生成新闻",
                "不得写 RSS items 中不存在的人名、机构、政策或市场事件",
                "同一事件只能出现一次，不要用不同标题重复同一军事行动、谈判或事故",
                "优先避开 recent_titles 中的近几天已展示标题，除非该分区没有足够新素材",
                "title 必须包含具体人物/机构/地点/事件动作/结果，禁止只写宏观分类",
                "避免使用“引发讨论”“风险升级”“议题焦点”“全球媒体聚焦”这类宽泛标题，除非同时写清具体事件",
                "不要把人生、心理健康、生活方式、科普解释稿、旧背景稿放进新闻列表，除非它们是当天重大政策或公共事件",
                "尽量保留素材里的数字、地点、人物、机构和动作",
                "sources 只能从 items 里已有的 source 字段原样选择，最多5个，不得自造来源名",
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
        brief = self._postprocess_brief_news(brief, items, recent_titles)
        brief["sources"] = self._clean_brief_sources(brief.get("sources"), items)
        brief = self._sanitize_brief_visible_text(brief)
        if not str(brief.get("lede") or "").strip():
            brief["lede"] = self._fallback_lede(brief["top"])
        return self._simplify_chinese_payload(brief)

    def _parse_brief_json(self, content: str) -> dict[str, Any]:
        data = self._load_brief_json_object(content)
        top = self._as_list(data.get("top"), MAINLAND_NEWS_MAX + WORLD_NEWS_MAX)
        mainland = self._as_list(
            data.get("mainland") or data.get("mainland_news") or data.get("china") or data.get("domestic"),
            MAINLAND_NEWS_MAX,
        )
        world = self._as_list(
            data.get("world") or data.get("world_news") or data.get("quick") or data.get("international"),
            WORLD_NEWS_MAX,
        )
        lead = data.get("lead") if isinstance(data.get("lead"), dict) else None
        lead = lead if lead and str(lead.get("title") or "").strip() else None
        lede = str((lead or {}).get("title") or data.get("lede") or "").strip()
        top_items = self._merge_section_news(mainland, world) or top
        if not lede or lede == "今日新闻简报已生成。":
            lede = self._fallback_lede(top_items)
        return self._simplify_chinese_payload({
            "lede": lede[:48],
            "lead": lead,
            "mainland": mainland,
            "world": world,
            "top": top_items,
            "sources": self._as_list(data.get("sources"), 5),
        })

    def _load_brief_json_object(self, content: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for candidate in self._json_object_candidates(content):
            repaired = self._strip_trailing_json_commas(candidate)
            for payload in (candidate, repaired):
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError as exc:
                    last_error = exc
                    continue
                if isinstance(data, dict):
                    return data
                last_error = ValueError("AI summary JSON was not an object.")
        if last_error:
            raise last_error
        raise ValueError("AI summary JSON was empty.")

    @staticmethod
    def _json_object_candidates(content: str) -> list[str]:
        text = str(content or "").strip()
        candidates = [text] if text else []
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            extracted = match.group(0).strip()
            if extracted and extracted not in candidates:
                candidates.append(extracted)
        return candidates

    @staticmethod
    def _strip_trailing_json_commas(text: str) -> str:
        output = []
        in_string = False
        escaped = False
        length = len(text)
        for index, char in enumerate(text):
            if in_string:
                output.append(char)
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                output.append(char)
                continue
            if char == ",":
                next_index = index + 1
                while next_index < length and text[next_index] in " \t\r\n":
                    next_index += 1
                if next_index < length and text[next_index] in "}]":
                    continue
            output.append(char)
        return "".join(output)

    def _as_list(self, value: Any, limit: int) -> list[Any]:
        if isinstance(value, list):
            return value[:limit]
        if value:
            return [value]
        return []

    def _postprocess_brief_news(
        self,
        brief: dict[str, Any],
        items: list[dict[str, str]],
        recent_titles: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = dict(brief)
        mainland_sources = [item for item in items if self._item_section(item) == "mainland"]
        world_sources = [item for item in items if self._item_section(item) == "world"]
        mainland = self._as_list(payload.get("mainland"), MAINLAND_NEWS_MAX)
        world = self._as_list(payload.get("world"), WORLD_NEWS_MAX)

        if not mainland and not world:
            mainland, world = self._split_legacy_top_sections(payload.get("top"))

        mainland = self._dedupe_top_items(
            mainland,
            mainland_sources,
            MAINLAND_NEWS_MAX,
            require_source_match=True,
            recent_titles=recent_titles,
        )
        world = self._dedupe_top_items(
            world,
            world_sources,
            WORLD_NEWS_MAX,
            recent_titles=recent_titles,
        )
        top = self._merge_section_news(mainland, world)
        if not top:
            top = self._dedupe_top_items(
                payload.get("top") or [],
                items,
                MAINLAND_NEWS_MAX + WORLD_NEWS_MAX,
                recent_titles=recent_titles,
            )
            mainland, world = self._split_legacy_top_sections(top)

        mainland = self._without_world_copies(mainland, world)
        top = self._merge_section_news(mainland, world) or top
        lead = self._checked_lead(payload.get("lead"), mainland_sources)
        if not lead:
            # Without a checked lead the page promotes the first de-duplicated story.
            payload["lede"] = ""
        if lead:
            mainland = [item for item in mainland if not self._similar_news_title(self._news_text(item)[0], lead["title"])]
            world = [item for item in world if not self._similar_news_title(self._news_text(item)[0], lead["title"])]
            top = self._merge_section_news(mainland, world)
            payload["lede"] = lead["title"]

        payload["lead"] = lead
        payload["mainland"] = [self._trimmed_news_item(item) for item in mainland]
        payload["world"] = [self._trimmed_news_item(item) for item in world]
        payload["top"] = [self._trimmed_news_item(item) for item in top]
        if not str(payload.get("lede") or "").strip():
            payload["lede"] = self._fallback_lede(top)
        return payload

    def _without_world_copies(self, mainland: list[Any], world: list[Any]) -> list[Any]:
        """Mainland stories minus those a world story already covers.

        The mixed 即时 desk carries foreign news too; a copy of a dedicated world-feed
        story is international news, so it stays in 国际 and leaves 国内.
        """
        world_titles = [self._news_text(item)[0] for item in world if item]
        return [
            item
            for item in mainland
            if item and not any(self._similar_news_title(self._news_text(item)[0], title) for title in world_titles)
        ]

    def _checked_lead(self, lead: Any, mainland_sources: list[dict[str, str]]) -> dict[str, str] | None:
        """The AI's lead story, kept only when it is one of the RSS items.

        A mainland lead must match a mainland RSS title, as mainland items do; a world
        lead is a translation of an English item, so like world items it cannot be
        matched by title and is accepted as world.
        """
        if not isinstance(lead, dict):
            return None
        lead = self._trimmed_news_item(lead)
        if not lead.get("title"):
            return None
        source_titles = [str(source.get("title") or "") for source in mainland_sources]
        matches_mainland = self._matches_source_title(lead["title"], source_titles)
        section = str(lead.get("section") or "").strip().lower()
        if section == "mainland" and not matches_mainland:
            return None
        lead["section"] = "mainland" if matches_mainland else "world"
        return lead

    @staticmethod
    def _trimmed_news_item(item: Any) -> Any:
        """Bound a story's visible fields: short title, one-sentence fact, 2-4 character tag."""
        if not isinstance(item, dict):
            return item
        trimmed = dict(item)
        if "tag" in trimmed:
            # Mapped from the AI's full tag, before any truncation.
            trimmed["tag"] = DailyAINews._normalized_tag(trimmed["tag"], trimmed.get("title"))
        limits = {"title": NEWS_TITLE_MAX_CHARS, "why": NEWS_FACT_MAX_CHARS, "tag": NEWS_TAG_MAX_CHARS}
        for key, limit in limits.items():
            if key in trimmed:
                trimmed[key] = DailyAINews._field_text(trimmed.get(key))[:limit]
        return trimmed

    @staticmethod
    def _normalized_tag(tag: Any, title: Any) -> str:
        """A tag from the fixed vocabulary.

        A vocabulary tag stays unless the title has a decisive keyword for another tag.
        Anything else is placed by the title's keywords, then by a vocabulary tag or known
        synonym inside the AI's tag, and finally falls back to 要闻.
        """
        tag = DailyAINews._field_text(tag)
        title = str(title or "")
        guess = next((vocabulary_tag for vocabulary_tag, words in TAG_TITLE_KEYWORDS
                      if any(word in title for word in words)), "")
        if tag in NEWS_TAGS:
            return guess if guess in DECISIVE_TITLE_TAGS and guess != tag else tag
        if guess:
            # 要闻 means the AI was unsure, so any title keyword places the story.
            return guess
        for vocabulary_tag in NEWS_TAGS:
            if vocabulary_tag in tag:
                return vocabulary_tag
        for synonym, vocabulary_tag in TAG_SYNONYMS.items():
            if synonym in tag:
                return vocabulary_tag
        return FALLBACK_NEWS_TAG

    @staticmethod
    def _field_text(value: Any) -> str:
        """A visible text field from AI output: a string, a number, or the first string of a list."""
        if isinstance(value, (list, tuple)):
            value = next(
                (
                    entry
                    for entry in value
                    if isinstance(entry, (str, int, float)) and not isinstance(entry, bool) and str(entry).strip()
                ),
                "",
            )
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            return ""
        return str(value).strip()

    @staticmethod
    def _summary_fact(summary: Any) -> str:
        """The first sentence of an RSS summary, without its dateline or 据…消息 attribution."""
        text = re.sub(r"\s+", " ", str(summary or "")).strip()
        text = re.sub(r"^\S{0,24}?\d{1,2}月\d{1,2}日电\s*(?:[（(][^）)]{0,24}[）)])?\s*", "", text)
        text = re.sub(r"^(?:\d{1,2}月)?\d{1,2}日[，,]\s*", "", text)
        text = re.sub(r"^据[^，,。]{0,24}消息[，,]\s*", "", text)
        return re.split(r"[。！？]", text, maxsplit=1)[0].strip()[:36]

    def _source_tag(self, source: Any) -> str:
        """An RSS-only card's tag: its chinanews desk, else a generic 要闻."""
        desk = re.sub(r"^(中国新闻网|中新网)", "", str(source or "").strip())
        return SOURCE_DESK_TAGS.get(desk, "要闻")

    def _split_legacy_top_sections(self, top: Any) -> tuple[list[Any], list[Any]]:
        top_items = self._as_list(top, MAINLAND_NEWS_MAX + WORLD_NEWS_MAX)
        left_count = min(3, len(top_items))
        return top_items[:left_count], top_items[left_count:left_count + WORLD_NEWS_MAX]

    @staticmethod
    def _merge_section_news(mainland: list[Any], world: list[Any]) -> list[Any]:
        return list(mainland or [])[:MAINLAND_NEWS_MAX] + list(world or [])[:WORLD_NEWS_MAX]

    def _clean_brief_sources(self, sources: Any, items: list[dict[str, str]]) -> list[str]:
        allowed = []
        for item in items:
            source = self._simplify_chinese_text(str(item.get("source") or "").strip())
            if source and source not in allowed:
                allowed.append(source)
        if not allowed:
            return [self._simplify_chinese_text(str(source).strip()) for source in self._as_list(sources, 5) if str(source).strip()]

        normalized_allowed = {self._normalize_source_label(source): source for source in allowed}
        cleaned = []
        for source in self._as_list(sources, 5):
            source_text = self._simplify_chinese_text(str(source or "").strip())
            if not source_text:
                continue
            allowed_source = normalized_allowed.get(self._normalize_source_label(source_text))
            if allowed_source and allowed_source not in cleaned:
                cleaned.append(allowed_source)
        return cleaned[:5] if cleaned else allowed[:5]

    def _normalize_source_label(self, source: str) -> str:
        return re.sub(r"[\W_]+", "", source, flags=re.UNICODE).casefold()

    def _sanitize_brief_visible_text(self, brief: dict[str, Any]) -> dict[str, Any]:
        sanitized = dict(brief)
        sanitized["lede"] = self._strip_untranslated_english_terms(str(sanitized.get("lede") or ""))

        def cleaned_story(item: dict[str, Any]) -> dict[str, Any]:
            cleaned_item = dict(item)
            for key in ("title", "why", "tag"):
                if key in cleaned_item or key != "tag":
                    cleaned_item[key] = self._strip_untranslated_english_terms(str(cleaned_item.get(key) or ""))
            return cleaned_item

        for list_name, limit in (
            ("mainland", MAINLAND_NEWS_MAX),
            ("world", WORLD_NEWS_MAX),
            ("top", MAINLAND_NEWS_MAX + WORLD_NEWS_MAX),
        ):
            cleaned_items = []
            for item in self._as_list(sanitized.get(list_name), limit):
                if isinstance(item, dict):
                    cleaned_items.append(cleaned_story(item))
                else:
                    cleaned_items.append(self._strip_untranslated_english_terms(str(item or "")))
            sanitized[list_name] = cleaned_items
        if isinstance(sanitized.get("lead"), dict):
            sanitized["lead"] = cleaned_story(sanitized["lead"])

        return sanitized

    def _strip_untranslated_english_terms(self, text: str) -> str:
        cleaned = self._simplify_chinese_text(text)

        def replace_match(match: re.Match[str]) -> str:
            term = match.group(0)
            canonical = term.replace(".", "").replace("-", "").upper()
            if canonical in VISIBLE_ENGLISH_ALLOWLIST:
                return term
            # Brand and agency names stay as Chinese media print them: camel case
            # (SpaceX, OpenAI, iPhone) or a short all-caps acronym (FAA, NASA).
            if any(char.isupper() for char in term[1:]) and any(char.islower() for char in term):
                return term
            letters = term.replace(".", "").replace("-", "")
            if letters.isalpha() and letters.isupper() and 2 <= len(letters) <= 6:
                return term
            # A word between numbers is a model designation, e.g. 737 MAX 10.
            before = match.string[: match.start()].rstrip()[-1:]
            after = match.string[match.end():].lstrip()[:1]
            if before.isdigit() and after.isdigit():
                return term
            return ""

        cleaned = VISIBLE_ENGLISH_WORD_RE.sub(replace_match, cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        cleaned = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", cleaned)
        cleaned = re.sub(r"\s+([，。；：、])", r"\1", cleaned)
        cleaned = re.sub(r"([（(])\s+", r"\1", cleaned)
        cleaned = re.sub(r"\s+([）)])", r"\1", cleaned)
        return self._tidy_visible_text(cleaned)

    @staticmethod
    def _tidy_visible_text(text: str) -> str:
        """Drop the space between Latin text and Chinese characters, and any separator a
        removed word left at either end, e.g. "/特斯拉". Safe on already-clean text."""
        text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[A-Za-z0-9])|(?<=[A-Za-z0-9])\s+(?=[\u4e00-\u9fff])", "", str(text or ""))
        return re.sub(r"^[\s/／、，,·]+|[\s/／、，,·]+$", "", text).strip()

    def _rss_news_cards(
        self,
        items: list[dict[str, str]],
        limit: int,
        existing: list[Any] | None = None,
        recent_titles: list[str] | None = None,
    ) -> list[Any]:
        result = list(existing or [])[:limit]
        delayed_recent = []
        for item in items:
            title = str(item.get("title") or "").strip()
            if not title or not self._is_chinese_title(title):
                continue
            if any(self._similar_news_title(title, self._news_text(existing_item)[0]) for existing_item in result):
                continue
            source = str(item.get("source") or "").strip()
            summary = str(item.get("summary") or "").strip()
            why = self._summary_fact(summary) or source[:36] or "来自 RSS 源。"
            card = {"title": title[:32], "why": why, "tag": self._source_tag(source)}
            if self._matches_recent_news_title(title, recent_titles):
                delayed_recent.append(card)
                continue
            result.append(card)
            if len(result) >= limit:
                break
        for card in delayed_recent:
            if len(result) >= limit:
                break
            if any(self._similar_news_title(card["title"], self._news_text(existing_item)[0]) for existing_item in result):
                continue
            result.append(card)
        return result[:limit]

    def _rss_only_brief(
        self,
        items: list[dict[str, str]],
        warning: str,
        recent_titles: list[str] | None = None,
    ) -> dict[str, Any]:
        mainland_sources = [item for item in items if self._item_section(item) == "mainland"]
        world_sources = [item for item in items if self._item_section(item) == "world"]
        mainland = self._rss_news_cards(mainland_sources, MAINLAND_NEWS_MAX, recent_titles=recent_titles)
        world = self._rss_news_cards(world_sources, WORLD_NEWS_MAX, recent_titles=recent_titles)
        if not mainland and not world:
            mainland, world = self._split_legacy_top_sections(self._rss_news_cards(items, MAINLAND_NEWS_MAX + WORLD_NEWS_MAX))
        top = self._merge_section_news(mainland, world)

        sources = []
        for item in mainland_sources + world_sources:
            source = str(item.get("source") or "").strip()
            if source and source not in sources:
                sources.append(source)

        return {
            "lede": self._fallback_lede(top),
            "mainland": mainland,
            "world": world,
            "top": top,
            "lead": None,
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

    def _dedupe_top_items(
        self,
        top: list[Any],
        source_items: list[dict[str, str]],
        limit: int = 7,
        *,
        require_source_match: bool = False,
        recent_titles: list[str] | None = None,
    ) -> list[Any]:
        result = []
        delayed_recent = []
        recent_titles = list(recent_titles or [])
        source_titles = [
            str(source.get("title") or "").strip()
            for source in source_items
            if str(source.get("title") or "").strip()
        ]

        def append_candidate(candidate: Any, headline: str) -> bool:
            if any(self._similar_news_title(headline, self._news_text(existing)[0]) for existing in result):
                return False
            if self._matches_recent_news_title(headline, recent_titles):
                delayed_recent.append(candidate)
                return False
            result.append(candidate)
            return True

        for item in top:
            headline, _why = self._news_text(item)
            if not headline:
                continue
            if require_source_match and source_titles and not self._matches_source_title(headline, source_titles):
                continue
            append_candidate(item, headline)
            if len(result) >= limit:
                return result

        for source in source_items:
            title = str(source.get("title") or "").strip()
            if not title or not self._is_chinese_title(title):
                continue
            summary = self._summary_fact(source.get("summary")) or str(source.get("source") or "").strip()[:36]
            card = {"title": title[:32], "why": summary, "tag": self._source_tag(source.get("source"))}
            append_candidate(card, title)
            if len(result) >= limit:
                break

        for item in delayed_recent:
            if len(result) >= limit:
                break
            headline, _why = self._news_text(item)
            if not headline:
                continue
            if any(self._similar_news_title(headline, self._news_text(existing)[0]) for existing in result):
                continue
            result.append(item)
        return result

    def _matches_source_title(self, headline: str, source_titles: list[str]) -> bool:
        return any(self._similar_news_title(headline, title) for title in source_titles)

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
        if overlap >= 0.56:
            return True
        for group in HOT_NEWS_TERM_GROUPS:
            left_named = [term for term in group if term in left]
            right_named = [term for term in group if term in right]
            if not left_named or not right_named:
                continue
            # Both name the same hot event; the words beyond its name must still overlap,
            # so two different stories about one place are not merged.
            named = set("".join(left_named + right_named))
            rest_left = left_chars - named
            rest_right = right_chars - named
            if rest_left and rest_right and len(rest_left & rest_right) / min(len(rest_left), len(rest_right)) >= 0.38:
                return True
        return False

    def _fallback_brief(self, settings, now: datetime, error: str) -> dict[str, Any]:
        return {
            "date": now.strftime("%Y-%m-%d"),
            "generated_at": now.isoformat(),
            "model": settings.get("model") or DEFAULT_MODEL,
            "brief": {
                "lede": "AI新闻暂不可用，等待下次刷新。",
                "mainland": [{"title": "新闻简报生成失败", "why": error[:48]}],
                "world": [],
                "top": [{"title": "新闻简报生成失败", "why": error[:48]}],
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
        for pattern, replacement in ENGLISH_NEWS_TERM_REPLACEMENTS:
            simplified = pattern.sub(replacement, simplified)
        simplified = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", simplified)
        return simplified.translate(TRADITIONAL_TO_SIMPLIFIED)

    def _render(self, dimensions, settings, payload: dict[str, Any], now: datetime, theme_context=None) -> Image.Image:
        raw_title = str(settings.get("brief_title") or "").strip()
        title = DEFAULT_TITLE if not raw_title or raw_title == "二狗新闻" else raw_title
        title = self._simplify_chinese_text(title)
        payload = self._simplify_chinese_payload(payload)
        brief = payload.get("brief") or {}
        palette = self._page_palette(theme_context)
        img = self._base_background(PAGE_SIZE, palette["background"], (theme_context or {}).get("mode", "day"))
        lead, mainland, world = self._page_news(brief, payload)
        NewsPage(self, img, palette, DEFAULT_FONT).render(
            title=title,
            lead=lead,
            mainland=mainland,
            world=world,
            quotes=self._page_quotes(payload),
            meta=self._page_meta(payload, now),
        )
        if tuple(dimensions) != PAGE_SIZE:
            img = self._fit_page(img, dimensions, palette["background"])
        return img

    @staticmethod
    def _fit_page(page: Image.Image, dimensions, background) -> Image.Image:
        """The 800x480 page scaled to fit another display, centred on the page colour."""
        resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
        fitted = ImageOps.contain(page, tuple(int(value) for value in dimensions), method=resample)
        canvas = Image.new("RGB", tuple(int(value) for value in dimensions), background)
        canvas.paste(fitted, ((canvas.width - fitted.width) // 2, (canvas.height - fitted.height) // 2))
        return canvas

    def _page_palette(self, theme_context) -> dict[str, Any]:
        palette = dict(self._render_palette(theme_context))
        night = isinstance(theme_context, dict) and theme_context.get("mode") == "night"
        palette.update(PAGE_NIGHT_COLOURS if night else PAGE_DAY_COLOURS)
        return palette

    @staticmethod
    def _contrast_ink(colour, palette) -> tuple[int, int, int]:
        red, green, blue = [int(value) for value in tuple(colour)[:3]]
        return (0, 0, 0) if 0.299 * red + 0.587 * green + 0.114 * blue > 150 else (255, 255, 255)

    def _page_news(self, brief: dict[str, Any], payload: dict[str, Any]):
        """The lead story and the two columns, the lead never repeated in a column."""
        mainland = [self._page_item(item) for item in self._render_news_section_items(brief, payload, "mainland")]
        world = [self._page_item(item) for item in self._render_news_section_items(brief, payload, "world")]
        mainland = self._without_world_copies([item for item in mainland if item], world)
        world = [item for item in world if item]
        raw_lead = brief.get("lead") if isinstance(brief.get("lead"), dict) else None
        lead = self._page_item(raw_lead) if raw_lead else None
        section = str((raw_lead or {}).get("section") or "")
        if lead:
            mainland = [item for item in mainland if not self._similar_news_title(item["title"], lead["title"])]
            world = [item for item in world if not self._similar_news_title(item["title"], lead["title"])]
        elif mainland:
            lead, section = mainland.pop(0), "mainland"
        elif world:
            lead, section = world.pop(0), "world"
        if lead:
            lead["section_label"] = "国际" if section == "world" else "国内"
        return lead, mainland, world

    def _page_item(self, item) -> dict[str, str] | None:
        title, why = self._news_text(item)
        # Briefs cached before a sanitizer fix are tidied here as well.
        title = self._tidy_visible_text(title)
        if not title:
            return None
        # Cached tags from before the fixed vocabulary are mapped onto it too.
        tag = self._normalized_tag(item.get("tag"), title) if isinstance(item, dict) and "tag" in item else ""
        return {"title": title, "why": self._tidy_visible_text(why), "tag": tag[:NEWS_TAG_MAX_CHARS]}

    def _page_quotes(self, payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        groups = (payload.get("market_snapshot") or {}).get("groups") or {}
        quotes = {}
        for key in MARKET_GROUPS:
            rows = groups.get(key) if isinstance(groups.get(key), list) else []
            quotes[key] = [
                {
                    "label": MARKET_SHORT_NAMES.get(str(row.get("name") or ""), str(row.get("name") or row.get("symbol") or "")),
                    "price": row.get("price"),
                    "change_pct": row.get("change_pct"),
                    "closes": row.get("closes") or [],
                    "as_of_label": self._month_day(row.get("as_of")),
                }
                for row in rows[:3]
                if isinstance(row, dict)
            ]
        return quotes

    @staticmethod
    def _month_day(value: Any) -> str:
        match = re.match(r"\d{4}-(\d{2})-(\d{2})", str(value or ""))
        return f"{match.group(1)}/{match.group(2)}" if match else ""

    def _page_meta(self, payload: dict[str, Any], now: datetime) -> dict[str, Any]:
        try:
            generated = datetime.fromisoformat(str(payload.get("generated_at") or ""))
        except ValueError:
            generated = now
        sources = {"mainland": [], "world": []}
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "").strip().replace("中国新闻网", "中新网")
            names = sources[self._item_section(item)]
            if source and source not in names:
                names.append(source)
        count = len(sources["mainland"]) + len(sources["world"])
        detail = f"{generated:%H:%M} 更新" + (f" · {count} 个信源" if count else "")
        if payload.get("from_cache"):
            detail += " · 缓存"
        warning = str(payload.get("warning") or "").strip()
        return {
            "date_text": news_date_text(generated),
            "detail": warning or detail,
            "warning": bool(warning),
            "mainland_sources": sources["mainland"],
            "world_sources": sources["world"],
        }

    @staticmethod
    def _render_palette(theme_context):
        if not isinstance(theme_context, dict) or theme_context.get("mode") != "night":
            return get_theme_palette("day")
        canonical = theme_context.get("palette")
        if not isinstance(canonical, dict):
            return get_theme_palette("night")
        red = canonical["accent"]
        muted = canonical["muted"]
        return {
            "background": (0, 0, 0),
            "header": (0, 0, 0),
            "ink": canonical["ink"],
            "muted": muted,
            "dim": muted,
            "rule": canonical["rule"],
            "red": red,
            "gold": tuple(
                round((accent * 3 + secondary) / 4)
                for accent, secondary in zip(red, muted)
            ),
            "cyan": tuple(
                round((accent + secondary) / 2)
                for accent, secondary in zip(red, muted)
            ),
            "green": tuple(
                round((accent + secondary * 3) / 4)
                for accent, secondary in zip(red, muted)
            ),
        }

    def _render_news_section_items(self, brief: dict[str, Any], payload: dict[str, Any], section: str) -> list[Any]:
        max_count = MAINLAND_NEWS_MAX if section == "mainland" else WORLD_NEWS_MAX
        existing = self._as_list(brief.get(section), max_count)
        # Only a brief from before the two sections splits its "top" list; an empty
        # section of a current brief must not borrow the other section's stories.
        if not existing and "mainland" not in brief and "world" not in brief:
            legacy_mainland, legacy_world = self._split_legacy_top_sections(brief.get("top"))
            existing = legacy_mainland if section == "mainland" else legacy_world

        target_count = self._section_display_limit(payload, section, len(existing))
        selected = existing[:target_count]
        if len(selected) < target_count:
            rss_items = [item for item in list(payload.get("items") or []) if isinstance(item, dict) and self._item_section(item) == section]
            selected = self._rss_news_cards(rss_items, target_count, selected)
        return selected[:target_count]

    def _section_display_limit(self, payload: dict[str, Any], section: str, existing_count: int) -> int:
        max_count = MAINLAND_NEWS_MAX if section == "mainland" else WORLD_NEWS_MAX
        if existing_count:
            return min(max_count, max(0, existing_count))
        rss_count = sum(
            1
            for item in list(payload.get("items") or [])
            if isinstance(item, dict) and self._item_section(item) == section
        )
        return min(max_count, max(0, rss_count))

    def _base_background(self, dimensions, bg, theme_mode="day") -> Image.Image:
        return Image.new("RGB", dimensions, bg)

    def _draw_title_wordmark(self, image: Image.Image, x, y, size, ink):
        source = self._load_title_wordmark()
        if source is None:
            return None

        try:
            target_w, target_h = [int(value) for value in size]
            resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
            art = ImageOps.contain(source.copy(), (target_w, target_h), method=resample)
            art = self._prepare_title_wordmark(art, ink)
            layer = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
            paste_x = int((target_w - art.width) / 2)
            paste_y = int((target_h - art.height) / 2)
            layer.alpha_composite(art, (paste_x, paste_y))
            image.paste(layer.convert("RGB"), (int(x), int(y)), layer.getchannel("A"))
            bbox = layer.getchannel("A").getbbox()
            if not bbox:
                return None
            return (int(x) + bbox[0], int(y) + bbox[1], int(x) + bbox[2], int(y) + bbox[3])
        except Exception as exc:
            logger.warning("Daily AI News title wordmark unavailable: %s", exc)
            return None

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
    def _prepare_title_wordmark(source: Image.Image, ink):
        wordmark = source.convert("RGBA")
        ink_rgb = tuple(int(value) for value in tuple(ink)[:3])
        if sum(ink_rgb) < 384:
            return wordmark

        pixels = wordmark.load()
        for y in range(wordmark.height):
            for x in range(wordmark.width):
                r, g, b, a = pixels[x, y]
                if not a:
                    continue
                is_red_accent = r > 120 and r > g * 1.25 and r > b * 1.25
                if not is_red_accent:
                    pixels[x, y] = ink_rgb + (a,)
        return wordmark

    @staticmethod
    @lru_cache(maxsize=1)
    def _load_title_wordmark():
        path = PLUGIN_DIR / TITLE_WORDMARK_IMAGE
        if not path.is_file():
            return None
        try:
            return Image.open(path).convert("RGBA")
        except Exception as exc:
            logger.warning("Could not load Daily AI News title wordmark %s: %s", path, exc)
            return None

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

    @lru_cache(maxsize=128)
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
        return get_base_ui_font(
            int(size),
            bold=str(weight or "normal").lower() == "bold",
        )

    def _news_text(self, item) -> tuple[str, str]:
        if isinstance(item, dict):
            return str(item.get("title") or ""), str(item.get("why") or "")
        return str(item), ""

    def _is_chinese_title(self, text: str) -> bool:
        """A displayable RSS title: mostly Chinese, not an English one with a translated word."""
        cjk = len(re.findall(r"[\u3400-\u9fff]", text))
        latin = len(re.findall(r"[A-Za-z]", text))
        return cjk >= 2 and cjk * 2 >= latin

    def _has_cjk(self, text: str) -> bool:
        return bool(re.search(r"[\u3400-\u9fff]", text))

    def _tw(self, draw, text: str, font) -> int:
        return text_width(draw, text, font)
