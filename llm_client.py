from __future__ import annotations

import json
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any

from openai import OpenAI

from config import load_config


SYSTEM = """你是一位拥有20年实战经验的A股专业分析师，精通宏观政策、产业链传导、资金行为、价格行为学和技术指标。
核心原则：
1. 只分析A股主板，过滤创业板、科创板、北交所和明显不相关资产。
2. 所有输出必须是严格 JSON，不要输出 Markdown，不要输出解释性前后缀。
3. 所有价格、支撑位、压力位、止损位和目标位必须来自给定行情与技术数据，不能编造数字。
4. 新闻分析不能停留在标题表面，要判断预期差、产业链传导、资金可能的交易方向和海外热点对A股的滞后影响。
5. 大盘弱势时降低仓位和置信度，但仍要寻找结构性机会；不要因为整体弱势就机械输出空结果。
6. 候选池模式下应给出排序和充分解释，帮助用户比较选择，而不是只给一只股票。
"""

NEWS_EXTRACT_PROMPT = """你正在分析第{batch_idx}/{total_batches}批全球财经新闻。
请用“信息传导链 + 预期差 + 资金行为”的方式提炼真正可能影响A股主板的热点，不要只看标题。
重点识别：
- 新闻表面含义与更深层含义是否不同。
- 海外新闻是否会通过能源、汇率、利率、科技供应链、资源品、出口链传导到A股。
- 热点的时效性：未来3天、1周、2周哪个更可能发酵。
- 可能被资金交易的A股主板板块。

输出 hot_news 数组，每条包含：title/source/surface/hidden/fund_behavior/impact/a_share_sectors/transmission_chain/confidence。"""

NEWS_SECTOR_PROMPT = """以下是提取出的A股热点新闻（共{count}条）。
请推荐最多{top_k}个A股主板板块，重点看：
- 海外信息对A股的滞后影响。
- 国内政策与海外事件是否形成共振。
- 预期差是否仍未被市场充分定价。
- 未来3天是否可能出现资金加速。

输出 recommended_sectors 数组，每条包含：sector/driver/expectation_gap/related_news/confidence/time_horizon/risk_factors/related_main_board_keywords。"""

STOCK_TREND_PROMPT = """请以价格行为学为核心，结合 MACD / RSI / KDJ / BOLL / 均线 / ATR / 成交量，对该A股主板个股进行技术研判。
必须给出：
- 当前价 current_price。
- 趋势阶段 trend_stage：底部震荡/蓄势整理/突破前夜/主升浪启动/主升浪中/加速赶顶/见顶风险。
- 信号强度 signal_strength，0-100分。
- 趋势结构、均线、MACD、RSI、KDJ、BOLL、成交量解释。
- 明确支撑位 key_support、压力位 key_resistance。
- 买入观察价 entry_price、止损价 stop_loss_price、未来3日目标 target_price_3d。
- 风险收益比 risk_reward_ratio、入场观察条件 entry_watch、失效条件 invalidation。

所有数字必须来自给定行情和技术数据，不得填0。若结构一般，也要给出合理的观察价位和风险说明。"""


def _extract_json_text(text: str) -> str:
    if not text:
        return "{}"
    raw = text.strip()
    match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", raw)
    if match:
        raw = match.group(1).strip()

    starts = [pos for pos in (raw.find("{"), raw.find("[")) if pos >= 0]
    if not starts:
        return raw or "{}"

    start = min(starts)
    stack: list[str] = []
    in_string = False
    escaped = False
    for index in range(start, len(raw)):
        char = raw[index]
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char in "[{":
            stack.append(char)
        elif char in "]}":
            if not stack:
                break
            expected = "]" if stack[-1] == "[" else "}"
            if char != expected:
                break
            stack.pop()
            if not stack:
                return raw[start:index + 1]
    return raw[start:]


def _balance_brackets(text: str) -> str:
    opens = text.count("{")
    closes = text.count("}")
    if opens > closes:
        text += "}" * (opens - closes)
    opens = text.count("[")
    closes = text.count("]")
    if opens > closes:
        text += "]" * (opens - closes)
    return text


