from __future__ import annotations

import hashlib
import html
import json
import logging
import math
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode
try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - older Python fallback
    ZoneInfo = None

from PIL import Image, ImageDraw, ImageFont, ImageOps

from plugins.base_plugin.base_plugin import BasePlugin
from plugins.context_cache import write_context
from utils.app_utils import coerce_bool, get_available_font_names, get_font
from utils.http_client import get_http_session
from utils.image_utils import text_width

logger = logging.getLogger(__name__)

PLUGIN_ID = "species_radar"
PLUGIN_DIR = Path(__file__).resolve().parent
SRC_DIR = PLUGIN_DIR.parent.parent
STATIC_FONT_DIR = SRC_DIR / "static" / "fonts"
SHARED_YAHEI_FONT_DIR = PLUGIN_DIR.parent / "sports_dashboard" / "fonts"
TITLE_WORDMARK_IMAGE = "species_radar_title_wordmark.png"
PIXEL_PLACEHOLDER_IMAGE = "species_radar_pixel_placeholder.png"
HEADER_PIXEL_BACKGROUND_IMAGE = "species_radar_header_pixel_background.png"
TITLE_WORDMARK_DISPLAY_SIZE = (150, 34)
TITLE_WORDMARK_EMPTY_DISPLAY_SIZE = (172, 40)
HEADER_PIXEL_BACKGROUND_DISPLAY_SIZE = (450, 48)
CACHE_SCHEMA_VERSION = "species-radar-v2"
GBIF_OCCURRENCE_URL = "https://api.gbif.org/v1/occurrence/search"
GBIF_VERNACULAR_URL = "https://api.gbif.org/v1/species/{taxon_key}/vernacularNames"
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
GOOGLE_STATIC_MAPS_URL = "https://maps.googleapis.com/maps/api/staticmap"
REQUEST_HEADERS = {"User-Agent": "InkyPi SpeciesRadar/1.0", "Accept": "application/json,*/*;q=0.8"}
WIKIDATA_HEADERS = {"User-Agent": "InkyPi SpeciesRadar/1.0", "Accept": "application/sparql-results+json,application/json;q=0.9,*/*;q=0.8"}
CHINESE_LANGUAGE_CODES = {"zh", "zh-cn", "zh-hans", "zh-hant", "zh-tw", "zho", "chi", "cmn", "yue", "wuu", "nan", "chinese", "mandarin", "mandarin chinese", "simplified chinese", "traditional chinese"}
CHINESE_LANGUAGE_PRIORITY = {"zh-hans": 0, "zh-cn": 1, "simplified chinese": 2, "cmn": 3, "mandarin": 4, "mandarin chinese": 4, "zh": 5, "zho": 6, "chi": 6, "zh-hant": 7, "zh-tw": 8, "traditional chinese": 8, "yue": 9, "wuu": 9, "nan": 9, "chinese": 10}
ENGLISH_LANGUAGE_CODES = {"en", "eng", "english"}
TRADITIONAL_TO_SIMPLIFIED_CHARS = {
    "亞": "亚", "個": "个", "兩": "两", "並": "并", "為": "为", "烏": "乌", "樂": "乐", "鄉": "乡", "書": "书", "買": "买", "亂": "乱", "於": "于", "雲": "云", "產": "产", "親": "亲", "從": "从", "侖": "仑", "倉": "仓", "們": "们", "會": "会", "傘": "伞", "傳": "传", "優": "优", "兒": "儿", "內": "内", "兩": "两", "冊": "册", "寫": "写", "凍": "冻", "劃": "划", "劉": "刘", "則": "则", "剛": "刚", "創": "创", "劍": "剑", "劑": "剂", "動": "动", "勢": "势", "區": "区", "華": "华", "協": "协", "單": "单", "卻": "却", "厭": "厌", "參": "参", "雙": "双", "發": "发", "變": "变", "葉": "叶", "號": "号", "嘗": "尝", "團": "团", "園": "园", "國": "国", "圖": "图", "圓": "圆", "場": "场", "塊": "块", "塵": "尘", "壓": "压", "壘": "垒", "壞": "坏", "壯": "壮", "聲": "声", "壺": "壶", "處": "处", "備": "备", "復": "复", "頭": "头", "夾": "夹", "奧": "奥", "媽": "妈", "學": "学", "寶": "宝", "實": "实", "寬": "宽", "對": "对", "導": "导", "將": "将", "專": "专", "尋": "寻", "層": "层", "屬": "属", "歲": "岁", "島": "岛", "嶺": "岭", "巖": "岩", "巢": "巢", "幹": "干", "幾": "几", "庫": "库", "廟": "庙", "廠": "厂", "廣": "广", "廳": "厅", "異": "异", "張": "张", "強": "强", "彈": "弹", "彎": "弯", "當": "当", "錄": "录", "後": "后", "徑": "径", "從": "从", "復": "复", "徵": "征", "恆": "恒", "惡": "恶", "愛": "爱", "慶": "庆", "憂": "忧", "懷": "怀", "態": "态", "應": "应", "戰": "战", "戶": "户", "拋": "抛", "挾": "挟", "捨": "舍", "採": "采", "揚": "扬", "換": "换", "損": "损", "搖": "摇", "摺": "折", "撐": "撑", "據": "据", "擬": "拟", "擇": "择", "擊": "击", "擔": "担", "據": "据", "擴": "扩", "攝": "摄", "擺": "摆", "攜": "携", "數": "数", "斂": "敛", "斃": "毙", "斑": "斑", "斷": "断", "於": "于", "時": "时", "晉": "晋", "晝": "昼", "暫": "暂", "曆": "历", "書": "书", "會": "会", "東": "东", "條": "条", "來": "来", "極": "极", "構": "构", "槍": "枪", "標": "标", "樁": "桩", "樂": "乐", "樑": "梁", "樓": "楼", "樣": "样", "樹": "树", "樺": "桦", "橈": "桡", "橋": "桥", "機": "机", "橫": "横", "檔": "档", "檢": "检", "檸": "柠", "檻": "槛", "櫟": "栎", "櫻": "樱", "權": "权", "欄": "栏", "歐": "欧", "殼": "壳", "氣": "气", "氫": "氢", "沖": "冲", "沒": "没", "況": "况", "洶": "汹", "浹": "浃", "涇": "泾", "涼": "凉", "淺": "浅", "淚": "泪", "淨": "净", "濕": "湿", "渦": "涡", "測": "测", "渾": "浑", "湯": "汤", "準": "准", "溝": "沟", "溪": "溪", "滄": "沧", "滅": "灭", "滌": "涤", "滾": "滚", "滿": "满", "漁": "渔", "漚": "沤", "漢": "汉", "漣": "涟", "漫": "漫", "漿": "浆", "潑": "泼", "潛": "潜", "潤": "润", "潰": "溃", "澀": "涩", "澤": "泽", "濁": "浊", "濃": "浓", "濟": "济", "濤": "涛", "濫": "滥", "灣": "湾", "灘": "滩", "災": "灾", "為": "为", "烏": "乌", "無": "无", "煉": "炼", "煙": "烟", "煩": "烦", "熱": "热", "燈": "灯", "營": "营", "燒": "烧", "燭": "烛", "爭": "争", "爾": "尔", "牆": "墙", "牠": "它", "獅": "狮", "獨": "独", "獵": "猎", "獸": "兽", "獺": "獭", "獼": "猕", "現": "现", "琺": "珐", "環": "环", "璽": "玺", "瓊": "琼", "產": "产", "畝": "亩", "畫": "画", "當": "当", "疊": "叠", "瘡": "疮", "瘋": "疯", "癢": "痒", "發": "发", "皺": "皱", "盜": "盗", "盞": "盏", "監": "监", "盤": "盘", "眾": "众", "睏": "困", "矇": "蒙", "礎": "础", "種": "种", "穀": "谷", "積": "积", "稱": "称", "穩": "稳", "窩": "窝", "窮": "穷", "竄": "窜", "筆": "笔", "筍": "笋", "節": "节", "範": "范", "築": "筑", "簡": "简", "簾": "帘", "籃": "篮", "籠": "笼", "類": "类", "粵": "粤", "糞": "粪", "糧": "粮", "糾": "纠", "紀": "纪", "約": "约", "紅": "红", "紋": "纹", "納": "纳", "紙": "纸", "級": "级", "紛": "纷", "素": "素", "絕": "绝", "絲": "丝", "絡": "络", "給": "给", "統": "统", "經": "经", "綠": "绿", "網": "网", "綱": "纲", "綿": "绵", "緊": "紧", "緒": "绪", "線": "线", "緣": "缘", "編": "编", "緩": "缓", "縣": "县", "縫": "缝", "縮": "缩", "總": "总", "績": "绩", "繩": "绳", "繪": "绘", "繫": "系", "續": "续", "纖": "纤", "罌": "罂", "羅": "罗", "羆": "罴", "義": "义", "習": "习", "翹": "翘", "聖": "圣", "聞": "闻", "聯": "联", "聲": "声", "聰": "聪", "肅": "肃", "脅": "胁", "脈": "脉", "脫": "脱", "腎": "肾", "腫": "肿", "腳": "脚", "腸": "肠", "膚": "肤", "膽": "胆", "膠": "胶", "膩": "腻", "臉": "脸", "臟": "脏", "臺": "台", "與": "与", "興": "兴", "舉": "举", "舊": "旧", "艙": "舱", "艦": "舰", "艱": "艰", "艷": "艳", "藝": "艺", "節": "节", "莖": "茎", "莢": "荚", "華": "华", "萊": "莱", "萬": "万", "葉": "叶", "著": "著", "葯": "药", "葷": "荤", "蒼": "苍", "蓋": "盖", "蓮": "莲", "蓯": "苁", "蔔": "卜", "蔞": "蒌", "蔣": "蒋", "蔥": "葱", "蔦": "茑", "蔭": "荫", "蕁": "荨", "蕆": "蒇", "蕎": "荞", "蕒": "荬", "蕭": "萧", "薈": "荟", "薊": "蓟", "薔": "蔷", "薘": "荙", "薟": "莶", "薦": "荐", "薩": "萨", "薺": "荠", "藍": "蓝", "藎": "荩", "藝": "艺", "藥": "药", "藪": "薮", "蘄": "蕲", "蘆": "芦", "蘇": "苏", "蘊": "蕴", "蘋": "苹", "蘚": "藓", "蘭": "兰", "蘿": "萝", "處": "处", "虛": "虚", "號": "号", "蛺": "蛱", "蜆": "蚬", "蝦": "虾", "蝸": "蜗", "螢": "萤", "螞": "蚂", "蟄": "蛰", "蟈": "蝈", "蟎": "螨", "蟣": "虮", "蟬": "蝉", "蟻": "蚁", "蠅": "蝇", "蠍": "蝎", "蠔": "蚝", "蠟": "蜡", "蠣": "蛎", "蠶": "蚕", "蠑": "蝾", "蠻": "蛮", "衆": "众", "術": "术", "衛": "卫", "衝": "冲", "袞": "衮", "裝": "装", "補": "补", "複": "复", "褐": "褐", "襯": "衬", "覓": "觅", "視": "视", "親": "亲", "覺": "觉", "觀": "观", "觸": "触", "計": "计", "訊": "讯", "記": "记", "訓": "训", "託": "托", "許": "许", "設": "设", "訪": "访", "註": "注", "該": "该", "詳": "详", "語": "语", "誤": "误", "說": "说", "誰": "谁", "調": "调", "談": "谈", "請": "请", "諸": "诸", "諾": "诺", "謀": "谋", "謂": "谓", "謹": "谨", "識": "识", "譜": "谱", "譯": "译", "議": "议", "護": "护", "變": "变", "貝": "贝", "貓": "猫", "貝": "贝", "負": "负", "財": "财", "貨": "货", "貪": "贪", "貧": "贫", "責": "责", "貴": "贵", "買": "买", "費": "费", "賀": "贺", "賁": "贲", "賃": "赁", "資": "资", "賊": "贼", "賓": "宾", "賞": "赏", "賢": "贤", "質": "质", "賴": "赖", "贊": "赞", "贏": "赢", "趙": "赵", "跡": "迹", "踐": "践", "蹤": "踪", "車": "车", "軌": "轨", "軍": "军", "軟": "软", "較": "较", "輕": "轻", "輛": "辆", "轉": "转", "辦": "办", "辭": "辞", "農": "农", "迴": "回", "這": "这", "連": "连", "週": "周", "進": "进", "遊": "游", "運": "运", "過": "过", "達": "达", "遠": "远", "適": "适", "遷": "迁", "選": "选", "遺": "遗", "遼": "辽", "邊": "边", "郵": "邮", "鄉": "乡", "鄭": "郑", "鄰": "邻", "醫": "医", "醜": "丑", "釀": "酿", "釋": "释", "里": "里", "針": "针", "釣": "钓", "鈴": "铃", "鈷": "钴", "鉀": "钾", "鉅": "钜", "鉛": "铅", "鉤": "钩", "銀": "银", "銅": "铜", "銜": "衔", "銳": "锐", "鋁": "铝", "鋒": "锋", "鋸": "锯", "鋼": "钢", "錐": "锥", "錘": "锤", "錦": "锦", "錯": "错", "錢": "钱", "錫": "锡", "錨": "锚", "鍊": "链", "鍋": "锅", "鍛": "锻", "鍵": "键", "鍾": "钟", "鎂": "镁", "鎊": "镑", "鎖": "锁", "鎮": "镇", "鏡": "镜", "鐵": "铁", "鐺": "铛", "鑑": "鉴", "鑰": "钥", "鑽": "钻", "長": "长", "門": "门", "閃": "闪", "閉": "闭", "開": "开", "閏": "闰", "間": "间", "閒": "闲", "閣": "阁", "閩": "闽", "關": "关", "闊": "阔", "隊": "队", "階": "阶", "際": "际", "險": "险", "隱": "隐", "隻": "只", "雜": "杂", "雞": "鸡", "離": "离", "難": "难", "雲": "云", "電": "电", "霧": "雾", "靈": "灵", "靜": "静", "頂": "顶", "項": "项", "順": "顺", "須": "须", "頌": "颂", "預": "预", "頑": "顽", "頒": "颁", "頓": "顿", "頗": "颇", "領": "领", "頸": "颈", "頻": "频", "題": "题", "額": "额", "顏": "颜", "類": "类", "顯": "显", "風": "风", "飛": "飞", "飢": "饥", "飯": "饭", "飲": "饮", "飽": "饱", "養": "养", "餓": "饿", "館": "馆", "餘": "余", "饅": "馒", "馬": "马", "駁": "驳", "駐": "驻", "駝": "驼", "駭": "骇", "騎": "骑", "騙": "骗", "騰": "腾", "驅": "驱", "驚": "惊", "驗": "验", "體": "体", "鬚": "须", "魚": "鱼", "魯": "鲁", "鮁": "鲅", "鮑": "鲍", "鮭": "鲑", "鮮": "鲜", "鯉": "鲤", "鯊": "鲨", "鯽": "鲫", "鯧": "鲳", "鯨": "鲸", "鯰": "鲶", "鯷": "鳀", "鯿": "鳊", "鰁": "鳈", "鰂": "鲗", "鰃": "鳂", "鰍": "鳅", "鰒": "鳆", "鰓": "鳃", "鰛": "鳁", "鰥": "鳏", "鰭": "鳍", "鰱": "鲢", "鰲": "鳌", "鰻": "鳗", "鱈": "鳕", "鱉": "鳖", "鱒": "鳟", "鱗": "鳞", "鱘": "鲟", "鱟": "鲎", "鱧": "鳢", "鳥": "鸟", "鳩": "鸠", "鳳": "凤", "鳴": "鸣", "鳶": "鸢", "鴉": "鸦", "鴒": "鸰", "鴕": "鸵", "鴛": "鸳", "鴝": "鸲", "鴞": "鸮", "鴟": "鸱", "鴣": "鸪", "鴦": "鸯", "鴨": "鸭", "鴯": "鸸", "鴰": "鸹", "鴴": "鸻", "鴿": "鸽", "鵂": "鸺", "鵑": "鹃", "鵒": "鹆", "鵓": "鹁", "鵜": "鹈", "鵝": "鹅", "鵟": "鵟", "鵠": "鹄", "鵡": "鹉", "鵪": "鹌", "鵬": "鹏", "鵯": "鹎", "鵲": "鹊", "鵷": "鹓", "鵾": "鹍", "鶇": "鸫", "鶉": "鹑", "鶊": "鹒", "鶓": "鹋", "鶘": "鹕", "鶚": "鹗", "鶩": "鹜", "鶯": "莺", "鶲": "鹟", "鶴": "鹤", "鶹": "鹠", "鶺": "鹡", "鶻": "鹘", "鶼": "鹣", "鷁": "鹢", "鷂": "鹞", "鷄": "鸡", "鷓": "鹧", "鷗": "鸥", "鷙": "鸷", "鷚": "鹨", "鷟": "鹔", "鷥": "鸶", "鷦": "鹪", "鷯": "鹩", "鷲": "鹫", "鷸": "鹬", "鷹": "鹰", "鷺": "鹭", "鷿": "䴙", "鸇": "鹯", "鸌": "鹱", "鸕": "鸬", "鸚": "鹦", "鸛": "鹳", "鸝": "鹂", "鸞": "鸾", "鹵": "卤", "鹹": "咸", "鹽": "盐", "麥": "麦", "黃": "黄", "黌": "黉", "點": "点", "黨": "党", "鼴": "鼹", "齊": "齐", "齒": "齿", "龍": "龙", "龜": "龟",
}
TRADITIONAL_TO_SIMPLIFIED = str.maketrans(TRADITIONAL_TO_SIMPLIFIED_CHARS)
IMAGE_HEADERS = {"User-Agent": "InkyPi SpeciesRadar/1.0", "Accept": "image/jpeg,image/png,image/webp,image/*;q=0.8"}

