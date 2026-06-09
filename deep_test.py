# -*- coding: utf-8 -*-
"""Deeper validation for live market/news helpers and parser resilience."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

errors = []
print("=" * 60)
print("  Aimashi deep validation")
print("=" * 60)

try:
    from config import load_config, save_config
    cfg = load_config()
    cfg["test_key"] = 12345
    save_config(cfg)
    cfg2 = load_config()
    assert cfg2.get("test_key") == 12345
    cfg2.pop("test_key", None)
    save_config(cfg2)
    print("[PASS] 1. config round-trip")
except Exception as exc:
    errors.append(f"config: {exc}")
    print(f"[FAIL] 1. config: {exc}")

try:
    from data_source import get_market_regime, get_sector_stock_pool, normalize_candidate_names, fetch_price_text, get_historical_klines, _is_mainboard_code
    regime = get_market_regime()
    assert isinstance(regime, dict)
    assert regime.get("regime") in {"bull", "neutral", "bear", "unknown"}
    print(f"[PASS] 2. market regime: {regime.get('regime')}")

    sectors = ["\u77f3\u6cb9\u77f3\u5316", "\u56fd\u9632\u519b\u5de5", "\u4eba\u5de5\u667a\u80fd", "\u94f6\u884c", "\u7164\u70ad"]
    for sector in sectors:
        pool = get_sector_stock_pool(sector)
        assert len(pool) >= 5, f"{sector} pool too small: {len(pool)}"
        assert all(_is_mainboard_code(code) for code in pool[:20])
    print("[PASS] 3. sector pools")

    names = normalize_candidate_names(["600519", "000001", "601857"])
    assert len(names) == 3 and all(code in names[idx] for idx, code in enumerate(["600519", "000001", "601857"]))
    print(f"[PASS] 4. name normalization: {names}")

    price_text = fetch_price_text("600519")
    for marker in ["MACD", "RSI", "KDJ"]:
        assert marker in price_text, f"missing {marker}"
    assert "600519" in price_text
    print(f"[PASS] 5. price text: {len(price_text)} chars")

    klines = get_historical_klines("600519", days=80)
    assert len(klines) >= 30, f"kline too short: {len(klines)}"
    assert {"date", "close", "high", "low"}.issubset(klines[-1].keys())
    print(f"[PASS] 6. historical klines: {len(klines)} rows")
except Exception as exc:
    errors.append(f"data_source: {exc}")
    print(f"[FAIL] data_source: {exc}")

try:
    from news_fetcher import NewsItem, news_items_to_text, fetch_sina_7x24
    text = news_items_to_text([
        NewsItem(title="AI demand", source="TestCN", url="http://x", weight=1.8, content="full article"),
        NewsItem(title="Oil rises", source="TestUS", url="http://y", weight=2.0, content="market impact"),
    ])
    assert "[W=1.80]" in text and "[W=2.00]" in text
    assert "full article" in text and "market impact" in text
    print("[PASS] 7. news text conversion")

    live_items = fetch_sina_7x24(limit=5)
    if live_items:
        print(f"[PASS] 8. live news quick check: {len(live_items)} items")
    else:
        print("[WARN] 8. live news returned empty")
except Exception as exc:
    print(f"[WARN] news live check skipped: {exc}")

try:
    from llm_client import _parse_json_safe, _fallback_sectors, _fallback_hot_news
    cases = [
        ('{"a":1}', dict),
        ('```json\n{"a":1}\n```', dict),
        ('{"a":1,}', dict),
        ('text before {"a":1} text after', dict),
        ('[{"a":1}, {"b":2}]', list),
    ]
    for raw, expected_type in cases:
        parsed = _parse_json_safe(raw)
        assert isinstance(parsed, expected_type), f"failed parser case: {raw}"
    fallback_news = _fallback_hot_news("AI chip demand. Oil price rises. Bank policy support.")
    fallback_sectors = _fallback_sectors("AI chip demand. Oil price rises. Bank policy support.", top_k=3)
    assert fallback_news and fallback_sectors
    print("[PASS] 9. LLM parser and fallback")
except Exception as exc:
    errors.append(f"llm_client: {exc}")
    print(f"[FAIL] llm_client: {exc}")

try:
    from pipeline import _analyze_one_stock, _emit, _ts
    import gui_app, dashboard, chat_panel
    _emit(None, "stage", "detail")
    assert len(_ts()) == 15
    assert callable(_analyze_one_stock)
    print("[PASS] 10. pipeline and GUI imports")
except Exception as exc:
    errors.append(f"pipeline/gui: {exc}")
    print(f"[FAIL] pipeline/gui: {exc}")

try:
    from backtest import get_historical_klines as bt_klines, build_price_text_from_klines, get_forward_returns
    klines = bt_klines("600519", days=100)
    assert len(klines) >= 30
    target_date = klines[-5]["date"]
    price_text = build_price_text_from_klines(klines, target_date)
    forward = get_forward_returns(klines, target_date, days=3)
    assert "MACD" in price_text and "RSI" in price_text
    assert "returns" in forward and "T+3" in forward["returns"]
    print("[PASS] 11. backtest helpers")
except Exception as exc:
    errors.append(f"backtest: {exc}")
    print(f"[FAIL] backtest: {exc}")

print("=" * 60)
if errors:
    print(f"RESULT: {len(errors)} failure(s)")
    for error in errors:
        print(f"- {error}")
    sys.exit(1)
print("RESULT: ALL TESTS PASSED")