def _parse_json_safe(text: str) -> Any:
    extracted = _extract_json_text(text)
    candidates = [
        extracted,
        re.sub(r",\s*([}\]])", r"\1", extracted),
    ]
    candidates.append(_balance_brackets(candidates[-1]))
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception as exc:
            last_error = exc
    raise json.JSONDecodeError(str(last_error), extracted, 0)


class OpenClient:
    def __init__(self):
        cfg = load_config()
        if not cfg.get("openai_api_key"):
            raise ValueError("未配置 openai_api_key，请先在设置里填写。")
        self.c = OpenAI(
            api_key=cfg["openai_api_key"],
            base_url=cfg.get("openai_base_url") or "https://xinyuanai666.com/v1",
            timeout=120.0,
            max_retries=1,
        )
        self.model = cfg.get("model", "gpt-4o")

    def chat_json(self, system: str, user: str, schema_hint: str, max_retries: int = 2, timeout: float = 120.0) -> Any:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user + "\n\n输出必须为严格 JSON，结构参考：\n" + schema_hint},
        ]
        text = "{}"
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                kwargs = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.1 + attempt * 0.1,
                    "timeout": timeout,
                }
                try:
                    response = self.c.chat.completions.create(**kwargs, response_format={"type": "json_object"})
                except Exception as exc:
                    if "response_format" not in str(exc) and "json_object" not in str(exc):
                        raise
                    response = self.c.chat.completions.create(**kwargs)
                text = response.choices[0].message.content or "{}"
                return _parse_json_safe(text)
            except Exception as exc:
                last_error = exc
                if attempt < max_retries - 1:
                    messages.append({"role": "assistant", "content": text})
                    messages.append({"role": "user", "content": f"上次 JSON 解析或请求未成功：{exc}\n请只返回合法 JSON。"})
                    time.sleep(0.5)
        raise ValueError(f"LLM JSON 响应未完成：{last_error}")


def _client() -> OpenClient:
    return OpenClient()


def _keyword_sectors(text: str) -> list[str]:
    mapping = [
        ("人工智能", ["AI", "人工智能", "算力", "英伟达", "半导体", "芯片", "数据中心", "光模块", "CPO"]),
        ("光通信", ["光通信", "光模块", "CPO", "数据中心", "通信"]),
        ("石油石化", ["原油", "石油", "油价", "OPEC", "LNG", "天然气", "中东", "伊朗", "以色列"]),
        ("煤炭", ["煤炭", "焦煤", "动力煤", "煤价"]),
        ("有色金属", ["黄金", "铜", "铝", "锂", "稀土", "金属", "矿业"]),
        ("国防军工", ["军工", "国防", "导弹", "无人机", "地缘", "冲突", "战争"]),
        ("银行", ["银行", "利率", "降息", "央行", "金融监管", "高股息"]),
        ("电力", ["电力", "核电", "风电", "光伏", "储能", "电网", "智能电网"]),
        ("汽车", ["汽车", "新能源汽车", "电动车", "特斯拉", "智能驾驶"]),
        ("医药", ["医药", "创新药", "医疗", "药品", "医保"]),
        ("房地产", ["房地产", "地产", "房贷", "楼市", "城市更新"]),
        ("农业", ["农业", "粮食", "猪肉", "玉米", "大豆"]),
    ]
    found: list[str] = []
    upper_text = text.upper()
    for sector, keywords in mapping:
        if any(keyword.upper() in upper_text for keyword in keywords):
            found.append(sector)
    return found[:3]


def _fallback_hot_news(news_text: str, limit: int = 80) -> list[dict]:
    items = re.split(r"(?=^\d+\.\s)", news_text.strip(), flags=re.MULTILINE)
    out: list[dict] = []
    for raw in items:
        text = raw.strip()
        if not text:
            continue
        title_match = re.match(r"\d+\.\s*(?:\[W=[\d.]+\]\s*)?\[([^\]]+)\]\s*(.+)", text)
        source = title_match.group(1) if title_match else "未知"
        title = title_match.group(2).split("  时间:")[0][:120] if title_match else text[:120]
        sectors = _keyword_sectors(title + "\n" + text)
        if sectors:
            out.append({
                "title": title,
                "source": source,
                "surface": "关键词兜底识别的市场热点",
                "hidden": "模型批处理未完全返回时使用规则兜底，保留新闻驱动方向供后续板块映射。",
                "fund_behavior": "观察相关板块资金承接和量价确认。",
                "impact": "待技术面确认",
                "a_share_sectors": sectors,
                "transmission_chain": "新闻关键词 -> 产业方向 -> A股主板板块",
                "confidence": "中",
            })
        if len(out) >= limit:
            break
    return out


