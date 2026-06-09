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
    from config import DEFAULT, load_config
    cfg = load_config()
    assert cfg.get("enable_realtime_news") is True
    assert DEFAULT.get("openai_base_url") == "https://xinyuanai666.com"
    assert "offline_demo" not in cfg
    print("[PASS] config defaults")
except Exception as exc:
    errors.append(f"config: {exc}")
    print(f"[FAIL] config: {exc}")

try:
    from data_source import get_sector_stock_pool, normalize_candidate_names, fetch_price_text, _is_mainboard_code
    pool = get_sector_stock_pool("\u77f3\u6cb9\u77f3\u5316")
    assert len(pool) >= 5, f"sector pool too small: {len(pool)}"
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
    from news_fetcher import NewsItem, news_items_to_text
    item = NewsItem(title="test title", source="test_src", url="http://example.com", weight=1.5, content="hello world")
    text = news_items_to_text([item])
    assert "[W=1.50]" in text
    assert "test title" in text
    assert "hello world" in text
    print("[PASS] news text conversion")
except Exception as exc:
    errors.append(f"news_fetcher: {exc}")
    print(f"[FAIL] news_fetcher: {exc}")

try:
    from llm_client import _parse_json_safe
    assert _parse_json_safe('{"a": 1}') == {"a": 1}
    assert _parse_json_safe('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json_safe('{"a": 1,}') == {"a": 1}
    assert _parse_json_safe('prefix {"x": 42} suffix').get("x") == 42
    assert _parse_json_safe('[{"a": 1}, {"b": 2}]')[1]["b"] == 2
    print("[PASS] llm JSON parser")
except Exception as exc:
    errors.append(f"llm_client: {exc}")
    print(f"[FAIL] llm_client: {exc}")

try:
    import gui_app, dashboard, chat_panel
    from pipeline import _emit, _ts
    _emit(None, "test", "detail")
    assert len(_ts()) == 15
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
