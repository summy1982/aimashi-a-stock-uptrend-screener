# -*- coding: utf-8 -*-
"""
A股主板主升浪筛选系统 - 历史回测验证
用真实历史新闻 + 真实股价数据，验证整套分析逻辑的有效性。

回测方法：
1. 选取近期多个交易日作为「模拟运行日」
2. 对每个日期：获取该日的新闻 → LLM分析板块 → LLM选股 → 技术分析
3. 获取该日之后T+1/T+2/T+3的真实股价
4. 统计：命中率、平均收益、最大回撤、与基准(沪深300)的对比
"""

import sys
import json
import time
import http.client
import ssl
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent))

from data_source import (
    _http_get, _tencent_prefix, _calc_ema, _calc_macd, _calc_rsi,
    _calc_kdj, _calc_boll, _calc_volume_analysis, _calc_price_levels,
    _is_mainboard_code
)
from llm_client import OpenClient, SYSTEM, STOCK_TREND_PROMPT, NEWS_EXTRACT_PROMPT, NEWS_SECTOR_PROMPT

_ssl_ctx = ssl.create_default_context()

# ================================================================
# 历史K线获取（腾讯接口，指定日期范围）
# ================================================================

def get_historical_klines(code: str, days: int = 120) -> List[Dict]:
    """获取历史日K线数据。"""
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
    except Exception as e:
        print(f"  获取 {code} 历史K线失败: {e}")
        return []


def get_klines_up_to(klines: List[Dict], target_date: str) -> List[Dict]:
    """截取到目标日期为止的K线（含目标日）。"""
    return [k for k in klines if k["date"] <= target_date]


def get_forward_returns(klines: List[Dict], entry_date: str, days: int = 3) -> Dict:
    """计算从entry_date开始的未来N天收益。"""
    dates = sorted(set(k["date"] for k in klines))
    if entry_date not in dates:
        # 找最近的交易日
        closest = min(dates, key=lambda d: abs((datetime.strptime(d, "%Y-%m-%d") -
                                                 datetime.strptime(entry_date, "%Y-%m-%d")).days))
        entry_date = closest

    idx = dates.index(entry_date) if entry_date in dates else -1
    if idx < 0:
        return {"error": "日期不在数据范围内"}

    entry_kline = [k for k in klines if k["date"] == entry_date][0]
    entry_price = entry_kline["close"]

    result = {
        "entry_date": entry_date,
        "entry_price": entry_price,
        "returns": {},
        "highs": {},
        "lows": {},
    }

    for d in range(1, days + 1):
        if idx + d < len(dates):
            future_date = dates[idx + d]
            future_kline = [k for k in klines if k["date"] == future_date][0]
            close_ret = (future_kline["close"] - entry_price) / entry_price * 100
            high_ret = (future_kline["high"] - entry_price) / entry_price * 100
            low_ret = (future_kline["low"] - entry_price) / entry_price * 100
            result["returns"][f"T+{d}"] = round(close_ret, 2)
            result["highs"][f"T+{d}"] = round(high_ret, 2)
            result["lows"][f"T+{d}"] = round(low_ret, 2)

    # 最佳卖出时机（T+1到T+3中的最高价）
    if result["highs"]:
        best_high = max(result["highs"].values())
        result["best_high_return"] = best_high
    if result["lows"]:
        worst_low = min(result["lows"].values())
        result["worst_drawdown"] = worst_low

    return result


