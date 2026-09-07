"""分页基类：首次显示时才构建，切换时刷新。"""
from __future__ import annotations

from tkinter import ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型提示，避免循环导入
    from ..app import AppWindow

# 页面构建过程中出现的异常（供自动化测试检查）
BUILD_ERRORS: list = []


class Page(ttk.Frame):
    key = "page"
    title = "页面"
    nav_label = "页面"

    def __init__(self, master, app: "AppWindow") -> None:
        super().__init__(master, style="TFrame")
        self.app = app
        self.p = app.palette
        self._built = False
        self._after_ids: set = set()
        self.columnconfigure(0, weight=1)

    # ---- 安全定时器（窗口销毁前自动取消，避免 Tcl 报错） ----
    def after_safe(self, ms: int, fn, *args) -> str:
        """等价于 self.after，但回调前检查控件存活，并在 destroy 时自动取消。"""

        def cb() -> None:
            self._after_ids.discard(jid)
            if self.winfo_exists():
                try:
                    fn(*args)
                except Exception:
                    pass

        jid = self.after(ms, cb)
        self._after_ids.add(jid)
        return jid

    def destroy(self) -> None:
        for jid in list(self._after_ids):
            try:
                self.after_cancel(jid)
            except Exception:
                pass
        self._after_ids.clear()
        try:
            super().destroy()
        except Exception:
            pass

    # ---- 生命周期 ----
    def on_show(self) -> None:
        if not self._built:
            try:
                self.build()
            except Exception:
                import logging
                import traceback

                logging.exception("页面 %s 构建失败", self.key)
                BUILD_ERRORS.append(f"[{self.key}] " + traceback.format_exc())
                try:
                    self.app.notify(f"{self.title} 页面构建失败，请查看日志", "error")
                except Exception:
                    pass
            self._built = True
        self.refresh()

    def on_hide(self) -> None:
        pass

    def build(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        pass

    # ---- 工具 ----
    def section_title(self, text: str, subtitle: str = "") -> ttk.Frame:
        head = ttk.Frame(self, style="TFrame")
        head.pack(fill="x", padx=20, pady=(18, 8))
        ttk.Label(head, text=text, style="Title.TLabel").pack(anchor="w")
        if subtitle:
            ttk.Label(head, text=subtitle, foreground=self.p.muted,
                      background=self.p.bg, font=("Microsoft YaHei UI", 9)).pack(
                anchor="w", pady=(3, 0))
        return head
