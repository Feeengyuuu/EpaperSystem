from __future__ import annotations

import hashlib
import html
import json
import logging
import re
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
from plugins.context_cache import write_context
from utils.app_utils import bounded_int, get_available_font_names, get_font
from utils.image_utils import text_width
from utils.http_client import get_http_client
from utils.massive_market_data import MassiveMarketData, MassiveMarketDataError, load_massive_api_key
from utils.plugin_cache import read_json, write_json
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
TITLE_WORDMARK_IMAGE = "title_wordmark.png"
TITLE_WORDMARK_SIZE = (220, 58)
SECTION_WORDMARK_IMAGES = {
    "top": "section_top_wordmark.png",
    "quick": "section_quick_wordmark.png",
    "a_share": "section_a_share_wordmark.png",
    "us_stock": "section_us_stock_wordmark.png",
}
SECTION_WORDMARK_SIZES = {
    "top": (122, 28),
    "quick": (132, 28),
    "a_share": (108, 28),
    "us_stock": (108, 28),
}
SECTION_WORDMARK_Y_OFFSET = -3
SECTION_HEADER_RULE_Y_OFFSET = 27
SUMMARY_SCHEMA_VERSION = "mainland-world-rss-only-dedupe-v16"
DEFAULT_FEED_FETCH_TIMEOUT_SECONDS = 8
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