def _fallback_sectors(news_text: str, top_k: int = 5) -> list[dict]:
    scores: dict[str, int] = {}
    upper_text = news_text.upper()
    for sector in _keyword_sectors(news_text):
        scores[sector] = scores.get(sector, 0) + 3
    for sector, keywords in {
        "人工智能": ["AI", "人工智能", "算力", "芯片", "光模块", "CPO"],
        "石油石化": ["原油", "石油", "油价", "LNG", "中东", "OPEC"],
        "煤炭": ["煤炭", "焦煤", "动力煤"],
        "有色金属": ["黄金", "铜", "铝", "锂", "稀土", "金属"],
        "国防军工": ["军工", "地缘", "冲突", "战争", "无人机"],
        "银行": ["银行", "利率", "降息", "央行", "高股息"],
        "电力": ["电力", "储能", "智能电网", "电网", "光伏", "风电"],
        "汽车": ["汽车", "新能源汽车", "智能驾驶"],
    }.items():
        scores[sector] = scores.get(sector, 0) + sum(upper_text.count(k.upper()) for k in keywords)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:top_k]
    if not ranked:
        ranked = [("人工智能", 1), ("银行", 1), ("石油石化", 1)][:top_k]
    return [{
        "sector": sector,
        "driver": f"多源新闻规则兜底识别：{sector} 相关关键词出现频率较高，后续由技术面候选池做二次验证。",
        "expectation_gap": "模型响应较慢时的稳定兜底结果，需结合技术信号确认。",
        "related_news": [],
        "confidence": min(0.75, 0.45 + score * 0.03),
        "time_horizon": "3天-1周",
        "risk_factors": ["新闻深度分析未完全返回，使用规则兜底补足"],
        "related_main_board_keywords": [sector],
    } for sector, score in ranked]