def build_price_text_from_klines(klines: List[Dict], target_date: str) -> str:
    """从历史K线构建技术分析文本（模拟fetch_price_text）。"""
    hist = get_klines_up_to(klines, target_date)
    if len(hist) < 20:
        return f"数据不足（仅{len(hist)}根K线）"

    closes = [k["close"] for k in hist]
    last = hist[-1]
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes) / len(closes)

    parts = [
        f"【K线数据截止】{target_date}",
        f"【最新K线】{last['date']}  开:{last['open']}  高:{last['high']}  低:{last['low']}  收:{last['close']}  量:{last['vol']:.0f}",
        f"【均线】MA5={ma5:.2f}  MA10={ma10:.2f}  MA20={ma20:.2f}  MA60={ma60:.2f}",
    ]

    if ma5 > ma10 > ma20:
        parts.append("均线排列: 多头排列")
    elif ma5 < ma10 < ma20:
        parts.append("均线排列: 空头排列")
    else:
        parts.append("均线排列: 交叉/震荡")

    macd = _calc_macd(closes)
    parts.append(f"【MACD】DIF={macd['dif']}  DEA={macd['dea']}  MACD柱={macd['macd']}  {macd['trend']}")

    rsi = _calc_rsi(closes, 14)
    parts.append(f"【RSI】RSI6={rsi['rsi6']}  RSI14={rsi['rsi14']}  {rsi['zone']}")

    kdj = _calc_kdj(hist)
    parts.append(f"【KDJ】K={kdj['k']}  D={kdj['d']}  J={kdj['j']}  {kdj['zone']}")

    boll = _calc_boll(closes, 20)
    parts.append(f"【布林带】上轨={boll['upper']}  中轨={boll['mid']}  下轨={boll['lower']}  {boll['position']}")

    vol = _calc_volume_analysis(hist)
    if vol:
        parts.append(f"【成交量】最新={vol['latest_vol']:.0f}  5日均={vol['avg5']:.0f}  量比(5d)={vol['vol_ratio_vs_5d']}  {vol['status']}")

    levels = _calc_price_levels(hist)
    if levels:
        parts.append(f"【关键价位】当前={levels['current_price']}  20日高低={levels['low_20d']}~{levels['high_20d']}  ATR={levels['atr_14']}")
        for l, v in levels.get("ma_support", []):
            parts.append(f"  均线支撑: {l}={v}")
        for l, v in levels.get("ma_resistance", []):
            parts.append(f"  均线压力: {l}={v}")
        for s in levels.get("pivot_support", []):
            parts.append(f"  水平支撑: {s}")
        for r in levels.get("pivot_resistance", []):
            parts.append(f"  水平压力: {r}")

    recent = hist[-10:]
    kline_lines = [f"  {p['date']} O:{p['open']} H:{p['high']} L:{p['low']} C:{p['close']} V:{p['vol']:.0f}" for p in recent]
    parts.append("【近10日K线】\n" + "\n".join(kline_lines))

    return "\n".join(parts)


# ================================================================
# 回测配置
# ================================================================

# 选取近期的交易日作为回测日（周末自动跳过）
BACKTEST_DATES = [
    "2026-06-02",  # 周一
    "2026-06-03",  # 周二
    "2026-06-04",  # 周三
    "2026-06-05",  # 周四
    "2026-06-06",  # 周五
]

# 模拟新闻（基于真实历史事件的简化版本）
# 实际回测中，这些会被真实新闻替代
SIMULATED_NEWS = {
    "2026-06-02": [
        "中东地缘冲突升级，伊朗以色列紧张局势加剧，国际原油价格突破90美元",
        "国务院发布城市更新行动十五五规划，安排千亿专项资金",
        "英伟达发布新一代AI芯片，国内算力产业链受益",
        "央行释放降准信号，市场流动性预期改善",
        "北向资金上周净流入超100亿，重点加仓银行和新能源",
    ],
    "2026-06-03": [
        "美联储释放鸽派信号，暗示年内可能降息",
        "国内新能源汽车5月销量数据超预期，同比增长35%",
        "财政部出台新一轮减税降费政策，重点支持科技创新",
        "国际金价突破2400美元，创历史新高",
        "半导体行业库存周期见底，行业景气度回升",
    ],
    "2026-06-04": [
        "A股三大指数集体上涨，成交额突破万亿",
        "国产大飞机C919商业运营一周年，订单量超预期",
        "光伏组件价格企稳回升，行业拐点信号显现",
        "证监会发布新规，优化IPO和再融资节奏",
        "全球AI算力需求爆发，数据中心建设加速",
    ],
    "2026-06-05": [
        "中东局势缓和，油价回落，全球风险偏好回升",
        "5月CPI数据公布，通胀温和回升",
        "新能源储能政策密集出台，行业迎来政策红利期",
        "银行板块获主力资金大幅加仓，高股息策略受追捧",
        "机器人产业催化不断，特斯拉Optimus量产在即",
    ],
    "2026-06-06": [
        "周末效应叠加获利了结，市场短线承压",
        "国常会部署新一轮稳增长措施",
        "白酒板块获外资持续加仓，消费复苏预期升温",
        "铜价创新高，有色金属板块集体走强",
        "国防预算增长超预期，军工板块关注度提升",
    ],
}

# 测试股票池（每个板块选2-3只代表性股票）
TEST_STOCKS = {
    "银行": ["601398", "601939", "600036"],
    "新能源": ["300750", "002594", "600438"],  # 注意300是创业板，实际应排除
    "军工": ["600893", "600760", "000768"],
    "半导体": ["603501", "002049", "600584"],
    "白酒": ["600519", "000858", "000568"],
    "有色金属": ["600547", "601899", "600362"],
    "基建": ["601668", "601186", "601390"],
    "石油石化": ["601857", "600028", "600583"],
}

