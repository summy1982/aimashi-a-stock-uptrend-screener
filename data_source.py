from __future__ import annotations

import json
import http.client
import ssl
import re
import math
import requests
from typing import List, Dict, Any

_ssl_ctx = ssl.create_default_context()

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _is_mainboard_code(code: str) -> bool:
    c = str(code).strip()
    return c.startswith("60") or c.startswith("00")


def _tencent_prefix(code: str) -> str:
    if code.startswith("6"):
        return f"sh{code}"
    return f"sz{code}"


def _http_get(host: str, path: str, timeout: int = 12, encoding: str = "utf-8", https: bool = True) -> str:
    if https:
        conn = http.client.HTTPSConnection(host, timeout=timeout, context=_ssl_ctx)
    else:
        conn = http.client.HTTPConnection(host, timeout=timeout)
    conn.request("GET", path, headers=_HEADERS)
    r = conn.getresponse()
    data = r.read().decode(encoding, errors="ignore")
    conn.close()
    return data


def _requests_get_no_proxy(url: str, params: dict = None, timeout: int = 15) -> requests.Response:
    """GET请求，绕过系统代理。"""
    s = requests.Session()
    s.trust_env = False
    return s.get(url, params=params, headers=_HEADERS, timeout=timeout)


# ================================================================
# 板块成分股：Sina Market Center API（已验证可用）
# ================================================================

# LLM推荐的板块名 -> Sina行业代码 映射
# 先做精确匹配，再做模糊匹配
_SECTOR_TO_SINA = {
    # 金融
    "银行": "new_jrhy", "金融": "new_jrhy", "券商": "new_jrhy", "证券": "new_jrhy", "保险": "new_jrhy",
    "高股息": "new_jrhy", "高股息板块": "new_jrhy", "公用事业": "new_dlhy",
    # 房地产
    "房地产": "new_fdc", "地产": "new_fdc",
    # 科技
    "电子信息": "new_dzxx", "电子": "new_dzxx", "计算机": "new_dzxx", "软件": "new_dzxx",
    "人工智能": "new_dzxx", "AI": "new_dzxx", "算力": "new_dzxx",
    "电子器件": "new_dzqj", "半导体": "new_dzqj", "芯片": "new_dzqj", "集成电路": "new_dzqj",
    # 制造
    "化工": "new_hghy", "化工行业": "new_hghy",
    "钢铁": "new_gthy", "钢铁行业": "new_gthy",
    "有色": "new_ysjs", "有色金属": "new_ysjs",
    "煤炭": "new_mthy", "煤炭行业": "new_mthy",
    "石油": "new_syhy", "石化": "new_syhy", "石油石化": "new_syhy", "石油行业": "new_syhy",
    "汽车": "new_qczz", "汽车制造": "new_qczz", "新能源车": "new_qczz",
    "电力": "new_dlhy", "电力行业": "new_dlhy",
    "机械": "new_jxhy", "机械行业": "new_jxhy",
    # 消费
    "白酒": "new_ljhy", "酿酒": "new_ljhy", "酿酒行业": "new_ljhy",
    "食品": "new_sphy", "食品饮料": "new_sphy", "食品行业": "new_sphy",
    "家电": "new_jdhy", "家电行业": "new_jdhy", "电器": "new_dqhy",
    "商业": "new_sybh", "商业百货": "new_sybh", "消费": "new_sybh", "大消费": "new_sphy",
    # 医药
    "医药": "new_swzz", "医药生物": "new_swzz", "生物制药": "new_swzz", "生物": "new_swzz",
    "医疗器械": "new_ylqx",
    # 军工
    "军工": "new_fjzz", "国防军工": "new_fjzz", "航空航天": "new_fjzz", "飞机制造": "new_fjzz",
    # 建筑
    "建筑": "new_jzjc", "建材": "new_jzjc", "建筑建材": "new_jzjc", "基建": "new_jzjc",
    # 能源
    "光伏": "new_fdsb", "新能源": "new_fdsb", "发电设备": "new_fdsb", "风电": "new_fdsb",
    "储能": "new_fdsb", "智能电网": "new_dlhy", "电力设备": "new_fdsb", "电池": "new_dzqj", "钠离子电池": "new_dzqj", "锂电": "new_dzqj", "锂": "new_ysjs",
    # 交通
    "交运": "new_jtys", "交通运输": "new_jtys", "铁路": "new_jtys", "物流": "new_jtys",
    # 其他
    "传媒": "new_cmyl", "传媒娱乐": "new_cmyl", "游戏": "new_cmyl",
    "环保": "new_hbhy", "环保行业": "new_hbhy",
    "纺织": "new_fzhy", "纺织行业": "new_fzhy",
    "船舶": "new_cbzz", "船舶制造": "new_cbzz",
    "农业": "new_nlmy", "农林牧渔": "new_nlmy",
    "机器人": "new_jxhy",
    "CPO": "new_dzxx",
    "通信": "new_dzxx",
    "核电": "new_dlhy",
    "城市更新": "new_jzjc",
}

