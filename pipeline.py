from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Any, Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import save_config
from llm_client import (
    analyze_news_to_sectors,
    analyze_sector_to_stocks,
    analyze_stock_trend,
    final_pick_report,
)
from data_source import get_sector_stock_pool, normalize_candidate_names, fetch_price_text, get_market_regime
from news_fetcher import fetch_all_news, news_items_to_text


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _emit(stage_cb, stage, detail, counts=None):
    if stage_cb:
        stage_cb(stage, detail, counts)


def _as_float(value, default: float = 0.0) -> float:
    try:
        if value in (None, "", [], {}):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _compact_text(*parts: Any, limit: int = 240) -> str:
    text = "；".join(str(part).strip() for part in parts if part)
    return text[:limit]

def _analyze_one_stock(code: str, name: str, sec_name: str, sec_logic: str, max_retries: int = 2) -> Dict[str, Any]:
    """分析单只股票，带重试。"""
    price_text = fetch_price_text(code)
    last_err = None
    for attempt in range(max_retries):
        try:
            report = analyze_stock_trend(code, name, price_text)
            report.setdefault("code", code)
            report.setdefault("name", name)
            report["sector"] = sec_name
            report["sector_logic"] = sec_logic
            # 兜底：确保价格字段不为0
            cp = float(report.get("current_price", 0) or 0)
            if cp <= 0:
                # 从price_text中提取当前价
                import re as _re
                m = _re.search(r'(?:现价|最新价|收盘)[：:]?\s*(\d+\.?\d*)', price_text)
                if m:
                    cp = float(m.group(1))
                    report["current_price"] = cp
            if cp > 0:
                if not report.get("entry_price") or report["entry_price"] in (0, 0.0):
                    report["entry_price"] = cp
                atr_val = float(report.get("atr", 0) or 0) or cp * 0.02
                if not report.get("stop_loss_price") or report["stop_loss_price"] in (0, 0.0):
                    report["stop_loss_price"] = round(cp - atr_val * 1.5, 2)
                if not report.get("target_price_3d") or report["target_price_3d"] in (0, 0.0):
                    report["target_price_3d"] = round(cp + atr_val * 2, 2)
            return report
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                time.sleep(1)
    # 重试全部失败
    return {
        "code": code, "name": name,
        "sector": sec_name, "sector_logic": sec_logic,
        "summary": f"分析失败（重试{max_retries}次）：{last_err}",
        "trend_stage": "分析失败", "signal_strength": 0,
    }