SECTION_LABELS = {
    "top": "大陆新闻",
    "quick": "世界快报",
    "a_share": "A股今日",
    "us_stock": "美股今日",
}

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
WORLD_NEWS_MAX = 5

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

        context_news = self._as_list(payload.get("top"), MAINLAND_NEWS_MAX + WORLD_NEWS_MAX)
        if not context_news:
            context_news = self._as_list(payload.get("mainland"), MAINLAND_NEWS_MAX) + self._as_list(payload.get("world"), WORLD_NEWS_MAX)

        items = []
        for item in context_news[:MAINLAND_NEWS_MAX + WORLD_NEWS_MAX]:
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
        feeds_text = self._effective_feeds_text(settings.get("feed_urls"))
        max_items = _parse_int(settings.get("max_items"), 22, 6, 40)
        cache_only_render = _enabled(settings.get("_cached_render_only") or settings.get("cached_render_only"))
        force_refresh = _enabled(settings.get("force_refresh")) and not cache_only_render
        date_key = now.strftime("%Y-%m-%d")
        cache_key = self._cache_key(date_key, model, feeds_text, max_items, settings.get("region_focus"))

        cache_file = self._cache_dir() / "brief.json"
        cached = _safe_json_load(cache_file, {})
        cache_matches_current_settings = cached.get("cache_key") == cache_key
        if cached.get("brief") and (cache_only_render or (cache_matches_current_settings and not force_refresh)):
            cached["from_cache"] = True
            if cache_only_render and not cache_matches_current_settings:
                cached["warning"] = "仅重渲染显示，复用现有新闻缓存。"
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

    def _cache_dir(self) -> Path:
        return self.cache_dir(leaf="cache", create=True)

    def _cache_key(self, date_key: str, model: str, feeds_text: str, max_items: int, region_focus: Any) -> str:
        raw = "\n".join([SUMMARY_SCHEMA_VERSION, date_key, model, feeds_text, str(max_items), str(region_focus or "")])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

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
        for section, limit in (("mainland", MAINLAND_NEWS_MAX), ("world", WORLD_NEWS_MAX)):
            for item in self._as_list(brief.get(section), limit):
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
            snapshot["macro"] = self._fetch_massive_macro(massive_client)
        for group, symbols in MARKET_GROUPS.items():
            rows = []
            for symbol, name in symbols:
                row = self._fetch_massive_quote(massive_client, symbol, name)
                if self._is_massive_index_proxy_quote(symbol, row):
                    logger.warning(
                        "Discarding Massive ETF proxy %s for index %s; using Yahoo fallback",
                        row.get("massive_symbol"),
                        symbol,
                    )
                    row = None
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

    def _fetch_yahoo_quote(self, symbol: str, name: str) -> dict[str, Any] | None:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}"
        try:
            resp = get_http_client().request_json(
                "GET",
                url,
                params={"range": "5d", "interval": "1d"},
                timeout=10,
                headers={"User-Agent": "InkyPi Daily AI News/1.0"},
            )
            result = resp.data["chart"]["result"][0]
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
        client_kwargs = {"api_key": api_key}
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
                "lede": "不超过32字的总览",
                "mainland": [{"title": "大陆新闻标题", "why": "为什么重要"}],
                "world": [{"title": "世界新闻标题", "why": "为什么重要"}],
                "sources": ["来源名"],
            },
            "rules": [
                "所有输出字段只使用简体中文；如果素材是繁体中文，必须转换为简体中文再写入 JSON",
                "不要留下英文单词或英文地名，例如 Moscow 必须写成莫斯科，United States 必须写成美国，Iran 必须写成伊朗",
                "如果无法确定某个英文术语的标准中文译名，必须改写句子，不要把英文原词放进 title、why 或 lede",
                "中文词内部不要加空格，例如写“美伊谈判”，不要写“美 伊谈判”",
                "mainland 给 2 到 4 条，只能选择 section=mainland 的 RSS 条目，优先大陆时政、经济、社会、公共安全和重大政策更新",
                "world 给 3 到 5 条，只能选择 section=world 的 RSS 条目，优先国际冲突、外交、政策、事故、市场、法律和重大科技治理",
                "如果某个分区素材不足，宁可少写，不得跨区挪用，也不得编造新闻",
                "mainland 和 world 不允许使用 market_snapshot、股票指数或你自己的背景知识生成新闻",
                "不得写 RSS items 中不存在的人名、机构、政策或市场事件",
                "同一事件只能出现一次，不要用不同标题重复同一军事行动、谈判或事故",
                "优先避开 recent_titles 中的近几天已展示标题，除非该分区没有足够新素材",
                "title 必须包含具体人物/机构/地点/事件动作/结果，禁止只写宏观分类",
                "避免使用“引发讨论”“风险升级”“议题焦点”“全球媒体聚焦”这类宽泛标题，除非同时写清具体事件",
                "不要把人生、心理健康、生活方式、科普解释稿、旧背景稿放进新闻列表，除非它们是当天重大政策或公共事件",
                "lede 必须概括今天最重要的新变化，不要写“今日新闻简报已生成”",
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
        lede = str(data.get("lede") or "").strip()
        top_items = self._merge_section_news(mainland, world) or top
        if not lede or lede == "今日新闻简报已生成。":
            lede = self._fallback_lede(top_items)
        return self._simplify_chinese_payload({
            "lede": lede[:48],
            "mainland": mainland,
            "world": world,
            "top": top_items,
            "a_share": self._as_market_block(data.get("a_share")),
            "us_stock": self._as_market_block(data.get("us_stock")),
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

        payload["mainland"] = mainland
        payload["world"] = world
        payload["top"] = top
        if not str(payload.get("lede") or "").strip():
            payload["lede"] = self._fallback_lede(top)
        return payload

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

        for list_name, limit in (
            ("mainland", MAINLAND_NEWS_MAX),
            ("world", WORLD_NEWS_MAX),
            ("top", MAINLAND_NEWS_MAX + WORLD_NEWS_MAX),
        ):
            cleaned_items = []
            for item in self._as_list(sanitized.get(list_name), limit):
                if isinstance(item, dict):
                    cleaned_item = dict(item)
                    cleaned_item["title"] = self._strip_untranslated_english_terms(str(cleaned_item.get("title") or ""))
                    cleaned_item["why"] = self._strip_untranslated_english_terms(str(cleaned_item.get("why") or ""))
                    cleaned_items.append(cleaned_item)
                else:
                    cleaned_items.append(self._strip_untranslated_english_terms(str(item or "")))
            sanitized[list_name] = cleaned_items

        for block_name in ("a_share", "us_stock"):
            block = sanitized.get(block_name)
            if isinstance(block, dict):
                cleaned_block = dict(block)
                cleaned_block["summary"] = self._strip_untranslated_english_terms(str(cleaned_block.get("summary") or ""))
                cleaned_block["analysis"] = self._strip_untranslated_english_terms(str(cleaned_block.get("analysis") or ""))
                sanitized[block_name] = cleaned_block

        return sanitized

    def _strip_untranslated_english_terms(self, text: str) -> str:
        cleaned = self._simplify_chinese_text(text)

        def replace_match(match: re.Match[str]) -> str:
            term = match.group(0)
            canonical = term.replace(".", "").replace("-", "").upper()
            if canonical in VISIBLE_ENGLISH_ALLOWLIST:
                return term
            return ""

        cleaned = VISIBLE_ENGLISH_WORD_RE.sub(replace_match, cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        cleaned = re.sub(r"\b(ABC|AI|API|BBC|G7|G20|NPR|PBS|RSS)\s+(?=[\u4e00-\u9fff])", r"\1", cleaned)
        cleaned = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", cleaned)
        cleaned = re.sub(r"\s+([，。；：、])", r"\1", cleaned)
        cleaned = re.sub(r"([（(])\s+", r"\1", cleaned)
        cleaned = re.sub(r"\s+([）)])", r"\1", cleaned)
        return cleaned.strip()

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
            if not title or not self._has_cjk(title):
                continue
            if any(self._similar_news_title(title, self._news_text(existing_item)[0]) for existing_item in result):
                continue
            source = str(item.get("source") or "").strip()
            summary = str(item.get("summary") or "").strip()
            why = summary[:36] if summary else (source[:36] or "来自 RSS 源。")
            card = {"title": title[:32], "why": why}
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
            if not title or not self._has_cjk(title):
                continue
            summary = str(source.get("summary") or source.get("source") or "").strip()
            append_candidate({"title": title[:32], "why": summary[:36]}, title)
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
        shared_hot_terms = any(term in left and term in right for term in ("伊朗", "空袭", "煤矿", "太空人", "黎巴嫩", "基辅", "教宗"))
        return overlap >= 0.56 or (shared_hot_terms and overlap >= 0.38)

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
        for pattern, replacement in ENGLISH_NEWS_TERM_REPLACEMENTS:
            simplified = pattern.sub(replacement, simplified)
        simplified = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", simplified)
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
        meta = f"{date_label}  |  智能生成"
        if payload.get("from_cache"):
            meta += "  |  缓存"
        theme_label = self._theme_label(theme_context)
        meta_left = width - margin - max(self._tw(draw, meta, meta_font), self._tw(draw, theme_label, meta_font))

        title_wordmark_box = self._draw_title_wordmark(img, margin, 8, TITLE_WORDMARK_SIZE, ink)
        if title_wordmark_box is None:
            draw.text((margin, 17), title, font=title_font, fill=ink)
            draw.line((margin, 64, margin + min(210, self._tw(draw, title, title_font)), 64), fill=red, width=3)
            title_right = margin + self._tw(draw, title, title_font)
        else:
            title_right = title_wordmark_box[2]
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

        mainland_items = self._render_news_section_items(brief, payload, "mainland")
        world_items = self._render_news_section_items(brief, payload, "world")
        main_gap = 14
        side_w = 350
        main_w = width - margin * 2 - main_gap - side_w
        top_x = margin
        side_x = top_x + main_w + main_gap
        y = max(136, lede_end + 8)
        top_limit_y = module_y = 360

        self._section_header(draw, "◆ " + SECTION_LABELS["top"], top_x, y, main_w, section_font, red, rule, image=img, asset_key="top")
        self._section_header(draw, "◇ " + SECTION_LABELS["quick"], side_x, y, side_w, section_font, cyan, rule, image=img, asset_key="quick")
        self._draw_news_items(
            draw,
            mainland_items,
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
            drop_why_if_needed=True,
        )

        self._draw_news_items(
            draw,
            world_items,
            side_x,
            y + 28,
            side_w,
            side_font,
            small_font,
            cyan,
            ink,
            muted,
            max_y=top_limit_y,
            start_index=1,
            compact=True,
            force_all=True,
            fit_family=font_family,
        )
        module_h = 96
        gap = 20
        col_w = (width - margin * 2 - gap) // 2
        modules = [
            (SECTION_LABELS["a_share"], "a_share", red),
            (SECTION_LABELS["us_stock"], "us_stock", green),
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
                target_image=img,
            )

        footer = self._footer_text(payload, brief)
        draw.text((margin, height - 20), footer, font=footer_font, fill=dim)
        return img

    def _render_news_section_items(self, brief: dict[str, Any], payload: dict[str, Any], section: str) -> list[Any]:
        max_count = MAINLAND_NEWS_MAX if section == "mainland" else WORLD_NEWS_MAX
        existing = self._as_list(brief.get(section), max_count)
        if not existing:
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

    def _theme_label(self, theme_context) -> str:
        return "午夜简报" if (theme_context or {}).get("mode") == "night" else "日间简报"

    def _draw_section_wordmark(self, image: Image.Image, key: str, x: int, y: int, accent):
        source = self._load_section_wordmark(key)
        if source is None:
            return None
        size = SECTION_WORDMARK_SIZES.get(key)
        if not size:
            return None
        try:
            target_w, target_h = [int(value) for value in size]
            resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
            art = ImageOps.contain(source.copy(), (target_w, target_h), method=resample)
            art = self._prepare_section_wordmark(art, accent)
            layer = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
            paste_x = int((target_w - art.width) / 2)
            paste_y = int((target_h - art.height) / 2)
            layer.paste(art, (paste_x, paste_y), art)
            out_y = int(y + SECTION_WORDMARK_Y_OFFSET)
            image.paste(layer, (int(x), out_y), layer)
            return (int(x), out_y, int(x) + target_w, out_y + target_h)
        except Exception as exc:
            logger.warning("Could not draw Daily AI News section wordmark %s: %s", key, exc)
            return None

    @staticmethod
    def _prepare_section_wordmark(source: Image.Image, accent):
        wordmark = source.convert("RGBA")
        accent_rgb = DailyAINews._readable_section_color(accent)
        pixels = wordmark.load()
        for py in range(wordmark.height):
            for px in range(wordmark.width):
                red, green, blue, alpha = pixels[px, py]
                if alpha <= 0:
                    continue
                luma = (red * 299 + green * 587 + blue * 114) / 1000
                if luma < 190:
                    pixels[px, py] = accent_rgb + (alpha,)
        return wordmark

    @staticmethod
    def _readable_section_color(color):
        rgb = tuple(int(value) for value in tuple(color)[:3])
        luma = (rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) / 1000
        if luma >= 170 or max(rgb) < 220:
            return rgb
        target_luma = 185
        mix = max(0.0, min(1.0, (target_luma - luma) / max(1, 255 - luma)))
        return tuple(max(0, min(255, int(round(value + (255 - value) * mix)))) for value in rgb)
    @staticmethod
    @lru_cache(maxsize=8)
    def _load_section_wordmark(key: str):
        filename = SECTION_WORDMARK_IMAGES.get(str(key or ""))
        if not filename:
            return None
        path = PLUGIN_DIR / filename
        if not path.is_file():
            return None
        try:
            return Image.open(path).convert("RGBA")
        except Exception as exc:
            logger.warning("Could not load Daily AI News section wordmark %s: %s", path, exc)
            return None
    def _footer_text(self, payload: dict[str, Any], brief: dict[str, Any]) -> str:
        sources = brief.get("sources") or []
        if sources:
            source_text = " / ".join(str(s) for s in sources[:2])
        else:
            source_text = "新闻源 + AI摘要"
        warning = payload.get("warning")
        if warning:
            return f"来源: {source_text}  |  {warning[:54]}"
        generated_at = str(payload.get("generated_at") or "")[:16].replace("T", " ")
        return f"来源: {source_text}  |  生成: {generated_at}"

    def _section_header(self, draw, label: str, x: int, y: int, width: int, font, accent, rule, image=None, asset_key=None) -> None:
        if image is not None and asset_key:
            wordmark_box = self._draw_section_wordmark(image, asset_key, x, y, accent)
            if wordmark_box is not None:
                self._draw_section_header_rule(draw, x, y, width, rule)
                return
        draw.text((x, y), label, font=font, fill=accent)
        self._draw_section_header_rule(draw, x, y, width, rule)

    @staticmethod
    def _draw_section_header_rule(draw, x: int, y: int, width: int, rule) -> None:
        line_y = int(y + SECTION_HEADER_RULE_Y_OFFSET)
        draw.line((int(x), line_y, int(x + width), line_y), fill=rule, width=1)

    def _market_section_header(self, draw, label: str, key: str, x: int, y: int, width: int, font, accent, rule, image=None) -> None:
        if image is not None:
            wordmark_box = self._draw_section_wordmark(image, key, x, y, accent)
            if wordmark_box is not None:
                return
        icon_size = 12
        icon_x = x + 1
        icon_y = y + 5
        if key == "a_share":
            draw.rectangle((icon_x, icon_y, icon_x + icon_size, icon_y + icon_size), outline=accent, width=2)
            draw.line((icon_x + 3, icon_y + 8, icon_x + 6, icon_y + 5, icon_x + 9, icon_y + 7), fill=accent, width=2)
        else:
            draw.ellipse((icon_x, icon_y, icon_x + icon_size, icon_y + icon_size), outline=accent, width=2)
            center = icon_x + icon_size // 2
            draw.ellipse((center - 2, icon_y + 4, center + 2, icon_y + 8), fill=accent)
        text_x = x + icon_size + 9
        draw.text((text_x, y), label, font=font, fill=accent)
        underline_w = min(width, max(48, icon_size + 9 + self._tw(draw, label, font) + 8))
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
        target_image=None,
    ) -> int:
        self._market_section_header(draw, label, key, x, y, width, section_font, accent, rule, image=target_image)
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
        item_list = list(items or [])
        news_count = self._renderable_news_count(item_list)
        styles = []
        for title_size in range(26, 5, -1):
            style = self._news_fit_style(title_size, news_count)
            styles.append(style)
            if drop_why_if_needed:
                for why_line_limit in (2, 1, 0):
                    limited = dict(style)
                    limited["why_line_limit"] = why_line_limit
                    styles.append(limited)

        prepared = []
        available = max_y - y
        allowed_available = available + 4
        best_score: tuple[int, int, int] | None = None
        for style in styles:
            candidate_rows, needed_total = self._prepare_news_rows_for_style(draw, item_list, width, font_family, style)
            if needed_total <= allowed_available:
                why_penalty = self._why_limit_penalty(style.get("why_line_limit"))
                score = (abs(allowed_available - needed_total) + why_penalty, -style["title"], why_penalty)
                if best_score is None or score < best_score:
                    prepared = candidate_rows
                    best_score = score
            elif best_score is None:
                prepared = candidate_rows

        # Distribute any leftover height as extra leading and inter-item spacing
        # so the column ends near max_y instead of leaving a void above it.
        line_bump = 0
        gap_bump = 0
        if prepared and best_score is not None:
            leftover = available - sum(row[2] for row in prepared)
            if leftover > 0:
                total_lines = sum(len(title_lines) + len(why_lines) for title_lines, why_lines, *_rest in prepared)
                if total_lines:
                    line_bump = min(3, leftover // total_lines)
                    leftover -= line_bump * total_lines
                if len(prepared) > 1:
                    gap_bump = min(10, leftover // (len(prepared) - 1))

        markers = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨"]
        for offset, (title_lines, why_lines, _needed, headline_font, why_font, style) in enumerate(prepared):
            number = start_index + offset
            marker = markers[number - 1] if 0 < number <= len(markers) else f"{number}."
            draw.text((x, y - 1), marker, font=headline_font, fill=marker_color)
            text_x = x + style["marker_w"]
            for line in title_lines:
                draw.text((text_x, y), line, font=headline_font, fill=ink)
                y += style["title_h"] + line_bump
            for line in why_lines:
                draw.text((text_x, y), line, font=why_font, fill=muted)
                y += style["why_h"] + line_bump
            y += style["gap"]
            if offset < len(prepared) - 1:
                y += gap_bump
        return y

    @staticmethod
    def _why_limit_penalty(why_line_limit) -> int:
        if why_line_limit is None:
            return 0
        limit = int(why_line_limit)
        if limit >= 2:
            return 10
        if limit == 1:
            return 20
        return 50

    def _renderable_news_count(self, items) -> int:
        return sum(1 for item in items if any(self._news_text(item)))


    def _prepare_news_rows_for_style(self, draw, items, width: int, font_family: str, style: dict[str, int]):
        headline_font = self._font(font_family, style["title"], "bold")
        why_font = self._font(font_family, style["why"])
        rows = []
        needed_total = 0
        text_w = max(1, width - style["marker_w"])
        for item in list(items or []):
            headline, why = self._news_text(item)
            if not headline and not why:
                continue
            title_lines = self._wrap(draw, headline, headline_font, text_w)
            why_lines = self._wrap(draw, why, why_font, text_w) if why else []
            why_line_limit = style.get("why_line_limit")
            if why_line_limit is not None:
                why_lines = why_lines[:max(0, int(why_line_limit))]
            needed = (
                len(title_lines) * style["title_h"]
                + len(why_lines) * style["why_h"]
                + style["gap"]
            )
            rows.append((title_lines, why_lines, needed, headline_font, why_font, style))
            needed_total += needed
        return rows, needed_total

    @staticmethod
    def _news_body_size_cap(news_count: int) -> int:
        news_count = max(0, int(news_count))
        if news_count <= 1:
            return 23
        if news_count == 2:
            return 22
        if news_count == 3:
            return 20
        if news_count == 4:
            return 18
        return 15

    @staticmethod
    def _news_fit_style(title_size: int, news_count: int | None = None) -> dict[str, int]:
        title_size = max(6, int(title_size))
        why_size = max(6, title_size - 3)
        if news_count is not None:
            why_size = min(why_size, DailyAINews._news_body_size_cap(news_count))
        if title_size <= 14:
            title_h = title_size + 1
            why_h = why_size + 1
            gap = 1
        else:
            title_h = title_size + 3
            why_h = why_size + 2
            gap = max(0, min(7, (title_size - 11) // 2))
        return {
            "title": title_size,
            "why": why_size,
            "marker_w": max(18, title_size * 2 + 2),
            "title_h": title_h,
            "why_h": why_h,
            "gap": gap,
        }

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
        return text_width(draw, text, font)