# 模糊关键词列表（按优先级排序，用于二次匹配）
_FUZZY_KEYWORDS = [
    ("银行", "new_jrhy"), ("金融", "new_jrhy"), ("证券", "new_jrhy"), ("券商", "new_jrhy"),
    ("高股息", "new_jrhy"), ("公用事业", "new_dlhy"),
    ("地产", "new_fdc"), ("房", "new_fdc"),
    ("半导体", "new_dzqj"), ("芯片", "new_dzqj"), ("集成电路", "new_dzqj"),
    ("人工智能", "new_dzxx"), ("AI", "new_dzxx"), ("算力", "new_dzxx"), ("软件", "new_dzxx"), ("计算机", "new_dzxx"),
    ("石油", "new_syhy"), ("石化", "new_syhy"), ("原油", "new_syhy"),
    ("军工", "new_fjzz"), ("国防", "new_fjzz"), ("航天", "new_fjzz"), ("航空", "new_fjzz"),
    ("白酒", "new_ljhy"), ("酿酒", "new_ljhy"), ("酒", "new_ljhy"),
    ("医药", "new_swzz"), ("生物", "new_swzz"), ("制药", "new_swzz"),
    ("汽车", "new_qczz"), ("新能源车", "new_qczz"),
    ("电力", "new_dlhy"), ("核电", "new_dlhy"), ("发电", "new_fdsb"),
    ("智能电网", "new_dlhy"), ("电网", "new_dlhy"), ("储能", "new_fdsb"), ("电池", "new_dzqj"), ("锂", "new_ysjs"),
    ("光伏", "new_fdsb"), ("风电", "new_fdsb"), ("新能源", "new_fdsb"),
    ("有色", "new_ysjs"), ("黄金", "new_ysjs"), ("铜", "new_ysjs"),
    ("煤炭", "new_mthy"), ("钢铁", "new_gthy"),
    ("化工", "new_hghy"),
    ("建筑", "new_jzjc"), ("建材", "new_jzjc"), ("基建", "new_jzjc"),
    ("消费", "new_sphy"), ("食品", "new_sphy"), ("家电", "new_jdhy"),
    ("电子", "new_dzxx"), ("信息", "new_dzxx"),
    ("机械", "new_jxhy"), ("机器人", "new_jxhy"),
    ("交运", "new_jtys"), ("物流", "new_jtys"),
    ("传媒", "new_cmyl"), ("游戏", "new_cmyl"),
    ("环保", "new_hbhy"),
    ("船舶", "new_cbzz"),
    ("农业", "new_nlmy"),
]


