"""Fetch the official logos the pages print where they name their data sources.

    python tools/build_source_logos.py

- Caltrans: the logo its own site header serves (dot.ca.gov), for the traffic cameras
  (bay_traffic_cams) and the District 4 lane closures (bay_commute).
- Ticketmaster: its wordmark from Wikimedia Commons (public domain), for Local Events.
- USGS: its green logo from Commons (public domain), cut to the mark and the letters (the
  tagline under them is too small to read on the panel), for The National Map's credit.
- NOAA: its round logo from Commons (public domain), for the tide predictions.
- NWS, CAL FIRE and NASA (for EONET): their logos from Commons (public domain), for 湾区要紧事.
  USGS goes there too, for its earthquakes.
- The cover pages (Daily Art, Comic Covers, Magazines, through base_plugin/cover_gallery): The
  Met, the Art Institute of Chicago, Europeana, the Grand Comics Database, the Internet Archive,
  Wikimedia Commons, the Library of Congress and Gallica from Commons; Harvard Art Museums' white
  wordmark from its own site, kept as a black mask the page tints. Comic Vine's logo is the inline
  SVG its site header draws; the site answers scripts with a bot check, so that one was captured
  in a browser and rasterized once (base_plugin/assets/source_logos/comicvine.png). Daily Art's
  keyless collections: the Cleveland Museum of Art and the V&A from Commons (public domain), and
  SMK's wordmark, the inline SVG symbol its own site header draws (smk.dk).
- 资金雷达 (money_radar), whose header names the page's sources: the ECB (its reference rates,
  served by Frankfurter), BLS, the SEC seal and Yahoo Finance from Commons (public domain);
  CoinGecko's SVG and Finnhub's mark from their own sites. FRED's logo is the SVG its home page
  shows; the site answers scripts with a bot check, so it was captured in a browser and rasterized
  once (money_radar/assets/logos/fred.png).
- Tesla's wordmark from Commons (public domain), for Vehicle Status' footer.
- 全球票房双城榜: the official China chart's mark (its site's icon, the white ground cleared), The
  Numbers' and Maoyan's logos from their own sites (Maoyan's white lettering, made for its dark
  header, set dark for the page to ink), and TMDB's attribution logo from its logos page.
- BacktotheDate: Chinese Wikipedia's globe with its 维基百科 wordmark, both from Commons, and
  chineseposters.net's masthead (the site has no logo: its name in capitals, Montserrat SemiBold
  in white on its red header), redrawn with the site's own web font.
- 全网热搜 (cn_hot_search): Baidu's logo from its own home page and Weibo's from its site
  navigation; Toutiao's, Zhihu's and Douyin's from Commons (public domain).
- 世界赔率榜 (world_odds): Kalshi's and Polymarket's logos from Commons (public domain).
- NASA Pics × Space Weather: JPL's logo from Commons (public domain), for the near-Earth objects.
- AI 论文日报 (ai_papers): Hugging Face's logo, with and without its name, and arXiv's, from Commons.
- 资金雷达's Fear & Greed gauge: CNN's logo from Commons (public domain).
- 湾区交通观测's BART departures: BART's logo and its five lines' lettered bullets from Commons (public domain).
- 环球此刻's fireballs: JPL's logo (as for NASA Pics) beside NASA's.
- Orbital Signal's space-news line: each news site's own mark (NASA's, ESA's and Ars Technica's from
  Commons; SpaceNews', NASASpaceflight's, Spaceflight Now's and European Spaceflight's from their own
  sites) and the Spaceflight News API's logo from its site.
- The sports page's NHL panel: the league's shield from Commons (public domain).
- Tech Pulse: OpenRouter's logo and icon from Commons (public domain), for the newest models, and
  their makers' marks: Commons where it has one, else the maker's own site icon.
- The cover pages' publishers and magazines (base_plugin/assets/publisher_logos, PUBLISHER_LOGOS):
  each one's logo as Wikidata names it on Commons (Marvel, DC, Kodansha, Rolling Stone, TIME, ...),
  else the one its own site's header serves (BOOM!, Oni, Yen Press, DC Thomson, ...), white lettering
  made for dark headers set dark for the page to ink, and a white ground cleared from the edge.

SVGs are drawn by a headless Chrome or Edge (tools/build_planet_now_logos.py). Each is cropped
to its visible pixels and scaled to at most 64 px tall into the plugin's assets;
logos.source.json beside each records where it came from.

    python tools/build_source_logos.py [key ...]    # only the named logos
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import sys
from pathlib import Path

import requests
from PIL import Image, ImageChops, ImageDraw

from build_planet_now_logos import _rasterize

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS = REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "plugins"
HEADERS = {"User-Agent": "Mozilla/5.0 InkyPi asset build (https://github.com/Feeengyuuu/EpaperSystem)"}
COMMONS = "https://upload.wikimedia.org/wikipedia/commons/thumb/{path}/500px-{name}.png"
PUBLIC_DOMAIN = "Public domain (Wikimedia Commons)"
COVERS = PLUGINS / "base_plugin" / "assets" / "source_logos"
MONEY = PLUGINS / "money_radar" / "assets" / "logos"
BOX_OFFICE = PLUGINS / "box_office_top_movies" / "assets" / "logos"
POSTERS = PLUGINS / "backtothedate" / "assets" / "logos"
HOT_SEARCH = PLUGINS / "cn_hot_search" / "assets" / "logos"
ODDS = PLUGINS / "world_odds" / "assets" / "logos"
TECH_LOGOS = PLUGINS / "tech_pulse" / "assets" / "logos"
ORGANISERS = PLUGINS / "lol_info" / "assets" / "cs2_organisers"
PAPERS = PLUGINS / "ai_papers" / "assets" / "logos"
SPACE_NEWS = PLUGINS / "orbital_signal" / "assets" / "news"
COMMONS_FILE = "https://commons.wikimedia.org/wiki/File:{name}"
CALTRANS = ("https://dot.ca.gov/-/media/dot-media/images/logos/caltrans/caltranslogo.png", "https://dot.ca.gov/",
            "Caltrans' official logo from its own site header, shown to name the source")
# key -> ((source, page, licence), [plugin asset folders], top share of the art kept, or a
# processing step: "knockout", "white_lettering" or "white_ground")
LOGOS = {
    "caltrans": (CALTRANS, [PLUGINS / "bay_traffic_cams" / "assets" / "logos", PLUGINS / "bay_commute" / "assets" / "logos"], 1.0),
    "ticketmaster": ((COMMONS.format(path="7/7d/TicketMaster_wordmark.svg", name="TicketMaster_wordmark.svg"),
                      "https://commons.wikimedia.org/wiki/File:TicketMaster_wordmark.svg", PUBLIC_DOMAIN),
                     [PLUGINS / "ticketmaster_events" / "assets"], 1.0),
    "usgs": ((COMMONS.format(path="1/1c/USGS_logo_green.svg", name="USGS_logo_green.svg"),
              "https://commons.wikimedia.org/wiki/File:USGS_logo_green.svg", PUBLIC_DOMAIN),
             [PLUGINS / "bay_commute" / "assets" / "logos", PLUGINS / "bay_alerts" / "assets" / "logos"], 0.66),
    "noaa": ((COMMONS.format(path="7/79/NOAA_logo.svg", name="NOAA_logo.svg"),
              "https://commons.wikimedia.org/wiki/File:NOAA_logo.svg", PUBLIC_DOMAIN),
             [PLUGINS / "bay_commute" / "assets" / "logos"], 1.0),
    "nws": ((COMMONS.format(path="f/ff/US-NationalWeatherService-Logo.svg", name="US-NationalWeatherService-Logo.svg"),
             "https://commons.wikimedia.org/wiki/File:US-NationalWeatherService-Logo.svg", PUBLIC_DOMAIN),
            [PLUGINS / "bay_alerts" / "assets" / "logos"], 1.0),
    "calfire": ((COMMONS.format(path="4/4b/Logo_of_CAL_FIRE.svg", name="Logo_of_CAL_FIRE.svg"),
                 "https://commons.wikimedia.org/wiki/File:Logo_of_CAL_FIRE.svg", PUBLIC_DOMAIN),
                [PLUGINS / "bay_alerts" / "assets" / "logos"], 1.0),
    "nasa": ((COMMONS.format(path="e/e5/NASA_logo.svg", name="NASA_logo.svg"),
              "https://commons.wikimedia.org/wiki/File:NASA_logo.svg", PUBLIC_DOMAIN),
             [PLUGINS / "bay_alerts" / "assets" / "logos"], 1.0),
    "met": (("https://upload.wikimedia.org/wikipedia/commons/thumb/7/73/The_Metropolitan_Museum_of_Art_Logo.svg/"
             "500px-The_Metropolitan_Museum_of_Art_Logo.svg.png",
             "https://commons.wikimedia.org/wiki/File:The_Metropolitan_Museum_of_Art_Logo.svg", PUBLIC_DOMAIN), [COVERS], 1.0),
    "aic": ((COMMONS.format(path="3/32/Art_Institute_of_Chicago_logo.svg", name="Art_Institute_of_Chicago_logo.svg"),
             "https://commons.wikimedia.org/wiki/File:Art_Institute_of_Chicago_logo.svg", PUBLIC_DOMAIN), [COVERS], 1.0),
    "europeana": ((COMMONS.format(path="4/49/Europeana_logo_black.svg", name="Europeana_logo_black.svg"),
                   "https://commons.wikimedia.org/wiki/File:Europeana_logo_black.svg", "CC BY-SA 4.0 (Wikimedia Commons)"),
                  [COVERS], 1.0),
    "harvard": (("https://harvardartmuseums.org/assets/images/HAM-Wordmark-Knockout.png", "https://harvardartmuseums.org/",
                 "Harvard Art Museums' wordmark from its own site, shown to name the source"), [COVERS], "knockout"),
    "gcd": (("https://upload.wikimedia.org/wikipedia/commons/8/8d/Grand_Comics_Database_logo.png",
             "https://commons.wikimedia.org/wiki/File:Grand_Comics_Database_logo.png", PUBLIC_DOMAIN), [COVERS], 1.0),
    "internet_archive": ((COMMONS.format(path="7/71/Internet_Archive_logo_wide_black.svg", name="Internet_Archive_logo_wide_black.svg"),
                          "https://commons.wikimedia.org/wiki/File:Internet_Archive_logo_wide_black.svg", PUBLIC_DOMAIN),
                         [COVERS], 1.0),
    "wikimedia_commons": ((COMMONS.format(path="4/4a/Commons-logo.svg", name="Commons-logo.svg"),
                           "https://commons.wikimedia.org/wiki/File:Commons-logo.svg", "CC BY-SA 3.0 (Wikimedia Commons)"),
                          [COVERS], 1.0),
    "loc": ((COMMONS.format(path="b/be/Logo_of_the_United_States_Library_of_Congress.svg",
                            name="Logo_of_the_United_States_Library_of_Congress.svg"),
             "https://commons.wikimedia.org/wiki/File:Logo_of_the_United_States_Library_of_Congress.svg", PUBLIC_DOMAIN),
            [COVERS], 1.0),
    "gallica": (("https://upload.wikimedia.org/wikipedia/commons/thumb/e/e8/Logo_Gallica_2025.png/500px-Logo_Gallica_2025.png",
                 "https://commons.wikimedia.org/wiki/File:Logo_Gallica_2025.png", PUBLIC_DOMAIN), [COVERS], 1.0),
    "cleveland": ((COMMONS.format(path="7/74/Logo_Cleveland_Museum_of_Art.svg", name="Logo_Cleveland_Museum_of_Art.svg"),
                   COMMONS_FILE.format(name="Logo_Cleveland_Museum_of_Art.svg"), PUBLIC_DOMAIN), [COVERS], 1.0),
    "vam": ((COMMONS.format(path="d/d7/Victoria_and_Albert_Museum_Logo.svg", name="Victoria_and_Albert_Museum_Logo.svg"),
             COMMONS_FILE.format(name="Victoria_and_Albert_Museum_Logo.svg"), PUBLIC_DOMAIN), [COVERS], 1.0),
    "ecb": ((COMMONS.format(path="c/cb/Logo_European_Central_Bank.svg", name="Logo_European_Central_Bank.svg"),
             COMMONS_FILE.format(name="Logo_European_Central_Bank.svg"), PUBLIC_DOMAIN), [MONEY], 1.0),
    "bls": ((COMMONS.format(path="7/76/Bureau_of_Labor_Statistics_logo.svg", name="Bureau_of_Labor_Statistics_logo.svg"),
             COMMONS_FILE.format(name="Bureau_of_Labor_Statistics_logo.svg"), PUBLIC_DOMAIN), [MONEY], 1.0),
    "sec": ((COMMONS.format(path="1/1c/Seal_of_the_United_States_Securities_and_Exchange_Commission.svg",
                            name="Seal_of_the_United_States_Securities_and_Exchange_Commission.svg"),
             COMMONS_FILE.format(name="Seal_of_the_United_States_Securities_and_Exchange_Commission.svg"), PUBLIC_DOMAIN),
            [MONEY], 1.0),
    "yahoo_finance": (("https://upload.wikimedia.org/wikipedia/commons/thumb/8/8f/Yahoo%21_Finance_logo_2021.png/"
                       "500px-Yahoo%21_Finance_logo_2021.png",
                       COMMONS_FILE.format(name="Yahoo!_Finance_logo_2021.png"), PUBLIC_DOMAIN), [MONEY], 1.0),
    "coingecko": (("https://static.coingecko.com/s/coingecko-logo-5683263fd3ea8a4f152dd5f7299acfc5f84ee73955428acff22913b8e59e6c54.svg",
                   "https://www.coingecko.com/", "CoinGecko's logo from its own site, shown to name the source"), [MONEY], 1.0),
    "finnhub": (("https://finnhub.io/static/img/webp/finnhub-logo.webp", "https://finnhub.io/",
                 "Finnhub's mark from its own site, shown to name the source"), [MONEY], 1.0),
    "tesla": ((COMMONS.format(path="6/62/Tesla_Motors_Logo.svg", name="Tesla_Motors_Logo.svg"),
               COMMONS_FILE.format(name="Tesla_Motors_Logo.svg"), PUBLIC_DOMAIN),
              [PLUGINS / "vehicle_status" / "assets" / "logos"], 1.0),
    "zgdypw": (("https://zgdypf.zgdypw.cn/favicon.ico", "https://zgdypf.zgdypw.cn/box",
                "The official China chart's (中国电影票房) site icon, shown to name the source"), [BOX_OFFICE], "white_ground"),
    "the_numbers": (("https://www.the-numbers.com/site-images/numbers-logo-r.svg", "https://www.the-numbers.com/",
                     "The Numbers' logo from its own site header, shown to name the source"), [BOX_OFFICE], 1.0),
    "maoyan": (("https://s0.pipi.cn/festatic/moviepro/image/logo-text-color_8ee29f55.png", "https://piaofang.maoyan.com/dashboard",
                "Maoyan Pro's logo from its own site header, shown to name the source"), [BOX_OFFICE], "white_lettering"),
    "tmdb": (("https://www.themoviedb.org/assets/v4/logos/v2/blue_short-8e7b30f73a4020692ccca9c88bafe5dcb6f8a62a4c6bc55cd9ba82bb2cd95f6c.svg",
              "https://www.themoviedb.org/about/logos-attribution", "TMDB's attribution logo, as its terms ask"),
             [BOX_OFFICE], 1.0),
    "baidu": (("https://www.baidu.com/img/flexible/logo/pc/result@2.png", "https://www.baidu.com/",
               "Baidu's logo from its own home page, shown to name the source"), [HOT_SEARCH], 1.0),
    "weibo": (("https://img.t.sinajs.cn/t6/style/images/global_nav/WB_logo.png", "https://weibo.com/",
               "Weibo's logo from its own site navigation, shown to name the source"), [HOT_SEARCH], 1.0),
    "toutiao": ((COMMONS.format(path="7/7b/Toutiao_logo.svg", name="Toutiao_logo.svg"),
                 COMMONS_FILE.format(name="Toutiao_logo.svg"), PUBLIC_DOMAIN), [HOT_SEARCH], 1.0),
    "zhihu": ((COMMONS.format(path="a/ad/Zhihu_logo.svg", name="Zhihu_logo.svg"),
               COMMONS_FILE.format(name="Zhihu_logo.svg"), PUBLIC_DOMAIN), [HOT_SEARCH], 1.0),
    "douyin": ((COMMONS.format(path="1/1a/Douyin_wordmark.svg", name="Douyin_wordmark.svg"),
                COMMONS_FILE.format(name="Douyin_wordmark.svg"), PUBLIC_DOMAIN), [HOT_SEARCH], 1.0),
    "kalshi": ((COMMONS.format(path="b/b8/Kalshi-logo-2026.svg", name="Kalshi-logo-2026.svg"),
                COMMONS_FILE.format(name="Kalshi-logo-2026.svg"), PUBLIC_DOMAIN), [ODDS], 1.0),
    "polymarket": ((COMMONS.format(path="e/ec/Polymarket.svg", name="Polymarket.svg"),
                    COMMONS_FILE.format(name="Polymarket.svg"), PUBLIC_DOMAIN), [ODDS], 1.0),
    # The marks of the sources Kalshi's US-economy markets settle on, one beside each board row.
    "fed": ((COMMONS.format(path="1/1a/Seal_of_the_United_States_Federal_Reserve_System.svg",
                            name="Seal_of_the_United_States_Federal_Reserve_System.svg"),
             COMMONS_FILE.format(name="Seal_of_the_United_States_Federal_Reserve_System.svg"), PUBLIC_DOMAIN), [ODDS], 1.0),
    "nber": (("https://upload.wikimedia.org/wikipedia/commons/2/2f/NBER_Logo.gif",
              COMMONS_FILE.format(name="NBER_Logo.gif"), PUBLIC_DOMAIN), [ODDS], "white_ground"),
    "bls": ((COMMONS.format(path="7/76/Bureau_of_Labor_Statistics_logo.svg", name="Bureau_of_Labor_Statistics_logo.svg"),
             COMMONS_FILE.format(name="Bureau_of_Labor_Statistics_logo.svg"), PUBLIC_DOMAIN), [ODDS], 1.0),
    "bea": ((COMMONS.format(path="e/ef/US-BureauOfEconomicAnalysis-Logo.svg", name="US-BureauOfEconomicAnalysis-Logo.svg"),
             COMMONS_FILE.format(name="US-BureauOfEconomicAnalysis-Logo.svg"), PUBLIC_DOMAIN), [ODDS], 1.0),
    "aaa": ((COMMONS.format(path="d/d4/American-Automobile-Association-Logo.svg",
                            name="American-Automobile-Association-Logo.svg"),
             COMMONS_FILE.format(name="American-Automobile-Association-Logo.svg"), PUBLIC_DOMAIN), [ODDS], 1.0),
    "spdji": ((COMMONS.format(path="c/c4/S%26P_Dow_Jones_Indices_logo.svg", name="S%26P_Dow_Jones_Indices_logo.svg"),
               COMMONS_FILE.format(name="S&P_Dow_Jones_Indices_logo.svg"), PUBLIC_DOMAIN), [ODDS], 1.0),
    "bitcoin": ((COMMONS.format(path="4/46/Bitcoin.svg", name="Bitcoin.svg"),
                 COMMONS_FILE.format(name="Bitcoin.svg"), PUBLIC_DOMAIN), [ODDS], 1.0),
    "nhl": ((COMMONS.format(path="e/e5/National_Hockey_League_shield.svg", name="National_Hockey_League_shield.svg"),
             COMMONS_FILE.format(name="National_Hockey_League_shield.svg"), PUBLIC_DOMAIN),
            [PLUGINS / "sports_dashboard" / "assets" / "logos"], 1.0),
    "jpl": ((COMMONS.format(path="c/c6/Jet_Propulsion_Laboratory_logo.svg", name="Jet_Propulsion_Laboratory_logo.svg"),
             COMMONS_FILE.format(name="Jet_Propulsion_Laboratory_logo.svg"), PUBLIC_DOMAIN),
            [PLUGINS / "apod" / "assets" / "logos", PLUGINS / "planet_now" / "assets" / "logos"], 0.56),
    # The makers of the models on Tech Pulse's OpenRouter strip, one mark beside each card.
    "provider-openai": (("https://status.openai.com/favicon.ico", "https://status.openai.com/",
                         "OpenAI's site icon, shown to name the model's maker"), [TECH_LOGOS], 1.0),
    "provider-anthropic": (("https://www.anthropic.com/images/icons/apple-touch-icon.png", "https://www.anthropic.com/",
                            "Anthropic's site icon, shown to name the model's maker"), [TECH_LOGOS], 1.0),
    "provider-moonshotai": (("https://statics.moonshot.cn/kimi-chat/favicon.ico", "https://www.kimi.com/",
                             "Moonshot AI's Kimi site icon, shown to name the model's maker"), [TECH_LOGOS], 1.0),
    "provider-google": ((COMMONS.format(path="3/3c/Google_Favicon_2025.svg", name="Google_Favicon_2025.svg"),
                         COMMONS_FILE.format(name="Google_Favicon_2025.svg"), PUBLIC_DOMAIN), [TECH_LOGOS], 1.0),
    "provider-meta-llama": ((COMMONS.format(path="0/05/Meta_Platforms_Inc._logo_%28cropped%29.svg",
                                            name="Meta_Platforms_Inc._logo_%28cropped%29.svg"),
                             COMMONS_FILE.format(name="Meta_Platforms_Inc._logo_(cropped).svg"), PUBLIC_DOMAIN),
                            [TECH_LOGOS], 1.0),
    "provider-mistralai": ((COMMONS.format(path="e/e6/Mistral_AI_logo_%282025%E2%80%93%29.svg",
                                           name="Mistral_AI_logo_%282025%E2%80%93%29.svg"),
                            COMMONS_FILE.format(name="Mistral_AI_logo_(2025–).svg"), PUBLIC_DOMAIN), [TECH_LOGOS], 1.0),
    "provider-deepseek": ((COMMONS.format(path="9/95/DeepSeek-icon.svg", name="DeepSeek-icon.svg"),
                           COMMONS_FILE.format(name="DeepSeek-icon.svg"), "MIT (Wikimedia Commons)"), [TECH_LOGOS], 1.0),
    "provider-qwen": ((COMMONS.format(path="6/69/Qwen_logo.svg", name="Qwen_logo.svg"),
                       COMMONS_FILE.format(name="Qwen_logo.svg"), "CC0 (Wikimedia Commons)"), [TECH_LOGOS], 1.0),
    "provider-x-ai": ((COMMONS.format(path="9/93/XAI_Logo.svg", name="XAI_Logo.svg"),
                       COMMONS_FILE.format(name="XAI_Logo.svg"), "CC BY-SA 4.0 (Wikimedia Commons)"), [TECH_LOGOS], 1.0),
    "provider-minimax": ((COMMONS.format(path="0/08/Minimax_Logo.svg", name="Minimax_Logo.svg"),
                          COMMONS_FILE.format(name="Minimax_Logo.svg"), PUBLIC_DOMAIN), [TECH_LOGOS], 1.0),
    "provider-z-ai": (("https://upload.wikimedia.org/wikipedia/commons/6/62/Zhipu_AI.png",
                       COMMONS_FILE.format(name="Zhipu_AI.png"), PUBLIC_DOMAIN), [TECH_LOGOS], "white_ground"),
    "openrouter_icon": ((COMMONS.format(path="f/f8/OpenRouter_icon.svg", name="OpenRouter_icon.svg"),
                         COMMONS_FILE.format(name="OpenRouter_icon.svg"), PUBLIC_DOMAIN),
                        [PLUGINS / "tech_pulse" / "assets" / "logos"], 1.0),
    "openrouter": ((COMMONS.format(path="c/ca/OpenRouter_Logo.svg", name="OpenRouter_Logo.svg"),
                    COMMONS_FILE.format(name="OpenRouter_Logo.svg"), PUBLIC_DOMAIN),
                   [PLUGINS / "tech_pulse" / "assets" / "logos"], 1.0),
    "huggingface": ((COMMONS.format(path="d/d6/Hf-logo-with-title.svg", name="Hf-logo-with-title.svg"),
                     COMMONS_FILE.format(name="Hf-logo-with-title.svg"), PUBLIC_DOMAIN), [PAPERS], 1.0),
    "huggingface_mark": ((COMMONS.format(path="3/3f/Hf-logo-without-title.svg", name="Hf-logo-without-title.svg"),
                          COMMONS_FILE.format(name="Hf-logo-without-title.svg"), PUBLIC_DOMAIN), [PAPERS], 1.0),
    "arxiv": ((COMMONS.format(path="b/bc/ArXiv_logo_2022.svg", name="ArXiv_logo_2022.svg"),
               COMMONS_FILE.format(name="ArXiv_logo_2022.svg"), PUBLIC_DOMAIN), [PAPERS], 1.0),
    "cnn": ((COMMONS.format(path="b/b1/CNN.svg", name="CNN.svg"), COMMONS_FILE.format(name="CNN.svg"), PUBLIC_DOMAIN),
            [MONEY], 1.0),
    "bart": ((COMMONS.format(path="2/26/Bart-logo.svg", name="Bart-logo.svg"), COMMONS_FILE.format(name="Bart-logo.svg"),
              PUBLIC_DOMAIN), [PLUGINS / "bay_commute" / "assets" / "logos"], 1.0),
    "bart-orange": ((COMMONS.format(path="7/71/Orange_Line_%28BART%29.svg", name="Orange_Line_%28BART%29.svg"),
                      COMMONS_FILE.format(name="Orange_Line_(BART).svg"), PUBLIC_DOMAIN),
                     [PLUGINS / "bay_commute" / "assets" / "logos"], 1.0),
    "bart-red": ((COMMONS.format(path="c/c1/Red_Line_%28BART%29.svg", name="Red_Line_%28BART%29.svg"),
                      COMMONS_FILE.format(name="Red_Line_(BART).svg"), PUBLIC_DOMAIN),
                     [PLUGINS / "bay_commute" / "assets" / "logos"], 1.0),
    "bart-yellow": ((COMMONS.format(path="4/4e/Yellow_Line_%28BART%29.svg", name="Yellow_Line_%28BART%29.svg"),
                      COMMONS_FILE.format(name="Yellow_Line_(BART).svg"), PUBLIC_DOMAIN),
                     [PLUGINS / "bay_commute" / "assets" / "logos"], 1.0),
    "bart-blue": ((COMMONS.format(path="8/8f/Blue_Line_%28BART%29.svg", name="Blue_Line_%28BART%29.svg"),
                      COMMONS_FILE.format(name="Blue_Line_(BART).svg"), PUBLIC_DOMAIN),
                     [PLUGINS / "bay_commute" / "assets" / "logos"], 1.0),
    "bart-green": ((COMMONS.format(path="e/ed/Green_Line_%28BART%29.svg", name="Green_Line_%28BART%29.svg"),
                      COMMONS_FILE.format(name="Green_Line_(BART).svg"), PUBLIC_DOMAIN),
                     [PLUGINS / "bay_commute" / "assets" / "logos"], 1.0),
    "news-nasa": ((COMMONS.format(path="e/e5/NASA_logo.svg", name="NASA_logo.svg"),
                   COMMONS_FILE.format(name="NASA_logo.svg"), PUBLIC_DOMAIN), [SPACE_NEWS], 1.0),
    "news-esa": ((COMMONS.format(path="b/bd/European_Space_Agency_logo.svg", name="European_Space_Agency_logo.svg"),
                  COMMONS_FILE.format(name="European_Space_Agency_logo.svg"), PUBLIC_DOMAIN), [SPACE_NEWS], 1.0),
    "news-arstechnica": ((COMMONS.format(path="5/51/Ars_Technica_logo_%282016%29.svg", name="Ars_Technica_logo_%282016%29.svg"),
                          COMMONS_FILE.format(name="Ars_Technica_logo_(2016).svg"), PUBLIC_DOMAIN), [SPACE_NEWS], 1.0),
    "news-spacenews": (("https://i0.wp.com/spacenews.com/wp-content/uploads/2024/05/cropped-SN_forza_star.png?fit=192%2C192&ssl=1",
                        "https://spacenews.com/", "SpaceNews' site icon, shown to name the story's source"), [SPACE_NEWS], 1.0),
    "news-nasaspaceflight": (("https://www.nasaspaceflight.com/wp-content/uploads/2023/03/NSF-Logo-Blue-300.svg",
                              "https://www.nasaspaceflight.com/", "NASASpaceflight's logo from its own site header, shown to "
                              "name the story's source"), [SPACE_NEWS], 1.0),
    "news-spaceflight_now": (("https://spaceflightnow.com/new/assets/images/favicon-180.png", "https://spaceflightnow.com/",
                              "Spaceflight Now's site icon, shown to name the story's source"), [SPACE_NEWS], 1.0),
    "news-european_spaceflight": (("https://europeanspaceflight.com/wp-content/uploads/2021/12/"
                                   "cropped-European-Spaceflight-logo-192x192.png", "https://europeanspaceflight.com/",
                                   "European Spaceflight's site icon, shown to name the story's source"), [SPACE_NEWS], 1.0),
    "snapi": (("https://www.spaceflightnewsapi.net/img/SNAPI_logo.png", "https://www.spaceflightnewsapi.net/",
               "The Spaceflight News API's logo from its own site, shown to name the source"), [SPACE_NEWS], 1.0),
}
PUBLISHERS = PLUGINS / "base_plugin" / "assets" / "publisher_logos"
# The cover pages' publishers and magazines (base_plugin/publisher_logos.py knows their names): key ->
# its logo's Commons file (Wikidata's "logo image" for it, drawn 600 px wide), an https file its own
# site's header serves, or "inline:<site>|<text>", the inline SVG in that site's page holding <text>;
# alone or with a processing step. A white ground reaching the logo's edge is cleared either way.
PUBLISHER_LOGOS = {
    "abril": "Logo editora abril.jpg", "ahoy": "AhoyComicsLogo.png", "akita_shoten": "Akita Shoten Logo.svg",
    "allure": "Allure logo.svg", "amazing_stories": "Amazing Stories interior title.png",
    "aperture": "Aperture magazine logo.svg", "archie": "Archie Comics logo 1987.svg",
    "architectural_digest": "Architectural Digest logo.svg", "art_in_america": "Logo Art in America.svg",
    "artforum": "Artforum logo.svg", "atlas": "Atlas Comics logo.svg", "awa": "AWA Studios logo, Nov 2018.jpg",
    "billboard": "Billboard logo.svg", "bonelli": "Sergio Bonelli Editore Logo.svg",
    "boys_life": "Boys' Life Header.png", "businessweek": "Bloomberg Businessweek logo.svg",
    "car_and_driver": "Car and Driver logo.svg", "carlsen": "Carlsen-verlag-logo-2012.svg",
    "casterman": "Logo Casterman.gif", "charlton": "Charlton Comics logo.png", "closer_weekly": "Closer logo.svg",
    "cosmopolitan": "Comopolitan Magazine Logo.svg", "dargaud": "Dargaud Logo.png",
    "dark_horse": "Dark Horse Comics wordmark.svg", "dc": "DC Comics 2024.svg", "delcourt": "Delcourt logo.png",
    "dell": "Dellcomicslogo.jpeg", "der_spiegel": "Logo-der spiegel.svg",
    "dupuis": "DUPUIS-EDITION ET AUDIOVISUEL-ROUGE Print Transparence.png",
    "dynamite": "Dynamite Entertainment Logo.png", "ec": "Eclogo.jpeg", "eclipse": "Eclipse Comics logo.jpg",
    "economist": "The Economist Logo.svg", "egmont": "Egmont Group logo.svg", "ehapa": "Egmont-Ehapa-Media-Logo.png",
    "elle": "Elle logo.svg", "empire": "Empire (magazine) logo.svg",
    "entertainment_weekly": "Entertainment Weekly logo.svg", "esquire": "Esquire logo (2017).svg",
    "fantagraphics": "Fantagraphics logo.png", "fawcett": "FawcettIcon2.png", "forbes": "Forbes logo.svg",
    "fortune": "Fortune magazine logo 2016.svg", "futabasha": "Futabasha logo.png", "glamour": "Glamour logo.svg",
    "golf_digest": "Golf Digest logo.svg", "gq": "GQ Logo.svg", "hakusensha": "Hakusensha logo.png",
    "harpers": "Harper's Magazine logo.svg", "harpers_bazaar": "Harper's Bazaar Logo.svg",
    "hollywood_reporter": "The Hollywood Reporter logo.svg", "humanoids": ("Humanoids logo.png", "white_lettering"),
    "i_d": "I-D Magazine Logo.svg", "idw": "IDW Publishing logo.svg", "image": "Image Comics logo.svg",
    "instyle": "InStyle.svg", "interview": "Interview mag logo.svg", "jet": "Jet Magazine.png",
    "kadokawa": "Kadokawa logo.svg", "kodansha": "Kōdansha logo.svg", "life": "LIFE magazine logo.svg",
    "lombard": "Le Lombard logo (2010).svg", "mad": "Madlogo.png", "marie_claire": "Marie Claire Magazine logo.svg",
    "marvel": "Marvel Comics 2024 logo.svg", "maxim": "Maxim Magazine Logo.svg", "mens_health": "Men's Health.svg",
    "milestone": "Milestone Media logo.png", "mojo": "Mojo (magazine) Logo.svg",
    "motor_trend": "Motor Trend logo.svg", "national_geographic": "Natgeologo.svg",
    "nature": "Nature journal logo.svg", "new_republic": "The New Republic wordmark horizontal.svg",
    "new_yorker": "The New Yorker.svg", "newsweek": "Newsweek Logo.svg",
    "nme": "NME - New Musical Express - Logo.svg", "outside": "Outside logo 2020.png",
    "panini": "Panini group logo.png", "paris_match": "Paris Match 1981 logo.svg",
    "penthouse": "Penthouse text logo.svg", "people": "People Magazine logo.svg",
    "planeta": "Planeta DeAgostini logo.svg", "playboy": "Playboy logo.svg",
    "popular_mechanics": "Popular Mechanics logo.svg", "popular_science": "Popular Science.svg",
    "quality": "Quality Comic Group logo.svg", "readers_digest": "Reader's Digest logo.svg",
    "road_track": "Road and Track Logo.svg", "rolling_stone": "Rolling Stone 2022.svg",
    "saturday_evening_post": "The Saturday Evening Post logo.svg", "scholastic": "Logo Scholastic.svg",
    "science": "Science AAAS logo.svg", "scientific_american": "Scientific American logo.svg",
    "shogakukan": "Shogakukan logo.svg", "shueisha": "Shueisha Logo.svg", "spin": "Spin-logo.svg",
    "sporting_news": "SPORTINGNEWS - Master Logo .jpg",
    "sports_illustrated": "Sports Illustrated - official red text logo.svg", "square_enix": "Square Enix logo.svg",
    "stern": "Stern Logo 2020.svg", "teen_vogue": "Teen Vogue.svg", "the_atlantic": "The Atlantic Logo 11.2019.svg",
    "the_face": ("The Face logo.svg", "white_lettering"), "the_nation": "The Nation logo.svg",
    "the_spectator": "The Spectator logo.svg", "time": "Time Magazine logo.svg", "timely": "TimelyShield2.png",
    "tokyopop": "TOKYOPOP logo.png", "top_shelf": "Topshelf logo.png", "topps": "Topps comics logo.png",
    "town_country": "Town&Country wordmark.svg", "tv_guide": "TV Guide logo 2016.png",
    "us_weekly": "Us Weekly logo.png", "valiant": "Valiant Comics logo (April 2012).svg",
    "vanity_fair": "Vanity Fair logo (2025, black).svg", "variety": "Variety logo.svg",
    "vertigo": "DC Vertigo 2024.svg", "viz": "Viz Media 2017 logo.svg", "vogue": "VOGUE LOGO.svg",
    "w": "W magazine logo.jpg", "weird_tales": "Weird Tales Logo.png", "wildstorm": "WildStorm logo.png",
    "wired": "Wired logo.svg", "womens_health": "Womenshealthlogo.svg",
    "boom": ("https://www.boom-studios.com/wp-content/uploads/2023/02/boom-logo-header.webp", "white_lettering"),
    "oni": ("https://images.squarespace-cdn.com/content/v1/651302f86c2c335a35c606d6/9686d5fc-d2c4-4a58-b0ea-"
            "43a22784db28/Standard+Coin+W300dpi.png?format=1500w", "white_lettering"),
    "yen_press": "https://yenpress.com/images/header/logo.svg",
    "seven_seas": "https://sevenseasentertainment.com/wp-content/themes/gomanga2025/images/SevenSeas-logo-sm.png",
    "dc_thomson": "https://www.dcthomson.co.uk/hubfs/raw_assets/public/dc-thomson/images/logo.svg",
    "glenat": "https://media.hachette.fr/17/2025-05/logo-glenat.png",
    "aftershock": ("https://aftershockcomics.com/cdn/shop/files/logo-white2_340x.png?v=1629327231", "white_lettering"),
    "vault": ("https://vaultcomics.com/cdn/shop/t/15/assets/logo.svg?v=91623706365843156311709921395",
              "white_lettering"),
    "zenescope": ("https://zenescope.com/cdn/shop/files/Zenescope_logo_final_2019_white_copy.png?v=1614370979"
                  "&width=360", "white_lettering"),
    "first_second": "https://firstsecondbooks.com/wp-content/uploads/2019/04/FirstSecond_logo_100x117.png",
    "taste_of_home": "https://www.tasteofhome.com/wp-content/uploads/2021/09/toh-interior-logo.svg",
    "hustler": ("https://hustlermagazine.com/file/hustlermag_logo_sm.png", "white_lettering"),
    "first_for_women": "https://firstforwomen.com/wp-content/uploads/2024/11/logo.png",
    "smithsonian": ("https://www.smithsonianmag.com/static/smithsonianmag/img/"
                    "smithmag-logo-horizontal-white.98dddd9f3f4d.svg", "white_lettering"),
    "essence": "https://media.essence.com/essence_pubp/uploads/2026/04/02/logo.svg",
    "ebony": "https://media.ebony.com/sytwmfsyue/uploads/2026/04/01/ebony-logo-1.svg",
    "field_stream": ("https://fieldandstream.com/wp-content/uploads/2025/10/"
                     "FS_Logo_Estb1871_Cream_Horiz-e1760450600626.png", "white_lettering"),
    "better_homes": 'inline:https://www.bhg.com/|viewBox="0 0 444 32"',
    "womans_world": "inline:https://www.womansworld.com/|aria-label=\"Woman's World\"",
    "robb_report": "inline:https://robbreport.com/|<title>Robb Report</title>",
}
# The Pro Tracker's CS2 page names an event no logo is found for by its organiser's logo
# (lol_info/pro_render.CS2_ORGANISERS): Commons' as Wikidata names it, else the organiser's site's.
# The page is dark in both modes, so black marks are drawn white.
ORGANISER_LOGOS = {
    "iem": "IEM v2019 logo.svg", "esl_pro_league": "ESL Pro League logo.svg",
    "esl": ("ESL Horizontal Logo 2019.svg", "white_mark"), "perfect_world": ("Perfect World Logo.svg", "white_mark"),
    "ewc": ("Esports World Cup logo.svg", "white_mark"), "dreamhack": ("DreamHack logo.png", "white_mark"),
    "blast": ('inline:https://blast.tv/|viewBox="0 0 26 30"', "white_mark"),
    "starladder": ("inline:https://starladder.com/|header-sl-logo", "white_mark"),
    "pgl": "https://www.pglesports.com/images/logo.webp",
    "cct": "https://cctour.gg/wp-content/uploads/2024/06/CCT-Logo-Favicon.svg",
}
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
SITE_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/130.0 Safari/537.36"}
WIKIPEDIA_GLOBE = ("https://upload.wikimedia.org/wikipedia/commons/thumb/3/36/Wikipedia-logo-v2-zh-hans.svg/"
                   "500px-Wikipedia-logo-v2-zh-hans.svg.png", COMMONS_FILE.format(name="Wikipedia-logo-v2-zh-hans.svg"),
                   "CC BY-SA 3.0 (Wikimedia Commons)")
WIKIPEDIA_WORDMARK = ("https://upload.wikimedia.org/wikipedia/commons/thumb/c/c2/Wikipedia-wordmark-zh-hans.svg/"
                      "500px-Wikipedia-wordmark-zh-hans.svg.png", COMMONS_FILE.format(name="Wikipedia-wordmark-zh-hans.svg"),
                      PUBLIC_DOMAIN)
SMK_SITE = "https://www.smk.dk/"
CHINESEPOSTERS_FONT = "https://chineseposters.net/themes/cpnetv2/css/fonts/montserrat-semibold-webfont.woff2"
CHINESEPOSTERS_RED = (203, 36, 0)


def _knockout(image):
    """White artwork for a dark header as a black mask: the page tints it with its ink."""
    mask = Image.new("RGBA", image.size, (0, 0, 0, 255))
    mask.putalpha(image.getchannel("A"))
    return mask


def _white_lettering(image):
    """White lettering made for a dark header set near-black, for the page to ink."""
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            red, green, blue, alpha = pixels[x, y]
            if alpha and min(red, green, blue) > 200:
                pixels[x, y] = (26, 26, 26, alpha)
    return image


def _white_ground(image):
    """The opaque white square behind a site icon cleared."""
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            red, green, blue, alpha = pixels[x, y]
            if min(red, green, blue) > 235:
                pixels[x, y] = (red, green, blue, 0)
    return image


def _white_mark(image):
    """A black mark (one drawn in the page's text colour) set white, for a dark page."""
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            red, green, blue, alpha = pixels[x, y]
            if alpha and max(red, green, blue) < 110 and max(red, green, blue) - min(red, green, blue) < 40:
                pixels[x, y] = (255, 255, 255, alpha)
    return image


PROCESSING = {"knockout": (_knockout, "white artwork kept as a black mask the page tints"),
              "white_lettering": (_white_lettering, "white lettering (for a dark header) set near-black"),
              "white_ground": (_white_ground, "the white ground cleared"),
              "white_mark": (_white_mark, "its black drawn white for a dark page")}


def _visible(image):
    return image.crop(image.getchannel("A").point(lambda value: 255 if value > 16 else 0).getbbox())


def _art(url, content):
    if url.endswith(".svg"):
        return _rasterize(content)
    with Image.open(io.BytesIO(content)) as image:
        if image.format == "ICO":
            image.size = max(image.info.get("sizes") or [image.size])
        return image.convert("RGBA")


def _fit(art):
    if art.height > 64:
        art = art.resize((round(art.width * 64 / art.height), 64), Image.Resampling.LANCZOS)
    return art


def _fetch(url):
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.content


def _write(records, folder, key, art, entry):
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{key}.png"
    art.save(target, optimize=True)
    records.setdefault(folder, {})[key] = {**entry, "size": list(art.size),
                                           "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    print(key, art.size)


def _wikipedia_zh(records):
    """The globe from Chinese Wikipedia's logo, its 维基百科 wordmark beside it."""
    (globe_url, globe_page, globe_licence), (mark_url, mark_page, mark_licence) = WIKIPEDIA_GLOBE, WIKIPEDIA_WORDMARK
    globe_bytes, mark_bytes = _fetch(globe_url), _fetch(mark_url)
    logo = _visible(_art(globe_url, globe_bytes))
    # The logo's globe stands above its two lines of lettering, the first clear row between them.
    alpha = logo.getchannel("A").point(lambda value: 255 if value > 16 else 0)
    gap = next(y for y in range(logo.height // 2, logo.height)
               if not alpha.crop((0, y, logo.width, y + 1)).getbbox())
    globe = _visible(logo.crop((0, 0, logo.width, gap)))
    mark = _visible(_art(mark_url, mark_bytes))
    mark = mark.resize((round(mark.width * globe.height * 0.62 / mark.height), round(globe.height * 0.62)),
                       Image.Resampling.LANCZOS)
    gap = round(globe.height * 0.16)
    art = Image.new("RGBA", (globe.width + gap + mark.width, globe.height), (0, 0, 0, 0))
    art.alpha_composite(globe, (0, 0))
    art.alpha_composite(mark, (globe.width + gap, (globe.height - mark.height) // 2))
    _write(records, POSTERS, "wikipedia_zh", _fit(art), {
        "source": [globe_url, mark_url], "page": [globe_page, mark_page],
        "licence": f"globe: {globe_licence}; wordmark: {mark_licence}",
        "source_sha256": [hashlib.sha256(globe_bytes).hexdigest(), hashlib.sha256(mark_bytes).hexdigest()],
        "processing": "the logo's globe (its lettering below cut off) with the 维基百科 wordmark beside it"})


def _chineseposters(records):
    """The site's masthead: its name in capitals, white Montserrat SemiBold on its header red."""
    font = _fetch(CHINESEPOSTERS_FONT)
    face = base64.b64encode(font).decode("ascii")
    svg = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1200 120'><style>@font-face{font-family:m;"
           f"src:url(data:font/woff2;base64,{face}) format('woff2')}}</style>"
           "<text x='10' y='92' font-family='m' font-size='96' fill='#fff'>CHINESEPOSTERS.NET</text></svg>")
    letters = _visible(_rasterize(svg.encode("utf-8")))
    pad_x, pad_y = round(letters.height * 0.55), round(letters.height * 0.45)
    art = Image.new("RGBA", (letters.width + 2 * pad_x, letters.height + 2 * pad_y), (*CHINESEPOSTERS_RED, 255))
    art.alpha_composite(letters, (pad_x, pad_y))
    _write(records, POSTERS, "chineseposters", _fit(art), {
        "source": CHINESEPOSTERS_FONT, "page": "https://chineseposters.net/",
        "licence": "The site's name as its header sets it (Montserrat, SIL Open Font License)",
        "source_sha256": hashlib.sha256(font).hexdigest(),
        "processing": "the site has no logo; its header's name redrawn: capitals in its web font, white on #cb2400"})


def _smk(records):
    """SMK's wordmark: the svg-logo symbol inlined in its site's pages, drawn in black."""
    page = _fetch(SMK_SITE)
    symbol = re.search(rb'<symbol id="svg-logo"([^>]*)>(.*?)</symbol>', page, re.S)
    view_box = re.search(rb'viewBox="([^"]+)"', symbol.group(1)).group(1)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="' + view_box + b'"><g fill="#000">' + symbol.group(2) + b"</g></svg>"
    _write(records, COVERS, "smk", _fit(_visible(_rasterize(svg))), {
        "source": SMK_SITE + " (the page's inline SVG symbol svg-logo)", "page": SMK_SITE,
        "licence": "SMK's wordmark from its own site, shown to name the source",
        "source_sha256": hashlib.sha256(svg).hexdigest(), "processing": "the symbol drawn in black, cropped, 64 px tall"})


COMPOSED = {"wikipedia_zh": _wikipedia_zh, "chineseposters": _chineseposters, "smk": _smk}


def _edge_ground(image):
    """The near-white ground reaching the logo's edge cleared (a JPEG's or a boxed file's), the
    white inside its mark kept. A logo already on transparency (its corners clear) has none: its
    white lettering may touch the edge."""
    alpha = image.getchannel("A")
    width, height = image.size
    if min(alpha.getpixel(corner) for corner in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1))) < 200:
        return image
    light = image.convert("L").point(lambda value: 255 if value > 235 else 0)
    near = ImageChops.multiply(light, alpha.point(lambda value: 255 if value > 200 else 0))
    edge = [(x, y) for x in range(width) for y in (0, height - 1)] + [(x, y) for y in range(height) for x in (0, width - 1)]
    for point in edge:
        if near.getpixel(point) == 255:
            ImageDraw.floodfill(near, point, 128)
    ground = near.point(lambda value: 255 if value == 128 else 0)
    if ground.getbbox():
        image.putalpha(ImageChops.subtract(alpha, ground))
    return image


def _publisher_source(key, spec):
    """(url, page, licence, content) for one PUBLISHER_LOGOS entry."""
    if spec.startswith("inline:"):
        site, text = spec[len("inline:"):].split("|", 1)
        page = _fetch_site(site)
        svg = next(match.group(0) for match in re.finditer(rb"<svg\b.*?</svg>", page, re.S)
                   if text.encode("utf-8") in match.group(0))
        if b"xmlns" not in svg[:200]:
            svg = svg.replace(b"<svg", b'<svg xmlns="http://www.w3.org/2000/svg"', 1)
        return site + " (the page's inline SVG logo)", site, f"{key}'s logo from its own site's header", svg
    if spec.startswith("https://"):
        site = "https://" + spec.split("/")[2] + "/"
        return spec, site, f"{key}'s logo from its own site, shown to name the publisher", _fetch_site(spec)
    response = requests.get(COMMONS_API, params={"action": "query", "titles": f"File:{spec}", "prop": "imageinfo",
                                                 "iiprop": "url|extmetadata", "iiurlwidth": 600, "format": "json"},
                            headers=HEADERS, timeout=30)
    info = next(iter(response.json()["query"]["pages"].values()))["imageinfo"][0]
    licence = (info.get("extmetadata", {}).get("LicenseShortName") or {}).get("value") or "see the file page"
    url = info.get("thumburl") or info["url"]
    return url, info["descriptionurl"], f"{licence} (Wikimedia Commons)", _fetch(url)


def _fetch_site(url):
    response = requests.get(url, headers=SITE_HEADERS, timeout=30)
    response.raise_for_status()
    return response.content


def _publisher(records, key, spec, folder=PUBLISHERS):
    """One publisher's or magazine's logo (or a CS2 organiser's) into ``folder``."""
    spec, step = spec if isinstance(spec, tuple) else (spec, None)
    url, page, licence, content = _publisher_source(key, spec)
    vector = spec.startswith("inline:") or url.split("?")[0].endswith(".svg")
    art = _visible(_edge_ground(_rasterize(content) if vector else _art(url, content)))
    steps = ["a white ground reaching the edge cleared"]
    if step:
        process, note = PROCESSING[step]
        art = _visible(process(art))
        steps.append(note)
    _write(records, folder, key, _fit(art), {"source": url, "page": page, "licence": licence,
                                             "source_sha256": hashlib.sha256(content).hexdigest(),
                                             "processing": "; ".join(steps)})


def main(keys=()):
    records = {}
    for key, ((url, page, licence), folders, keep) in LOGOS.items():
        if keys and key not in keys:
            continue
        content = _fetch(url)
        art = _visible(_art(url, content))
        step = None
        if isinstance(keep, str):
            process, step = PROCESSING[keep]
            art, keep = _visible(process(art)), 1.0
        if keep < 1:
            art = _visible(art.crop((0, 0, art.width, round(art.height * keep))))
            step = f"the top {keep:.0%} kept (the mark and letters, not the tagline)"
        entry = {"source": url, "page": page, "licence": licence, "source_sha256": hashlib.sha256(content).hexdigest()}
        for folder in folders:
            _write(records, folder, key, _fit(art), {**entry, **({"processing": step} if step else {})})
    for key, build in COMPOSED.items():
        if not keys or key in keys:
            build(records)
    for key, spec in PUBLISHER_LOGOS.items():
        if not keys or key in keys:
            _publisher(records, key, spec)
    for key, spec in ORGANISER_LOGOS.items():
        if not keys or key in keys:
            _publisher(records, key, spec, ORGANISERS)
    for folder, logos in records.items():
        path = folder / ("logos.source.json" if folder.name == "logos" else f"{folder.name}.source.json")
        existing = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        existing.setdefault("logos", {}).update(logos)
        path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main(set(sys.argv[1:]))
