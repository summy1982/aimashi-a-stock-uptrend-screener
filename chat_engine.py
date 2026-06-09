from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from openai import OpenAI
from config import load_config


SYSTEM = (
    "你是A股技术分析专家助手。用户会给你一只A股主板股票的技术面报告和新闻背景，"
    "你需要基于这些信息回答用户的问题。\n"
    "回答要求：\n"
    "1) 紧扣技术面数据（均线、量价、支撑压力、突破形态等）\n"
    "2) 结合新闻面逻辑解释催化剂\n"
    "3) 给出具体可操作的观察条件、买入信号、止损位\n"
    "4) 明确未来3天的走势预判和置信度\n"
    "5) 每次回答控制在300字以内，结构清晰"
)


def _extract_json_text(text: str) -> str:
    if not text:
        return "{}"
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        return m.group(0)
    return text


def _client(cfg: dict):
    if not cfg.get("openai_api_key"):
        raise ValueError("未配置 openai_api_key")
    return OpenAI(
        api_key=cfg["openai_api_key"],
        base_url=cfg["openai_base_url"],
    )


def chat_analyze_stock(report: Dict[str, Any], history: List[Dict[str, str]], cfg: dict) -> str:
    """基于股票报告和对话历史，返回LLM分析回答。"""
    c = _client(cfg)
    model = cfg.get("model", "gpt-4o")

    # 构建系统消息：注入股票报告
    report_text = json.dumps(report, ensure_ascii=False, indent=2)
    sys_msg = SYSTEM + f"\n\n当前标的报告：\n{report_text}"

    messages = [{"role": "system", "content": sys_msg}]
    # 加对话历史（最多保留最近10轮）
    for h in history[-20:]:
        messages.append(h)

    r = c.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.3,
        max_tokens=800,
    )
    return r.choices[0].message.content or "未获取到回答。"
