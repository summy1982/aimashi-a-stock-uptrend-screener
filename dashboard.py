from __future__ import annotations

import math
import tkinter as tk
from typing import Any, Dict, List

import ttkbootstrap as ttk
from ttkbootstrap.constants import *


class GaugeCanvas(tk.Canvas):
    """高颜值半圆仪表盘"""
    def __init__(self, master, size=200, **kw):
        super().__init__(master, width=size, height=size // 2 + 30, highlightthickness=0, bg="#1a1d23", **kw)
        self.size = size
        self.value = 0.0
        self._draw(0.0)

    def set_value(self, v: float):
        self.value = max(0.0, min(100.0, float(v)))
        self._draw(self.value)

    def _draw(self, v: float):
        self.delete("all")
        s = self.size
        cx, cy = s // 2, s // 2 - 10
        r = s // 2 - 20

        # 背景弧
        self.create_arc(cx - r, cy - r, cx + r, cy + r, start=0, extent=180,
                        style="arc", width=20, outline="#2a2e36")

        # 渐变色弧（红→黄→绿）
        extent = 180 * (v / 100.0)
        if v >= 75:
            color = "#66bb6a"
        elif v >= 50:
            color = "#ffa726"
        elif v >= 25:
            color = "#ef5350"
        else:
            color = "#b71c1c"

        if extent > 0:
            self.create_arc(cx - r, cy - r, cx + r, cy + r, start=0, extent=extent,
                            style="arc", width=20, outline=color)

        # 数值
        self.create_text(cx, cy - 20, text=f"{v:.0f}", font=("Consolas", 28, "bold"), fill=color)
        # 标签
        label = "高" if v >= 75 else ("中" if v >= 50 else "低")
        self.create_text(cx, cy + 10, text=f"综合置信度: {label}", font=("Microsoft YaHei UI", 10), fill="#78909c")


class DashboardFrame(ttk.Frame):
    def __init__(self, master, cfg: dict):
        super().__init__(master, padding=10)
        self.cfg = cfg
        self._build()

    def _build(self):
        # 顶部区域：仪表盘 + 统计卡片
        top = ttk.Frame(self)
        top.pack(fill=X, pady=(0, 8))

        # 左侧：仪表盘
        gauge_frame = ttk.Labelframe(top, text="  综合评估  ", padding=8)
        gauge_frame.pack(side=LEFT, padx=(0, 12))
        self.gauge = GaugeCanvas(gauge_frame, size=220)
        self.gauge.pack()

        # 右侧：统计卡片
        cards = ttk.Frame(top)
        cards.pack(side=LEFT, fill=BOTH, expand=True)

        self.card_labels = {}
        card_defs = [
            ("total", "入选标的", "0", "primary"),
            ("sectors", "覆盖板块", "0", "info"),
            ("high", "高置信", "0", "success"),
            ("avg_signal", "平均信号", "0", "warning"),
        ]
        for i, (key, title, default, style) in enumerate(card_defs):
            card = ttk.Labelframe(cards, text=f"  {title}  ", padding=8)
            card.grid(row=0, column=i, padx=4, sticky=EW)
            cards.columnconfigure(i, weight=1)
            lbl = ttk.Label(card, text=default, font=("Consolas", 22, "bold"), bootstyle=style)
            lbl.pack()
            self.card_labels[key] = lbl
        # 大盘环境指示器
        market_frame = ttk.Labelframe(cards, text="  大盘环境  ", padding=8)
        market_frame.grid(row=0, column=4, padx=4, sticky=EW)
        cards.columnconfigure(4, weight=1)
        self.lbl_market = ttk.Label(market_frame, text="等待分析...", font=("Microsoft YaHei UI", 10))
        self.lbl_market.pack()

        # 底部：详细表格
        table_frame = ttk.Labelframe(self, text="  板块候选池排行榜  （每板块2-5只，双击可进入AI对话深度分析）", padding=6)
        table_frame.pack(fill=BOTH, expand=True)

        columns = ("rank", "code", "name", "sector", "price", "entry", "stop", "target", "signal", "prob", "logic", "watch")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=15)

        headers = {
            "rank": ("排名", 48), "code": ("代码", 70), "name": ("名称", 90), "sector": ("板块", 90),
            "price": ("当前价", 70), "entry": ("买入观察", 80), "stop": ("止损价", 70),
            "target": ("3日目标", 80), "signal": ("信号", 55), "prob": ("置信", 55),
            "logic": ("新闻/板块逻辑", 260), "watch": ("技术面观察", 330),
        }
        for col, (text, width) in headers.items():
            self.tree.heading(col, text=text)
            anchor = CENTER if col in ("rank", "signal", "prob") else (E if col in ("price", "entry", "stop", "target") else W)
            self.tree.column(col, width=width, anchor=anchor, minwidth=40)

        scrollbar = ttk.Scrollbar(table_frame, orient=VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)

        self.tree.bind("<Double-1>", self._on_double_click)

        # 底部状态
        self.status = ttk.Label(self, text="提示：结果按综合评分排序，每个板块保留2-5只候选，双击标的进入AI深度分析。",
                                font=("Microsoft YaHei UI", 9), bootstyle=INFO)
        self.status.pack(anchor=W, pady=(4, 0))

    def _on_double_click(self, event):
        """双击表格行，切换到对话分析标签"""
        item = self.tree.identify_row(event.y)
        if not item:
            return
        # 找到父窗口的notebook，切换到对话标签
        try:
            parent = self.master
            while parent and not isinstance(parent, ttk.Window):
                parent = parent.master
            if parent:
                for child in parent.winfo_children():
                    if isinstance(child, ttk.Notebook):
                        child.select(2)  # 对话分析是第3个标签
                        break
        except Exception:
            pass

    def refresh_from_final(self, final: Dict[str, Any], market: dict = None):
        items: List[Dict[str, Any]] = final.get("final_list", [])

        for i in self.tree.get_children():
            self.tree.delete(i)

        # Update market regime display
        if market:
            regime = market.get("regime", "unknown")
            trend = market.get("trend", "")
            rg_map = {"bull": "++", "neutral_bull": "+", "consolidation": "~", "bear": "--", "choppy": "?"}
            cl_map = {"bull": "#66bb6a", "neutral_bull": "#9ccc65", "consolidation": "#ffa726", "bear": "#ef5350", "choppy": "#78909c"}
            self.lbl_market.configure(text=f"[{rg_map.get(regime, '?')}] {trend[:30]}", foreground=cl_map.get(regime, "#78909c"))

        if not items:
            self.gauge.set_value(0)
            self.card_labels["total"].configure(text="0")
            self.card_labels["sectors"].configure(text="0")
            self.card_labels["high"].configure(text="0")
            self.card_labels["avg_signal"].configure(text="0")
            self.status.configure(text="暂无结果。")
            return

        # 统计
        scores = [float(it.get("probability_score", 60)) for it in items]
        avg_score = sum(scores) / max(1, len(scores))
        self.gauge.set_value(avg_score)

        sectors = set(it.get("sector", "") for it in items)
        high_count = sum(1 for it in items if it.get("probability_label") == "高")
        signals = [int(it.get("signal_strength", 0)) for it in items if it.get("signal_strength")]
        avg_signal = sum(signals) / max(1, len(signals)) if signals else 0

        self.card_labels["total"].configure(text=str(len(items)))
        self.card_labels["sectors"].configure(text=str(len(sectors)))
        self.card_labels["high"].configure(text=str(high_count))
        self.card_labels["avg_signal"].configure(text=f"{avg_signal:.0f}")

        # 填表
        for rank, it in enumerate(sorted(items, key=lambda x: x.get("probability_score", x.get("signal_strength", 0)), reverse=True), start=1):
            signal = it.get("signal_strength", "")
            prob = it.get("probability_label", "")
            logic = str(it.get("logic_analysis") or it.get("sector_logic") or it.get("reason", ""))[:90]
            watch = str(it.get("technical_analysis") or it.get("watch_3d", ""))[:110]
            self.tree.insert("", END, values=(
                rank, it.get("code", ""), it.get("name", ""), it.get("sector", ""),
                it.get("current_price", ""), it.get("entry_price", ""),
                it.get("stop_loss_price", ""), it.get("target_price_3d", ""),
                signal, prob, logic, watch,
            ))

        # 着色行（需在treeview中用tag）
        self.tree.tag_configure("high_prob", foreground="#66bb6a")
        self.tree.tag_configure("mid_prob", foreground="#ffa726")
        self.tree.tag_configure("low_prob", foreground="#ef5350")

        top1 = items[0]
        self.status.configure(
            text=f"  Top1: {top1.get('code')} {top1.get('name')} | {top1.get('probability_label')}({top1.get('probability_score', '')}分) | "
                 f"当前:{top1.get('current_price', '-')} 买入:{top1.get('entry_price', '-')} "
                 f"止损:{top1.get('stop_loss_price', '-')} 目标:{top1.get('target_price_3d', '-')}"
        )