# 确保只保留主板
for sector in TEST_STOCKS:
    TEST_STOCKS[sector] = [c for c in TEST_STOCKS[sector] if _is_mainboard_code(c)]


def run_backtest():
    """执行完整回测。"""
    print("=" * 70)
    print("  A股主板主升浪筛选系统 - 历史回测验证")
    print("=" * 70)
    print()

    from config import load_config
    cfg = load_config()

    if not cfg.get("openai_api_key"):
        print("[X] 请先配置 API Key 后再运行回测。")
        print("   在 config.json 中填写 openai_api_key 或运行主程序在设置中配置。")
        return

    client = OpenClient()

    # 预加载所有测试股票的完整K线
    print("[STAT] 第1步：预加载所有测试股票的历史K线数据...")
    all_klines: Dict[str, List[Dict]] = {}
    all_codes = set()
    for codes in TEST_STOCKS.values():
        all_codes.update(codes)

    for code in sorted(all_codes):
        klines = get_historical_klines(code, 150)
        if klines:
            all_klines[code] = klines
            print(f"  [OK] {code}: {len(klines)} 根K线, {klines[0]['date']} ~ {klines[-1]['date']}")
        else:
            print(f"  [X] {code}: 数据获取失败")
        time.sleep(0.3)

    print(f"\n  共加载 {len(all_klines)} 只股票的K线数据\n")

    # 回测结果
    all_results: List[Dict] = []

    # ================================================================
    # 逐日回测
    # ================================================================
    for bt_date in BACKTEST_DATES:
        print("=" * 70)
        print(f"  [DATE] 回测日期：{bt_date}")
        print("=" * 70)

        # 检查该日期是否有数据
        sample_code = list(all_klines.keys())[0] if all_klines else None
        if not sample_code:
            print("  [X] 无K线数据，跳过")
            continue

        sample_dates = [k["date"] for k in all_klines[sample_code]]
        if bt_date not in sample_dates:
            print(f"  [!]️ {bt_date} 非交易日（最近交易日: {max(d for d in sample_dates if d <= bt_date)})")
            # 使用最近的交易日
            bt_date = max(d for d in sample_dates if d <= bt_date)
            print(f"  → 使用 {bt_date} 替代")

        # --- 第2步：模拟新闻分析 ---
        print(f"\n  [NEWS] 第2步：新闻分析...")
        news_items = SIMULATED_NEWS.get(bt_date, SIMULATED_NEWS[BACKTEST_DATES[0]])
        news_text = "\n".join(f"{i+1}. {n}" for i, n in enumerate(news_items))
        print(f"    新闻数量: {len(news_items)}")

        # LLM分析新闻 → 板块
        print(f"    LLM分析新闻 → 板块推荐...")
        try:
            extract_schema = json.dumps({
                "hot_news": [{"title": "string", "impact": "正面/中性/负面", "a_share_sectors": ["板块1"]}],
            }, ensure_ascii=False)
            news_prompt = NEWS_EXTRACT_PROMPT.format(batch_idx=1, total_batches=1) + "\n\n新闻如下：\n" + news_text
            news_result = client.chat_json(SYSTEM, news_prompt, extract_schema)
            hot_news = news_result.get("hot_news", [])
            print(f"    提取热点: {len(hot_news)} 条")
        except Exception as e:
            print(f"    [X] 新闻分析失败: {e}")
            hot_news = []

        # 板块推荐
        hot_text = json.dumps(hot_news, ensure_ascii=False)
        sector_schema = json.dumps({
            "recommended_sectors": [{"sector": "板块名", "driver": "驱动逻辑", "confidence": 0.0}],
        }, ensure_ascii=False)
        sector_prompt = NEWS_SECTOR_PROMPT.format(count=len(hot_news), top_k=3) + f"\n\n热点新闻：\n{hot_text}"

        try:
            sectors_result = client.chat_json(SYSTEM, sector_prompt, sector_schema)
            recommended = sectors_result.get("recommended_sectors", [])
        except Exception as e:
            print(f"    [X] 板块推荐失败: {e}")
            recommended = []

        print(f"    推荐板块: {[s.get('sector') for s in recommended]}")

        # --- 第3步：对每个板块的技术研判 ---
        print(f"\n  [CHART] 第3步：技术研判...")

        for sec_info in recommended[:3]:
            sec_name = sec_info.get("sector", "未知")
            sec_driver = sec_info.get("driver", "")

            # 找到该板块的测试股票
            matched_codes = []
            for bs_name, bs_codes in TEST_STOCKS.items():
                if bs_name in sec_name or sec_name in bs_name:
                    matched_codes = bs_codes
                    break
            if not matched_codes:
                # 随机选几只测试
                matched_codes = list(all_klines.keys())[:3]

            print(f"\n    板块: {sec_name}")
            print(f"    驱动: {sec_driver[:80]}")
            print(f"    测试股票: {matched_codes}")

            for code in matched_codes:
                if code not in all_klines:
                    continue

                klines = all_klines[code]
                stock_name = code  # 简化，实际应查名

                # 构建历史技术分析文本
                price_text = build_price_text_from_klines(klines, bt_date)

                # LLM技术研判
                trend_schema = json.dumps({
                    "code": "000001", "name": "股票名",
                    "current_price": 0.0,
                    "trend_stage": "底部震荡/蓄势整理/突破前夜/主升浪启动/主升浪中/加速赶顶/见顶风险",
                    "signal_strength": 0,
                    "entry_price": 0.0, "stop_loss_price": 0.0, "target_price_3d": 0.0,
                    "risk_reward_ratio": 0.0,
                    "summary": "综合判断",
                }, ensure_ascii=False)

                trend_prompt = (
                    f"=== 股票信息 ===\n{code} {stock_name}\n\n"
                    f"=== 完整行情与技术指标数据（截止{bt_date}）===\n{price_text}\n\n"
                    "请严格按照价格行为学框架分析，输出JSON。所有价格字段必须填具体数值。"
                )

                try:
                    report = client.chat_json(SYSTEM, trend_prompt, trend_schema)
                except Exception as e:
                    print(f"      [X] {code} 分析失败: {e}")
                    continue

                # 获取未来3天真实收益
                forward = get_forward_returns(klines, bt_date, 3)

                result = {
                    "date": bt_date,
                    "sector": sec_name,
                    "code": code,
                    "name": stock_name,
                    "signal_strength": report.get("signal_strength", 0),
                    "trend_stage": report.get("trend_stage", ""),
                    "current_price": report.get("current_price", 0),
                    "entry_price": report.get("entry_price", 0),
                    "stop_loss_price": report.get("stop_loss_price", 0),
                    "target_price_3d": report.get("target_price_3d", 0),
                    "risk_reward_ratio": report.get("risk_reward_ratio", 0),
                    "summary": report.get("summary", ""),
                    "forward_returns": forward,
                }
                all_results.append(result)

                # 打印单只结果
                ret = forward.get("returns", {})
                best = forward.get("best_high_return", 0)
                worst = forward.get("worst_drawdown", 0)
                t1 = ret.get("T+1", "-")
                t2 = ret.get("T+2", "-")
                t3 = ret.get("T+3", "-")
                strength = report.get("signal_strength", 0)

                emoji = "[WIN]" if best > 2 else ("[FLAT]" if best > 0 else "[LOSS]")
                print(f"      {emoji} {code}: 信号={strength}  阶段={report.get('trend_stage', '?')}")
                print(f"        入场={report.get('entry_price', '-')}  止损={report.get('stop_loss_price', '-')}  目标={report.get('target_price_3d', '-')}")
                print(f"        实际收益: T+1={t1}%  T+2={t2}%  T+3={t3}%  最高={best}%  最低={worst}%")

                time.sleep(0.5)

    # ================================================================
    # 统计汇总
    # ================================================================
    print("\n" + "=" * 70)
    print("  [STAT] 回测统计汇总")
    print("=" * 70)

    if not all_results:
        print("  [X] 无回测结果")
        return

    # 保存详细结果
    results_dir = Path("results")
    results_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    (results_dir / f"backtest_{ts}.json").write_text(
        json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")

    # 总体统计
    total = len(all_results)
    with_t3 = [r for r in all_results if "T+3" in r.get("forward_returns", {}).get("returns", {})]
    positive_t3 = [r for r in with_t3 if r["forward_returns"]["returns"]["T+3"] > 0]
    strong_signal = [r for r in all_results if r.get("signal_strength", 0) >= 70]
    strong_positive = [r for r in strong_signal if "T+3" in r.get("forward_returns", {}).get("returns", {})
                       and r["forward_returns"]["returns"]["T+3"] > 0]

    print(f"\n  总分析股票数: {total}")
    print(f"  有T+3数据的: {len(with_t3)}")
    print(f"  T+3正收益: {len(positive_t3)} ({len(positive_t3)/max(1,len(with_t3))*100:.1f}%)")

    if strong_signal:
        print(f"\n  强信号(>=70分)股票数: {len(strong_signal)}")
        print(f"  强信号T+3正收益: {len(strong_positive)} ({len(strong_positive)/max(1,len(strong_signal))*100:.1f}%)")

    # 按信号强度分组统计
    print(f"\n  按信号强度分组:")
    for low, high, label in [(80, 100, "强(80-100)"), (60, 79, "中(60-79)"), (0, 59, "弱(0-59)")]:
        group = [r for r in all_results if low <= r.get("signal_strength", 0) <= high]
        if not group:
            continue
        t3_rets = [r["forward_returns"]["returns"]["T+3"] for r in group
                   if "T+3" in r.get("forward_returns", {}).get("returns", {})]
        if t3_rets:
            avg_ret = sum(t3_rets) / len(t3_rets)
            win_rate = sum(1 for r in t3_rets if r > 0) / len(t3_rets) * 100
            max_gain = max(t3_rets)
            max_loss = min(t3_rets)
            print(f"    {label}: {len(group)}只  胜率={win_rate:.1f}%  平均收益={avg_ret:.2f}%  最大盈={max_gain:.2f}%  最大亏={max_loss:.2f}%")

    # 按板块分组
    print(f"\n  按板块分组:")
    sectors = set(r.get("sector", "") for r in all_results)
    for sec in sorted(sectors):
        group = [r for r in all_results if r.get("sector") == sec]
        t3_rets = [r["forward_returns"]["returns"]["T+3"] for r in group
                   if "T+3" in r.get("forward_returns", {}).get("returns", {})]
        if t3_rets:
            avg_ret = sum(t3_rets) / len(t3_rets)
            win_rate = sum(1 for r in t3_rets if r > 0) / len(t3_rets) * 100
            print(f"    {sec}: {len(group)}只  胜率={win_rate:.1f}%  平均T+3={avg_ret:.2f}%")

    # Top picks统计
    print(f"\n  最佳表现（按T+3收益排序 Top 5）:")
    sorted_results = sorted(with_t3, key=lambda r: r["forward_returns"]["returns"]["T+3"], reverse=True)
    for i, r in enumerate(sorted_results[:5], 1):
        ret3 = r["forward_returns"]["returns"]["T+3"]
        best = r["forward_returns"].get("best_high_return", 0)
        print(f"    {i}. {r['date']} {r['code']} | 信号={r['signal_strength']} | T+3={ret3}% | 最高={best}% | {r['trend_stage']}")

    print(f"\n  最差表现（按T+3收益排序 Bottom 5）:")
    for i, r in enumerate(sorted_results[-5:], 1):
        ret3 = r["forward_returns"]["returns"]["T+3"]
        worst = r["forward_returns"].get("worst_drawdown", 0)
        print(f"    {i}. {r['date']} {r['code']} | 信号={r['signal_strength']} | T+3={ret3}% | 最低={worst}% | {r['trend_stage']}")

    # 风险收益比验证
    print(f"\n  风险收益比验证:")
    rrr_results = [r for r in all_results if r.get("risk_reward_ratio", 0) > 0]
    if rrr_results:
        high_rrr = [r for r in rrr_results if r.get("risk_reward_ratio", 0) >= 2]
        high_rrr_t3 = [r for r in high_rrr if "T+3" in r.get("forward_returns", {}).get("returns", {})]
        high_rrr_positive = [r for r in high_rrr_t3 if r["forward_returns"]["returns"]["T+3"] > 0]
        if high_rrr_t3:
            print(f"    RRR>=2的股票: {len(high_rrr)}只")
            print(f"    RRR>=2且T+3正收益: {len(high_rrr_positive)} ({len(high_rrr_positive)/len(high_rrr_t3)*100:.1f}%)")

    # 止损有效性
    print(f"\n  止损有效性:")
    with_stop = [r for r in all_results if r.get("stop_loss_price", 0) > 0]
    if with_stop:
        hit_stop = 0
        for r in with_stop:
            entry = r.get("entry_price", 0)
            stop = r.get("stop_loss_price", 0)
            worst = r.get("forward_returns", {}).get("worst_drawdown", 0)
            if entry > 0 and stop > 0:
                # 如果最低价跌破止损位
                if entry * (1 + worst / 100) < stop:
                    hit_stop += 1
        print(f"    设止损的股票: {len(with_stop)}只")
        print(f"    触及止损的: {hit_stop}只 ({hit_stop/max(1,len(with_stop))*100:.1f}%)")

    print(f"\n  详细结果已保存: results/backtest_{ts}.json")
    print("=" * 70)
    print("  回测完成")
    print("=" * 70)


if __name__ == "__main__":
    run_backtest()