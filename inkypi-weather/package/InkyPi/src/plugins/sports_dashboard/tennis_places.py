"""Short Chinese display labels for known tennis venues, separate from official logos.

These are editorial labels for the panel, not claims about official Chinese branding.
Keep an unfamiliar provider name intact instead of assigning it a guessed identity.
"""

VENUE_LABELS = {
    "Melbourne Park": "墨尔本公园",
    "Stade Roland-Garros": "罗兰加洛斯球场",
    "All England Club": "全英俱乐部",
    "Billie Jean King Tennis Center": "比莉简金网球中心",
    "Queensland Tennis Centre": "昆士兰网球中心",
    "Victoria Park": "维园网球场",
    "Victoria Park Tennis Stadium": "维园网球场",
    "Victoria Park Centre Court": "维园网球场",
    "Victoria Park Tennis Centre Court": "维园网球场",
    "Victoria Park Tennis Stadium Centre Court": "维园网球场",
    "Memorial Drive": "纪念大道球场",
    "ASB Tennis Arena": "ASB网球场",
    "Sud de France Arena": "法国南部竞技场",
    "Ford Center at The Star": "The Star福特中心",
    "Rotterdam Ahoy": "鹿特丹Ahoy馆",
    "Buenos Aires Lawn Tennis": "布宜诺斯艾利斯球会",
    "Khalifa Tennis Complex": "哈里发网球中心",
    "Jockey Club Brasileiro": "巴西赛马会",
    "Delray Beach Tennis Center": "德尔雷比奇网球中心",
    "Dubai Tennis Stadium": "迪拜网球场",
    "Arena GNP Seguros": "GNP Seguros球场",
    "San Carlos de Apoquindo": "阿波金多体育中心",
    "Indian Wells Tennis Garden": "印第安维尔斯网球园",
    "Hard Rock Stadium": "硬石体育场",
    "River Oaks Country Club": "River Oaks球会",
    "Royal Tennis Club": "皇家网球会",
    "Arenele BNR": "BNR网球场",
    "Monte-Carlo Country Club": "蒙特卡洛乡村俱乐部",
    "RCT Barcelona": "巴塞罗那皇家球会",
    "MTTC Iphitos": "Iphitos网球会",
    "Caja Mágica": "魔术盒球场",
    "Foro Italico": "Foro Italico球场",
    "Am Rothenbaum": "罗滕鲍姆球场",
    "Tennis Club de Genève": "日内瓦网球会",
    "Autotron Rosmalen": "Autotron球场",
    "Queen's Club": "女王俱乐部",
    "OWL Arena": "OWL竞技场",
    "Mallorca Country Club": "马略卡乡村俱乐部",
    "Devonshire Park": "德文郡公园",
    "Roy Emerson Arena": "罗伊爱默生球场",
    "Båstad Tennis Stadium": "巴斯塔德网球场",
    "Sportpark Kitzbühel": "基茨比厄尔体育公园",
    "Clube de Ténis do Estoril": "埃斯托里尔网球会",
    "Rock Creek Park": "岩溪公园",
    "Cabo Sports Complex": "卡波体育中心",
    "Lindner Family Tennis Center": "林德纳网球中心",
    "Wake Forest Tennis Complex": "维克森林网球中心",
    "Sichuan Tennis Center": "四川网球中心",
    "National Tennis Center": "国家网球中心",
    "Ariake Coliseum": "有明网球馆",
    "Qizhong Arena": "旗忠网球中心",
    "Almaty Arena": "阿拉木图体育馆",
    "Wiener Stadthalle": "维也纳城市体育馆",
    "St. Jakobshalle": "圣雅各布体育馆",
    "Kungliga tennishallen": "瑞典皇家网球馆",
    "Domain Tennis Centre": "Domain网球中心",
    "Zayed Sports City": "扎耶德体育城",
    "Credit One Stadium": "Credit One体育场",
    "Design Center Linz": "林茨设计中心",
    "Porsche Arena": "保时捷竞技场",
    "Kindarena": "Kindarena体育馆",
    "Tennis Club de Strasbourg": "斯特拉斯堡网球会",
    "Nottingham Tennis Centre": "诺丁汉网球中心",
    "LTTC Rot-Weiß Berlin": "柏林红白网球会",
    "Club Sonoma": "Sonoma俱乐部",
    "Panamerican Tennis Center": "泛美网球中心",
    "Seoul Olympic Park": "首尔奥林匹克公园",
    "Optics Valley Tennis Center": "光谷网球中心",
    "Utsubo Tennis Center": "Utsubo网球中心",
    "SDAT Tennis Stadium": "SDAT网球场",
    "Sobeys Stadium": "Sobeys体育场",
    "IGA Stadium": "IGA体育场",
}


def _venue_key(venue):
    return "".join(character for character in venue.casefold() if character.isalnum())


_NORMALIZED_LABELS = {_venue_key(name): label for name, label in VENUE_LABELS.items()}


def venue_label(venue):
    """Return a known short display label, or preserve the provider's unfamiliar text."""
    name = "" if venue is None else str(venue)
    return _NORMALIZED_LABELS.get(_venue_key(name), name)
