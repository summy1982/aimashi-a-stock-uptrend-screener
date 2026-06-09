from __future__ import annotations

import threading
import traceback
import time
from pathlib import Path

import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from tkinter import messagebox

from config import load_config, save_config
from pipeline import run_pipeline
from dashboard import DashboardFrame
from chat_panel import ChatFrame


class App(ttk.Window):
    def __init__(self):
        super().__init__(
            title="A股主板主升浪筛选系统  v2.3",
            themename="flatly",
            size=(1450, 980),
            minsize=(1180, 760),
        )
        self.cfg = load_config()
        self.cfg["enable_realtime_news"] = True
        self.cfg.pop("offline_demo", None)
        save_config(self.cfg)

        self._running = False
        self._start_time = None

        self._build_menu()
        self._build_header()
        self._build_notebook()
        self._build_statusbar()

        self._log("=" * 70, "header")
        self._log("  A股主板主升浪筛选系统  v2.3", "header")
        self._log("=" * 70, "header")
        if not self.cfg.get("openai_api_key"):
            self._log("请先在「文件 -> 接口设置」中填写 API Key 后再运行。", "warn")
        else:
            self._log("API 已配置，点击「开始分析」即可运行。", "success")
        self._log("模式：强制联网新闻抓取 + LLM全量分析")
        self._log("数据源：39个国内外新闻源 + 腾讯实时行情")
        self._log("输出方式：每板块2-5只候选 + 新闻逻辑 + 技术面解释 + 关键价位")
        self._log("技术指标：MACD / RSI / KDJ / BOLL / 均线 / ATR / 量价")

    def _build_menu(self):
        menubar = ttk.Menu(self)
        m_file = ttk.Menu(menubar, tearoff=0)
        m_file.add_command(label="接口设置", command=self._open_settings)
        m_file.add_command(label="打开结果目录", command=self._open_results_dir)
        m_file.add_separator()
        m_file.add_command(label="退出", command=self.destroy)
        menubar.add_cascade(label="文件", menu=m_file)

        m_help = ttk.Menu(menubar, tearoff=0)
        m_help.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=m_help)
        self.config(menu=menubar)

    def _build_header(self):
        header = ttk.Frame(self, padding=(14, 10), bootstyle=LIGHT)
        header.pack(fill=X)

        left = ttk.Frame(header)
        left.pack(side=LEFT)
        ttk.Label(left, text="A股主板主升浪筛选系统", font=("Microsoft YaHei UI", 17, "bold"), bootstyle=PRIMARY).pack(anchor=W)
        ttk.Label(left, text="多源新闻 -> LLM热点分析 -> 板块映射 -> 技术研判 -> 板块候选池排行榜", font=("Microsoft YaHei UI", 9), bootstyle=SECONDARY).pack(anchor=W)

        right = ttk.Frame(header)
        right.pack(side=RIGHT)
        self.btn_run = ttk.Button(right, text="开始分析", bootstyle=SUCCESS, command=self._on_run, width=14)
        self.btn_run.pack(side=LEFT, padx=(0, 12))
        self.lbl_status = ttk.Label(right, text="就绪", font=("Microsoft YaHei UI", 10), bootstyle=INFO)
        self.lbl_status.pack(side=LEFT, padx=(0, 8))
        self.lbl_timer = ttk.Label(right, text="00:00", font=("Consolas", 11), bootstyle=SECONDARY)
        self.lbl_timer.pack(side=LEFT)

    def _build_notebook(self):
        self.note = ttk.Notebook(self, bootstyle=PRIMARY)
        self.note.pack(fill=BOTH, expand=True, padx=10, pady=(0, 6))

        tab_run = ttk.Frame(self.note)
        self.note.add(tab_run, text="  运行监控  ")
        body = ttk.Frame(tab_run, padding=8)
        body.pack(fill=BOTH, expand=True)

        left = ttk.Labelframe(body, text="执行阶段", padding=12, bootstyle=INFO)
        left.pack(side=LEFT, fill=Y, padx=(0, 8))
        stages = [
            "联网抓取多源新闻",
            "LLM热点提取 -> 板块推荐",
            "主板候选池构建",
            "个股多线程技术研判",
            "候选池综合排序",
        ]
        self._stage_labels = []
        for i, name in enumerate(stages, 1):
            lbl = ttk.Label(left, text=f"  {i}. {name}", font=("Microsoft YaHei UI", 10), bootstyle=SECONDARY)
            lbl.pack(anchor=W, pady=3)
            self._stage_labels.append(lbl)

        ttk.Separator(left, orient=HORIZONTAL).pack(fill=X, pady=10)
        self.lbl_counts = ttk.Label(left, text="新闻: -  来源: -  板块: -  候选: -", font=("Microsoft YaHei UI", 9), bootstyle=SECONDARY)
        self.lbl_counts.pack(anchor=W, pady=2)
        self.progress = ttk.Progressbar(left, mode="determinate", length=220, bootstyle=SUCCESS)
        self.progress.pack(anchor=W, pady=8)

        right = ttk.Labelframe(body, text="运行日志", padding=8, bootstyle=PRIMARY)
        right.pack(side=LEFT, fill=BOTH, expand=True)
        self.txt = ttk.Text(
            right,
            wrap="word",
            font=("Consolas", 9),
            state="normal",
            bg="#f7f9fc",
            fg="#1f2937",
            insertbackground="#1f2937",
            selectbackground="#d8e8ff",
            relief="flat",
        )
        scrollbar = ttk.Scrollbar(right, command=self.txt.yview)
        self.txt.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=RIGHT, fill=Y)
        self.txt.pack(fill=BOTH, expand=True)
        self.txt.tag_config("info", foreground="#1f2937")
        self.txt.tag_config("success", foreground="#1f9d55")
        self.txt.tag_config("warn", foreground="#c77800")
        self.txt.tag_config("error", foreground="#cf3f3f")
        self.txt.tag_config("header", foreground="#2f80ed", font=("Consolas", 9, "bold"))
        self.txt.tag_config("dim", foreground="#667085")

        self.dashboard = DashboardFrame(self.note, self.cfg)
        self.note.add(self.dashboard, text="  分析结果  ")
        self.chat_panel = ChatFrame(self.note, self.cfg)
        self.note.add(self.chat_panel, text="  AI对话  ")

    def _build_statusbar(self):
        bar = ttk.Frame(self, padding=(10, 5), bootstyle=LIGHT)
        bar.pack(fill=X, side=BOTTOM)
        ttk.Label(bar, text="数据来源：新浪/腾讯/东财/财联社/CNBC/WSJ/Fed 等 | 每板块2-5只候选", font=("Microsoft YaHei UI", 8), bootstyle=SECONDARY).pack(side=LEFT)
        ttk.Label(bar, text="仅供参考，不构成投资建议", font=("Microsoft YaHei UI", 8), bootstyle=DANGER).pack(side=RIGHT)

    def _log(self, msg: str, tag: str = "info"):
        self.txt.configure(state="normal")
        self.txt.insert("end", str(msg) + "\n", tag)
        self.txt.configure(state="disabled")
        self.txt.see("end")

    def _set_stage(self, idx: int):
        for i, lbl in enumerate(self._stage_labels, start=1):
            base = lbl.cget("text").split(". ", 1)[-1].split("  完成")[0].split("  进行中")[0]
            if idx == -1 or i < idx:
                lbl.configure(text=f"  {i}. {base}  完成", bootstyle=SUCCESS)
            elif i == idx:
                lbl.configure(text=f"  {i}. {base}  进行中", bootstyle=WARNING)
            else:
                lbl.configure(text=f"  {i}. {base}", bootstyle=SECONDARY)

    def _open_settings(self):
        win = ttk.Toplevel(self)
        win.title("接口设置")
        win.geometry("640x520")
        win.transient(self)
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill=BOTH, expand=True)

        ttk.Label(frm, text="OpenAI 兼容接口配置", font=("Microsoft YaHei UI", 13, "bold"), bootstyle=PRIMARY).grid(row=0, column=0, columnspan=2, sticky=W, pady=(0, 12))
        fields = [
            ("Base URL", "openai_base_url", "", False),
            ("API Key", "openai_api_key", "", True),
            ("Model", "model", "", False),
            ("推荐板块数", "top_sectors", 5, False),
            ("候选股票数", "top_stocks", 20, False),
            ("每板块最少候选", "min_per_sector", 2, False),
            ("每板块最多候选", "max_per_sector", 5, False),
            ("每源新闻上限", "news_per_source", 40, False),
            ("新闻抓取并发", "news_workers", 12, False),
            ("个股分析并发", "stock_workers", 4, False),
            ("总新闻上限", "news_total_limit", 3000, False),
        ]
        entries = []
        for row, (label, key, default, secret) in enumerate(fields, start=1):
            ttk.Label(frm, text=label, font=("Microsoft YaHei UI", 9)).grid(row=row, column=0, sticky=W, pady=4)
            entry = ttk.Entry(frm, show="*" if secret else "", font=("Consolas", 10))
            entry.insert(0, str(self.cfg.get(key, default)))
            entry.grid(row=row, column=1, sticky=EW, pady=4, padx=(8, 0))
            entries.append((key, entry))
        frm.columnconfigure(1, weight=1)

        def _save():
            int_keys = {"top_sectors", "top_stocks", "min_per_sector", "max_per_sector", "news_per_source", "news_workers", "stock_workers", "news_total_limit"}
            for key, entry in entries:
                value = entry.get().strip()
                if key in int_keys:
                    try:
                        self.cfg[key] = int(value)
                    except ValueError:
                        messagebox.showwarning("参数错误", f"{key} 必须是整数", parent=win)
                        return
                else:
                    self.cfg[key] = value
            save_config(self.cfg)
            self._log("设置已保存。", "success")
            win.destroy()

        btns = ttk.Frame(frm)
        btns.grid(row=len(fields) + 2, column=0, columnspan=2, sticky=E, pady=(16, 0))
        ttk.Button(btns, text="取消", bootstyle=SECONDARY, command=win.destroy).pack(side=RIGHT, padx=4)
        ttk.Button(btns, text="保存", bootstyle=SUCCESS, command=_save).pack(side=RIGHT, padx=4)

    def _open_results_dir(self):
        path = Path(self.cfg.get("results_dir", "results"))
        path.mkdir(parents=True, exist_ok=True)
        try:
            import os
            os.startfile(str(path.resolve()))
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc), parent=self)

    def _show_about(self):
        messagebox.showinfo(
            "关于",
            "A股主板主升浪筛选系统 v2.3\n\n多源新闻 + LLM板块映射 + 技术面候选池排行榜。\n结果仅供研究参考，不构成投资建议。",
            parent=self,
        )

    def _on_run(self):
        if self._running:
            return
        if not self.cfg.get("openai_api_key"):
            messagebox.showwarning("缺少API Key", "请先在 文件 -> 接口设置 中填写 API Key。", parent=self)
            return

        self._running = True
        self._start_time = time.time()
        self.btn_run.configure(text="运行中...", state=DISABLED, bootstyle=WARNING)
        self.lbl_status.configure(text="运行中...", bootstyle=WARNING)
        self.progress.configure(value=0)
        self._set_stage(1)
        self._tick_timer()

        def stage_cb(stage: str, detail: str, counts: dict | None = None):
            def _update():
                if stage == "news_fetching":
                    self._set_stage(1)
                    self.lbl_status.configure(text="抓取新闻...")
                elif stage in ("news_done", "llm_sector"):
                    self._set_stage(2)
                    self.lbl_status.configure(text="LLM分析中...")
                    self.progress.configure(value=18)
                elif stage == "sector_done":
                    self._set_stage(3)
                    self.lbl_status.configure(text="构建候选池...")
                    self.progress.configure(value=34)
                elif stage in ("pool_done", "stock_progress"):
                    self._set_stage(4)
                    self.progress.configure(value=55)
                elif stage == "stock_done":
                    self._set_stage(5)
                    self.lbl_status.configure(text="综合排序...")
                    self.progress.configure(value=78)
                elif stage == "final_done":
                    self._set_stage(-1)
                    self.lbl_status.configure(text="完成", bootstyle=SUCCESS)
                    self.progress.configure(value=100)

                if counts:
                    parts = []
                    if "news" in counts:
                        parts.append(f"新闻:{counts['news']}")
                    if "sources" in counts:
                        parts.append(f"来源:{counts['sources']}")
                    if "sectors" in counts:
                        parts.append(f"板块:{counts['sectors']}")
                    if "candidates" in counts:
                        parts.append(f"候选:{counts['candidates']}")
                    if parts:
                        self.lbl_counts.configure(text="  ".join(parts))

                tag = "info"
                if "完成" in detail or "选出" in detail or "通过" in detail:
                    tag = "success"
                elif "失败" in detail or "异常" in detail:
                    tag = "error"
                elif "=" in detail or "【" in detail:
                    tag = "header"
                elif "警" in detail or "弱势" in detail or "补足" in detail or "稳定汇总" in detail:
                    tag = "warn"
                self._log(detail, tag)
            self.after(0, _update)

        def worker():
            try:
                final_payload = run_pipeline(self.cfg, stage_cb=stage_cb)
                all_reports = final_payload.pop("all_reports", []) if isinstance(final_payload, dict) else []
                if isinstance(final_payload, dict):
                    market_data = final_payload.pop("_market", None)
                    self.after(0, self.dashboard.refresh_from_final, final_payload, market_data)
                    self.after(0, self.chat_panel.refresh_data, final_payload, all_reports)
                    count = len(final_payload.get("final_list", []))
                    elapsed = int(time.time() - self._start_time)
                    self.after(0, self._log, "=" * 70, "header")
                    self.after(0, self._log, f"分析完成：耗时 {elapsed // 60}分{elapsed % 60}秒，共 {count} 只候选标的。", "success")
                    self.after(0, self._log, "请切换到「分析结果」或「AI对话」查看逻辑和技术解释。", "success")
                else:
                    self.after(0, self._log, "运行完成但未返回结果。", "warn")
            except Exception as exc:
                tb = traceback.format_exc()
                self.after(0, self._log, f"运行异常：{exc}\n{tb}", "error")
            finally:
                self._running = False
                self.after(0, lambda: self.btn_run.configure(text="开始分析", state=NORMAL, bootstyle=SUCCESS))
                self.after(0, lambda: self.lbl_status.configure(text="就绪", bootstyle=INFO))

        threading.Thread(target=worker, daemon=True).start()

    def _tick_timer(self):
        if self._running and self._start_time:
            elapsed = int(time.time() - self._start_time)
            self.lbl_timer.configure(text=f"{elapsed // 60:02d}:{elapsed % 60:02d}")
            self.after(1000, self._tick_timer)


if __name__ == "__main__":
    App().mainloop()