DEFAULT_LATITUDE = 37.5485
DEFAULT_LONGITUDE = -121.9886
DEFAULT_LOCATION_NAME = "Fremont, CA"
LUOYANG_LATITUDE = 34.6197
LUOYANG_LONGITUDE = 112.4540
LUOYANG_LOCATION_NAME = "\u6d1b\u9633"
LUOYANG_LOOKBACK_DAYS = 730
DEFAULT_RADIUS_KM = 25
DEFAULT_LOOKBACK_DAYS = 365
DEFAULT_REFRESH_HOURS = 6
DEFAULT_MAP_CACHE_HOURS = 24
DEFAULT_LIMIT = 50
MAX_LIMIT = 100
DEFAULT_NIGHT_START_HOUR = 18
DEFAULT_NIGHT_END_HOUR = 6
EARTH_RADIUS_KM = 6371.0088
MICROSOFT_YAHEI_FONT = "Microsoft YaHei"
DEFAULT_FONT = MICROSOFT_YAHEI_FONT
DEFAULT_CJK_FONT = MICROSOFT_YAHEI_FONT
FALLBACK_FONT = MICROSOFT_YAHEI_FONT
FALLBACK_CJK_FONT = MICROSOFT_YAHEI_FONT

# Color tokens follow docs/color-ui-guidelines.md: warm paper, process black
# linework, and limited vintage comic process-color accents.
COMIC_PAPER = (255, 248, 220)  # 25Y PANTONE 100
COMIC_PANEL = (255, 253, 240)
COMIC_PANEL_BLUE = (235, 246, 255)  # 25B PANTONE 304 family, paper-tinted
COMIC_PANEL_GOLD = (255, 239, 176)  # 50Y PANTONE 101 family, paper-tinted
COMIC_PANEL_GREEN = (235, 249, 236)  # 50Y-25B PANTONE 358 family, paper-tinted
COMIC_PANEL_ORANGE = (255, 239, 222)  # 50Y-25R PANTONE 156 family, paper-tinted
COMIC_PANEL_MAGENTA = (255, 236, 245)  # 25R PANTONE 196 family, paper-tinted
COMIC_PANEL_PURPLE = (244, 239, 255)  # 25R-25B PANTONE 263 family, paper-tinted
COMIC_INK = (8, 8, 8)  # PROCESS BLACK
COMIC_MUTED = (126, 112, 82)  # 50Y-25R-25B PANTONE 465 family
COMIC_RULE = (190, 177, 134)
COMIC_BLUE = (0, 92, 185)  # 100B-25R PANTONE 285 family
COMIC_CYAN = (0, 163, 173)  # 50Y-100B PANTONE 327 family
COMIC_GOLD = (255, 196, 30)  # 100Y-25R PANTONE 123 family
COMIC_ORANGE = (245, 122, 38)  # 100Y-50R PANTONE ORANGE 021 family
COMIC_RED = (222, 45, 38)  # 100Y-100R PANTONE RED 032 family
COMIC_GREEN = (0, 152, 82)  # 100Y-100B PANTONE 354 family
COMIC_PURPLE = (98, 58, 160)  # 100R-100B PANTONE 266 family
COMIC_BROWN = (137, 88, 56)  # 100Y-50R-50B PANTONE 470 family
COMIC_NIGHT_PAPER = (18, 21, 34)
COMIC_NIGHT_PANEL = (29, 34, 51)
COMIC_NIGHT_INK = (255, 248, 220)
COMIC_NIGHT_DIM = (218, 204, 158)
COMIC_NIGHT_MUTED = (167, 154, 117)
COMIC_NIGHT_RULE = (94, 103, 132)

CATEGORY_STYLES = {
    "植物": {"slug": "plants", "color": COMIC_GREEN, "light": COMIC_PANEL_GREEN},
    "真菌": {"slug": "fungi", "color": COMIC_BROWN, "light": COMIC_PANEL_ORANGE},
    "鸟类": {"slug": "birds", "color": COMIC_BLUE, "light": COMIC_PANEL_BLUE},
    "昆虫": {"slug": "insects", "color": COMIC_ORANGE, "light": COMIC_PANEL_GOLD},
    "哺乳动物": {"slug": "mammals", "color": COMIC_PURPLE, "light": COMIC_PANEL_PURPLE},
    "两栖动物": {"slug": "amphibians", "color": COMIC_CYAN, "light": COMIC_PANEL_GREEN},
    "蜗牛/贝类": {"slug": "gastropods", "color": COMIC_RED, "light": COMIC_PANEL_MAGENTA},
    "蛛形动物": {"slug": "arachnids", "color": COMIC_MUTED, "light": COMIC_PANEL},
    "爬行动物": {"slug": "reptiles", "color": COMIC_GREEN, "light": COMIC_PANEL_GOLD},
    "鱼类": {"slug": "fish", "color": COMIC_CYAN, "light": COMIC_PANEL_BLUE},
    "动物": {"slug": "animals", "color": COMIC_BROWN, "light": COMIC_PANEL},
    "其他生物": {"slug": "other", "color": COMIC_PURPLE, "light": COMIC_PANEL_PURPLE},
}


