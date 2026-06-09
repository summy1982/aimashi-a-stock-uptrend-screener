# -*- coding: utf-8 -*-
"""Smoke test for public project modules."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

errors = []
print("=" * 50)
print("  Aimashi smoke test")
print("=" * 50)

try:
    from config import DEFAULT, load_config, _normalize_config
    cfg = load_config()
    assert cfg.get("enable_realtime_news") is True
    assert DEFAULT.get("openai_base_url") == "https://xinyuanai666.com/v1"
    assert _normalize_config({"openai_base_url": "https://xinyuanai666.com"})["openai_base_url"] == "https://xinyuanai666.com/v1"
    assert "offline_demo" not in cfg
    print("[PASS] config defaults")
except Exception as exc:
    errors.append(f"config: {exc}")
    print(f"[FAIL] config: {exc}")

try:
    from data_source import get_sector_stock_pool, normalize_candidate_names, fetch_price_text, _is_mainboard_code
    pool = get_sector_stock_pool("\u77f3\u6cb9\u77f3\u5316")
    assert len(pool) >= 5, f"sector pool too small: {len(pool)}"
    assert len(get_sector_stock_pool("\u50a8\u80fd/\u7535\u6c60\u6280\u672f")) >= 5
    assert all(_is_mainboard_code(code) for code in pool[:20])
    names = normalize_candidate_names(["600519"])
    assert len(names) == 1 and "600519" in names[0]
    price_text = fetch_price_text("600519")
    for marker in ["MACD", "RSI", "KDJ"]:
        assert marker in price_text, f"missing {marker}"
    print("[PASS] data source and indicators")
except Exception as exc:
    errors.append(f"data_source: {exc}")
    print(f"[FAIL] data_source: {exc}")

try:
    from news_fetcher import NewsItem, news_items_to_text, _get
    item = NewsItem(title="test title", source="test_src", url="http://example.com", weight=1.5, content="hello world")
    text = news_items_to_text([item])
    assert "[W=1.50]" in text
    assert "test title" in text
    assert "hello world" in text
    assert callable(_get)
    print("[PASS] news text conversion")
except Exception as exc:
    errors.append(f"news_fetcher: {exc}")
    print(f"[FAIL] news_fetcher: {exc}")

try:
    from llm_client import _parse_json_safe, _fallback_hot_news, _fallback_sectors
    assert _parse_json_safe('{"a": 1}') == {"a": 1}
    assert _parse_json_safe('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json_safe('{"a": 1,}') == {"a": 1}
    assert _parse_json_safe('prefix {"x": 42} suffix').get("x") == 42
    assert _parse_json_safe('[{"a": 1}, {"b": 2}]')[1]["b"] == 2
    assert _fallback_hot_news("1. [W=2.00] [Test] AI chip and oil price news")
    assert _fallback_sectors("AI chip oil bank power storage", top_k=3)
    print("[PASS] llm JSON parser and fallback")
except Exception as exc:
    errors.append(f"llm_client: {exc}")
    print(f"[FAIL] llm_client: {exc}")

try:
    import gui_app, dashboard, chat_panel
    from pipeline import _emit, _ts, _pool_fallback_candidates, _local_stock_report
    _emit(None, "test", "detail")
    assert len(_ts()) == 15
    assert _pool_fallback_candidates(["600519 Test"], 1)[0]["code"] == "600519"
    local_report = _local_stock_report("600519", "Test", "白酒", "逻辑", "最新价: 100元\n收:100\nMA5=101 MA10=99 MA20=95 MA60=90\n5日高低: 96 ~ 104\n10日高低: 94 ~ 108\n20日高低: 90 ~ 112\n14日ATR(波动): 2元", "test")
    assert local_report["entry_price"] > 0 and local_report["stop_loss_price"] > 0 and local_report["target_price_3d"] > 0
    print("[PASS] GUI and pipeline imports")
except Exception as exc:
    errors.append(f"imports: {exc}")
    print(f"[FAIL] imports: {exc}")

print("=" * 50)
if errors:
    print(f"RESULT: {len(errors)} failure(s)")
    for error in errors:
        print(f"- {error}")
    sys.exit(1)
print("RESULT: ALL TESTS PASSED")
