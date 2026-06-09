from __future__ import annotations

import re
import time
import json
import http.client
import ssl
from dataclasses import dataclass, field
from typing import List
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import feedparser
from bs4 import BeautifulSoup

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_ssl_ctx = ssl.create_default_context()


@dataclass
class NewsItem:
    title: str
    source: str
    url: str
    weight: float = 1.0
    content: str = ""
    timestamp: str = ""


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _strip_html(s: str) -> str:
    return _clean(BeautifulSoup(s, "html.parser").get_text())


def _get(url: str, timeout: int = 6, extra_headers: dict | None = None, params: dict | None = None) -> requests.Response:
    h = {**_HEADERS, **(extra_headers or {})}
    s = requests.Session()
    s.trust_env = False
    return s.get(url, params=params, headers=h, timeout=timeout, allow_redirects=True)


def _http_get_direct(host: str, path: str, timeout: int = 6, https: bool = True) -> str:
    """直接HTTP请求，绕过requests和系统代理。"""
    if https:
        conn = http.client.HTTPSConnection(host, timeout=timeout, context=_ssl_ctx)
    else:
        conn = http.client.HTTPConnection(host, timeout=timeout)
    conn.request("GET", path, headers=_HEADERS)
    r = conn.getresponse()
    data = r.read().decode("utf-8", errors="ignore")
    conn.close()
    return data


def _decode(r: requests.Response) -> str:
    try:
        enc = r.apparent_encoding or r.encoding
        if enc:
            return r.content.decode(enc, errors="ignore")
    except Exception:
        pass
    return r.content.decode("utf-8", errors="ignore")


def _rss(url: str, source: str, limit: int = 80, weight: float = 1.0) -> List[NewsItem]:
    """通用RSS解析，增强容错。"""
    items: List[NewsItem] = []
    try:
        r = _get(url, timeout=8)
        feed = feedparser.parse(r.content)
        for entry in feed.entries[:min(limit, 60)]:
            title = _clean(entry.get("title", ""))
            if not title or len(title) < 4:
                continue
            summary = _clean(entry.get("summary", "") or entry.get("description", ""))
            summary = _strip_html(summary) if "<" in summary else summary
            link = entry.get("link", "")
            pub = entry.get("published", "") or entry.get("updated", "")
            items.append(NewsItem(title=title, source=source, url=link, weight=weight,
                                  content=summary[:2000], timestamp=pub))
    except Exception:
        pass
    return items


def _rss_multi(urls: List[str], source: str, limit: int = 80, weight: float = 1.0) -> List[NewsItem]:
    """从多个RSS URL聚合同一来源的新闻。"""
    items: List[NewsItem] = []
    seen = set()
    for url in urls:
        batch = _rss(url, source, limit, weight)
        for item in batch:
            key = item.title[:40]
            if key not in seen:
                seen.add(key)
                items.append(item)
        if len(items) >= limit:
            break
    return items[:limit]


# ================================================================
# 第1类：实时API源（国内，每条都有实时时间戳）
# ================================================================