def _resolve_sina_code(sector_name: str) -> str:
    """将板块名解析为Sina行业代码。"""
    # 精确匹配
    code = _SECTOR_TO_SINA.get(sector_name, "")
    if code:
        return code

    # 包含匹配
    for key, scode in _SECTOR_TO_SINA.items():
        if key in sector_name or sector_name in key:
            return scode

    # 模糊关键词匹配
    for keyword, scode in _FUZZY_KEYWORDS:
        if keyword in sector_name:
            return scode

    return ""


def get_sector_stock_pool(sector_name: str) -> list[str]:
    """获取板块成分股代码列表（仅主板）。"""
    sina_code = _resolve_sina_code(sector_name)
    if not sina_code:
        combined = []
        seen = set()
        for keyword, _ in _FUZZY_KEYWORDS:
            if keyword in sector_name and keyword != sector_name:
                for code in get_sector_stock_pool(keyword):
                    if code not in seen:
                        seen.add(code)
                        combined.append(code)
        return combined[:200]

    try:
        # Sina Market Center API - 每页最多300条
        all_stocks = []
        for page in range(1, 6):  # 最多5页
            resp = _requests_get_no_proxy(
                "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData",
                params={
                    "page": page,
                    "num": 300,
                    "sort": "changepercent",
                    "asc": 0,
                    "node": sina_code,
                    "symbol": "",
                    "_s_r_a": "sort",
                },
            )
            resp.encoding = "gbk"
            if not resp.text or resp.text == "null":
                break
            data = resp.json()
            if not data:
                break
            all_stocks.extend(data)
            if len(data) < 100:
                break

        # 提取主板代码（sh60xxxx, sz00xxxx）
        mainboard = []
        seen = set()
        for d in all_stocks:
            symbol = d.get("symbol", "")
            # symbol格式: sh600519 或 sz000001
            code = symbol.replace("sh", "").replace("sz", "")
            if _is_mainboard_code(code) and code not in seen:
                seen.add(code)
                mainboard.append(code)

        return mainboard[:200]
    except Exception as e:
        print(f"获取板块 {sector_name} ({sina_code}) 成分股失败: {e}")
        return []


def normalize_candidate_names(codes: list[str]) -> list[str]:
    """将股票代码列表转换为「代码 名称」格式。"""
    if not codes:
        return codes
    result = _tencent_batch_names(codes)
    if result:
        return result
    result = []
    for c in codes[:20]:
        name = _fetch_single_name(c)
        result.append(f"{c} {name}" if name else c)
    return result


def _tencent_batch_names(codes: list[str]) -> list[str]:
    """腾讯接口批量查股票名称。"""
    try:
        batch_size = 50
        all_results = []
        for i in range(0, len(codes), batch_size):
            batch = codes[i:i + batch_size]
            prefixes = ",".join(_tencent_prefix(c) for c in batch)
            text = _http_get("qt.gtimg.cn", f"/q={prefixes}", encoding="gbk", https=False)
            for line in text.strip().split(";"):
                line = line.strip()
                if not line or "=" not in line:
                    continue
                parts = line.split('"')
                if len(parts) < 2:
                    continue
                fields = parts[1].split("~")
                if len(fields) >= 3:
                    code = fields[2]
                    name = fields[1]
                    if _is_mainboard_code(code) and name:
                        all_results.append(f"{code} {name}")
        return all_results if all_results else []
    except Exception:
        return []


def _fetch_single_name(code: str) -> str:
    """单只股票查名称（腾讯接口）。"""
    try:
        prefix = _tencent_prefix(code)
        text = _http_get("qt.gtimg.cn", f"/q={prefix}", encoding="gbk", https=False)
        raw = text.split('"')[1] if '"' in text else ""
        fields = raw.split("~")
        if len(fields) >= 3:
            return fields[1]
    except Exception:
        pass
    return ""


# ================================================================
# 技术指标计算
# ================================================================

def _calc_ema(closes: List[float], period: int) -> List[float]:
    if not closes:
        return []
    emas = [closes[0]]
    k = 2.0 / (period + 1)
    for i in range(1, len(closes)):
        emas.append(closes[i] * k + emas[-1] * (1 - k))
    return emas


