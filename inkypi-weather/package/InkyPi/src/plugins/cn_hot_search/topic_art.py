"""Stand-in pictures for the entries no list gives one.

A generated illustration of the story's topic (assets/topics, GPT Image 2.5; topics.source.json),
chosen by the words of its title, else by Weibo's category for the entry; a story that names no
topic gets the street scene of 社会.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from utils.safe_image import safe_open_image

TOPIC_DIR = Path(__file__).resolve().parent / "assets" / "topics"
DEFAULT_TOPIC = "society"
CATEGORY_TOPICS = {
    "时政": "politics", "政务": "politics", "社会": "society", "民生": "society", "财经": "finance",
    "股市": "finance", "科技": "tech", "数码": "tech", "互联网": "tech", "游戏": "tech", "娱乐": "entertainment",
    "明星": "entertainment", "综艺": "entertainment", "电影": "entertainment", "电视剧": "entertainment",
    "音乐": "entertainment", "体育": "sports", "国际": "world", "军事": "military", "天气": "weather",
    "灾害": "weather", "健康": "health", "医疗": "health", "美食": "life", "生活": "life", "旅游": "life",
    "汽车": "transport", "交通": "transport",
}
# Checked in order, so a narrower topic wins over a broader one ("降温" before "跌").
TITLE_TOPICS = (
    ("military", ("军演", "军方", "军事", "解放军", "美军", "海军", "空军", "陆军", "军舰", "导弹", "战机", "航母",
                  "国防", "演习")),
    ("weather", ("台风", "暴雨", "暴雪", "地震", "降温", "寒潮", "洪水", "高温", "天气", "气象", "火灾", "大风", "冰雹")),
    ("health", ("医院", "医生", "疫情", "疫苗", "病毒", "患者", "健康", "医保", "药品", "癌", "心理")),
    ("sports", ("足球", "篮球", "女足", "男足", "国足", "世界杯", "奥运", "冠军", "夺冠", "比赛", "联赛", "晋级", "NBA",
                "CBA", "LPL", "电竞", "决赛", "球员", "教练", "全运")),
    ("entertainment", ("电影", "演唱会", "明星", "综艺", "电视剧", "歌手", "导演", "票房", "官宣", "恋情", "离婚",
                       "结婚", "主演", "粉丝")),
    ("tech", ("AI", "芯片", "手机", "华为", "苹果", "小米", "科技", "机器人", "卫星", "火箭", "航天", "大模型", "英伟达",
              "英特尔", "互联网")),
    ("finance", ("A股", "股市", "股价", "基金", "银行", "利率", "降息", "经济", "GDP", "楼市", "房价", "黄金", "汇率",
                 "存款", "贷款", "暴跌", "大涨", "涨价", "茅台")),
    ("transport", ("高铁", "地铁", "航班", "机场", "汽车", "交通", "高速", "铁路", "新能源车")),
    ("world", ("美国", "日本", "俄罗斯", "乌克兰", "以色列", "伊朗", "特朗普", "欧洲", "联合国", "外交", "韩国", "朝鲜",
               "英国", "法国", "德国")),
    ("politics", ("中共中央", "国务院", "全会", "人大", "政协", "发布会", "纪委", "反腐", "党", "政策")),
    ("life", ("美食", "奶茶", "旅游", "假期", "宠物", "猫", "狗", "做饭", "喝酒", "火锅")),
)


def topic(title, category=""):
    """The topic an entry's stand-in shows: its title's words name it best, Weibo's broad category
    ("社会" for most) next."""
    text = str(title or "")
    named = next((key for key, words in TITLE_TOPICS if any(word in text for word in words)), None)
    return named or next((key for word, key in CATEGORY_TOPICS.items() if word in str(category or "")), DEFAULT_TOPIC)


@lru_cache(maxsize=16)
def _art(key):
    try:
        with safe_open_image(TOPIC_DIR / f"{key}.jpg") as source:
            return source.convert("RGB")
    except (OSError, ValueError):
        return None


def picture(title, category=""):
    """The stand-in illustration for an entry, or None when the file is missing."""
    return _art(topic(title, category))