def run_pipeline(cfg: dict, stage_cb=None) -> Dict[str, Any]:
    results_dir = Path(cfg.get("results_dir", "results"))
    results_dir.mkdir(parents=True, exist_ok=True)

    top_sectors = int(cfg.get("top_sectors", 5))
    top_stocks = int(cfg.get("top_stocks", 20))
    min_per_sector = max(2, min(5, int(cfg.get("min_per_sector", 2))))
    default_per_sector = max(min_per_sector, (top_stocks + max(1, top_sectors) - 1) // max(1, top_sectors))
    max_per_sector = max(min_per_sector, min(5, int(cfg.get("max_per_sector", default_per_sector))))
    stock_workers = int(cfg.get("stock_workers", 4))  # 个股分析并发数

    if not cfg.get("openai_api_key"):
        raise ValueError("未配置 openai_api_key，请在设置里填写后重试。")

    # ================================================================
    # 阶段1：联网新闻抓取
    # ================================================================
    _emit(stage_cb, "news_fetching", "=" * 60)
    _emit(stage_cb, "news_fetching", "【阶段1】开始联网抓取多源新闻...")
    limit_per_source = int(cfg.get("news_per_source", 40))
    max_workers = int(cfg.get("news_workers", 8))
    total_limit = int(cfg.get("news_total_limit", 3000))
    _emit(stage_cb, "news_fetching", f"  参数：每源{limit_per_source}条、并发{max_workers}、总上限{total_limit}条")

    items = fetch_all_news(limit_per_source=limit_per_source, max_workers=max_workers, total_limit=total_limit)
    if not items:
        _emit(stage_cb, "news_done", "  联网抓取未获取到新闻，请检查网络或源可用性。", {"news": 0, "sources": 0})
        return {"final_list": [], "all_reports": [], "disclaimer": "未获取到新闻。"}

    sources = {n.source for n in items}
    _emit(stage_cb, "news_done", f"  抓取完成：共 {len(items)} 条新闻，来自 {len(sources)} 个来源。", {"news": len(items), "sources": len(sources)})

    source_counts = {}
    for n in items:
        source_counts[n.source] = source_counts.get(n.source, 0) + 1
    for src, cnt in sorted(source_counts.items(), key=lambda x: -x[1]):
        _emit(stage_cb, "news_done", f"    - {src}: {cnt}条")

    raw = news_items_to_text(items, max_items=total_limit)

    # ================================================================
    # 阶段2：LLM分析新闻→板块
    # ================================================================
    _emit(stage_cb, "llm_sector", "=" * 60)
    _emit(stage_cb, "llm_sector", f"【阶段2】开始LLM分批分析热点新闻（总长度 {len(raw)} 字符）...")
    def _news_progress(msg):
        _emit(stage_cb, "llm_sector", msg)

    news_result = analyze_news_to_sectors(raw, top_k=top_sectors, progress_cb=_news_progress)
    recommended = news_result.get("recommended_sectors", [])
    hot_count = len(news_result.get("hot_news", []))
    _emit(stage_cb, "sector_done", f"  LLM板块分析完成，提取热点 {hot_count} 条，推荐 {len(recommended)} 个板块。", {"sectors": len(recommended)})

    for idx, sec in enumerate(recommended, start=1):
        sec_name = sec.get("sector", f"板块{idx}")
        sec_logic = sec.get("driver", "")
        conf = sec.get("confidence", 0)
        _emit(stage_cb, "sector_done", f"  [{idx}] {sec_name}  置信度:{conf}  驱动:{sec_logic[:80]}")

    # ================================================================
    # 大盘环境判断（回测验证：最大变量）
    # ================================================================
    _emit(stage_cb, "sector_done", "  检查大盘环境...")
    market = get_market_regime()
    _emit(stage_cb, "sector_done", f"  大盘环境: {market.get('trend', '未知')}")
    if market.get("warning"):
        _emit(stage_cb, "sector_done", f"  [!] {market['warning']}")
    _emit(stage_cb, "sector_done", f"  沪深300: {market.get('index_price', '-')}  5日涨跌: {market.get('chg_5d', '-')}%  20日: {market.get('chg_20d', '-')}%")

    # 如果大盘空头，减少推荐板块数
    if not market.get("suitable", True):
        _emit(stage_cb, "sector_done", f"  [!] 大盘弱势：保留每板块{min_per_sector}-{max_per_sector}只候选，但降低仓位建议并强化风控。")

    news_text = json.dumps(news_result, ensure_ascii=False, indent=2)
    all_reports: List[Dict[str, Any]] = []

    # ================================================================
    # 阶段3+4：主板候选池 + LLM筛选 + 多线程技术研判
    # ================================================================
    _emit(stage_cb, "pool_done", "=" * 60)
    _emit(stage_cb, "pool_done", f"【阶段3+4】构建候选池并多线程技术研判（并发={stock_workers}）...")

    sector_reports: Dict[str, List[Dict[str, Any]]] = {}
    total_candidates = 0

    for idx, sec in enumerate(recommended[:top_sectors], start=1):
        sec_name = sec.get("sector", f"板块{idx}")
        sec_logic = sec.get("driver", "")
        _emit(stage_cb, "pool_done", f"  [{idx}/{min(top_sectors, len(recommended))}] 获取板块「{sec_name}」成分股...")

        pool_codes = get_sector_stock_pool(sec_name)
        pool_names = normalize_candidate_names(pool_codes)
        if not pool_names:
            _emit(stage_cb, "pool_done", f"    未取到主板成分股，跳过。", {"candidates": total_candidates})
            continue

        _emit(stage_cb, "pool_done", f"    候选池: {len(pool_names)} 只主板股，开始LLM筛选...")
        pick_count = max(max_per_sector * 3, min_per_sector * 4, top_stocks)
        try:
            pick = analyze_sector_to_stocks(sec_name, sec_logic, pool_names[:200], top_k=pick_count)
        except Exception as e:
            _emit(stage_cb, "pool_done", f"    LLM筛选失败: {e}，跳过。")
            continue
        candidates = pick.get("candidates", [])
        if not candidates:
            _emit(stage_cb, "pool_done", f"    LLM未选出候选股，跳过。")
            continue
        if len(candidates) < min_per_sector:
            existing_codes = {str(c.get("code", "")).strip() for c in candidates}
            for item in pool_names:
                parts = str(item).split(maxsplit=1)
                code = parts[0].strip() if parts else ""
                if not code or code in existing_codes:
                    continue
                candidates.append({
                    "code": code,
                    "name": parts[1].strip() if len(parts) > 1 else code,
                    "reason": "候选池补充，用于保证板块内横向比较",
                })
                existing_codes.add(code)
                if len(candidates) >= min_per_sector:
                    break
        total_candidates += len(candidates)
        _emit(stage_cb, "pool_done", f"    LLM筛选: {len(candidates)} 只入选", {"candidates": total_candidates})

        # ---- 多线程技术研判 ----
        _emit(stage_cb, "stock_progress", f"  「{sec_name}」开始多线程技术研判 {len(candidates)} 只（并发={stock_workers}）...")

        sec_report_list: List[Dict[str, Any]] = []
        completed = 0

        with ThreadPoolExecutor(max_workers=stock_workers) as pool:
            future_map = {}
            for c in candidates:
                code = str(c.get("code", "")).strip()
                name = str(c.get("name", "")).strip()
                if not code:
                    continue
                fut = pool.submit(_analyze_one_stock, code, name, sec_name, sec_logic, 2)
                future_map[fut] = (code, name)

            for fut in as_completed(future_map):
                code, name = future_map[fut]
                completed += 1
                try:
                    report = fut.result()
                    trend = report.get("trend_stage", "未知")
                    strength = report.get("signal_strength", 0)
                    entry = report.get("entry_price", 0)
                    stop = report.get("stop_loss_price", 0)
                    target = report.get("target_price_3d", 0)
                    status = "✅" if strength > 0 else "⚠️"
                    _emit(stage_cb, "stock_progress",
                        f"    [{completed}/{len(future_map)}] {status} {code} {name}: "
                        f"趋势={trend}  信号={strength}  买入={entry}  止损={stop}  目标={target}"
                    )
                except Exception as e:
                    report = {
                        "code": code, "name": name, "sector": sec_name, "sector_logic": sec_logic,
                        "trend_stage": "分析失败", "signal_strength": 0, "summary": str(e),
                    }
                    _emit(stage_cb, "stock_progress", f"    [{completed}/{len(future_map)}] ❌ {code} {name}: {e}")

                all_reports.append(report)
                sec_report_list.append(report)

        # 按信号强度排序，取前 max_per_sector
        sec_report_list.sort(key=lambda x: x.get("signal_strength", 0), reverse=True)
        sector_reports[sec_name] = sec_report_list[:max_per_sector]
        top_in_sector = sector_reports[sec_name]
        if top_in_sector:
            _emit(stage_cb, "stock_progress",
                f"  「{sec_name}」板块选出 {len(top_in_sector)} 只最强: "
                + ", ".join(f"{r.get('code')}({r.get('signal_strength', 0)}分)" for r in top_in_sector)
            )

    _emit(stage_cb, "stock_done", f"  技术研判完成，累计报告 {len(all_reports)} 只。", {"candidates": total_candidates})

    # ================================================================
    # 阶段5：跨板块均衡汇总
    # ================================================================
    _emit(stage_cb, "final_done", "=" * 60)
    _emit(stage_cb, "final_done", "【阶段5】跨板块均衡汇总未来3天观察名单...")

    balanced_picks: List[Dict[str, Any]] = []
    for sec_name, reports in sector_reports.items():
        balanced_picks.extend(reports)

    balanced_picks.sort(key=lambda x: x.get("signal_strength", 0), reverse=True)

    final_limit = min(len(balanced_picks), max(top_stocks, len(sector_reports) * min_per_sector)) if balanced_picks else 0

    # 板块分散选股（用code去重）
    final_balanced: List[Dict[str, Any]] = []
    seen_codes: set = set()
    sector_count: Dict[str, int] = {}
    for r in balanced_picks:
        code = str(r.get("code", "")).strip()
        sec = r.get("sector", "未知")
        if code in seen_codes:
            continue
        cnt = sector_count.get(sec, 0)
        if cnt < max_per_sector:
            final_balanced.append(r)
            seen_codes.add(code)
            sector_count[sec] = cnt + 1
        if final_limit and len(final_balanced) >= final_limit:
            break

    # 补充不足
    if len(final_balanced) < min(top_stocks, len(balanced_picks)):
        for r in balanced_picks:
            code = str(r.get("code", "")).strip()
            if code not in seen_codes:
                final_balanced.append(r)
                seen_codes.add(code)
            if final_limit and len(final_balanced) >= final_limit:
                break

    # LLM最终综合报告
    try:
        market_info = json.dumps(market, ensure_ascii=False)
        final = final_pick_report(news_text, json.dumps(recommended, ensure_ascii=False), final_balanced,
                                   top_k=max(1, len(final_balanced)), market_info=market_info)
        for item in final.get("final_list", []):
            if "probability_score" not in item:
                label = item.get("probability_label", "中")
                item["probability_score"] = {"高": 82, "中": 62, "低": 40}.get(label, 60)
    except Exception as e:
        final = {"final_list": [], "disclaimer": f"汇总失败：{e}"}

    final["all_reports"] = all_reports
    final["_market"] = market

    # ================================================================
    # 兜底逻辑：如果LLM返回空列表或全零数据，从技术报告中直接构建
    # ================================================================
    final_list = final.get("final_list", [])
    need_fallback = False
    if not final_list:
        need_fallback = True
    elif len(final_list) < min(len(final_balanced), max(1, len(sector_reports) * min_per_sector)):
        need_fallback = True
    elif all(item.get("entry_price", 0) in (0, 0.0, None, "") for item in final_list):
        need_fallback = True

    if need_fallback and all_reports:
        _emit(stage_cb, "final_done", "  LLM汇总结果不完整，启动兜底策略：从技术报告中直接筛选...")
        # 从所有报告中筛选有技术数据的标的
        fallback_source = final_balanced or all_reports
        viable = [r for r in fallback_source
                  if r.get("trend_stage") not in ("分析失败", "数据不足", "")
                  and _as_float(r.get("current_price"), 0.0) > 0]
        viable.sort(key=lambda x: x.get("signal_strength", 0), reverse=True)

        # 跨板块均衡选取
        fallback_list = []
        seen = set()
        sec_cnt = {}
        for r in viable:
            code = str(r.get("code", "")).strip()
            sec = r.get("sector", "未知")
            if code in seen:
                continue
            cnt = sec_cnt.get(sec, 0)
            if cnt < max_per_sector:
                # 计算缺失的价格字段
                price = _as_float(r.get("current_price"), 0.0)
                atr = _as_float(r.get("atr"), price * 0.02)
                entry = _as_float(r.get("entry_price"), price)
                stop = _as_float(r.get("stop_loss_price"), round(price - atr * 1.5, 2))
                target = _as_float(r.get("target_price_3d"), round(price + atr * 2, 2))
                rrr = round((target - entry) / max(0.01, entry - stop), 2) if entry > stop else 1.5

                fallback_list.append({
                    "code": r.get("code"),
                    "name": r.get("name"),
                    "sector": sec,
                    "sector_logic": r.get("sector_logic", ""),
                    "current_price": price,
                    "reason": _compact_text(f"新闻逻辑：{r.get('sector_logic', '')}", f"技术面：{r.get('trend_stage', '')} {r.get('signal_description', '')} {r.get('summary', '')}", limit=360),
                    "logic_analysis": r.get("sector_logic", ""),
                    "technical_analysis": _compact_text(r.get("trend_stage", ""), r.get("signal_description", ""), r.get("summary", ""), limit=300),
                    "probability_label": "中" if r.get("signal_strength", 0) >= 60 else "低",
                    "probability_score": max(40, min(75, r.get("signal_strength", 0))),
                    "entry_price": entry,
                    "stop_loss_price": stop,
                    "target_price_3d": target,
                    "risk_reward_ratio": rrr,
                    "key_support_levels": r.get("key_support", []),
                    "key_resistance_levels": r.get("key_resistance", []),
                    "watch_3d": r.get("summary", "")[:150] or f"观察{sec}板块资金动向",
                    "risk_warning": r.get("invalidation", ["大盘系统性风险"])[0] if isinstance(r.get("invalidation"), list) and r.get("invalidation") else "大盘系统性风险",
                    "signal_strength": r.get("signal_strength", 0),
                    "trend_stage": r.get("trend_stage", ""),
                    "signal_description": r.get("signal_description", ""),
                })
                seen.add(code)
                sec_cnt[sec] = cnt + 1
            if final_limit and len(fallback_list) >= final_limit:
                break

        if fallback_list:
            final["final_list"] = fallback_list
            final["disclaimer"] = final.get("disclaimer", "") + "（注：部分标的由技术指标直接筛选，未经LLM综合评估，仅供参考）"
            _emit(stage_cb, "final_done", f"  兜底策略选出 {len(fallback_list)} 只标的。")


    # 合并技术数据到final_list
    report_map = {}
    for r in all_reports:
        code = str(r.get("code", "")).strip()
        if code:
            report_map[code] = r

    for item in final.get("final_list", []):
        code = str(item.get("code", "")).strip()
        if code in report_map:
            r = report_map[code]
            for key in ["current_price", "trend_stage", "signal_strength", "signal_description",
                        "ma_analysis", "macd_analysis", "rsi_analysis", "kdj_analysis", "boll_analysis",
                        "volume_analysis", "key_support", "key_resistance",
                        "entry_price", "stop_loss_price", "target_price_3d",
                        "entry_watch", "invalidation"]:
                if key in r and key not in item:
                    item[key] = r[key]
                elif key in r and item.get(key) in (None, "", 0, 0.0, [], {}):
                    item[key] = r[key]

    # 板块分布统计
    final_sector_counts = {}
    for item in final.get("final_list", []):
        sec = item.get("sector", "未知")
        final_sector_counts[sec] = final_sector_counts.get(sec, 0) + 1

    ts = _ts()
    (results_dir / f"news_{ts}.json").write_text(json.dumps(news_result, ensure_ascii=False, indent=2), encoding="utf-8")
    (results_dir / f"reports_{ts}.json").write_text(json.dumps(all_reports, ensure_ascii=False, indent=2), encoding="utf-8")
    (results_dir / f"final_{ts}.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

    final_list = final.get("final_list", [])
    _emit(stage_cb, "final_done", "  最终候选池（每板块2-5只，按综合评分排序，供客户比较选择）：")
    _emit(stage_cb, "final_done", f"  板块分布: {final_sector_counts}")
    for i, item in enumerate(final_list, start=1):
        score = item.get("probability_score", "")
        entry = item.get("entry_price", "-")
        stop = item.get("stop_loss_price", "-")
        target = item.get("target_price_3d", "-")
        _emit(stage_cb, "final_done",
            f"  {i}. {item.get('code')} {item.get('name')} | {item.get('sector')} | "
            f"置信:{item.get('probability_label')}({score}) | "
            f"当前价:{item.get('current_price', '-')} | 买入观察:{entry} | 止损:{stop} | 3日目标:{target}"
        )
        _emit(stage_cb, "final_done", f"     观察要点: {item.get('watch_3d', '')}")
    _emit(stage_cb, "final_done", f"  风险提示：{final.get('disclaimer', '仅供参考，不构成投资建议。')}")

    save_config(cfg)
    return final