def fetch_sina_7x24(limit: int = 80) -> List[NewsItem]:
    """新浪7x24直播 - 支持多页，每页30条"""
    items: List[NewsItem] = []
    pages_needed = (limit // 30) + 2
    for page in range(1, pages_needed + 1):
        try:
            url = f"https://zhibo.sina.com.cn/api/zhibo/feed?page={page}&page_size=30&zhibo_id=152&tag_id=0&type=0"
            r = _get(url, extra_headers={"Referer": "https://zhibo.sina.com.cn/"})
            r.raise_for_status()
            data = r.json()
            feed_list = data.get("result", {}).get("data", {}).get("feed", {}).get("list", [])
            if not feed_list:
                break
            for it in feed_list:
                rich = it.get("rich_text", "") or it.get("text_content", "")
                text = _clean(re.sub(r"<[^>]+>", "", rich))
                if not text or len(text) < 6:
                    continue
                ct = it.get("create_time", "")
                items.append(NewsItem(title=text[:120], source="新浪7x24", url="", weight=1.4, content=text, timestamp=ct))
                if len(items) >= limit:
                    return items
        except Exception:
            break
    return items


def fetch_cls_realtime(limit: int = 80) -> List[NewsItem]:
    """财联社 - 电报API + 网页"""
    items: List[NewsItem] = []
    try:
        r = _get("https://www.cls.cn/nodeapi/updateTelegraphList", params={
            "app": "CailianpressWeb", "os": "web", "sv": "7.7.5", "rn": str(min(limit, 100)),
        }, extra_headers={"Referer": "https://www.cls.cn/"})
        if r.status_code == 200:
            data = r.json()
            for item in data.get("data", {}).get("roll_data", []):
                title = _clean(item.get("title", "") or item.get("brief", ""))
                content = _clean(item.get("content", "") or item.get("brief", ""))
                if not title or len(title) < 4:
                    continue
                ct = str(item.get("ctime", ""))
                items.append(NewsItem(title=title, source="财联社", url="", weight=1.6, content=content, timestamp=ct))
                if len(items) >= limit:
                    return items
    except Exception:
        pass
    # 网页兜底
    if len(items) < 10:
        try:
            r = _get("https://www.cls.cn/")
            r.raise_for_status()
            soup = BeautifulSoup(_decode(r), "html.parser")
            for a in soup.select("a[href*=detail]"):
                title = _clean(a.get_text())
                if not title or len(title) < 6 or len(title) > 80:
                    continue
                href = a.get("href", "")
                full = href if href.startswith("http") else "https://www.cls.cn" + href
                items.append(NewsItem(title=title, source="财联社", url=full, weight=1.6))
                if len(items) >= limit:
                    break
        except Exception:
            pass
    return items


def fetch_eastmoney_realtime(limit: int = 80) -> List[NewsItem]:
    """东方财富证券要闻 - API多页"""
    items: List[NewsItem] = []
    for page in range(1, 6):
        try:
            r = _get("https://np-listapi.eastmoney.com/comm/web/getNewsByColumns", params={
                "client": "web", "biz": "web_news_col", "column": "350",
                "order": "1", "needInteractData": "0", "page_index": str(page),
                "page_size": "20",
            })
            if r.status_code == 200:
                data = r.json()
                news_list = data.get("data", {}).get("list", [])
                if not news_list:
                    break
                for item in news_list:
                    title = _clean(item.get("title", ""))
                    if not title or len(title) < 4:
                        continue
                    digest = _clean(item.get("digest", ""))
                    url = item.get("url", "")
                    ct = item.get("showtime", "")
                    items.append(NewsItem(title=title, source="东方财富", url=url, weight=1.3, content=digest, timestamp=ct))
                    if len(items) >= limit:
                        return items
            else:
                break
        except Exception:
            break
    return items


def fetch_10jqka_realtime(limit: int = 80) -> List[NewsItem]:
    """同花顺 - 财经快讯"""
    items: List[NewsItem] = []
    for page in range(1, 5):
        try:
            r = _get("https://news.10jqka.com.cn/tapp/news/push/stock/", params={
                "page": str(page), "tag": "", "track": "website", "pagesize": "25",
            }, extra_headers={"Referer": "https://news.10jqka.com.cn/"})
            if r.status_code == 200:
                data = r.json()
                for item in data.get("data", {}).get("list", []):
                    title = _clean(item.get("title", ""))
                    if not title or len(title) < 4:
                        continue
                    seq = item.get("seq", "")
                    items.append(NewsItem(title=title, source="同花顺", url="", weight=1.2, timestamp=str(seq)))
                    if len(items) >= limit:
                        return items
            else:
                break
        except Exception:
            break
    return items


def fetch_thepaper_realtime(limit: int = 80) -> List[NewsItem]:
    """澎湃新闻 - 财经频道"""
    items: List[NewsItem] = []
    try:
        r = _get("https://cache.thepaper.cn/contentapi/wwwIndex/rightSidebar", timeout=12)
        if r.status_code == 200:
            data = r.json()
            for item in data.get("data", {}).get("hotNews", [])[:limit]:
                title = _clean(item.get("name", ""))
                if not title or len(title) < 4:
                    continue
                cont_id = item.get("contId", "")
                url = f"https://www.thepaper.cn/newsDetail_forward_{cont_id}" if cont_id else ""
                items.append(NewsItem(title=title, source="澎湃新闻", url=url, weight=1.2))
                if len(items) >= limit:
                    return items
    except Exception:
        pass
    # 网页兜底
    if len(items) < 10:
        try:
            r = _get("https://www.thepaper.cn/channel_25462")
            r.raise_for_status()
            soup = BeautifulSoup(_decode(r), "html.parser")
            for a in soup.select("a[href*=newsDetail]"):
                title = _clean(a.get_text())
                if title and len(title) >= 8 and len(title) <= 80:
                    href = a.get("href", "")
                    full = href if href.startswith("http") else "https://www.thepaper.cn" + href
                    items.append(NewsItem(title=title, source="澎湃新闻", url=full, weight=1.2))
                    if len(items) >= limit:
                        break
        except Exception:
            pass
    return items


def fetch_stcn_realtime(limit: int = 80) -> List[NewsItem]:
    """证券时报 - 快讯API"""
    items: List[NewsItem] = []
    try:
        r = _get("https://kuaixun.stcn.com/index/dkx.html")
        r.raise_for_status()
        soup = BeautifulSoup(_decode(r), "html.parser")
        for li in soup.select("li, .news_item, .item"):
            a = li.find("a")
            if not a:
                continue
            title = _clean(a.get_text())
            if title and len(title) >= 6 and len(title) <= 100:
                href = a.get("href", "")
                full = href if href.startswith("http") else "https://kuaixun.stcn.com" + href
                items.append(NewsItem(title=title, source="证券时报", url=full, weight=1.4))
                if len(items) >= limit:
                    break
    except Exception:
        pass
    return items


def fetch_nbd_realtime(limit: int = 80) -> List[NewsItem]:
    """每日经济新闻 - 首页"""
    items: List[NewsItem] = []
    try:
        r = _get("https://www.nbd.com.cn/")
        r.raise_for_status()
        soup = BeautifulSoup(_decode(r), "html.parser")
        for a in soup.select("a[href*=articles]"):
            title = _clean(a.get_text())
            if title and len(title) >= 8 and len(title) <= 100:
                href = a.get("href", "")
                full = href if href.startswith("http") else "https://www.nbd.com.cn" + href
                items.append(NewsItem(title=title, source="每日经济", url=full, weight=1.2))
                if len(items) >= limit:
                    break
    except Exception:
        pass
    return items


# ================================================================
# 第2类：新增国内新闻源（扩充覆盖面）
# ================================================================

def fetch_yicai(limit: int = 80) -> List[NewsItem]:
    """第一财经 - 快讯"""
    items: List[NewsItem] = []
    for page in range(1, 5):
        try:
            r = _get("https://www.yicai.com/ajax/getlatest", params={
                "page": str(page), "pagesize": "20",
            }, extra_headers={"Referer": "https://www.yicai.com/"})
            if r.status_code == 200:
                data = r.json()
                for item in data.get("data", []):
                    title = _clean(item.get("NewsTitle", "") or item.get("title", ""))
                    if not title or len(title) < 4:
                        continue
                    summary = _clean(item.get("NewsSummary", "") or item.get("summary", ""))
                    nid = item.get("NewsID", "") or item.get("id", "")
                    url = f"https://www.yicai.com/news/{nid}" if nid else ""
                    items.append(NewsItem(title=title, source="第一财经", url=url, weight=1.4, content=summary))
                    if len(items) >= limit:
                        return items
            else:
                break
        except Exception:
            break
    # 网页兜底
    if len(items) < 10:
        try:
            r = _get("https://www.yicai.com/news/")
            r.raise_for_status()
            soup = BeautifulSoup(_decode(r), "html.parser")
            for a in soup.select("a"):
                title = _clean(a.get_text())
                href = a.get("href", "")
                if title and len(title) >= 8 and len(title) <= 80 and "/news/" in href:
                    full = href if href.startswith("http") else "https://www.yicai.com" + href
                    items.append(NewsItem(title=title, source="第一财经", url=full, weight=1.4))
                    if len(items) >= limit:
                        break
        except Exception:
            pass
    return items


def fetch_xueqiu(limit: int = 80) -> List[NewsItem]:
    """雪球 - 热帖（需cookie）"""
    items: List[NewsItem] = []
    try:
        s = requests.Session()
        s.trust_env = False
        s.headers.update(_HEADERS)
        # 先访问首页获取cookie
        s.get("https://xueqiu.com/", timeout=10)
        r = s.get("https://xueqiu.com/statuses/hot/listV2.json", params={
            "since_id": "-1", "max_id": "-1", "size": str(min(limit, 50)),
        }, timeout=12)
        if r.status_code == 200:
            data = r.json()
            for item in data.get("items", []):
                original = item.get("original_status", {}) or item
                title = _clean(original.get("title", "") or original.get("text", "")[:120])
                if not title or len(title) < 6:
                    continue
                desc = _clean(original.get("description", ""))
                items.append(NewsItem(title=title[:120], source="雪球", url="", weight=1.1, content=desc))
                if len(items) >= limit:
                    break
    except Exception:
        pass
    return items


def fetch_wallstreetcn(limit: int = 80) -> List[NewsItem]:
    """华尔街见闻 - 快讯"""
    items: List[NewsItem] = []
    try:
        r = _get("https://api-one.wallstcn.com/apiv1/content/lives", params={
            "channel": "global-channel", "limit": str(min(limit, 100)),
        })
        if r.status_code == 200:
            data = r.json()
            for item in data.get("data", {}).get("items", []):
                title = _clean(item.get("title", "") or item.get("content_plain", "")[:120])
                if not title or len(title) < 4:
                    continue
                content = _clean(item.get("content_plain", ""))
                ct = str(item.get("display_time", ""))
                items.append(NewsItem(title=title, source="华尔街见闻", url="", weight=1.35, content=content, timestamp=ct))
                if len(items) >= limit:
                    break
    except Exception:
        pass
    return items


def fetch_cctv_finance(limit: int = 80) -> List[NewsItem]:
    """央视财经 - RSS + 网页"""
    items = _rss("https://news.cctv.com/finance/cjzx/index.shtml", "央视财经", limit, 1.3)
    if len(items) < 10:
        try:
            r = _get("https://news.cctv.com/finance/")
            r.raise_for_status()
            soup = BeautifulSoup(_decode(r), "html.parser")
            for a in soup.select("a[href*=ARTI]"):
                title = _clean(a.get_text())
                if title and 6 <= len(title) <= 80:
                    href = a.get("href", "")
                    items.append(NewsItem(title=title, source="央视财经", url=href, weight=1.3))
                    if len(items) >= limit:
                        break
        except Exception:
            pass
    return items


def fetch_caijing(limit: int = 80) -> List[NewsItem]:
    """财经杂志 - 网页"""
    items: List[NewsItem] = []
    try:
        r = _get("https://www.caijing.com.cn/")
        r.raise_for_status()
        soup = BeautifulSoup(_decode(r), "html.parser")
        for a in soup.select("a"):
            title = _clean(a.get_text())
            href = a.get("href", "")
            if title and 8 <= len(title) <= 80 and href and "caijing.com.cn" in href:
                items.append(NewsItem(title=title, source="财经杂志", url=href, weight=1.1))
                if len(items) >= limit:
                    break
    except Exception:
        pass
    return items


def fetch_huxiu(limit: int = 80) -> List[NewsItem]:
    """虎嗅 - 财经"""
    items: List[NewsItem] = []
    try:
        r = _get("https://www.huxiu.com/")
        r.raise_for_status()
        soup = BeautifulSoup(_decode(r), "html.parser")
        for a in soup.select("a[href*=article]"):
            title = _clean(a.get_text())
            if title and 8 <= len(title) <= 80:
                href = a.get("href", "")
                full = href if href.startswith("http") else "https://www.huxiu.com" + href
                items.append(NewsItem(title=title, source="虎嗅", url=full, weight=1.05))
                if len(items) >= limit:
                    break
    except Exception:
        pass
    return items


def fetch_zhihu_finance(limit: int = 60) -> List[NewsItem]:
    """知乎热榜 - 财经相关"""
    items: List[NewsItem] = []
    try:
        r = _get("https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total", params={
            "limit": str(min(limit, 50)),
        }, extra_headers={"Referer": "https://www.zhihu.com/hot"})
        if r.status_code == 200:
            data = r.json()
            finance_kw = ["股", "基金", "市场", "经济", "金融", "银行", "央行", "利率", "房价", "投资", "涨", "跌", "A股", "美联储", "通胀", "GDP"]
            for item in data.get("data", []):
                target = item.get("target", {})
                title = _clean(target.get("title", ""))
                if not title:
                    continue
                if any(kw in title for kw in finance_kw):
                    items.append(NewsItem(title=title, source="知乎财经", url="", weight=0.9))
                    if len(items) >= limit:
                        break
    except Exception:
        pass
    return items


def fetch_sina_finance(limit: int = 80) -> List[NewsItem]:
    """新浪财经 - 要闻频道"""
    items: List[NewsItem] = []
    try:
        r = _get("https://feed.mix.sina.com.cn/api/roll/get", params={
            "pageid": "153", "lid": "2516", "k": "", "num": str(min(limit, 50)),
            "page": "1", "r": str(time.time()),
        })
        if r.status_code == 200:
            data = r.json()
            for item in data.get("result", {}).get("data", []):
                title = _clean(item.get("title", ""))
                if not title or len(title) < 4:
                    continue
                url = item.get("url", "")
                intro = _clean(item.get("intro", ""))
                ct = item.get("ctime", "")
                items.append(NewsItem(title=title, source="新浪财经", url=url, weight=1.3, content=intro, timestamp=str(ct)))
                if len(items) >= limit:
                    break
    except Exception:
        pass
    return items


def fetch_tencent_finance(limit: int = 80) -> List[NewsItem]:
    """腾讯财经 - 要闻"""
    items: List[NewsItem] = []
    try:
        r = _get("https://i.news.qq.com/trpc.qqnews_web.kv_srv.kv_srv_http/list?sub_srv_id=finance&srv_id=pc&offset=0&limit=50&strategy=1&ext=%7B%22pool%22%3A%5B%22high%22%5D%7D")
        if r.status_code == 200:
            data = r.json()
            for item in data.get("data", {}).get("list", []):
                title = _clean(item.get("title", ""))
                if not title or len(title) < 4:
                    continue
                url = item.get("url", "")
                items.append(NewsItem(title=title, source="腾讯财经", url=url, weight=1.2))
                if len(items) >= limit:
                    break
    except Exception:
        pass
    return items


# ================================================================
# 第3类：国际新闻源（高权重，覆盖全球市场）
# ================================================================

def fetch_cnbc_rss(limit: int = 80) -> List[NewsItem]:
    return _rss_multi([
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147",
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910",
    ], "CNBC", limit, 1.5)


def fetch_wsj_rss(limit: int = 80) -> List[NewsItem]:
    return _rss_multi([
        "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml",
        "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
    ], "WSJ", limit, 1.5)


def fetch_marketwatch_rss(limit: int = 80) -> List[NewsItem]:
    return _rss_multi([
        "http://feeds.marketwatch.com/marketwatch/topstories/",
        "http://feeds.marketwatch.com/marketwatch/marketpulse/",
    ], "MarketWatch", limit, 1.4)


def fetch_ft_rss(limit: int = 80) -> List[NewsItem]:
    return _rss("https://www.ft.com/?format=rss", "Financial Times", limit, 1.5)


def fetch_reuters_rss(limit: int = 80) -> List[NewsItem]:
    return _rss_multi([
        "https://news.google.com/rss/search?q=site:reuters.com+markets+OR+business+OR+economy&hl=en-US&gl=US&ceid=US:en&t=" + str(int(time.time())),
        "https://news.google.com/rss/search?q=site:reuters.com+stocks+OR+fed+OR+china+economy&hl=en-US&gl=US&ceid=US:en&t=" + str(int(time.time())),
    ], "Reuters", limit, 1.6)


def fetch_bloomberg_rss(limit: int = 80) -> List[NewsItem]:
    return _rss_multi([
        "https://news.google.com/rss/search?q=site:bloomberg.com+markets+OR+stocks+OR+economy&hl=en-US&gl=US&ceid=US:en&t=" + str(int(time.time())),
        "https://news.google.com/rss/search?q=site:bloomberg.com+fed+OR+inflation+OR+china&hl=en-US&gl=US&ceid=US:en&t=" + str(int(time.time())),
    ], "Bloomberg", limit, 1.6)


def fetch_fed_rss(limit: int = 60) -> List[NewsItem]:
    return _rss("https://www.federalreserve.gov/feeds/press_all.xml", "Fed", limit, 1.65)


def fetch_imf_rss(limit: int = 60) -> List[NewsItem]:
    return _rss("https://www.imf.org/en/News/Rss?type=News", "IMF", limit, 1.45)


def fetch_ecb_rss(limit: int = 60) -> List[NewsItem]:
    """欧洲央行"""
    return _rss("https://www.ecb.europa.eu/rss/press.html", "ECB", limit, 1.45)


def fetch_boj_rss(limit: int = 60) -> List[NewsItem]:
    """日本央行"""
    return _rss("https://www.boj.or.jp/en/rss/whatsnew210.xml", "BOJ", limit, 1.35)


def fetch_oilprice_rss(limit: int = 80) -> List[NewsItem]:
    return _rss("https://oilprice.com/rss/main", "OilPrice", limit, 1.35)


def fetch_mining_rss(limit: int = 80) -> List[NewsItem]:
    return _rss("https://www.mining.com/feed/", "Mining.com", limit, 1.3)


def fetch_google_cn_rss(limit: int = 80) -> List[NewsItem]:
    return _rss_multi([
        "https://news.google.com/rss/search?q=A%E8%82%A1+%E8%B4%A2%E7%BB%8F+%E8%82%A1%E5%B8%82&hl=zh-CN&gl=CN&ceid=CN:zh-Hans&t=" + str(int(time.time())),
        "https://news.google.com/rss/search?q=%E6%94%BF%E7%AD%96+%E7%BB%8F%E6%B5%8E+%E5%A4%AE%E8%A1%8C&hl=zh-CN&gl=CN&ceid=CN:zh-Hans&t=" + str(int(time.time())),
        "https://news.google.com/rss/search?q=%E6%9D%BF%E5%9D%97+%E8%A1%8C%E4%B8%9A+%E7%83%AD%E7%82%B9&hl=zh-CN&gl=CN&ceid=CN:zh-Hans&t=" + str(int(time.time())),
    ], "Google财经CN", limit, 1.2)


def fetch_google_global_rss(limit: int = 80) -> List[NewsItem]:
    return _rss_multi([
        "https://news.google.com/rss/search?q=stock+market+economy+finance+fed+interest+rate&hl=en-US&gl=US&ceid=US:en&t=" + str(int(time.time())),
        "https://news.google.com/rss/search?q=wall+street+earnings+IPO+merger&hl=en-US&gl=US&ceid=US:en&t=" + str(int(time.time())),
        "https://news.google.com/rss/search?q=oil+gold+commodity+crypto+market&hl=en-US&gl=US&ceid=US:en&t=" + str(int(time.time())),
    ], "Google财经Global", limit, 1.3)


def fetch_seeking_alpha_rss(limit: int = 80) -> List[NewsItem]:
    return _rss_multi([
        "https://seekingalpha.com/market_currents.xml",
        "https://seekingalpha.com/feed.xml",
    ], "Seeking Alpha", limit, 1.35)


def fetch_coindesk_rss(limit: int = 60) -> List[NewsItem]:
    return _rss("https://www.coindesk.com/arc/outboundfeeds/rss/", "CoinDesk", limit, 1.05)


def fetch_yahoo_finance(limit: int = 80) -> List[NewsItem]:
    """Yahoo Finance - RSS"""
    return _rss_multi([
        "https://news.google.com/rss/search?q=site:finance.yahoo.com+stocks+OR+market+OR+earnings&hl=en-US&gl=US&ceid=US:en&t=" + str(int(time.time())),
        "https://news.google.com/rss/search?q=site:finance.yahoo.com+fed+OR+economy+OR+investing&hl=en-US&gl=US&ceid=US:en&t=" + str(int(time.time())),
    ], "Yahoo Finance", limit, 1.35)


def fetch_nikkei(limit: int = 60) -> List[NewsItem]:
    """日经新闻 - RSS"""
    return _rss("https://feeds.nikkei.com/rss/index.xml", "Nikkei", limit, 1.3)


def fetch_investingcom(limit: int = 80) -> List[NewsItem]:
    """Investing.com - Google News proxy"""
    return _rss_multi([
        "https://news.google.com/rss/search?q=site:investing.com+analysis+OR+forecast+OR+stocks&hl=en-US&gl=US&ceid=US:en&t=" + str(int(time.time())),
        "https://news.google.com/rss/search?q=site:investing.com+fed+OR+earnings+OR+commodities&hl=en-US&gl=US&ceid=US:en&t=" + str(int(time.time())),
    ], "Investing.com", limit, 1.25)


def fetch_business_insider(limit: int = 60) -> List[NewsItem]:
    """Business Insider"""
    return _rss("https://news.google.com/rss/search?q=site:businessinsider.com+markets+OR+stocks+OR+wall+street&hl=en-US&gl=US&ceid=US:en&t=" + str(int(time.time())),
                "Business Insider", limit, 1.25)


def fetch_zerohedge(limit: int = 60) -> List[NewsItem]:
    """ZeroHedge - 财经分析"""
    return _rss("https://feeds.feedburner.com/zerohedge/feed", "ZeroHedge", limit, 1.15)


def fetch_the_guardian_biz(limit: int = 60) -> List[NewsItem]:
    """卫报商业版"""
    return _rss("https://www.theguardian.com/uk/business/rss", "Guardian Business", limit, 1.2)


def fetch_bbc_biz(limit: int = 60) -> List[NewsItem]:
    """BBC商业版"""
    return _rss("https://feeds.bbci.co.uk/news/business/rss.xml", "BBC Business", limit, 1.25)


# ================================================================
# 第4类：行业/大宗商品专题源
# ================================================================

def fetch_metal_bulletin(limit: int = 60) -> List[NewsItem]:
    """金属导报"""
    return _rss("https://news.google.com/rss/search?q=site:metalbulletin.com+OR+site:fastmarkets.com+metals+steel&hl=en-US&gl=US&ceid=US:en&t=" + str(int(time.time())),
                "Metal Bulletin", limit, 1.15)


def fetch_agriculture(limit: int = 60) -> List[NewsItem]:
    """农业大宗商品"""
    return _rss("https://news.google.com/rss/search?q=agriculture+commodity+grain+soybean+wheat+corn+prices&hl=en-US&gl=US&ceid=US:en&t=" + str(int(time.time())),
                "Agri Commodities", limit, 1.1)


def fetch_crypto_finance(limit: int = 40) -> List[NewsItem]:
    """加密市场与传统金融交叉"""
    return _rss("https://news.google.com/rss/search?q=crypto+bitcoin+ETF+SEC+regulation+market&hl=en-US&gl=US&ceid=US:en&t=" + str(int(time.time())),
                "CryptoFinance", limit, 1.0)


# ================================================================
# 聚合（35+源）
# ================================================================

_REALTIME_SOURCES = [
    fetch_sina_7x24,
    fetch_cls_realtime,
    fetch_eastmoney_realtime,
    fetch_10jqka_realtime,
    fetch_thepaper_realtime,
    fetch_stcn_realtime,
    fetch_nbd_realtime,
]

_NEW_CN_SOURCES = [
    fetch_yicai,
    fetch_xueqiu,
    fetch_wallstreetcn,
    fetch_cctv_finance,
    fetch_caijing,
    fetch_huxiu,
    fetch_zhihu_finance,
    fetch_sina_finance,
    fetch_tencent_finance,
]

_INTERNATIONAL_SOURCES = [
    fetch_cnbc_rss,
    fetch_wsj_rss,
    fetch_marketwatch_rss,
    fetch_ft_rss,
    fetch_reuters_rss,
    fetch_bloomberg_rss,
    fetch_fed_rss,
    fetch_imf_rss,
    fetch_ecb_rss,
    fetch_boj_rss,
    fetch_oilprice_rss,
    fetch_mining_rss,
    fetch_google_cn_rss,
    fetch_google_global_rss,
    fetch_seeking_alpha_rss,
    fetch_coindesk_rss,
    fetch_yahoo_finance,
    fetch_nikkei,
    fetch_investingcom,
    fetch_business_insider,
    fetch_zerohedge,
    fetch_the_guardian_biz,
    fetch_bbc_biz,
]

_SECTOR_SOURCES = [
    fetch_metal_bulletin,
    fetch_agriculture,
    fetch_crypto_finance,
]

_ALL_SOURCES = _REALTIME_SOURCES + _NEW_CN_SOURCES + _INTERNATIONAL_SOURCES + _SECTOR_SOURCES


def fetch_all_news(limit_per_source: int = 40, max_workers: int = 12, total_limit: int = 3000,
                   weight_floor: float = 0.7, weight_ceil: float = 2.2) -> List[NewsItem]:
    """聚合所有来源的新闻（35+源）。"""
    merged: List[NewsItem] = []

    def _run_source(fn):
        """Run a single source with timeout protection."""
        try:
            return fn(limit_per_source) or []
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = {ex.submit(_run_source, fn): fn.__name__ for fn in _ALL_SOURCES}
        try:
            for f in as_completed(futs, timeout=30):
                try:
                    result = f.result(timeout=3) or []
                    merged.extend(result)
                except Exception:
                    pass
        except TimeoutError:
            # Some sources are slow, just use what we have
            pass
        except Exception:
            pass

    # 去重：标题前40字（宽松去重，避免误删不同事件）
    seen = set()
    deduped: List[NewsItem] = []
    for n in merged:
        key = n.title[:40]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(n)

    # 按权重降序
    deduped.sort(key=lambda x: x.weight, reverse=True)

    out: List[NewsItem] = []
    for n in deduped:
        n.weight = max(weight_floor, min(weight_ceil, n.weight))
        out.append(n)
        if len(out) >= total_limit:
            break
    return out


def news_items_to_text(items: List[NewsItem], max_items: int = 3000) -> str:
    """将新闻转为文本，保留完整内容不限字数。"""
    lines = []
    for i, n in enumerate(items[:max_items], start=1):
        ts_part = f"  时间: {n.timestamp}" if n.timestamp else ""
        content_part = f"\n  内容: {n.content}" if n.content else ""
        lines.append(f"{i}. [W={n.weight:.2f}] [{n.source}] {n.title}{ts_part}{content_part}\n  链接:{n.url}")
    return "\n".join(lines)
