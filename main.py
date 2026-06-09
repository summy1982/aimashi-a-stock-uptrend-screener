# -*- coding: utf-8 -*-
"""A股主板主升浪筛选系统 - 入口"""
import sys
import traceback

def _show_error(exc_type, exc_value, exc_tb):
    """全局异常兜底，防止exe闪退。"""
    import tkinter as tk
    from tkinter import messagebox
    err = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("运行异常", f"程序遇到未捕获的异常：\n\n{err}\n\n请截图联系技术支持。")
        root.destroy()
    except Exception:
        with open("crash.log", "w", encoding="utf-8") as f:
            f.write(err)
        print(err, file=sys.stderr)

sys.excepthook = _show_error

if __name__ == "__main__":
    from gui_app import App
    app = App()
    app.mainloop()