def _calc_macd(closes: List[float]) -> Dict[str, Any]:
    if len(closes) < 26:
        return {"dif": 0, "dea": 0, "macd": 0, "trend": "数据不足"}
    ema12 = _calc_ema(closes, 12)
    ema26 = _calc_ema(closes, 26)
    dif = [ema12[i] - ema26[i] for i in range(len(closes))]
    dea = _calc_ema(dif, 9)
    macd_bar = [(dif[i] - dea[i]) * 2 for i in range(len(closes))]

    latest_dif = dif[-1]
    latest_dea = dea[-1]
    latest_macd = macd_bar[-1]
    prev_macd = macd_bar[-2] if len(macd_bar) > 1 else 0

    trend = "震荡"
    if latest_dif > latest_dea and latest_macd > 0:
        trend = "多头发散加强" if latest_macd > prev_macd else "多头但动能减弱"
    elif latest_dif < latest_dea and latest_macd < 0:
        trend = "空头发散加强" if latest_macd < prev_macd else "空头但动能减弱"
    elif latest_dif > latest_dea and latest_macd < 0:
        trend = "金叉后回调"
    elif latest_dif < latest_dea and latest_macd > 0:
        trend = "死叉后反弹"

    cross = ""
    if len(dif) >= 2 and len(dea) >= 2:
        if dif[-2] <= dea[-2] and dif[-1] > dea[-1]:
            cross = "刚金叉"
        elif dif[-2] >= dea[-2] and dif[-1] < dea[-1]:
            cross = "刚死叉"

    return {"dif": round(latest_dif, 3), "dea": round(latest_dea, 3), "macd": round(latest_macd, 3), "trend": trend, "cross": cross}


def _calc_rsi(closes: List[float], period: int = 14) -> Dict[str, Any]:
    if len(closes) < period + 1:
        return {"rsi": 50, "zone": "数据不足"}
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    rsi = 100 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))

    avg_gain_6 = sum(gains[-6:]) / 6 if len(gains) >= 6 else avg_gain
    avg_loss_6 = sum(losses[-6:]) / 6 if len(losses) >= 6 else avg_loss
    rsi6 = 100 if avg_loss_6 == 0 else 100 - (100 / (1 + avg_gain_6 / avg_loss_6))

    zone = "中性"
    if rsi > 80: zone = "严重超买"
    elif rsi > 70: zone = "超买"
    elif rsi > 60: zone = "偏强"
    elif rsi < 20: zone = "严重超卖"
    elif rsi < 30: zone = "超卖"
    elif rsi < 40: zone = "偏弱"

    return {"rsi6": round(rsi6, 1), "rsi14": round(rsi, 1), "zone": zone}


def _calc_kdj(klines: List[Dict]) -> Dict[str, Any]:
    if len(klines) < 9:
        return {"k": 50, "d": 50, "j": 50, "zone": "数据不足"}

    k_values, d_values = [50.0], [50.0]
    for i in range(8, len(klines)):
        period = klines[i - 8:i + 1]
        high_9 = max(p["high"] for p in period)
        low_9 = min(p["low"] for p in period)
        close = klines[i]["close"]
        rsv = 50 if high_9 == low_9 else (close - low_9) / (high_9 - low_9) * 100
        k = 2 / 3 * k_values[-1] + 1 / 3 * rsv
        d = 2 / 3 * d_values[-1] + 1 / 3 * k
        k_values.append(k)
        d_values.append(d)

    k_val, d_val = k_values[-1], d_values[-1]
    j_val = 3 * k_val - 2 * d_val

    zone = "中性"
    if j_val > 100: zone = "超买"
    elif j_val > 80: zone = "偏强"
    elif j_val < 0: zone = "超卖"
    elif j_val < 20: zone = "偏弱"

    cross = ""
    if len(k_values) >= 2 and len(d_values) >= 2:
        if k_values[-2] <= d_values[-2] and k_values[-1] > d_values[-1]: cross = "金叉"
        elif k_values[-2] >= d_values[-2] and k_values[-1] < d_values[-1]: cross = "死叉"

    return {"k": round(k_val, 1), "d": round(d_val, 1), "j": round(j_val, 1), "zone": zone, "cross": cross}


