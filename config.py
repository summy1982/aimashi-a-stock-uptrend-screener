import json
import sys
from pathlib import Path

DEFAULT = {
    "openai_base_url": "https://xinyuanai666.com/v1",
    "openai_api_key": "",
    "model": "gpt-4o",
    "top_sectors": 5,
    "top_stocks": 20,
    "min_per_sector": 2,
    "max_per_sector": 5,
    "results_dir": "results",
    "enable_realtime_news": True,
    "news_per_source": 40,
    "news_workers": 12,
    "news_total_limit": 3000,
    "stock_workers": 4,
}


def _base_dir() -> Path:
    """获取exe所在目录（兼容PyInstaller打包和开发环境）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _normalize_config(data: dict) -> dict:
    data = dict(data or {})
    data["enable_realtime_news"] = True
    data.pop("offline_demo", None)
    if data.get("openai_base_url") in {"https://xinyuanai666.com", "http://xinyuanai666.com"}:
        data["openai_base_url"] = "https://xinyuanai666.com/v1"
    return {**DEFAULT, **data}


def config_path() -> Path:
    return _base_dir() / "config.json"


def load_config() -> dict:
    p = config_path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {}
    return _normalize_config(data)


def save_config(data: dict) -> None:
    p = config_path()
    out = _normalize_config(data)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