class SpeciesRadar(BasePlugin):
    def generate_settings_template(self):
        params = super().generate_settings_template()
        params["style_settings"] = False
        params["available_fonts"] = get_available_font_names(default=DEFAULT_FONT)
        params["theme_modes"] = ["auto", "comic", "night"]
        params["api_key"] = {
            "required": False,
            "service": "Google Maps Static API",
            "expected_key": "GOOGLE_MAPS_API_KEY or Google_KEY",
        }
        return params

    def generate_image(self, settings, device_config):
        settings = settings or {}
        dimensions = self._display_dimensions(device_config)
        now = datetime.now(timezone.utc)
        location = self._resolve_location(settings, device_config)
        payload = self._daily_payload(settings, now, location)
        payload = self._display_payload(payload, settings, now)
        payload["theme_mode"] = self._theme_mode(settings, now, device_config)
        self._write_context(payload, now)
        return self._render_page(dimensions, payload, settings, now, device_config)

    def _display_dimensions(self, device_config):
        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]
        return tuple(int(value) for value in dimensions)

    def _daily_payload(self, settings, now, location):
        cache_key = self._cache_key(settings, now, location)
        cache = self._read_cache()
        force_refresh = self._enabled(settings.get("forceRefresh") or settings.get("force_refresh"), default=False)
        if cache.get("schema") == CACHE_SCHEMA_VERSION and cache.get("cache_key") == cache_key and not force_refresh:
            cached = cache.get("payload")
            if isinstance(cached, dict):
                payload = dict(cached)
                payload["source_state"] = "cache"
                return payload

        try:
            payload = self._fetch_live_payload(settings, now, location)
            payload.update({"schema": CACHE_SCHEMA_VERSION, "cache_key": cache_key, "source_state": "live"})
            self._write_cache({"schema": CACHE_SCHEMA_VERSION, "cache_key": cache_key, "generated_at": now.isoformat(), "payload": payload})
            return payload
        except Exception as exc:
            logger.warning("SpeciesRadar live fetch failed: %s", exc)

        cached = cache.get("payload")
        if isinstance(cached, dict):
            payload = dict(cached)
            payload["source_state"] = "cache"
            return payload
        return self._fallback_payload(location, cache_key)

    def _display_payload(self, payload, settings, now):
        observations = list(payload.get("observations") or [])
        if len(observations) < 2:
            return payload
        index = self._next_display_index(payload, observations, now)
        if index <= 0 or index >= len(observations):
            selected = observations[0]
            reordered = observations
        else:
            selected = observations[index]
            reordered = [selected] + observations[:index] + observations[index + 1:]
        self._ensure_display_common_name(selected)
        display_payload = dict(payload)
        display_payload["observations"] = reordered
        display_payload["display_pool_size"] = len(observations)
        display_payload["display_pool_index"] = index
        display_payload["display_observation_key"] = self._observation_identity(selected) or str(index)
        display_payload["display_rotation"] = "random_pool"
        return display_payload

    def _next_display_index(self, payload, observations, now):
        pool_key = self._discovery_pool_key(payload, observations, now)
        count = len(observations)
        state = self._read_display_state()
        pools = self._display_state_pools(state, pool_key, count)
        if pools is None:
            available = self._shuffled_display_indices(count)
            discarded = []
            previous_index = None
        else:
            available, discarded = pools
            previous_index = self._coerce_display_index(state.get("selected_index"), count)

        if not available:
            available = self._shuffled_display_indices(discarded or count)
            discarded = []
            self._avoid_immediate_display_repeat(available, previous_index)

        selected_index = int(available.pop(0))
        discarded.append(selected_index)
        self._write_display_state({
            "schema": CACHE_SCHEMA_VERSION,
            "pool_key": pool_key,
            "count": count,
            "available": available,
            "discarded": discarded,
            "remaining": len(available),
            "discarded_count": len(discarded),
            "selected_index": selected_index,
            "selected_key": self._observation_identity(observations[selected_index]) or str(selected_index),
        })
        return selected_index

    def _discovery_pool_key(self, payload, observations, now):
        cache_key = self._clean_text(payload.get("cache_key"))
        if not cache_key:
            cache_key = self._refresh_bucket(now, {"refreshHours": payload.get("refresh_hours") or DEFAULT_REFRESH_HOURS})
        identities = []
        for index, observation in enumerate(observations):
            identities.append(self._observation_identity(observation) or str(index))
        digest = hashlib.sha1("|".join(identities).encode("utf-8")).hexdigest()[:16]
        return f"{cache_key}:{len(observations)}:{digest}"

    def _display_state_pools(self, state, pool_key, count):
        if not isinstance(state, dict) or state.get("pool_key") != pool_key:
            return None
        available = self._coerce_display_indices(state.get("available"), count)
        discarded = self._coerce_display_indices(state.get("discarded"), count)
        if available is None or discarded is None:
            return None
        combined = available + discarded
        if len(combined) != count or len(set(combined)) != count:
            return None
        if sorted(combined) != list(range(count)):
            return None
        return available, discarded

    def _coerce_display_indices(self, values, count):
        if not isinstance(values, list):
            return None
        coerced = []
        for value in values:
            index = self._coerce_display_index(value, count)
            if index is None:
                return None
            coerced.append(index)
        return coerced

    def _coerce_display_index(self, value, count):
        try:
            index = int(value)
        except (TypeError, ValueError):
            return None
        if index < 0 or index >= count:
            return None
        return index

    def _shuffled_display_indices(self, values):
        if isinstance(values, int):
            order = list(range(max(0, values)))
        else:
            order = [int(value) for value in values]
        random.shuffle(order)
        return order

    def _avoid_immediate_display_repeat(self, available, previous_index):
        if previous_index is None or len(available) < 2 or available[0] != previous_index:
            return
        replacement_index = next(
            (index for index, value in enumerate(available[1:], start=1) if value != previous_index),
            None,
        )
        if replacement_index is not None:
            available[0], available[replacement_index] = available[replacement_index], available[0]

    def _read_display_state(self):
        value = self._read_json(self._display_state_path(), {})
        return value if isinstance(value, dict) else {}

    def _write_display_state(self, payload):
        try:
            self._write_json(self._display_state_path(), payload)
        except Exception as exc:
            logger.warning("Could not write SpeciesRadar display rotation state: %s", exc)


    def _fetch_live_payload(self, settings, now, location):
        specs = self._location_specs(settings, location)
        location_payloads = []
        failures = []
        for spec in specs:
            try:
                location_payloads.append(self._fetch_location_payload(settings, now, spec))
            except Exception as exc:
                failures.append(f"{spec.get('id')}: {exc}")
                logger.warning("SpeciesRadar location fetch failed for %s: %s", spec.get("name"), exc)

        observations = []
        locations = []
        total_count = 0
        for location_payload in location_payloads:
            observations.extend(location_payload.get("observations") or [])
            locations.append(location_payload.get("location") or {})
            total = location_payload.get("total_count")
            if isinstance(total, int):
                total_count += total

        observations.sort(key=lambda obs: (obs.get("event_sort") or "", -float(obs.get("distance_km") or 0)), reverse=True)
        if not observations:
            detail = "; ".join(failures) if failures else "no location payloads"
            raise RuntimeError(f"GBIF returned no usable nearby observations with still images ({detail})")

        self._enrich_common_names(observations[:12])
        category_counts = self._category_counts(observations)
        location_counts = self._location_counts(observations)
        primary_location = dict((location_payloads[0].get("location") if location_payloads else specs[0]["location"]) or location)
        summary = self._location_summary(locations or [primary_location])
        payload = {
            "schema": CACHE_SCHEMA_VERSION,
            "source": "GBIF",
            "source_url": "https://www.gbif.org/",
            "location": {**primary_location, "name": summary},
            "primary_location": dict(primary_location),
            "locations": locations,
            "location_summary": summary,
            "location_counts": location_counts,
            "radius_km": specs[0]["radius_km"],
            "radius_label": self._radius_label(locations or [primary_location]),
            "lookback_days": max((int(item.get("lookback_days") or DEFAULT_LOOKBACK_DAYS) for item in locations), default=DEFAULT_LOOKBACK_DAYS),
            "refresh_hours": self._refresh_hours(settings),
            "event_date_range": " | ".join(item.get("event_date_range") or "" for item in locations if item.get("event_date_range")),
            "total_count": total_count if total_count else None,
            "observations": observations,
            "category_counts": category_counts,
            "dual_location_mode": len(locations) > 1,
        }
        if failures:
            payload["location_failures"] = failures
        return payload

    def _fetch_location_payload(self, settings, now, spec):
        location = dict(spec["location"])
        radius_km = int(spec["radius_km"])
        lookback_days = int(spec["lookback_days"])
        limit = int(spec["limit"])
        start = (now.date() - timedelta(days=lookback_days)).isoformat()
        end = now.date().isoformat()
        bbox = self._bbox_for_radius(location["latitude"], location["longitude"], radius_km)
        params = {
            "limit": limit,
            "mediaType": "StillImage",
            "hasCoordinate": "true",
            "basisOfRecord": "HUMAN_OBSERVATION",
            "eventDate": f"{start},{end}",
            "decimalLatitude": f"{bbox['min_lat']:.6f},{bbox['max_lat']:.6f}",
            "decimalLongitude": f"{bbox['min_lon']:.6f},{bbox['max_lon']:.6f}",
            "orderBy": "eventDate",
            "order": "desc",
        }
        data = self._get_json(GBIF_OCCURRENCE_URL, params=params)
        results = data.get("results") if isinstance(data, dict) else []
        observations = self._observations_from_results(results, location, radius_km)
        if not observations:
            raise RuntimeError(f"GBIF returned no usable observations for {location.get('name')}")
        return {
            "location": {
                "id": spec["id"],
                "name": location.get("name"),
                "label": location.get("radar_location_label") or location.get("name"),
                "latitude": location.get("latitude"),
                "longitude": location.get("longitude"),
                "radius_km": radius_km,
                "lookback_days": lookback_days,
                "event_date_range": f"{start},{end}",
                "total_count": data.get("count") if isinstance(data, dict) else None,
            },
            "event_date_range": f"{start},{end}",
            "total_count": data.get("count") if isinstance(data, dict) else None,
            "observations": observations,
        }

    def _location_specs(self, settings, primary_location):
        radius_km = self._int(settings.get("radiusKm") or settings.get("radius_km"), DEFAULT_RADIUS_KM, 1, 100)
        lookback_days = self._int(settings.get("lookbackDays") or settings.get("lookback_days"), DEFAULT_LOOKBACK_DAYS, 7, 1825)
        limit = self._int(settings.get("limit"), DEFAULT_LIMIT, 1, MAX_LIMIT)
        specs = []
        self._append_location_spec(specs, "primary", primary_location, radius_km, lookback_days, limit)

        include_fremont = settings.get("includeFremont")
        if include_fremont is None:
            include_fremont = settings.get("include_fremont")
        if self._enabled(include_fremont, default=True):
            self._append_location_spec(
                specs,
                "fremont",
                {"latitude": DEFAULT_LATITUDE, "longitude": DEFAULT_LONGITUDE, "name": DEFAULT_LOCATION_NAME, "source": "dual_default"},
                radius_km,
                lookback_days,
                limit,
            )

        include_luoyang = settings.get("includeLuoyang")
        if include_luoyang is None:
            include_luoyang = settings.get("include_luoyang")
        if self._enabled(include_luoyang, default=True):
            luoyang_radius = self._int(settings.get("luoyangRadiusKm") or settings.get("luoyang_radius_km"), radius_km, 1, 100)
            luoyang_lookback = self._int(settings.get("luoyangLookbackDays") or settings.get("luoyang_lookback_days"), LUOYANG_LOOKBACK_DAYS, 7, 1825)
            luoyang_limit = self._int(settings.get("luoyangLimit") or settings.get("luoyang_limit"), limit, 1, MAX_LIMIT)
            self._append_location_spec(
                specs,
                "luoyang",
                {"latitude": LUOYANG_LATITUDE, "longitude": LUOYANG_LONGITUDE, "name": LUOYANG_LOCATION_NAME, "source": "dual_default"},
                luoyang_radius,
                luoyang_lookback,
                luoyang_limit,
            )
        return specs

    def _append_location_spec(self, specs, spec_id, location, radius_km, lookback_days, limit):
        candidate = dict(location or {})
        if not self._lat_lon(candidate.get("latitude"), candidate.get("longitude")):
            return
        candidate["name"] = self._clean_text(candidate.get("name")) or DEFAULT_LOCATION_NAME
        candidate_label = self._location_short_name(candidate)
        for existing in specs:
            existing_location = existing.get("location") or {}
            existing_label = self._location_short_name(existing_location)
            if self._same_location(existing_location, candidate) or (candidate_label in {"Fremont", "\u6d1b\u9633"} and existing_label == candidate_label):
                return
        candidate["radar_location_id"] = spec_id
        candidate["radar_location_name"] = candidate["name"]
        candidate["radar_location_label"] = candidate_label
        candidate["radar_radius_km"] = radius_km
        candidate["radar_lookback_days"] = lookback_days
        specs.append({"id": spec_id, "name": candidate["name"], "location": candidate, "radius_km": radius_km, "lookback_days": lookback_days, "limit": limit})

    def _same_location(self, left, right):
        try:
            return abs(float(left.get("latitude")) - float(right.get("latitude"))) < 0.02 and abs(float(left.get("longitude")) - float(right.get("longitude"))) < 0.02
        except (TypeError, ValueError):
            return False

    def _location_short_name(self, location):
        name = self._clean_text((location or {}).get("name"))
        lower = name.casefold()
        if "fremont" in lower:
            return "Fremont"
        if "luoyang" in lower or "\u6d1b\u9633" in name:
            return "\u6d1b\u9633"
        return name or DEFAULT_LOCATION_NAME

    def _location_summary(self, locations):
        parts = []
        for location in locations or []:
            label = self._location_short_name(location)
            days = int(location.get("lookback_days") or DEFAULT_LOOKBACK_DAYS)
            part = f"{label} {self._lookback_label(days)}"
            if part not in parts:
                parts.append(part)
        return " + ".join(parts) if parts else DEFAULT_LOCATION_NAME

    def _lookback_label(self, days):
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = DEFAULT_LOOKBACK_DAYS
        if days >= 365 and days % 365 == 0:
            return f"{days // 365}\u5e74"
        return f"{days}\u5929"

    def _radius_label(self, locations):
        radii = []
        for location in locations or []:
            try:
                radius = int(location.get("radius_km") or DEFAULT_RADIUS_KM)
            except (TypeError, ValueError):
                radius = DEFAULT_RADIUS_KM
            if radius not in radii:
                radii.append(radius)
        if len(radii) == 1:
            return f"{radii[0]} km"
        return "/".join(str(radius) for radius in radii) + " km"

    def _location_counts(self, observations):
        counts = {}
        for observation in observations or []:
            label = self._observation_radar_location_label(observation)
            if not label:
                continue
            counts[label] = counts.get(label, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))

    def _observation_radar_location_label(self, observation):
        label = self._clean_text((observation or {}).get("radar_location_label") or (observation or {}).get("radar_location_name"))
        if label:
            return label
        return ""

    def _observation_distance_meta(self, observation, include_date=False):
        prefix = self._observation_radar_location_label(observation)
        distance = f"{observation.get('distance_km', 0):.1f} km"
        meta = f"{prefix} {distance}" if prefix else distance
        if include_date:
            meta = f"{meta} / {observation.get('event_date') or '-'}"
        return meta

    def _observation_place_detail(self, observation, fallback_location):
        radar = self._observation_radar_location_label(observation)
        place = self._clean_text((observation or {}).get("location"))
        if radar and place:
            return f"{radar} / {place}"
        return place or radar or fallback_location

    def _observations_from_results(self, results, location, radius_km):
        observations = []
        for item in results or []:
            observation = self._observation_from_occurrence(item, location)
            if not observation:
                continue
            if observation["distance_km"] > radius_km:
                continue
            observations.append(observation)
        observations.sort(key=lambda obs: (obs.get("event_sort") or "", -obs.get("distance_km", 0)), reverse=True)
        deduped = []
        seen_species = set()
        for observation in observations:
            key = observation.get("species_key") or observation.get("taxon_key") or observation.get("gbif_key")
            if key and key in seen_species:
                continue
            if key:
                seen_species.add(key)
            deduped.append(observation)
        return deduped

    def _observation_from_occurrence(self, occurrence, location):
        if not isinstance(occurrence, dict):
            return None
        image = self._best_media(occurrence)
        if not image:
            return None
        try:
            lat = float(occurrence.get("decimalLatitude"))
            lon = float(occurrence.get("decimalLongitude"))
        except (TypeError, ValueError):
            return None
        category = self._category_for(occurrence)
        scientific_name = self._scientific_name(occurrence)
        common_name = self._clean_text(occurrence.get("vernacularName"))
        common_name_zh = self._to_simplified_chinese(common_name) if self._contains_cjk(common_name) else ""
        common_name_en = "" if common_name_zh else common_name
        normalized_common_name = common_name_zh or common_name_en or common_name
        event_date = self._event_date_label(occurrence.get("eventDate") or "")
        observation = {
            "gbif_key": str(occurrence.get("key") or occurrence.get("gbifID") or ""),
            "taxon_key": str(occurrence.get("taxonKey") or ""),
            "species_key": str(occurrence.get("speciesKey") or occurrence.get("acceptedTaxonKey") or occurrence.get("taxonKey") or ""),
            "common_name": normalized_common_name,
            "common_name_zh": common_name_zh,
            "common_name_en": common_name_en,
            "display_name": normalized_common_name or scientific_name or "Unknown species",
            "scientific_name": scientific_name,
            "category_label": category["label"],
            "category_slug": category["slug"],
            "kingdom": self._clean_text(occurrence.get("kingdom")),
            "class": self._clean_text(occurrence.get("class")),
            "order": self._clean_text(occurrence.get("order")),
            "family": self._clean_text(occurrence.get("family")),
            "genus": self._clean_text(occurrence.get("genus")),
            "species": self._clean_text(occurrence.get("species")),
            "taxonomy_path": self._taxonomy_path(occurrence, category["label"]),
            "event_date": event_date,
            "event_sort": self._event_sort_value(occurrence.get("eventDate") or ""),
            "latitude": lat,
            "longitude": lon,
            "distance_km": self._haversine_km(location["latitude"], location["longitude"], lat, lon),
            "radar_location_id": self._clean_text(location.get("radar_location_id") or location.get("id")),
            "radar_location_name": self._clean_text(location.get("radar_location_name") or location.get("name")),
            "radar_location_label": self._clean_text(location.get("radar_location_label") or location.get("name")),
            "radar_radius_km": self._int_or_none(location.get("radar_radius_km")),
            "radar_lookback_days": self._int_or_none(location.get("radar_lookback_days")),
            "coordinate_uncertainty_m": self._int_or_none(occurrence.get("coordinateUncertaintyInMeters")),
            "location": self._location_label(occurrence),
            "dataset_name": self._clean_text(occurrence.get("datasetName")),
            "recorded_by": self._clean_text(occurrence.get("recordedBy")),
            "identified_by": self._clean_text(occurrence.get("identifiedBy")),
            "references": str(occurrence.get("references") or ""),
            "iucn_red_list_category": self._clean_text(occurrence.get("iucnRedListCategory")),
            "license": self._clean_text(occurrence.get("license")),
            "image_url": image["url"],
            "photo_creator": image["creator"],
            "rights_holder": image["rights_holder"],
            "photo_license": image["license"] or self._clean_text(occurrence.get("license")),
            "photo_references": image["references"],
        }
        return observation

    def _best_media(self, occurrence):
        candidates = list(occurrence.get("media") or [])
        if not candidates:
            multimedia = (occurrence.get("extensions") or {}).get("http://rs.gbif.org/terms/1.0/Multimedia")
            if isinstance(multimedia, list):
                candidates.extend(multimedia)
        for media in candidates:
            if not isinstance(media, dict):
                continue
            media_type = self._media_value(media, "type")
            identifier = self._media_value(media, "identifier")
            if identifier and (not media_type or str(media_type).lower() == "stillimage"):
                return {
                    "url": str(identifier),
                    "creator": self._clean_text(self._media_value(media, "creator")),
                    "rights_holder": self._clean_text(self._media_value(media, "rightsHolder")),
                    "license": self._clean_text(self._media_value(media, "license")),
                    "references": str(self._media_value(media, "references") or ""),
                }
        return None

    def _media_value(self, media, short_key):
        if short_key in media:
            return media.get(short_key)
        dc_map = {
            "type": "http://purl.org/dc/terms/type",
            "identifier": "http://purl.org/dc/terms/identifier",
            "creator": "http://purl.org/dc/terms/creator",
            "rightsHolder": "http://purl.org/dc/terms/rightsHolder",
            "license": "http://purl.org/dc/terms/license",
            "references": "http://purl.org/dc/terms/references",
        }
        return media.get(dc_map.get(short_key, short_key))

    def _category_for(self, data):
        kingdom = self._clean_text(data.get("kingdom")).casefold()
        class_name = self._clean_text(data.get("class") or data.get("className")).casefold()
        label = "其他生物"
        if kingdom == "plantae":
            label = "植物"
        elif kingdom == "fungi":
            label = "真菌"
        elif class_name == "aves":
            label = "鸟类"
        elif class_name == "insecta":
            label = "昆虫"
        elif class_name == "mammalia":
            label = "哺乳动物"
        elif class_name == "amphibia":
            label = "两栖动物"
        elif class_name == "gastropoda":
            label = "蜗牛/贝类"
        elif class_name == "arachnida":
            label = "蛛形动物"
        elif class_name == "reptilia":
            label = "爬行动物"
        elif class_name in {"actinopterygii", "chondrichthyes"}:
            label = "鱼类"
        elif kingdom == "animalia":
            label = "动物"
        style = CATEGORY_STYLES.get(label, CATEGORY_STYLES["其他生物"])
        return {"label": label, **style}

    def _enrich_common_names(self, observations):
        for observation in observations:
            existing_common = self._clean_text(observation.get("common_name"))
            existing_zh = self._to_simplified_chinese(observation.get("common_name_zh"))
            existing_en = self._clean_text(observation.get("common_name_en"))
            if existing_common and self._contains_cjk(existing_common) and not existing_zh:
                existing_zh = self._to_simplified_chinese(existing_common)
            elif existing_common and not self._contains_cjk(existing_common) and not existing_en:
                existing_en = existing_common

            taxon_key = observation.get("species_key") or observation.get("taxon_key")
            vernacular = self._fetch_vernacular_name_candidates(taxon_key)
            any_name = vernacular.get("any") or ""
            zh_name = existing_zh or vernacular.get("zh") or self._fetch_wikidata_chinese_name(observation.get("species") or observation.get("scientific_name"))
            en_name = existing_en or vernacular.get("en")
            if not zh_name and any_name and self._contains_cjk(any_name):
                zh_name = any_name
            if not en_name and any_name and not self._contains_cjk(any_name):
                en_name = any_name
            zh_name = self._to_simplified_chinese(zh_name)

            display_name = zh_name or en_name or observation.get("scientific_name") or "Unknown species"
            observation["common_name_zh"] = zh_name
            observation["common_name_en"] = en_name
            observation["common_name"] = zh_name or en_name or ""
            observation["display_name"] = display_name
            observation["common_name_lookup_attempted"] = True

    def _ensure_display_common_name(self, observation):
        if not isinstance(observation, dict):
            return
        zh_name = self._to_simplified_chinese(observation.get("common_name_zh"))
        if zh_name:
            observation["common_name_zh"] = zh_name
            observation["common_name"] = zh_name
            observation["display_name"] = zh_name
            return

        existing_common = self._clean_text(observation.get("common_name"))
        display_name = self._clean_text(observation.get("display_name"))
        if existing_common and self._contains_cjk(existing_common):
            zh_name = self._to_simplified_chinese(existing_common)
        elif display_name and self._contains_cjk(display_name):
            zh_name = self._to_simplified_chinese(display_name)
        if zh_name:
            observation["common_name_zh"] = zh_name
            observation["common_name"] = zh_name
            observation["display_name"] = zh_name
            return

        if observation.get("common_name_lookup_attempted"):
            return
        self._enrich_common_names([observation])

    def _fetch_vernacular_name(self, taxon_key):
        names = self._fetch_vernacular_name_candidates(taxon_key)
        return names.get("zh") or names.get("en") or names.get("any") or ""

    def _fetch_vernacular_name_candidates(self, taxon_key):
        taxon_key = str(taxon_key or "").strip()
        empty = {"zh": "", "en": "", "any": ""}
        if not taxon_key:
            return dict(empty)
        cache = self._read_vernacular_cache()
        cache_key = f"vernacular-candidates-v2:{taxon_key}"
        cached = cache.get(cache_key)
        if isinstance(cached, dict):
            return {"zh": self._to_simplified_chinese(cached.get("zh")), "en": self._clean_text(cached.get("en")), "any": self._to_simplified_chinese(cached.get("any"))}
        try:
            data = self._get_json(GBIF_VERNACULAR_URL.format(taxon_key=taxon_key), params={"limit": 100})
            results = data.get("results") if isinstance(data, dict) else []
            names = self._select_vernacular_names(results)
        except Exception as exc:
            logger.warning("Could not fetch GBIF vernacular names for %s: %s", taxon_key, exc)
            names = dict(empty)
        cache[cache_key] = names
        try:
            self._write_json(self._vernacular_cache_path(), cache)
        except Exception as exc:
            logger.warning("Could not write SpeciesRadar vernacular cache: %s", exc)
        return names

    def _select_vernacular_name(self, results):
        names = self._select_vernacular_names(results)
        return names.get("zh") or names.get("en") or names.get("any") or ""

    def _select_vernacular_names(self, results):
        names = {"zh": "", "en": "", "any": ""}
        zh_score = 999
        for item in results or []:
            if not isinstance(item, dict):
                continue
            name = self._clean_text(item.get("vernacularName"))
            if not name:
                continue
            language = self._language_code(item.get("language"))
            simplified_name = self._to_simplified_chinese(name) if self._contains_cjk(name) else name
            if not names["any"]:
                names["any"] = simplified_name
            if self._is_chinese_vernacular(language, name):
                score = self._chinese_language_score(language)
                if not names["zh"] or score < zh_score:
                    names["zh"] = simplified_name
                    zh_score = score
            elif not names["en"] and self._is_english_vernacular(language):
                names["en"] = name
        return names

    def _fetch_wikidata_chinese_name(self, scientific_name):
        scientific_name = self._clean_text(scientific_name)
        if not scientific_name:
            return ""
        cache = self._read_vernacular_cache()
        cache_key = f"wikidata-zh-v3:{scientific_name.casefold()}"
        if cache_key in cache:
            return self._to_simplified_chinese(cache.get(cache_key))
        try:
            query = self._wikidata_taxon_label_query(scientific_name)
            response = get_http_session().get(
                WIKIDATA_SPARQL_URL,
                params={"query": query, "format": "json"},
                headers=WIKIDATA_HEADERS,
                timeout=(5, 20),
            )
            response.raise_for_status()
            data = response.json()
            name = self._to_simplified_chinese(self._select_wikidata_chinese_label(data))
        except Exception as exc:
            logger.warning("Could not fetch Wikidata Chinese label for %s: %s", scientific_name, exc)
            name = ""
        cache[cache_key] = name
        try:
            self._write_json(self._vernacular_cache_path(), cache)
        except Exception as exc:
            logger.warning("Could not write SpeciesRadar vernacular cache: %s", exc)
        return name

    def _wikidata_taxon_label_query(self, scientific_name):
        literal = json.dumps(scientific_name, ensure_ascii=False)
        return f'''
SELECT ?item ?label WHERE {{
  ?item wdt:P225 {literal} .
  ?item rdfs:label ?label .
  FILTER(LANG(?label) IN ("zh", "zh-cn", "zh-hans", "zh-hant", "zh-tw"))
}}
LIMIT 8
'''.strip()

    def _select_wikidata_chinese_label(self, data):
        bindings = (((data or {}).get("results") or {}).get("bindings") or []) if isinstance(data, dict) else []
        best_name = ""
        best_score = 999
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            label_data = binding.get("label") or {}
            label = self._clean_text(label_data.get("value") if isinstance(label_data, dict) else "")
            language = self._language_code(label_data.get("xml:lang") if isinstance(label_data, dict) else "")
            if label and self._contains_cjk(label):
                score = self._chinese_language_score(language)
                if not best_name or score < best_score:
                    best_name = label
                    best_score = score
        return self._to_simplified_chinese(best_name)

    def _chinese_language_score(self, language):
        language = self._language_code(language)
        if language in CHINESE_LANGUAGE_PRIORITY:
            return CHINESE_LANGUAGE_PRIORITY[language]
        if language.startswith("zh-hans"):
            return 0
        if language.startswith("zh-cn"):
            return 1
        if language.startswith("zh-hant"):
            return 7
        if language.startswith("zh"):
            return 5
        return 20

    def _name_lines(self, observation):
        zh_name = self._to_simplified_chinese(observation.get("common_name_zh"))
        en_name = self._clean_text(observation.get("common_name_en"))
        display_name = self._clean_text(observation.get("display_name"))
        scientific_name = self._clean_text(observation.get("scientific_name"))
        if not zh_name and display_name and self._contains_cjk(display_name):
            zh_name = self._to_simplified_chinese(display_name)
        if not en_name and display_name and not self._contains_cjk(display_name) and display_name.casefold() != scientific_name.casefold():
            en_name = display_name
        primary = zh_name or en_name or scientific_name or "Unknown species"
        secondary = en_name if zh_name and en_name else ""
        return primary, secondary, scientific_name

    def _compact_bilingual_name(self, observation):
        zh_name = self._to_simplified_chinese(observation.get("common_name_zh"))
        en_name = self._clean_text(observation.get("common_name_en"))
        if zh_name and en_name:
            return f"{zh_name} / {en_name}"
        if zh_name:
            return zh_name
        if en_name:
            return en_name
        return self._clean_text(observation.get("display_name") or observation.get("scientific_name")) or "Unknown"

    def _language_code(self, value):
        return self._clean_text(value).lower().replace("_", "-")

    def _is_chinese_vernacular(self, language, name):
        if language in CHINESE_LANGUAGE_CODES or language.startswith("zh"):
            return True
        if language in ("", "und", "unknown") and self._contains_cjk(name):
            return True
        return False

    def _is_english_vernacular(self, language):
        return language in ENGLISH_LANGUAGE_CODES or language.startswith("en")

    def _category_counts(self, observations):
        counts = {}
        for observation in observations:
            label = observation.get("category_label") or "其他生物"
            counts[label] = counts.get(label, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))

    def _resolve_location(self, settings, device_config):
        manual = self._settings_location(settings)
        if manual:
            manual["source"] = "settings"
            return manual
        if str(settings.get("locationSource") or "weather").strip().lower() != "manual":
            weather = self._weather_location(device_config)
            if weather:
                weather["source"] = "weather"
                return weather
        return {
            "latitude": DEFAULT_LATITUDE,
            "longitude": DEFAULT_LONGITUDE,
            "name": DEFAULT_LOCATION_NAME,
            "source": "default",
        }

    def _settings_location(self, settings):
        location = self._lat_lon(settings.get("latitude"), settings.get("longitude"))
        if not location:
            return None
        location["name"] = self._clean_text(settings.get("locationName")) or DEFAULT_LOCATION_NAME
        return location

    def _weather_location(self, device_config):
        try:
            playlist_manager = device_config.get_playlist_manager()
        except Exception:
            return None
        for playlist in getattr(playlist_manager, "playlists", []) or []:
            for plugin in getattr(playlist, "plugins", []) or []:
                if getattr(plugin, "plugin_id", "") != "weather":
                    continue
                settings = getattr(plugin, "settings", {}) or {}
                location = self._lat_lon(settings.get("latitude"), settings.get("longitude"))
                if not location:
                    continue
                location["name"] = self._clean_text(settings.get("customTitle")) or DEFAULT_LOCATION_NAME
                return location
        return None

    def _lat_lon(self, latitude, longitude):
        try:
            lat = float(latitude)
            lon = float(longitude)
        except (TypeError, ValueError):
            return None
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            return None
        return {"latitude": lat, "longitude": lon}

    def _render_page(self, dimensions, payload, settings, now, device_config=None):
        width, height = dimensions
        palette = self._palette(settings, now, device_config)
        canvas = Image.new("RGB", dimensions, palette["paper"])
        draw = ImageDraw.Draw(canvas, "RGBA")
        observations = payload.get("observations") or []
        if not observations:
            self._draw_empty(draw, dimensions, payload, palette, now)
            return canvas

        hero = observations[0]
        location = payload.get("location") or {}
        location_name = payload.get("location_summary") or location.get("name") or DEFAULT_LOCATION_NAME
        source = payload.get("source") or "GBIF"
        radius_km = payload.get("radius_km") or DEFAULT_RADIUS_KM
        radius_label = payload.get("radius_label") or f"{radius_km} km"
        margin = max(18, min(width, height) // 22)
        gap = max(18, width // 38)
        header_y = margin - 4
        rule_y = margin + 44
        content_top = rule_y + 15

        title_font = self._font(29, bold=True, cjk=True)
        subtitle_font = self._font(12, cjk=True)
        small_font = self._font(11, cjk=True)
        label_font = self._font(14, bold=True, cjk=True)
        name_font = self._font(26, bold=True, cjk=True)
        sci_font = self._font(14, cjk=True)
        english_common_font = self._font(17)
        latin_font = self._font(15)
        body_font = self._font(15, cjk=True)
        body_bold_font = self._font(16, bold=True, cjk=True)
        row_font = self._font(13, cjk=True)
        micro_font = self._font(10, cjk=True)

        footer = self._license_footer(hero)
        footer_h = self._text_height(draw, footer, micro_font) + 7
        footer_y = height - margin - footer_h
        gallery_items = self._gallery_observations(observations, max_items=4)
        show_gallery = len(observations) >= 4 and bool(gallery_items)
        gallery_h = 70 if show_gallery else 0
        content_bottom = footer_y - gallery_h - (16 if show_gallery else 10)
        primary_name, secondary_name, sci = self._name_lines(hero)

        left_x = margin
        left_w = int(width * 0.59) - margin
        right_x = left_x + left_w + gap
        right_w = width - margin - right_x
        base_image_h = int(height * (0.43 if show_gallery else 0.53))
        name_block_reserve = 112 if secondary_name else 82
        available_image_h = content_bottom - content_top - name_block_reserve
        min_image_h = 150 if show_gallery and secondary_name else (178 if show_gallery else 210)
        image_h = min(base_image_h, max(min_image_h, available_image_h))
        image_box = (left_x, content_top, left_x + left_w, content_top + image_h)

        date_text = now.astimezone(timezone.utc).strftime("%Y.%m.%d")
        date_w = self._text_width(draw, date_text, small_font)
        header_art_x = margin + TITLE_WORDMARK_DISPLAY_SIZE[0] + 42
        header_art_right = width - margin - date_w - 18
        header_art_w = min(HEADER_PIXEL_BACKGROUND_DISPLAY_SIZE[0], max(0, header_art_right - header_art_x))
        header_art_y = rule_y - HEADER_PIXEL_BACKGROUND_DISPLAY_SIZE[1]
        if header_art_w >= 120:
            self._draw_header_pixel_background(
                canvas,
                (
                    header_art_x,
                    header_art_y,
                    header_art_x + header_art_w,
                    rule_y,
                ),
            )

        title_tint = palette["ink"] if palette.get("night") else None
        title_drawn = self._draw_title_wordmark(canvas, margin, header_y - 1, TITLE_WORDMARK_DISPLAY_SIZE, title_tint) if title_tint else self._draw_title_wordmark(canvas, margin, header_y - 1, TITLE_WORDMARK_DISPLAY_SIZE)
        if not title_drawn:
            draw.text((margin, header_y), "\u7269\u79cd\u96f7\u8fbe", fill=palette["ink"], font=title_font)
        subtitle = self._ellipsize(draw, f"{location_name} / {radius_label} / {source}", subtitle_font, width - margin * 2 - 120)
        draw.text((margin, header_y + 33), subtitle, fill=palette["muted"], font=subtitle_font)
        draw.text((width - margin - date_w, header_y + 10), date_text, fill=palette["muted"], font=small_font)
        draw.line((margin, rule_y, width - margin, rule_y), fill=palette["rule"], width=2)

        hero_image = self._download_image(hero.get("image_url"), (left_w, image_h))
        if hero_image:
            fitted = ImageOps.fit(hero_image.convert("RGB"), (left_w, image_h), method=Image.LANCZOS)
            canvas.paste(fitted, (image_box[0], image_box[1]))
            draw.rectangle(image_box, outline=palette["ink"], width=2)
        else:
            self._draw_photo_placeholder(draw, image_box, palette)

        map_box = None
        if self._enabled(settings.get("showObservationMap"), default=True):
            map_box = self._middle_observation_map_box(left_x, image_box[3], left_w, content_bottom)
        name_text_w = left_w
        if map_box:
            name_text_w = max(175, map_box[0] - left_x - 6)

        category = CATEGORY_STYLES.get(hero.get("category_label"), CATEGORY_STYLES["其他生物"])
        badge_text = hero.get("category_label") or "其他生物"
        badge_y = image_box[3] + 10
        badge_w = min(name_text_w, self._text_width(draw, badge_text, label_font) + 24)
        draw.rounded_rectangle((left_x, badge_y, left_x + badge_w, badge_y + 27), radius=5, fill=category["light"], outline=category["color"], width=1)
        draw.text((left_x + 12, badge_y + 4), self._ellipsize(draw, badge_text, label_font, badge_w - 20), fill=category["color"], font=label_font)

        name_y = badge_y + 35
        zh_common = self._to_simplified_chinese(hero.get("common_name_zh"))
        en_common = self._clean_text(hero.get("common_name_en"))
        secondary_common = en_common if zh_common and en_common else ""
        if zh_common and en_common and primary_name:
            gap_w = 12
            english_min_w = min(150, max(104, int(name_text_w * 0.42)))
            primary_limit = max(92, name_text_w - gap_w - english_min_w)
            primary_font, primary_lines = self._fit_wrapped_font(draw, primary_name, primary_limit, max_lines=1, min_size=18, max_size=26, bold=True, cjk=True)
            primary_line = primary_lines[0] if primary_lines else primary_name
            primary_w = min(self._text_width(draw, primary_line, primary_font), primary_limit)
            english_x = left_x + primary_w + gap_w
            english_available = max(80, name_text_w - primary_w - gap_w)
            primary_h = self._line_height(draw, primary_line, primary_font, 1.02)
            english_font, english_lines = self._fit_wrapped_font(
                draw,
                en_common,
                english_available,
                max_lines=2,
                min_size=8,
                max_size=getattr(english_common_font, "size", 17) or 17,
                max_height=max(18, primary_h),
                line_multiplier=1.0,
            )
            draw.text((left_x, name_y), primary_line, fill=palette["ink"], font=self._font_for_text(primary_line, primary_font))
            english_line_h = self._line_height(draw, english_lines[0] if english_lines else en_common, english_font, 1.0)
            english_block_h = max(english_line_h, english_line_h * max(1, len(english_lines)))
            english_y = name_y + max(0, (primary_h - english_block_h) // 2)
            for english_line in english_lines:
                draw.text((english_x, english_y), english_line, fill=palette["accent"], font=english_font)
                english_y += english_line_h
            name_y += max(primary_h, english_block_h)
        else:
            main_is_english = bool(en_common and primary_name.casefold() == en_common.casefold())
            max_size = 27 if main_is_english else 26
            min_size = 8 if main_is_english else 17
            max_lines = 3 if main_is_english else 2
            reserved_sci_h = self._line_height(draw, sci or "Ag", latin_font, 1.0) + 11 if sci and sci.casefold() != primary_name.casefold() else 0
            name_available_h = max(24, content_bottom - name_y - reserved_sci_h - 4)
            main_font, main_lines = self._fit_wrapped_font(
                draw,
                primary_name,
                name_text_w,
                max_lines=max_lines,
                min_size=min_size,
                max_size=max_size,
                bold=True,
                cjk=not main_is_english,
                max_height=name_available_h,
                line_multiplier=1.02,
            )
            for line in main_lines:
                draw.text((left_x, name_y), line, fill=palette["ink"], font=self._font_for_text(line, main_font))
                name_y += self._line_height(draw, line, main_font, 1.02)
        if sci and sci.casefold() not in {primary_name.casefold(), secondary_common.casefold()} and name_y < content_bottom - 12:
            name_y += 7
            sci_line = self._ellipsize(draw, sci, latin_font, name_text_w)
            draw.text((left_x, name_y), sci_line, fill=palette["dim"], font=latin_font)

        if map_box:
            self._draw_observation_map_card(canvas, draw, map_box, hero, settings, device_config, palette, micro_font)

        self._draw_right_panel(canvas, draw, right_x, content_top, right_w, content_bottom - content_top, payload, observations, settings, device_config, palette, body_font, body_bold_font, row_font, micro_font)

        if show_gallery:
            gallery_y = footer_y - gallery_h - 7
            self._draw_thumbnail_strip(canvas, draw, margin, gallery_y, width - margin * 2, gallery_h, gallery_items, palette, row_font, micro_font)

        draw.line((margin, footer_y - 5, width - margin, footer_y - 5), fill=palette["rule"], width=1)
        draw.text((margin, footer_y), self._ellipsize(draw, footer, micro_font, width - margin * 2), fill=palette["muted"], font=micro_font)
        return canvas

    def _draw_right_panel(self, canvas, draw, x, y, width, height, payload, observations, settings, device_config, palette, body_font, body_bold_font, row_font, micro_font):
        hero = observations[0]
        location = payload.get("location") or {}
        location_name = payload.get("location_summary") or location.get("name") or DEFAULT_LOCATION_NAME
        left_text_w = width

        category_label = hero.get("category_label") or "\u5176\u4ed6\u751f\u7269"
        taxonomy_y = y + 2
        taxonomy = hero.get("taxonomy_path") or category_label
        for line in self._wrap(draw, taxonomy, body_font, left_text_w, max_lines=2):
            draw.text((x, taxonomy_y), line, fill=palette["dim"], font=self._font_for_text(line, body_font))
            taxonomy_y += self._line_height(draw, line, body_font, 1.0)

        cursor = taxonomy_y + 10
        draw.line((x, cursor, x + width, cursor), fill=palette["rule"], width=1)
        cursor += 10

        metric_font = self._font(19, bold=True, cjk=True)
        col_gap = 13
        col_w = (width - col_gap) // 2
        metrics = [
            ("\u8ddd\u79bb", f"{hero.get('distance_km', 0):.1f} km"),
            ("\u65e5\u671f", hero.get("event_date") or "-"),
        ]
        for index, (label, value) in enumerate(metrics):
            col_x = x + index * (col_w + col_gap)
            draw.text((col_x, cursor), label, fill=palette["muted"], font=micro_font)
            font = metric_font if index == 0 else row_font
            draw.text((col_x, cursor + 15), self._ellipsize(draw, value, font, col_w), fill=palette["ink"], font=font)
        cursor += 43

        details = [
            ("\u5730\u70b9", self._observation_place_detail(hero, location_name)),
            ("\u6765\u6e90", hero.get("dataset_name") or payload.get("source") or "GBIF"),
        ]
        for label, value in details:
            label_w = self._text_width(draw, label, row_font) + 10
            draw.text((x, cursor), label, fill=palette["accent"], font=row_font)
            draw.text((x + label_w, cursor), self._ellipsize(draw, value or "-", row_font, width - label_w), fill=palette["ink"], font=self._font_for_text(value, row_font))
            cursor += 20

        cursor = self._draw_location_count_chips(draw, x, cursor + 5, width, payload, palette, micro_font) + 5
        cursor = self._draw_category_count_chips(draw, x, cursor, width, payload, observations, palette, micro_font) + 14

        recent_y = max(cursor + 52, y + height - 66)
        placeholder_h = min(52, max(0, recent_y - cursor - 12))
        placeholder_box = (x, cursor, x + width, cursor + placeholder_h)
        if placeholder_h >= 30:
            self._draw_right_panel_pixel_placeholder(canvas, draw, placeholder_box, palette)
        draw.line((x, recent_y - 7, x + width, recent_y - 7), fill=palette["rule"], width=1)
        recent_items = observations[1:3]
        if not recent_items:
            draw.text((x, recent_y), "\u6ca1\u6709\u66f4\u591a\u53ef\u663e\u793a\u8bb0\u5f55", fill=palette["muted"], font=row_font)
            return
        for obs in recent_items:
            if recent_y > y + height - 20:
                break
            style = CATEGORY_STYLES.get(obs.get("category_label"), CATEGORY_STYLES["\u5176\u4ed6\u751f\u7269"])
            draw.ellipse((x, recent_y + 4, x + 9, recent_y + 13), fill=style["color"])
            primary = self._compact_bilingual_name(obs)
            category_label = obs.get("category_label") or "\u5176\u4ed6\u751f\u7269"
            line = f"{category_label} / {primary}"
            draw.text((x + 16, recent_y), self._ellipsize(draw, line, row_font, width - 16), fill=palette["ink"], font=self._font_for_text(line, row_font))
            meta = self._observation_distance_meta(obs, include_date=True)
            draw.text((x + 16, recent_y + 15), self._ellipsize(draw, meta, micro_font, width - 16), fill=palette["muted"], font=micro_font)
            recent_y += 32
    def _draw_location_count_chips(self, draw, x, y, width, payload, palette, micro_font):
        counts = payload.get("location_counts") or {}
        if not counts:
            return y - 5
        chip_x = x
        chip_y = y
        chip_h = 22
        max_y = y
        styles = [
            (COMIC_PANEL_BLUE, COMIC_BLUE),
            (COMIC_PANEL_GOLD, COMIC_ORANGE),
            (COMIC_PANEL_GREEN, COMIC_GREEN),
        ]
        for index, (label, count) in enumerate(list(counts.items())[:3]):
            text = f"{label} {int(count or 0)}"
            fill, outline = styles[index % len(styles)]
            chip_w = min(width, self._text_width(draw, text, micro_font) + 20)
            if chip_x > x and chip_x + chip_w > x + width:
                chip_x = x
                chip_y += chip_h + 4
            if chip_y > y + chip_h + 4:
                break
            box = (chip_x, chip_y, chip_x + chip_w, chip_y + chip_h)
            draw.rounded_rectangle(box, radius=5, fill=fill, outline=outline, width=1)
            draw.text((chip_x + 9, chip_y + 4), text, fill=outline, font=micro_font)
            chip_x += chip_w + 7
            max_y = max(max_y, chip_y + chip_h)
        return max_y
    def _draw_category_count_chips(self, draw, x, y, width, payload, observations, palette, micro_font):
        counts = payload.get("category_counts") or self._category_counts(observations)
        if not counts:
            return y - 5
        hero_label = (observations[0] or {}).get("category_label") if observations else None
        items = [(label, int(count or 0)) for label, count in counts.items() if count]
        items.sort(key=lambda item: (0 if item[0] == hero_label else 1, -item[1], item[0]))
        chip_x = x
        chip_y = y
        chip_h = 24
        max_y = y
        for label, count in items[:4]:
            text = f"{label} {count}"
            style = CATEGORY_STYLES.get(label, CATEGORY_STYLES["其他生物"])
            chip_w = min(width, self._text_width(draw, text, micro_font) + 22)
            if chip_x > x and chip_x + chip_w > x + width:
                chip_x = x
                chip_y += chip_h + 5
            if chip_y > y + chip_h + 5:
                break
            box = (chip_x, chip_y, chip_x + chip_w, chip_y + chip_h)
            draw.rounded_rectangle(box, radius=5, fill=style["light"], outline=style["color"], width=1)
            draw.text((chip_x + 10, chip_y + 5), text, fill=style["color"], font=micro_font)
            chip_x += chip_w + 7
            max_y = max(max_y, chip_y + chip_h)
        return max_y

    def _draw_right_panel_pixel_placeholder(self, canvas, draw, box, palette):
        x0, y0, x1, y1 = [int(v) for v in box]
        if x1 <= x0 or y1 <= y0:
            return
        target_size = (max(1, x1 - x0), max(1, y1 - y0))
        image_path = PLUGIN_DIR / PIXEL_PLACEHOLDER_IMAGE
        if image_path.is_file():
            try:
                with Image.open(image_path) as image:
                    fitted = ImageOps.fit(image.convert("RGB"), target_size, method=Image.NEAREST)
                canvas.paste(fitted, (x0, y0))
                draw.rectangle((x0, y0, x1, y1), outline=palette["rule"], width=1)
                return
            except Exception as exc:
                logger.debug("Failed to draw species radar pixel placeholder: %s", exc)

        draw.rounded_rectangle((x0, y0, x1, y1), radius=4, fill=palette["paper"], outline=palette["rule"], width=1)
        center_y = (y0 + y1) // 2
        step = 8
        for gx in range(x0 + step, x1, step * 2):
            draw.line((gx, y0 + 4, gx, y1 - 4), fill=palette["rule"], width=1)
        for gy in range(y0 + step, y1, step * 2):
            draw.line((x0 + 4, gy, x1 - 4, gy), fill=palette["rule"], width=1)
        radar_x = x0 + 36
        draw.ellipse((radar_x - 18, center_y - 18, radar_x + 18, center_y + 18), outline=palette["accent"], width=2)
        draw.line((radar_x, center_y, radar_x + 20, center_y - 12), fill=palette["accent"], width=3)
        pin_x = x0 + int(target_size[0] * 0.55)
        draw.ellipse((pin_x - 6, center_y - 16, pin_x + 6, center_y - 4), fill=COMIC_ORANGE)
        draw.rectangle((pin_x - 2, center_y - 4, pin_x + 2, center_y + 9), fill=COMIC_ORANGE)
        bird_x = x0 + int(target_size[0] * 0.78)
        draw.rectangle((bird_x, center_y - 10, bird_x + 18, center_y - 2), fill=COMIC_BLUE)
        draw.rectangle((bird_x + 5, center_y - 18, bird_x + 10, center_y - 10), fill=COMIC_BLUE)
        draw.rectangle((bird_x - 9, center_y - 6, bird_x, center_y - 2), fill=COMIC_PANEL_BLUE)
        leaf_x = x0 + int(target_size[0] * 0.32)
        draw.polygon([(leaf_x, center_y + 12), (leaf_x + 12, center_y), (leaf_x + 24, center_y + 12), (leaf_x + 12, center_y + 18)], fill=COMIC_GREEN)
        draw.line((leaf_x + 12, center_y + 2, leaf_x + 12, center_y + 18), fill=palette["ink"], width=1)

    def _middle_observation_map_box(self, left_x, image_bottom, left_w, content_bottom):
        map_w = min(195, max(175, int(left_w * 0.41)))
        x0 = int(left_x + left_w - map_w)
        y0 = int(image_bottom + 26)
        available_h = int(content_bottom - y0 - 4)
        if available_h < 48:
            return None
        map_h = min(86, max(58, available_h))
        return (x0, y0, x0 + map_w, y0 + map_h)

    def _draw_observation_map_card(self, canvas, draw, box, observation, settings, device_config, palette, micro_font):
        x0, y0, x1, y1 = [int(v) for v in box]
        map_w = max(1, x1 - x0)
        map_h = max(1, y1 - y0)
        map_image = self._load_observation_map(settings, device_config, observation, (map_w, map_h))
        if map_image:
            fitted = ImageOps.fit(map_image.convert("RGB"), (map_w, map_h), method=Image.LANCZOS)
            canvas.paste(fitted, (x0, y0))
            draw.rectangle((x0, y0, x1, y1), outline=palette["ink"], width=1)
        else:
            self._draw_observation_map_placeholder(draw, box, observation, palette, micro_font)
        label = "\u89c2\u5bdf\u4f4d\u7f6e"
        precision = self._uncertainty_label(observation)
        label_w = self._text_width(draw, label, micro_font) + 10
        label_box = (x0 + 5, y0 + 5, min(x1 - 5, x0 + 5 + label_w), y0 + 20)
        draw.rounded_rectangle(label_box, radius=3, fill=palette["paper"] + (225,), outline=palette["rule"], width=1)
        draw.text((label_box[0] + 5, label_box[1] + 2), label, fill=palette["ink"], font=micro_font)
        if precision:
            precision_w = self._text_width(draw, precision, micro_font) + 10
            precision_box = (max(x0 + 5, x1 - precision_w - 5), y1 - 20, x1 - 5, y1 - 5)
            draw.rounded_rectangle(precision_box, radius=3, fill=palette["paper"] + (225,), outline=palette["rule"], width=1)
            draw.text((precision_box[0] + 5, precision_box[1] + 2), precision, fill=palette["muted"], font=micro_font)

    def _gallery_observations(self, observations, max_items=4):
        if len(observations or []) < 2:
            return []
        hero_identity = self._observation_identity(observations[0])
        seen_identities = {hero_identity} if hero_identity else set()
        seen_categories = {observations[0].get("category_label")}
        candidates = []
        for observation in observations[1:]:
            if not observation.get("image_url"):
                continue
            identity = self._observation_identity(observation)
            if identity and identity in seen_identities:
                continue
            candidates.append(observation)

        selected = []
        for observation in candidates:
            category = observation.get("category_label")
            if category in seen_categories:
                continue
            selected.append(observation)
            identity = self._observation_identity(observation)
            if identity:
                seen_identities.add(identity)
            seen_categories.add(category)
            if len(selected) >= max_items:
                return selected

        for observation in candidates:
            identity = self._observation_identity(observation)
            if identity and identity in seen_identities:
                continue
            selected.append(observation)
            if identity:
                seen_identities.add(identity)
            if len(selected) >= max_items:
                break
        return selected

    def _observation_identity(self, observation):
        location_id = self._clean_text((observation or {}).get("radar_location_id"))
        for key in ("species_key", "taxon_key", "scientific_name", "gbif_key", "display_name"):
            value = self._clean_text(observation.get(key))
            if value:
                identity = value.casefold()
                return f"{location_id}:{identity}" if location_id else identity
        return ""
    def _draw_thumbnail_strip(self, canvas, draw, x, y, width, height, observations, palette, row_font, micro_font):
        if not observations:
            return
        title = "其他发现"
        draw.line((x, y, x + width, y), fill=palette["rule"], width=1)
        draw.text((x, y + 5), title, fill=palette["ink"], font=row_font)
        hint = "优先显示不同类群"
        draw.text((x + width - self._text_width(draw, hint, micro_font), y + 7), hint, fill=palette["muted"], font=micro_font)

        top = y + 24
        thumb_h = max(38, height - 30)
        gap = 9
        count = min(len(observations), 4)
        cell_w = int((width - gap * (count - 1)) / count)
        thumb_w = min(56, max(44, int(cell_w * 0.34)))
        for index, observation in enumerate(observations[:count]):
            cell_x = x + index * (cell_w + gap)
            style = CATEGORY_STYLES.get(observation.get("category_label"), CATEGORY_STYLES["其他生物"])
            thumb_box = (cell_x, top, cell_x + thumb_w, top + thumb_h)
            image = self._download_image(observation.get("image_url"), (thumb_w, thumb_h))
            if image:
                fitted = ImageOps.fit(image.convert("RGB"), (thumb_w, thumb_h), method=Image.LANCZOS)
                canvas.paste(fitted, (thumb_box[0], thumb_box[1]))
                draw.rectangle(thumb_box, outline=style["color"], width=2)
            else:
                self._draw_micro_photo_placeholder(draw, thumb_box, palette, style)

            text_x = thumb_box[2] + 6
            text_w = max(26, cell_w - thumb_w - 6)
            label = observation.get("category_label") or "其他生物"
            draw.text((text_x, top), self._ellipsize(draw, label, micro_font, text_w), fill=style["color"], font=micro_font)
            name = self._compact_bilingual_name(observation)
            draw.text((text_x, top + 15), self._ellipsize(draw, name, micro_font, text_w), fill=palette["ink"], font=self._font_for_text(name, micro_font))
            meta = self._observation_distance_meta(observation)
            draw.text((text_x, top + 30), self._ellipsize(draw, meta, micro_font, text_w), fill=palette["muted"], font=micro_font)

    def _draw_micro_photo_placeholder(self, draw, box, palette, style):
        draw.rectangle(box, fill=palette["panel"], outline=style["color"], width=1)
        x0, y0, x1, y1 = box
        draw.line((x0 + 5, y1 - 8, x0 + 18, y0 + 12, x1 - 6, y1 - 10), fill=palette["rule"], width=1)
        draw.ellipse((x0 + 8, y0 + 7, x0 + 18, y0 + 17), outline=palette["rule"], width=1)
    def _draw_radar(self, draw, x, y, size, observations, palette):
        cx = x + size / 2
        cy = y + size / 2
        radius = size / 2 - 4
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=palette["rule"], width=2)
        draw.ellipse((cx - radius * 0.55, cy - radius * 0.55, cx + radius * 0.55, cy + radius * 0.55), outline=palette["rule"], width=1)
        draw.line((cx - radius, cy, cx + radius, cy), fill=palette["rule"], width=1)
        draw.line((cx, cy - radius, cx, cy + radius), fill=palette["rule"], width=1)
        for index, obs in enumerate(observations[:10]):
            distance = max(0.0, min(float(obs.get("distance_km") or 0), 25.0))
            angle = (index * 137.5) % 360
            point_radius = 4 + (distance / 25.0) * (radius - 8)
            px = cx + math.cos(math.radians(angle)) * point_radius
            py = cy + math.sin(math.radians(angle)) * point_radius
            style = CATEGORY_STYLES.get(obs.get("category_label"), CATEGORY_STYLES["其他生物"])
            draw.ellipse((px - 3, py - 3, px + 3, py + 3), fill=style["color"])
        draw.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=palette["accent"])

    def _draw_photo_placeholder(self, draw, box, palette):
        draw.rectangle(box, fill=palette["panel"], outline=palette["ink"], width=2)
        x0, y0, x1, y1 = box
        cx = (x0 + x1) // 2
        cy = (y0 + y1) // 2
        draw.ellipse((cx - 48, cy - 48, cx + 48, cy + 48), outline=palette["rule"], width=2)
        draw.line((cx - 66, cy, cx + 66, cy), fill=palette["rule"], width=1)
        draw.line((cx, cy - 66, cx, cy + 66), fill=palette["rule"], width=1)
        font = self._font(18, bold=True, cjk=True)
        text = "GBIF PHOTO"
        draw.text((cx - self._text_width(draw, text, font) / 2, cy - 10), text, fill=palette["muted"], font=font)

    def _draw_empty(self, draw, dimensions, payload, palette, now):
        width, height = dimensions
        title_font = self._font(32, bold=True, cjk=True)
        body_font = self._font(19, cjk=True)
        title = "\u7269\u79cd\u96f7\u8fbe"
        message = "GBIF \u6682\u65f6\u6ca1\u6709\u8fd4\u56de\u9644\u8fd1\u5e26\u7167\u7247\u7684\u89c2\u5bdf\u8bb0\u5f55"
        canvas = getattr(draw, "_image", None)
        title_tint = palette["ink"] if palette.get("night") else None
        title_drawn = bool(canvas) and (self._draw_title_wordmark(canvas, 28, 24, TITLE_WORDMARK_EMPTY_DISPLAY_SIZE, title_tint) if title_tint else self._draw_title_wordmark(canvas, 28, 24, TITLE_WORDMARK_EMPTY_DISPLAY_SIZE))
        if not title_drawn:
            draw.text((28, 30), title, fill=palette["ink"], font=title_font)
        draw.line((28, 82, width - 28, 82), fill=palette["rule"], width=2)
        draw.text((28, 118), message, fill=palette["ink"], font=body_font)
        location = payload.get("location_summary") or (payload.get("location") or {}).get("name") or DEFAULT_LOCATION_NAME
        draw.text((28, 150), f"\u4f4d\u7f6e: {location}", fill=palette["muted"], font=body_font)
        draw.text((28, height - 42), f"GBIF / {now.date().isoformat()}", fill=palette["muted"], font=self._font(12))

    def _draw_header_pixel_background(self, canvas, box):
        path = PLUGIN_DIR / HEADER_PIXEL_BACKGROUND_IMAGE
        if not path.is_file():
            return False
        try:
            x0, y0, x1, y1 = [int(value) for value in box]
            target_w = max(1, x1 - x0)
            target_h = max(1, y1 - y0)
            with Image.open(path) as image:
                source = ImageOps.exif_transpose(image).convert("RGBA")
            resample = Image.Resampling.NEAREST if hasattr(Image, "Resampling") else Image.NEAREST
            art = ImageOps.contain(source, (target_w, target_h), method=resample)
            layer = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
            layer.alpha_composite(art, ((target_w - art.width) // 2, (target_h - art.height) // 2))
            canvas.paste(layer.convert("RGB"), (x0, y0), layer.getchannel("A"))
            return True
        except Exception as exc:
            logger.warning("SpeciesRadar header pixel background unavailable: %s", exc)
            return False
    def _draw_title_wordmark(self, canvas, x, y, size, tint=None):
        source = self._load_title_wordmark()
        if source is None:
            return False
        try:
            target_w, target_h = [int(value) for value in size]
            if target_w <= 0 or target_h <= 0:
                return False
            resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
            source = self._trim_transparent(source.copy())
            if tint:
                source = self._tint_rgba(source, tint)
            art = ImageOps.contain(source, (target_w, target_h), method=resample)
            layer = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
            layer.alpha_composite(art, (0, (target_h - art.height) // 2))
            canvas.paste(layer.convert("RGB"), (int(x), int(y)), layer.getchannel("A"))
            return True
        except Exception as exc:
            logger.warning("SpeciesRadar title wordmark unavailable: %s", exc)
            return False

    def _tint_rgba(self, image, color):
        source = image.convert("RGBA")
        tint = Image.new("RGBA", source.size, tuple(color[:3]) + (0,))
        tint.putalpha(source.getchannel("A"))
        return tint

    def _load_title_wordmark(self):
        path = PLUGIN_DIR / TITLE_WORDMARK_IMAGE
        if not path.is_file():
            return None
        try:
            with Image.open(path) as image:
                return ImageOps.exif_transpose(image).convert("RGBA")
        except Exception as exc:
            logger.warning("Could not load SpeciesRadar title wordmark %s: %s", path, exc)
            return None

    def _trim_transparent(self, image):
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        bbox = image.getchannel("A").getbbox()
        return image.crop(bbox) if bbox else image

    def _download_image(self, url, target_size):
        if not url:
            return None
        try:
            response = get_http_session().get(url, headers=IMAGE_HEADERS, timeout=(5, 20))
            response.raise_for_status()
            with Image.open(BytesIO(response.content)) as image:
                return ImageOps.exif_transpose(image).convert("RGB")
        except Exception as exc:
            logger.warning("Could not download SpeciesRadar image %s: %s", url, exc)
            return None

    def _load_observation_map(self, settings, device_config, observation, target_size):
        key = self._google_maps_api_key(settings, device_config)
        if not key:
            return None
        url = self._google_observation_map_url(settings, key, observation, target_size)
        if not url:
            return None

        cache_hours = self._int(settings.get("mapCacheHours"), DEFAULT_MAP_CACHE_HOURS, 1, 168)
        cache_file = self._map_cache_file(url)
        try:
            if cache_file.is_file() and time.time() - cache_file.stat().st_mtime < cache_hours * 3600:
                with Image.open(cache_file) as image:
                    return image.convert("RGB")
        except Exception as exc:
            logger.debug("Could not use cached SpeciesRadar observation map: %s", exc)

        timeout = self._int(settings.get("mapTimeoutSeconds"), 8, 3, 15)
        try:
            response = get_http_session().get(url, headers=IMAGE_HEADERS, timeout=(4, timeout))
            response.raise_for_status()
            if len(response.content) > 3 * 1024 * 1024:
                raise RuntimeError("map image too large")
            with Image.open(BytesIO(response.content)) as image:
                loaded = ImageOps.exif_transpose(image).convert("RGB")
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            loaded.save(cache_file)
            return loaded
        except Exception as exc:
            logger.warning("SpeciesRadar Google map fetch failed: %s", exc)
            return None

    def _google_maps_api_key(self, settings, device_config):
        explicit = str(settings.get("googleMapsApiKey") or "").strip()
        if explicit:
            return explicit
        for key in ("GOOGLE_MAPS_API_KEY", "Google_KEY", "GOOGLE_KEY"):
            value = self._load_env(device_config, key)
            if value:
                return value
        return ""

    def _google_observation_map_url(self, settings, api_key, observation, target_size):
        lat = observation.get("latitude")
        lon = observation.get("longitude")
        try:
            lat = float(lat)
            lon = float(lon)
        except (TypeError, ValueError):
            return ""
        width = max(160, min(640, int(target_size[0])))
        height = max(40, min(640, int(target_size[1])))
        zoom = self._int(settings.get("observationMapZoom"), 12, 8, 17)
        map_type = str(settings.get("googleMapType") or "terrain").strip().lower()
        if map_type not in {"roadmap", "terrain", "satellite", "hybrid"}:
            map_type = "terrain"
        marker = f"color:red|size:mid|label:S|{lat:.5f},{lon:.5f}"
        params = [
            ("center", f"{lat:.5f},{lon:.5f}"),
            ("zoom", str(zoom)),
            ("size", f"{width}x{height}"),
            ("scale", "2"),
            ("format", "png"),
            ("maptype", map_type),
            *[("style", style) for style in self._google_map_styles()],
            ("markers", marker),
            ("key", api_key),
        ]
        return f"{GOOGLE_STATIC_MAPS_URL}?{urlencode(params)}"

    @staticmethod
    def _google_map_styles():
        return [
            "feature:poi|visibility:off",
            "feature:transit|visibility:off",
            "feature:all|element:labels.text.fill|color:0x5c5246",
            "feature:all|element:labels.text.stroke|color:0xfbf5df",
            "feature:water|element:geometry|color:0x6bc2d6",
            "feature:landscape|element:geometry|color:0xf4edd0",
            "feature:road|element:geometry|color:0xd8b778",
        ]

    def _map_cache_file(self, url):
        digest = hashlib.sha1(str(url).encode("utf-8")).hexdigest()[:18]
        return self._cache_dir() / f"map_{digest}.png"

    def _draw_observation_map_placeholder(self, draw, box, observation, palette, micro_font):
        x0, y0, x1, y1 = [int(v) for v in box]
        draw.rectangle(box, fill=palette["panel"], outline=palette["ink"], width=1)
        water = (111, 194, 214)
        road = (216, 183, 120)
        land = (248, 240, 203)
        draw.rectangle((x0 + 1, y0 + 1, x1 - 1, y1 - 1), fill=land)
        draw.polygon([(x0 + 1, y0 + 1), (x0 + int((x1 - x0) * 0.42), y0 + 1), (x0 + int((x1 - x0) * 0.28), y1 - 1), (x0 + 1, y1 - 1)], fill=water)
        draw.line((x0 + 18, y1 - 10, x1 - 12, y0 + 12), fill=road, width=3)
        draw.line((x0 + 8, y0 + 12, x1 - 18, y1 - 8), fill=road, width=2)
        cx = (x0 + x1) // 2
        cy = (y0 + y1) // 2
        uncertainty = observation.get("coordinate_uncertainty_m") or 0
        if uncertainty:
            radius = max(7, min((x1 - x0) // 3, int(math.sqrt(max(1, uncertainty)) / 2)))
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=palette["accent"], width=1)
        draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=COMIC_ORANGE, outline=palette["ink"], width=1)
        label = "\u65e0 Google \u5e95\u56fe" if not self._google_maps_api_key({}, None) else "\u5730\u56fe\u52a0\u8f7d\u5931\u8d25"
        draw.text((x0 + 8, y1 - 14), self._ellipsize(draw, label, micro_font, x1 - x0 - 16), fill=palette["dim"], font=micro_font)

    def _uncertainty_label(self, observation):
        value = observation.get("coordinate_uncertainty_m")
        try:
            meters = int(float(value))
        except (TypeError, ValueError):
            return ""
        if meters <= 0:
            return ""
        if meters >= 1000:
            return f"\u00b1{meters / 1000:.1f} km"
        return f"\u00b1{meters} m"

    @staticmethod
    def _load_env(device_config, key):
        try:
            if hasattr(device_config, "load_env_key"):
                return device_config.load_env_key(key)
        except Exception:
            pass
        return os.getenv(key, "")
    def _fallback_payload(self, location, cache_key):
        return {
            "schema": CACHE_SCHEMA_VERSION,
            "cache_key": cache_key,
            "source": "GBIF",
            "location": dict(location),
            "radius_km": DEFAULT_RADIUS_KM,
            "lookback_days": DEFAULT_LOOKBACK_DAYS,
            "refresh_hours": DEFAULT_REFRESH_HOURS,
            "observations": [],
            "category_counts": {},
            "source_state": "local",
        }

    def _write_context(self, payload, now):
        observations = payload.get("observations") or []
        hero = observations[0] if observations else {}
        try:
            write_context(
                PLUGIN_ID,
                {
                    "kind": "species_radar",
                    "source": "GBIF",
                    "summary": hero.get("display_name") or "No nearby species observation",
                    "category": hero.get("category_label"),
                    "scientific_name": hero.get("scientific_name"),
                    "location": payload.get("location_summary") or (payload.get("location") or {}).get("name"),
                    "source_state": payload.get("source_state"),
                    "category_counts": payload.get("category_counts") or {},
                    "location_counts": payload.get("location_counts") or {},
                    "theme_mode": payload.get("theme_mode"),
                },
                generated_at=now,
                ttl_seconds=30 * 60 * 60,
            )
        except Exception as exc:
            logger.warning("Could not write SpeciesRadar context: %s", exc)

    def _cache_key(self, settings, now, location):
        specs = self._location_specs(settings, location)
        parts = [
            CACHE_SCHEMA_VERSION,
            self._refresh_bucket(now, settings),
            str(self._refresh_hours(settings)),
        ]
        for spec in specs:
            spec_location = spec.get("location") or {}
            parts.extend([
                str(spec.get("id") or ""),
                f"{float(spec_location.get('latitude')):.4f}",
                f"{float(spec_location.get('longitude')):.4f}",
                str(spec.get("radius_km") or DEFAULT_RADIUS_KM),
                str(spec.get("lookback_days") or DEFAULT_LOOKBACK_DAYS),
                str(spec.get("limit") or DEFAULT_LIMIT),
            ])
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    def _refresh_bucket(self, now, settings):
        refresh_hours = self._refresh_hours(settings)
        current = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
        bucket_hour = (current.hour // refresh_hours) * refresh_hours
        return current.replace(hour=bucket_hour, minute=0, second=0, microsecond=0).isoformat()

    def _refresh_hours(self, settings):
        return self._int(settings.get("refreshHours") or settings.get("refresh_hours"), DEFAULT_REFRESH_HOURS, 1, 24)

    def _cache_dir(self):
        return self.cache_dir(env_var="INKYPI_SPECIES_RADAR_CACHE", leaf="cache", create=True, strip=True)

    def _cache_path(self):
        return self._cache_dir() / "daily.json"

    def _display_state_path(self):
        return self._cache_dir() / "display_rotation.json"

    def _vernacular_cache_path(self):
        return self._cache_dir() / "vernacular_names.json"

    def _read_cache(self):
        return self._read_json(self._cache_path(), {})

    def _write_cache(self, payload):
        self._write_json(self._cache_path(), payload)

    def _read_vernacular_cache(self):
        value = self._read_json(self._vernacular_cache_path(), {})
        return value if isinstance(value, dict) else {}

    def _read_json(self, path, default):
        try:
            path = Path(path)
            if not path.is_file():
                return default
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, type(default)) else default
        except Exception as exc:
            logger.warning("Could not read SpeciesRadar cache %s: %s", path, exc)
            return default

    def _write_json(self, path, payload):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            path.write_text(text, encoding="utf-8")
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    def _get_json(self, url, params=None):
        response = get_http_session().get(url, params=params, headers=REQUEST_HEADERS, timeout=(5, 20))
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(f"response from {url} was not JSON") from exc

    def _bbox_for_radius(self, latitude, longitude, radius_km):
        lat_delta = radius_km / 111.32
        lon_scale = max(0.1, math.cos(math.radians(latitude)))
        lon_delta = radius_km / (111.32 * lon_scale)
        return {
            "min_lat": max(-90.0, latitude - lat_delta),
            "max_lat": min(90.0, latitude + lat_delta),
            "min_lon": max(-180.0, longitude - lon_delta),
            "max_lon": min(180.0, longitude + lon_delta),
        }

    def _haversine_km(self, lat1, lon1, lat2, lon2):
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lon2 - lon1)
        a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        return EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    def _taxonomy_path(self, occurrence, category_label):
        parts = [category_label]
        for key in ("family", "genus"):
            value = self._clean_text(occurrence.get(key))
            if value and value not in parts:
                parts.append(value)
        if len(parts) == 1:
            for key in ("class", "kingdom"):
                value = self._clean_text(occurrence.get(key))
                if value and value not in parts:
                    parts.append(value)
                    break
        return " / ".join(parts)

    def _scientific_name(self, occurrence):
        for key in ("species", "acceptedScientificName", "scientificName"):
            value = self._clean_text(occurrence.get(key))
            if value:
                return value
        return ""

    def _location_label(self, occurrence):
        parts = []
        locality = self._clean_text(occurrence.get("locality") or occurrence.get("verbatimLocality"))
        if locality:
            parts.append(locality)
        gadm = occurrence.get("gadm") if isinstance(occurrence.get("gadm"), dict) else {}
        for key in ("level2", "level1"):
            value = gadm.get(key)
            name = self._clean_text(value.get("name")) if isinstance(value, dict) else ""
            if name and name not in parts:
                parts.append(name)
                break
        state = self._clean_text(occurrence.get("stateProvince"))
        if state and state not in parts:
            parts.append(state)
        country = self._clean_text(occurrence.get("country"))
        if not parts and country:
            parts.append(country)
        return ", ".join(parts[:2]) if parts else ""

    def _event_date_label(self, value):
        text = str(value or "").strip()
        if not text:
            return ""
        match = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
        if match:
            year, month, day = match.groups()
            return f"{year}-{month}-{day}"
        return self._clean_text(text)

    def _event_sort_value(self, value):
        text = str(value or "")
        match = re.match(r"(\d{4}-\d{2}-\d{2})(?:[T ]?([0-9:]+)?)?", text)
        if not match:
            return text
        date_part, time_part = match.groups()
        return f"{date_part}T{time_part or '00:00:00'}"

    def _license_footer(self, observation):
        creator = observation.get("photo_creator") or observation.get("rights_holder") or observation.get("recorded_by") or "unknown"
        license_text = self._short_license(observation.get("photo_license") or observation.get("license"))
        return f"Photo: {creator} / {license_text} / GBIF"

    def _short_license(self, value):
        text = str(value or "").strip()
        if not text:
            return "license unknown"
        match = re.search(r"licenses?/([^/]+/[^/]+)/?", text, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()
        return text.replace("http://", "").replace("https://", "")

    def _palette(self, settings, now=None, device_config=None):
        if self._theme_mode(settings, now, device_config) == "night":
            return {
                "paper": COMIC_NIGHT_PAPER,
                "panel": COMIC_NIGHT_PANEL,
                "ink": COMIC_NIGHT_INK,
                "dim": COMIC_NIGHT_DIM,
                "muted": COMIC_NIGHT_MUTED,
                "accent": COMIC_CYAN,
                "rule": COMIC_NIGHT_RULE,
                "night": True,
            }
        return {
            "paper": COMIC_PAPER,
            "panel": COMIC_PANEL,
            "ink": COMIC_INK,
            "dim": (55, 50, 39),
            "muted": COMIC_MUTED,
            "accent": COMIC_BLUE,
            "rule": COMIC_RULE,
            "night": False,
        }

    def _theme_mode(self, settings, now=None, device_config=None):
        settings = settings or {}
        mode = self._clean_text(settings.get("themeMode") or settings.get("theme_mode") or settings.get("theme") or "auto").casefold()
        if mode in {"night", "dark", "black"}:
            return "night"
        if mode in {"comic", "day", "light"}:
            return "comic"
        if not now or not (device_config or settings.get("timezone")):
            return "comic"
        return "night" if self._is_night(now, settings, device_config) else "comic"

    def _is_night(self, now, settings=None, device_config=None):
        local_now = self._local_datetime(now, settings or {}, device_config)
        start = self._int((settings or {}).get("nightStartHour"), DEFAULT_NIGHT_START_HOUR, 0, 23)
        end = self._int((settings or {}).get("nightEndHour"), DEFAULT_NIGHT_END_HOUR, 0, 23)
        hour = int(local_now.hour)
        if start == end:
            return False
        if start > end:
            return hour >= start or hour < end
        return start <= hour < end

    def _local_datetime(self, now, settings=None, device_config=None):
        current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        timezone_name = self._device_timezone(settings or {}, device_config)
        if ZoneInfo and timezone_name:
            try:
                return current.astimezone(ZoneInfo(timezone_name))
            except Exception:
                logger.debug("SpeciesRadar could not resolve timezone %s", timezone_name)
        try:
            return current.astimezone()
        except Exception:
            return current.astimezone(timezone.utc)

    def _device_timezone(self, settings=None, device_config=None):
        explicit = self._clean_text((settings or {}).get("timezone"))
        if explicit:
            return explicit
        if device_config:
            try:
                return self._clean_text(device_config.get_config("timezone"))
            except Exception:
                return ""
        return ""

    def _font(self, size, bold=False, cjk=False):
        weight = "bold" if bold else "normal"
        for path in self._preferred_font_paths(bold=bold):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue

        for family in self._preferred_font_families():
            try:
                font = get_font(family, size, weight)
                if font:
                    return font
            except Exception:
                continue

        for path in self._emergency_font_paths(bold=bold):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _preferred_font_paths(self, bold=False):
        if bold:
            return (
                str(SHARED_YAHEI_FONT_DIR / "msyhbd.ttc"),
                str(SHARED_YAHEI_FONT_DIR / "msyh.ttc"),
                str(STATIC_FONT_DIR / "msyhbd.ttc"),
                str(STATIC_FONT_DIR / "msyh.ttc"),
                r"C:\Windows\Fonts\msyhbd.ttc",
                r"C:\Windows\Fonts\msyhbd.ttf",
                r"C:\Windows\Fonts\msyh.ttc",
                r"C:\Windows\Fonts\msyh.ttf",
                "/usr/share/fonts/truetype/microsoft/YaHei/msyhbd.ttc",
                "/usr/share/fonts/truetype/microsoft/YaHei/msyh.ttc",
                "/usr/share/fonts/truetype/msttcorefonts/msyhbd.ttc",
                "/usr/share/fonts/truetype/msttcorefonts/msyh.ttc",
            )
        return (
            str(SHARED_YAHEI_FONT_DIR / "msyh.ttc"),
            str(SHARED_YAHEI_FONT_DIR / "msyhl.ttc"),
            str(STATIC_FONT_DIR / "msyh.ttc"),
            str(STATIC_FONT_DIR / "msyhl.ttc"),
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\msyh.ttf",
            r"C:\Windows\Fonts\msyhl.ttc",
            "/usr/share/fonts/truetype/microsoft/YaHei/msyh.ttc",
            "/usr/share/fonts/truetype/microsoft/YaHei/msyhl.ttc",
            "/usr/share/fonts/truetype/msttcorefonts/msyh.ttc",
            "/usr/share/fonts/truetype/msttcorefonts/msyhl.ttc",
        )

    def _preferred_font_families(self):
        return (
            MICROSOFT_YAHEI_FONT,
            "微软雅黑",
            "Microsoft YaHei UI",
            "Microsoft YaHei UI Light",
        )

    def _emergency_font_paths(self, bold=False):
        if bold:
            return (
                str(STATIC_FONT_DIR / "NotoSansSC-VF.ttf"),
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
                "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
                r"C:\Windows\Fonts\simhei.ttf",
            )
        return (
            str(STATIC_FONT_DIR / "NotoSansSC-VF.ttf"),
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            r"C:\Windows\Fonts\simhei.ttf",
        )

    def _font_for_text(self, text, fallback_font):
        return self._font(getattr(fallback_font, "size", 14) or 14, cjk=True)

    def _wrap(self, draw, text, font, max_width, max_lines=3):
        text = self._clean_text(text)
        if not text:
            return []
        if self._contains_cjk(text):
            return self._wrap_chars(draw, text, font, max_width, max_lines)
        return self._wrap_words(draw, text, font, max_width, max_lines)

    def _wrap_words(self, draw, text, font, max_width, max_lines):
        words = text.split()
        lines, current = [], ""
        consumed = 0
        for word in words:
            consumed += 1
            candidate = word if not current else f"{current} {word}"
            if self._text_width(draw, candidate, font) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
            if len(lines) >= max_lines:
                break
        if current and len(lines) < max_lines:
            lines.append(current)
        if consumed < len(words) and lines:
            lines[-1] = self._ellipsize(draw, lines[-1], font, max_width)
        return lines

    def _wrap_chars(self, draw, text, font, max_width, max_lines):
        lines, current = [], ""
        consumed = 0
        for char in text:
            consumed += 1
            candidate = current + char
            if self._text_width(draw, candidate, font) <= max_width or not current:
                current = candidate
            else:
                lines.append(current)
                current = char
            if len(lines) >= max_lines:
                break
        if current and len(lines) < max_lines:
            lines.append(current)
        if consumed < len(text) and lines:
            lines[-1] = self._ellipsize(draw, lines[-1], font, max_width)
        return lines

    def _fit_wrapped_font(self, draw, text, max_width, max_lines=2, min_size=11, max_size=26, bold=False, cjk=False, max_height=None, line_multiplier=1.02):
        text = self._clean_text(text)
        if not text:
            return self._font(min_size, bold=bold, cjk=cjk), []
        use_cjk = cjk or self._contains_cjk(text)
        for size in range(int(max_size), int(min_size) - 1, -1):
            font = self._font(size, bold=bold, cjk=use_cjk)
            lines = self._wrap_complete(draw, text, font, max_width)
            if self._wrapped_block_fits(draw, lines, font, max_width, max_lines, max_height, line_multiplier):
                return font, lines
        for size in range(int(min_size) - 1, 6, -1):
            font = self._font(size, bold=bold, cjk=use_cjk)
            lines = self._wrap_complete(draw, text, font, max_width)
            if self._wrapped_block_fits(draw, lines, font, max_width, max_lines, max_height, line_multiplier):
                return font, lines
        font = self._font(max(7, int(min_size)), bold=bold, cjk=use_cjk)
        lines = self._wrap_complete(draw, text, font, max_width, break_long=True)
        return font, lines[:max_lines]

    def _wrapped_block_fits(self, draw, lines, font, max_width, max_lines, max_height=None, line_multiplier=1.02):
        if not lines or len(lines) > max_lines:
            return False
        if any(self._text_width(draw, line, font) > max_width for line in lines):
            return False
        if max_height is None:
            return True
        return self._wrapped_block_height(draw, lines, font, line_multiplier) <= max_height

    def _wrapped_block_height(self, draw, lines, font, line_multiplier=1.02):
        if not lines:
            return 0
        line_h = self._line_height(draw, lines[0], font, line_multiplier)
        return line_h * len(lines)

    def _wrap_complete(self, draw, text, font, max_width, break_long=False):
        text = self._clean_text(text)
        if not text:
            return []
        units = list(text) if self._contains_cjk(text) else text.split()
        if not units:
            return []
        lines = []
        current = ""
        for unit in units:
            candidate = current + unit if self._contains_cjk(text) else (unit if not current else f"{current} {unit}")
            if self._text_width(draw, candidate, font) <= max_width or not current:
                if break_long and not current and self._text_width(draw, candidate, font) > max_width:
                    broken = self._break_long_unit(draw, unit, font, max_width)
                    lines.extend(broken[:-1])
                    current = broken[-1] if broken else ""
                else:
                    current = candidate
            else:
                lines.append(current)
                current = unit
        if current:
            lines.append(current)
        return lines

    def _break_long_unit(self, draw, unit, font, max_width):
        parts = []
        current = ""
        for char in str(unit):
            candidate = current + char
            if self._text_width(draw, candidate, font) <= max_width or not current:
                current = candidate
            else:
                parts.append(current)
                current = char
        if current:
            parts.append(current)
        return parts
    def _ellipsize(self, draw, text, font, max_width):
        suffix = "..."
        text = str(text or "")
        if self._text_width(draw, text, font) <= max_width:
            return text
        while text and self._text_width(draw, text + suffix, font) > max_width:
            text = text[:-1].rstrip()
        return f"{text}{suffix}" if text else suffix

    def _line_height(self, draw, text, font, multiplier=1.12):
        return max(1, int(self._text_height(draw, text or "Ag", font) * multiplier))

    def _text_width(self, draw, text, font):
        return text_width(draw, str(text), font)

    def _text_height(self, draw, text, font):
        bbox = draw.textbbox((0, 0), str(text), font=font)
        return bbox[3] - bbox[1]

    def _to_simplified_chinese(self, value):
        text = self._clean_text(value)
        if not text or not self._contains_cjk(text):
            return text
        converter = self._simplified_chinese_converter()
        if converter:
            try:
                converted = self._clean_text(converter(text))
                if converted:
                    return converted
            except Exception as exc:
                logger.debug("Chinese simplifier failed; using built-in fallback: %s", exc)
        return text.translate(TRADITIONAL_TO_SIMPLIFIED)

    def _simplified_chinese_converter(self):
        if hasattr(self, "_simplified_chinese_converter_func"):
            return self._simplified_chinese_converter_func
        converter = None
        try:
            from opencc import OpenCC  # type: ignore

            converter = OpenCC("t2s").convert
        except Exception:
            try:
                from zhconv import convert as zh_convert  # type: ignore

                converter = lambda text: zh_convert(text, "zh-cn")
            except Exception:
                try:
                    from hanziconv import HanziConv  # type: ignore

                    converter = HanziConv.toSimplified
                except Exception:
                    converter = None
        self._simplified_chinese_converter_func = converter
        return converter

    def _contains_cjk(self, text):
        return any("\u3400" <= char <= "\u9fff" for char in str(text or ""))

    def _clean_text(self, value):
        value = html.unescape(str(value or ""))
        value = re.sub(r"<[^>]+>", " ", value)
        value = value.replace("\u201c", '"').replace("\u201d", '"')
        value = value.replace("\u2018", "'").replace("\u2019", "'")
        value = value.replace("\u2014", "-").replace("\u2013", "-").replace("\u2026", "...")
        return re.sub(r"\s+", " ", value).strip()

    def _enabled(self, value, default=False):
        return coerce_bool(value, default=default, truthy=("1", "true", "yes", "on"))

    def _int(self, value, default, minimum, maximum):
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return max(minimum, min(maximum, number))

    def _int_or_none(self, value):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None