def _calc_boll(closes: List[float], period: int = 20) -> Dict[str, Any]:
    if len(closes) < period:
        return {"upper": 0, "mid": 0, "lower": 0, "position": "数据不足"}
    mid = sum(closes[-period:]) / period
    std = math.sqrt(sum((c - mid) ** 2 for c in closes[-period:]) / period)
    upper, lower = mid + 2 * std, mid - 2 * std

    price = closes[-1]
    if upper == lower:
        position = "中轨附近"
    else:
        ratio = (price - lower) / (upper - lower)
        if ratio > 0.9: position = "接近上轨（超买区）"
        elif ratio > 0.7: position = "上轨与中轨之间偏上"
        elif ratio > 0.5: position = "中轨上方"
        elif ratio > 0.3: position = "中轨下方"
        elif ratio > 0.1: position = "下轨与中轨之间偏下"
        else: position = "接近下轨（超卖区）"

    return {"upper": round(upper, 2), "mid": round(mid, 2), "lower": round(lower, 2),
            "bandwidth": round((upper - lower) / mid * 100, 2), "position": position}


def _calc_volume_analysis(klines: List[Dict]) -> Dict[str, Any]:
    if len(klines) < 5:
        return {}
    vols = [k["vol"] for k in klines]
    avg_5 = sum(vols[-5:]) / 5
    avg_10 = sum(vols[-10:]) / min(10, len(vols))
    avg_20 = sum(vols[-20:]) / min(20, len(vols))
    latest_vol = vols[-1]

    vol_ratio_5 = latest_vol / avg_5 if avg_5 > 0 else 1
    vol_ratio_20 = latest_vol / avg_20 if avg_20 > 0 else 1

    status = "正常"
    if vol_ratio_5 > 2.0: status = "显著放量"
    elif vol_ratio_5 > 1.5: status = "温和放量"
    elif vol_ratio_5 < 0.5: status = "明显缩量"
    elif vol_ratio_5 < 0.7: status = "温和缩量"

    consec_up = 0
    for i in range(len(vols) - 1, 0, -1):
        if vols[i] > vols[i - 1]: consec_up += 1
        else: break

    return {
        "latest_vol": round(latest_vol, 0), "avg5": round(avg_5, 0), "avg10": round(avg_10, 0), "avg20": round(avg_20, 0),
        "vol_ratio_vs_5d": round(vol_ratio_5, 2), "vol_ratio_vs_20d": round(vol_ratio_20, 2),
        "status": status, "consec_vol_increase": consec_up,
    }