def analyze_news_to_sectors(news_text: str, top_k: int = 5, progress_cb=None) -> Any:
    client = _client()

    def emit(message: str) -> None:
        if progress_cb:
            try:
                progress_cb(message)
            except Exception:
                pass

    news_items_raw = re.split(r"(?=^\d+\.\s)", news_text.strip(), flags=re.MULTILINE)
    news_items_raw = [item.strip() for item in news_items_raw if item.strip() and len(item.strip()) > 10]
    if not news_items_raw:
        return {"hot_news": [], "recommended_sectors": _fallback_sectors(news_text, top_k)}

    batch_size = 20
    max_batches = 8
    prioritized = sorted(news_items_raw, key=lambda item: ("[W=1." in item or "[W=2." in item, len(item)), reverse=True)
    batches = []
    for index in range(0, min(len(prioritized), batch_size * max_batches), batch_size):
        batches.append("\n".join(prioritized[index:index + batch_size])[:14000])
    skipped = max(0, len(news_items_raw) - batch_size * max_batches)
    emit(f"  新闻分为 {len(batches)} 批（每批{batch_size}条，低权重长尾{skipped}条进入规则兜底），开始稳定分析...")

    schema = json.dumps({
        "hot_news": [{
            "title": "", "source": "", "surface": "", "hidden": "", "fund_behavior": "",
            "impact": "", "a_share_sectors": [""], "transmission_chain": "", "confidence": "",
        }]
    }, ensure_ascii=False)

    def process_batch(payload):
        batch_index, batch_text = payload
        prompt = NEWS_EXTRACT_PROMPT.format(batch_idx=batch_index + 1, total_batches=len(batches)) + "\n\n新闻如下：\n" + batch_text
        try:
            result = client.chat_json(SYSTEM, prompt, schema, max_retries=1, timeout=60.0)
            return batch_index, result.get("hot_news", []), None
        except Exception as exc:
            return batch_index, [], str(exc)

    all_hot_news: list[dict] = []
    delayed_batches = 0
    max_workers = min(3, len(batches)) or 1
    pool = ThreadPoolExecutor(max_workers=max_workers)
    futures = {pool.submit(process_batch, (idx, batch)): idx for idx, batch in enumerate(batches)}
    pending = set(futures)
    deadline = time.time() + 120
    try:
        while pending and time.time() < deadline:
            done, pending = wait(pending, timeout=3, return_when=FIRST_COMPLETED)
            for future in done:
                try:
                    batch_index, hot_news, error = future.result(timeout=0)
                    if error:
                        delayed_batches += 1
                        emit(f"  批次{batch_index + 1}未及时返回，已交给规则兜底补足。")
                    else:
                        all_hot_news.extend(hot_news)
                        emit(f"  批次{batch_index + 1}/{len(batches)} 完成，提取 {len(hot_news)} 条热点")
                except Exception:
                    delayed_batches += 1
                    emit("  单个新闻批次未及时返回，已交给规则兜底补足。")
        if pending:
            delayed_batches += len(pending)
            for future in pending:
                future.cancel()
            emit(f"  {len(pending)} 个新闻批次未在时限内返回，已用规则兜底补足并继续流程。")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    fallback_hot = _fallback_hot_news(news_text, limit=80)
    if len(all_hot_news) < max(top_k * 6, 24):
        seen_titles = {str(item.get("title", "")) for item in all_hot_news}
        added = 0
        for item in fallback_hot:
            title = str(item.get("title", ""))
            if title and title not in seen_titles:
                all_hot_news.append(item)
                seen_titles.add(title)
                added += 1
            if len(all_hot_news) >= max(top_k * 8, 40):
                break
        if added:
            emit(f"  已用全量新闻规则兜底补充 {added} 条热点，避免样本过少。")
    if not all_hot_news:
        emit("  热点提取不足，启用关键词兜底分析。")
        all_hot_news = fallback_hot

    emit(f"  热点提取完成：共 {len(all_hot_news)} 条（{delayed_batches} 批由兜底补足）")
    hot_text = json.dumps(all_hot_news[:80], ensure_ascii=False, indent=2)[:24000]
    sector_schema = json.dumps({
        "recommended_sectors": [{
            "sector": "", "driver": "", "expectation_gap": "", "related_news": [""],
            "confidence": 0.0, "time_horizon": "", "risk_factors": [""], "related_main_board_keywords": [""],
        }]
    }, ensure_ascii=False)
    user = NEWS_SECTOR_PROMPT.format(count=len(all_hot_news), top_k=top_k) + f"\n\n热点新闻：\n{hot_text}"
    emit(f"  开始板块推荐分析（{len(all_hot_news)}条热点）...")
    try:
        result = client.chat_json(SYSTEM, user, sector_schema, max_retries=1, timeout=60.0)
        recommended = result.get("recommended_sectors", [])
        if not recommended:
            recommended = _fallback_sectors(news_text + "\n" + hot_text, top_k)
        emit(f"  板块推荐完成：{len(recommended)} 个板块")
    except Exception:
        emit("  板块推荐未及时返回，已启用规则兜底继续运行。")
        recommended = _fallback_sectors(news_text + "\n" + hot_text, top_k)
    return {"hot_news": all_hot_news, "recommended_sectors": recommended}


def analyze_sector_to_stocks(sector_name: str, sector_logic: str, candidate_names: list[str], top_k: int = 15) -> Any:
    client = _client()
    schema = json.dumps({
        "sector": sector_name,
        "logic": sector_logic,
        "candidates": [{"code": "000001", "name": "股票名", "reason": "", "fit_score": 0, "catalyst": ""}],
    }, ensure_ascii=False)
    user = (
        "请结合板块逻辑和候选股票名称/代码，挑选最符合该逻辑的A股主板股票。"
        "输出候选数量尽量接近上限，方便后续技术面横向比较。\n"
        f"板块：{sector_name}\n逻辑：{sector_logic[:1200]}\n候选数量上限：{top_k}\n候选列表：\n" + "\n".join(candidate_names[:160])
    )
    return client.chat_json(SYSTEM, user, schema, max_retries=1, timeout=60.0)


