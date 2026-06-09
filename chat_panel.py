from __future__ import annotations

import threading
from typing import Any, Dict, List
from tkinter import scrolledtext

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from llm_client import chat_analyze_stock


class ChatFrame(ttk.Frame):
    """基于候选股票报告的交互式AI分析面板。"""

    def __init__(self, master, cfg: dict):
        super().__init__(master, padding=10)
        self.cfg = cfg
        self.final_data: Dict[str, Any] = {}
        self.reports_data: List[Dict[str, Any]] = []
        self.history: List[Dict[str, str]] = []
        self._build()

    def _build(self):
        row1 = ttk.Frame(self)
        row1.pack(fill=X, pady=(0, 6))
        ttk.Label(row1, text="选择标的：", font=("Microsoft YaHei UI", 10, "bold")).pack(side=LEFT)
        self.combo = ttk.Combobox(row1, state="readonly", width=82)
        self.combo.pack(side=LEFT, padx=8, fill=X, expand=True)
        self.combo.bind("<<ComboboxSelected>>", self._on_select)

        info_frame = ttk.Labelframe(self, text="逻辑与技术面详情", padding=8, bootstyle=INFO)
        info_frame.pack(fill=X, pady=(0, 6))
        self.info_text = scrolledtext.ScrolledText(
            info_frame,
            wrap="word",
            state="disabled",
            height=12,
            font=("Microsoft YaHei UI", 9),
            bg="#f7f9fc",
            fg="#233044",
            insertbackground="#233044",
            relief="flat",
        )
        self.info_text.pack(fill=X)
        self.info_text.tag_config("header", font=("Microsoft YaHei UI", 10, "bold"), foreground="#2f80ed")
        self.info_text.tag_config("price", foreground="#cf3f3f", font=("Microsoft YaHei UI", 9, "bold"))
        self.info_text.tag_config("support", foreground="#1f9d55")
        self.info_text.tag_config("resist", foreground="#cf3f3f")
        self.info_text.tag_config("label", foreground="#667085", font=("Microsoft YaHei UI", 9, "bold"))

        chat_frame = ttk.Labelframe(self, text="AI对话分析", padding=8, bootstyle=PRIMARY)
        chat_frame.pack(fill=BOTH, expand=True, pady=(0, 6))
        self.chat_area = scrolledtext.ScrolledText(
            chat_frame,
            wrap="word",
            state="disabled",
            font=("Microsoft YaHei UI", 10),
            height=12,
            bg="#f7f9fc",
            fg="#233044",
            insertbackground="#233044",
            relief="flat",
        )
        self.chat_area.pack(fill=BOTH, expand=True)
        self.chat_area.tag_config("user", foreground="#2f80ed", font=("Microsoft YaHei UI", 10, "bold"))
        self.chat_area.tag_config("assistant", foreground="#1f7a4d")
        self.chat_area.tag_config("system", foreground="#667085", font=("Microsoft YaHei UI", 9, "italic"))

        quick = ttk.Frame(self)
        quick.pack(fill=X, pady=(0, 6))
        ttk.Label(quick, text="快捷提问：", bootstyle=SECONDARY).pack(side=LEFT)
        for question in [
            "为什么它排在这个位置？",
            "新闻逻辑是否足够强？",
            "支撑和压力位怎么用？",
            "买入观察和止损怎么设置？",
            "未来3天最大风险是什么？",
        ]:
            ttk.Button(quick, text=question, bootstyle=OUTLINE, command=lambda q=question: self._ask_quick(q)).pack(side=LEFT, padx=3)

        bottom = ttk.Frame(self)
        bottom.pack(fill=X)
        self.entry = ttk.Entry(bottom, font=("Microsoft YaHei UI", 10))
        self.entry.pack(side=LEFT, fill=X, expand=True, padx=(0, 6))
        self.entry.bind("<Return>", self._on_send)
        self.btn_send = ttk.Button(bottom, text="发送", bootstyle=PRIMARY, command=self._on_send)
        self.btn_send.pack(side=LEFT)
        self.btn_clear = ttk.Button(bottom, text="清空", bootstyle=SECONDARY, command=self._clear_chat)
        self.btn_clear.pack(side=LEFT, padx=(6, 0))

    def refresh_data(self, final: Dict[str, Any], reports: list | None = None):
        self.final_data = final or {}
        self.reports_data = reports or []
        items = self.final_data.get("final_list", [])
        names = []
        for index, item in enumerate(items, 1):
            names.append(
                f"#{index} {item.get('code', '')} {item.get('name', '')} | {item.get('sector', '')} | "
                f"综合:{item.get('probability_score', '')} | 信号:{item.get('signal_strength', '')} | 价:{item.get('current_price', '')}"
            )
        self.combo["values"] = names
        if names:
            self.combo.current(0)
            self._on_select(None)
        else:
            self._set_info("暂无候选标的。")
        self._sys(f"已加载最新候选池（{len(items)}只标的）。请选择标的后提问。")

    def _get_selected_report(self) -> Dict[str, Any] | None:
        idx = self.combo.current()
        items = self.final_data.get("final_list", [])
        if idx < 0 or idx >= len(items):
            return None
        selected = dict(items[idx])
        code = str(selected.get("code", "")).strip()
        for report in self.reports_data:
            if str(report.get("code", "")).strip() == code:
                merged = dict(report)
                merged.update({k: v for k, v in selected.items() if v not in (None, "", [], {})})
                return merged
        return selected

    def _on_select(self, _event):
        report = self._get_selected_report()
        if not report:
            self._set_info("请选择标的。")
            return
        self._render_report(report)
        self.history = []
        self._clear_display()
        self._sys(f"已切换到 {report.get('code')} {report.get('name')}，可开始提问。")

    def _render_report(self, report: Dict[str, Any]):
        self.info_text.configure(state="normal")
        self.info_text.delete("1.0", "end")

        code = report.get("code", "")
        name = report.get("name", "")
        sector = report.get("sector", "")
        score = report.get("probability_score", "")
        signal = report.get("signal_strength", "")
        trend = report.get("trend_stage", "")
        self.info_text.insert("end", f"{code} {name} | {sector}\n", "header")
        self.info_text.insert("end", f"综合评分：{score}  技术信号：{signal}  趋势阶段：{trend}\n\n", "price")

        for label, key in [
            ("新闻/板块逻辑", "logic_analysis"),
            ("综合理由", "reason"),
            ("技术面解释", "technical_analysis"),
            ("未来3天观察", "watch_3d"),
        ]:
            value = report.get(key, "")
            if value:
                self.info_text.insert("end", f"{label}：", "label")
                self.info_text.insert("end", f"{value}\n")

        self.info_text.insert("end", "\n关键价位：\n", "header")
        self.info_text.insert("end", f"  当前价：{report.get('current_price', '-')}\n", "price")
        self.info_text.insert("end", f"  买入观察：{report.get('entry_price', '-')}\n", "price")
        self.info_text.insert("end", f"  止损价：{report.get('stop_loss_price', '-')}\n", "resist")
        self.info_text.insert("end", f"  3日目标：{report.get('target_price_3d', '-')}\n", "price")

        self._insert_levels("支撑位", report.get("key_support") or report.get("key_support_levels"), "support")
        self._insert_levels("压力位", report.get("key_resistance") or report.get("key_resistance_levels"), "resist")

        for label, key in [
            ("均线", "ma_analysis"),
            ("MACD", "macd_analysis"),
            ("RSI", "rsi_analysis"),
            ("KDJ", "kdj_analysis"),
            ("布林带", "boll_analysis"),
            ("成交量", "volume_analysis"),
        ]:
            value = report.get(key, "")
            if value:
                self.info_text.insert("end", f"\n{label}：", "label")
                self.info_text.insert("end", f"{value}")

        risk = report.get("risk_warning") or report.get("invalidation")
        if risk:
            self.info_text.insert("end", "\n\n风险提示：", "label")
            self.info_text.insert("end", str(risk), "resist")

        self.info_text.configure(state="disabled")

    def _insert_levels(self, title: str, levels: Any, tag: str):
        if not levels:
            return
        self.info_text.insert("end", f"\n{title}：\n", tag)
        if isinstance(levels, list):
            for item in levels:
                if isinstance(item, dict):
                    text = f"  - {item.get('level', '')} {item.get('description', '')}\n"
                else:
                    text = f"  - {item}\n"
                self.info_text.insert("end", text, tag)
        else:
            self.info_text.insert("end", f"  - {levels}\n", tag)

    def _set_info(self, text: str):
        self.info_text.configure(state="normal")
        self.info_text.delete("1.0", "end")
        self.info_text.insert("end", text)
        self.info_text.configure(state="disabled")

    def _append(self, role: str, text: str):
        self.chat_area.configure(state="normal")
        tag = {"user": "user", "assistant": "assistant"}.get(role, "system")
        prefix = {"user": "你", "assistant": "AI"}.get(role, "系统")
        self.chat_area.insert("end", f"{prefix}：{text}\n\n", tag)
        self.chat_area.configure(state="disabled")
        self.chat_area.see("end")

    def _sys(self, text: str):
        self._append("system", text)

    def _clear_display(self):
        self.chat_area.configure(state="normal")
        self.chat_area.delete("1.0", "end")
        self.chat_area.configure(state="disabled")

    def _clear_chat(self):
        self.history = []
        self._clear_display()
        self._sys("对话已清空。")

    def _ask_quick(self, question: str):
        self.entry.delete(0, "end")
        self.entry.insert(0, question)
        self._do_send()

    def _on_send(self, _event=None):
        self._do_send()

    def _do_send(self):
        question = self.entry.get().strip()
        if not question:
            return
        self.entry.delete(0, "end")
        self._append("user", question)
        self.history.append({"role": "user", "content": question})

        report = self._get_selected_report()
        if not report:
            self._sys("请先运行筛选，再选择标的。")
            return

        self.btn_send.configure(state="disabled")

        def worker():
            try:
                answer = chat_analyze_stock(report, self.history, self.cfg)
                self.history.append({"role": "assistant", "content": answer})
                self.after(0, self._append, "assistant", answer)
            except Exception as exc:
                self.after(0, self._sys, f"分析失败：{exc}")
            finally:
                self.after(0, lambda: self.btn_send.configure(state="normal"))

        threading.Thread(target=worker, daemon=True).start()