def _calc_price_levels(klines: List[Dict]) -> Dict[str, Any]:
    if len(klines) < 10:
        return {}
    highs = [k["high"] for k in klines]
    lows = [k["low"] for k in klines]
    closes = [k["close"] for k in klines]
    current = closes[-1]

    # 局部高低点
    pivots_high, pivots_low = [], []
    for i in range(2, len(klines) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            pivots_high.append(highs[i])
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            pivots_low.append(lows[i])

    resistance_levels = sorted(set(h for h in pivots_high if h > current))
    support_levels = sorted(set(l for l in pivots_low if l < current), reverse=True)

    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else sum(closes) / len(closes)
    ma60 = sum(closes) / len(closes)

    ma_support, ma_resistance = [], []
    for label, val in [("MA5", ma5), ("MA10", ma10), ("MA20", ma20), ("MA60", ma60)]:
        if val < current: ma_support.append((label, round(val, 2)))
        else: ma_resistance.append((label, round(val, 2)))

    trs = []
    for i in range(1, len(klines)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    atr_14 = sum(trs[-14:]) / min(14, len(trs)) if trs else 0

    return {
        "current_price": current,
        "high_5d": max(highs[-5:]), "low_5d": min(lows[-5:]),
        "high_10d": max(highs[-10:]), "low_10d": min(lows[-10:]),
        "high_20d": max(highs[-20:]), "low_20d": min(lows[-20:]),
        "high_60d": max(highs), "low_60d": min(lows),
        "pivot_resistance": [round(r, 2) for r in resistance_levels[:3]],
        "pivot_support": [round(s, 2) for s in support_levels[:3]],
        "ma_values": {"MA5": round(ma5, 2), "MA10": round(ma10, 2), "MA20": round(ma20, 2), "MA60": round(ma60, 2)},
        "ma_support": ma_support, "ma_resistance": ma_resistance,
        "atr_14": round(atr_14, 2),
    }


# ================================================================
# 实时行情 + 完整技术分析
# ================================================================

# ================================================================
# 大盘环境判断（回测发现：大盘下跌时技术信号失效严重）
# ================================================================

def get_market_regime() -> Dict[str, Any]:
    """判断当前大盘环境。返回大盘趋势和是否适合做多。"""
    try:
        # 获取沪深300指数60日K线
        text = _http_get("web.ifzq.gtimg.cn",
                         "/appstock/app/fqkline/get?param=sh000300,day,,,60,qfq", https=True)
        data = json.loads(text)
        stock_data = data.get("data", {}).get("sh000300", {})
        raw = stock_data.get("qfqday", []) or stock_data.get("day", [])
        if isinstance(raw, dict):
            raw = list(raw.values())

        klines = []
        for k in raw:
            if len(k) >= 6:
                klines.append({"date": k[0], "close": float(k[2]), "high": float(k[3]),
                               "low": float(k[4]), "vol": float(k[5])})

        if len(klines) < 20:
            return {"regime": "unknown", "trend": "数据不足", "suitable": True, "warning": ""}

        closes = [k["close"] for k in klines]
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes[-10:]) / 10
        ma20 = sum(closes[-20:]) / 20
        current = closes[-1]
        macd = _calc_macd(closes)

        # 判断大盘趋势
        if ma5 > ma10 > ma20 and current > ma5:
            regime = "bull"
            trend = "大盘多头排列，适合积极做多"
            suitable = True
            warning = ""
        elif ma5 > ma10 and current > ma20:
            regime = "neutral_bull"
            trend = "大盘偏多，可适度参与"
            suitable = True
            warning = "大盘未完全确认多头，控制仓位"
        elif current > ma20 and ma5 < ma10:
            regime = "consolidation"
            trend = "大盘震荡整理，精选个股"
            suitable = True
            warning = "大盘震荡，只做强信号标的"
        elif current < ma20:
            regime = "bear"
            trend = "大盘破位下行，不宜追多"
            suitable = False
            warning = "大盘空头，建议观望等待企稳"
        else:
            regime = "choppy"
            trend = "大盘方向不明"
            suitable = False
            warning = "大盘方向不清，观望为主"

        # 涨跌幅
        chg_5d = (closes[-1] / closes[-6] - 1) * 100 if len(closes) > 5 else 0
        chg_20d = (closes[-1] / closes[-21] - 1) * 100 if len(closes) > 20 else 0

        return {
            "regime": regime,
            "trend": trend,
            "suitable": suitable,
            "warning": warning,
            "index_price": current,
            "ma5": round(ma5, 2),
            "ma10": round(ma10, 2),
            "ma20": round(ma20, 2),
            "chg_5d": round(chg_5d, 2),
            "chg_20d": round(chg_20d, 2),
            "macd": macd,
        }
    except Exception as e:
        return {"regime": "unknown", "trend": f"获取失败: {e}", "suitable": True, "warning": "大盘数据异常，自行判断"}


def fetch_price_text(code: str) -> str:
    """获取股票的完整行情+技术分析数据文本。"""
    if not code or len(str(code).strip()) < 6:
        return f"无效的股票代码: {code}"
    code = str(code).strip()
    parts = []

    # 大盘环境（回测验证：大盘下跌时个股信号大幅失效）
    try:
        market = get_market_regime()
        if market.get("regime") != "unknown":
            parts.append(
                f"【大盘环境】{market.get('trend', '')}\n"
                f"  沪深300: {market.get('index_price', 0)}  "
                f"MA5={market.get('ma5', 0)} MA10={market.get('ma10', 0)} MA20={market.get('ma20', 0)}\n"
                f"  近5日涨跌: {market.get('chg_5d', 0)}%  近20日: {market.get('chg_20d', 0)}%"
                + ("\n  [!] " + str(market.get("warning", "")) if market.get("warning") else "")
            )
    except Exception:
        pass

    prefix = _tencent_prefix(code)

    # 1) 实时行情
    try:
        text = _http_get("qt.gtimg.cn", f"/q={prefix}", encoding="gbk", https=False)
        raw = text.split('"')[1] if '"' in text else ""
        fields = raw.split("~")
        if len(fields) > 40:
            parts.append(
                f"【实时行情】{fields[1]}（{fields[2]}）\n"
                f"日期: {fields[30]}  最新价: {fields[3]}元\n"
                f"涨跌额: {fields[31]}  涨跌幅: {fields[32]}%\n"
                f"今开: {fields[5]}  最高: {fields[33]}  最低: {fields[34]}\n"
                f"昨收: {fields[4]}  成交量(手): {fields[36]}  成交额(万): {fields[37]}\n"
                f"换手率: {fields[38]}%  市盈率(动): {fields[39]}\n"
                f"总市值(亿): {fields[45]}  流通市值(亿): {fields[44]}"
            )
        else:
            parts.append(f"实时行情获取异常：字段不足({len(fields)}个)")
    except Exception as e:
        parts.append(f"实时行情获取失败: {e}")

    # 2) K线+技术指标
    klines = []
    try:
        text = _http_get("web.ifzq.gtimg.cn", f"/appstock/app/fqkline/get?param={prefix},day,,,120,qfq", https=True)
        data = json.loads(text)
        stock_data = data.get("data", {}).get(prefix, {})
        raw_klines = stock_data.get("qfqday", []) or stock_data.get("day", [])
        if isinstance(raw_klines, dict):
            raw_klines = list(raw_klines.values())
        for k in raw_klines:
            if len(k) >= 6:
                klines.append({"date": k[0], "open": float(k[1]), "close": float(k[2]),
                               "high": float(k[3]), "low": float(k[4]), "vol": float(k[5])})
    except Exception as e:
        parts.append(f"K线数据获取失败: {e}")

    if not klines:
        return "\n".join(parts) if parts else f"股票 {code} 数据获取失败。"

    closes = [k["close"] for k in klines]
    ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else 0
    ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else 0
    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else 0
    ma60 = sum(closes) / len(closes) if closes else 0

    last = klines[-1]
    parts.append(f"\n【最新K线】{last['date']}  开:{last['open']}  高:{last['high']}  低:{last['low']}  收:{last['close']}  量:{last['vol']:.0f}")
    parts.append(f"【均线】MA5={ma5:.2f}  MA10={ma10:.2f}  MA20={ma20:.2f}  MA60={ma60:.2f}")
    if ma5 > ma10 > ma20: parts.append("均线排列: 多头排列（MA5>MA10>MA20）")
    elif ma5 < ma10 < ma20: parts.append("均线排列: 空头排列（MA5<MA10<MA20）")
    else: parts.append("均线排列: 交叉/震荡")

    macd = _calc_macd(closes)
    parts.append(f"【MACD(12,26,9)】DIF={macd['dif']}  DEA={macd['dea']}  MACD柱={macd['macd']}  状态: {macd['trend']}" + (f"  {macd['cross']}" if macd.get('cross') else ""))

    rsi = _calc_rsi(closes, 14)
    parts.append(f"【RSI】RSI6={rsi['rsi6']}  RSI14={rsi['rsi14']}  区域: {rsi['zone']}")

    kdj = _calc_kdj(klines)
    parts.append(f"【KDJ(9,3,3)】K={kdj['k']}  D={kdj['d']}  J={kdj['j']}  区域: {kdj['zone']}" + (f"  {kdj['cross']}" if kdj.get('cross') else ""))

    boll = _calc_boll(closes, 20)
    parts.append(f"【布林带(20)】上轨={boll['upper']}  中轨={boll['mid']}  下轨={boll['lower']}  带宽={boll['bandwidth']}%  价格位置: {boll['position']}")

    vol_info = _calc_volume_analysis(klines)
    if vol_info:
        parts.append(
            f"【成交量】最新量={vol_info['latest_vol']:.0f}  5日均量={vol_info['avg5']:.0f}  10日均量={vol_info['avg10']:.0f}  20日均量={vol_info['avg20']:.0f}\n"
            f"  量比(vs5日)={vol_info['vol_ratio_vs_5d']}  量比(vs20日)={vol_info['vol_ratio_vs_20d']}  状态: {vol_info['status']}"
            + (f"  连续放量{vol_info['consec_vol_increase']}日" if vol_info.get('consec_vol_increase', 0) > 1 else "")
        )

    levels = _calc_price_levels(klines)
    if levels:
        parts.append(
            f"\n【关键价位与支撑压力】\n  当前价: {levels['current_price']}元\n"
            f"  5日高低: {levels['low_5d']} ~ {levels['high_5d']}\n"
            f"  10日高低: {levels['low_10d']} ~ {levels['high_10d']}\n"
            f"  20日高低: {levels['low_20d']} ~ {levels['high_20d']}\n"
            f"  60日高低: {levels['low_60d']} ~ {levels['high_60d']}\n"
            f"  14日ATR(波动): {levels['atr_14']}元"
        )
        ma_sup = levels.get("ma_support", [])
        ma_res = levels.get("ma_resistance", [])
        if ma_sup: parts.append("  均线支撑: " + ", ".join(f"{l}={v}元" for l, v in ma_sup))
        if ma_res: parts.append("  均线压力: " + ", ".join(f"{l}={v}元" for l, v in ma_res))
        p_sup = levels.get("pivot_support", [])
        p_res = levels.get("pivot_resistance", [])
        if p_sup: parts.append("  水平支撑位: " + ", ".join(str(s) + "元" for s in p_sup))
        if p_res: parts.append("  水平压力位: " + ", ".join(str(r) + "元" for r in p_res))

    recent = klines[-10:]
    lines = [f"  {p['date']} O:{p['open']} H:{p['high']} L:{p['low']} C:{p['close']} V:{p['vol']:.0f}" for p in recent]
    parts.append("\n【近10日K线明细】\n" + "\n".join(lines))

    return "\n".join(parts)



def get_historical_klines(code: str, days: int = 200) -> List[Dict]:
    """获取历史日K线数据（前复权）。"""
    if not code or len(code) < 6:
        return []
    prefix = _tencent_prefix(code)
    try:
        text = _http_get("web.ifzq.gtimg.cn",
                         f"/appstock/app/fqkline/get?param={prefix},day,,,{days},qfq",
                         https=True)
        data = json.loads(text)
        stock_data = data.get("data", {}).get(prefix, {})
        raw = stock_data.get("qfqday", []) or stock_data.get("day", [])
        if isinstance(raw, dict):
            raw = list(raw.values())
        klines = []
        for k in raw:
            if len(k) >= 6:
                klines.append({
                    "date": k[0],
                    "open": float(k[1]),
                    "close": float(k[2]),
                    "high": float(k[3]),
                    "low": float(k[4]),
                    "vol": float(k[5]),
                })
        return klines
    except Exception:
        return []