def analyze_stock_trend(code: str, name: str, price_text: str) -> Any:
    client = _client()
    schema = json.dumps({
        "code": code, "name": name, "current_price": 0.0,
        "trend_stage": "底部震荡/蓄势整理/突破前夜/主升浪启动/主升浪中/加速赶顶/见顶风险",
        "signal_strength": 0, "signal_description": "", "trend_structure": "",
        "ma_analysis": "", "macd_analysis": "", "rsi_analysis": "", "kdj_analysis": "", "boll_analysis": "", "volume_analysis": "",
        "key_support": [], "key_resistance": [], "entry_price": 0.0, "stop_loss_price": 0.0, "stop_loss_logic": "",
        "target_price_3d": 0.0, "risk_reward_ratio": 0.0, "entry_watch": [], "invalidation": [],
        "summary": "", "max_hold_days": 3, "scale_out_rule": "", "market_sensitivity": "", "atr": 0.0,
    }, ensure_ascii=False)
    user = STOCK_TREND_PROMPT + f"\n\n股票：{code} {name}\n技术与行情数据：\n{price_text[:12000]}"
    return client.chat_json(SYSTEM, user, schema, max_retries=1, timeout=75.0)


def final_pick_report(news_summary: str, sector_summary: str, stock_reports: list[dict], top_k: int = 8, market_info: str = "{}") -> Any:
    client = _client()
    schema = json.dumps({
        "final_list": [{
            "code": "000001", "name": "股票名", "sector": "板块", "current_price": 0.0,
            "reason": "", "logic_analysis": "", "technical_analysis": "", "probability_label": "高/中/低", "probability_score": 70,
            "entry_price": 0.0, "stop_loss_price": 0.0, "target_price_3d": 0.0, "risk_reward_ratio": 0.0,
            "key_support_levels": [], "key_resistance_levels": [], "watch_3d": "", "risk_warning": "",
        }],
        "disclaimer": "风险提示",
    }, ensure_ascii=False)
    compact_reports = []
    for report in stock_reports[: min(len(stock_reports), top_k + 8)]:
        compact_reports.append({
            "code": report.get("code"),
            "name": report.get("name"),
            "sector": report.get("sector"),
            "current_price": report.get("current_price"),
            "trend_stage": report.get("trend_stage"),
            "signal_strength": report.get("signal_strength"),
            "entry_price": report.get("entry_price"),
            "stop_loss_price": report.get("stop_loss_price"),
            "target_price_3d": report.get("target_price_3d"),
            "risk_reward_ratio": report.get("risk_reward_ratio"),
            "summary": str(report.get("summary", ""))[:260],
            "sector_logic": str(report.get("sector_logic", ""))[:260],
        })
    reports_text = json.dumps(compact_reports, ensure_ascii=False)[:18000]
    user = (
        f"请对候选池做排序和解释，输出前{top_k}只候选。\n"
        "每只都要说明新闻/板块逻辑、技术面解释、支撑压力、买入观察、止损、风险。\n"
        f"大盘环境：{market_info[:2500]}\n板块摘要：{sector_summary[:4000]}\n新闻摘要：{news_summary[:3000]}\n个股报告：{reports_text}"
    )
    return client.chat_json(SYSTEM, user, schema, max_retries=1, timeout=60.0)


def chat_analyze_stock(report: dict, history: list, cfg: dict) -> str:
    if not cfg.get("openai_api_key"):
        raise ValueError("未配置 openai_api_key")
    client = OpenAI(api_key=cfg["openai_api_key"], base_url=cfg.get("openai_base_url"), timeout=120.0, max_retries=1)
    report_text = json.dumps(report, ensure_ascii=False, indent=2)
    messages = [{
        "role": "system",
        "content": "你是一位资深A股技术分析师和交易教练。回答必须紧扣报告中的具体数据和价位，不编造数字，给出操作条件和风险。\n\n标的完整报告：\n" + report_text,
    }]
    messages.extend(history[-20:])
    response = client.chat.completions.create(
        model=cfg.get("model", "gpt-4o"),
        messages=messages,
        temperature=0.3,
        max_tokens=1500,
        timeout=120.0,
    )
    return response.choices[0].message.content or "未获取到回答。"
