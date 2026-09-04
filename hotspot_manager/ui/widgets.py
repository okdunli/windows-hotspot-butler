"""自定义控件：可折叠侧边栏、圆角卡片、开关、可滚动区域、Toast、迷你图表。"""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .theme import FONTS, Palette


# --------------------------------------------------------------------------- #
#                                滚动容器                                     #
# --------------------------------------------------------------------------- #
class ScrollFrame(ttk.Frame):
    """带鼠标滚轮的滚动容器。外部使用 .body 作为内容父容器。"""

    def __init__(self, master, palette: Palette, **kw) -> None:
        super().__init__(master, style="TFrame", **kw)
        self.p = palette
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0,
                                background=palette.bg, takefocus=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.body = ttk.Frame(self.canvas, style="TFrame")
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.canvas.pack(side="left", fill="both", expand=True)
        self.vsb.pack(side="right", fill="y")

        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)
        self._bind_wheel(self.canvas)
        self._bind_wheel(self.body)
        self._bound_children: set = set()

    def _on_body(self, _e=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, e=None) -> None:
        if e:
            self.canvas.itemconfig(self._win, width=e.width)

    def _bind_wheel(self, widget: tk.Misc) -> None:
        widget.bind("<MouseWheel>", self._wheel, add="+")
        widget.bind("<Button-4>", self._wheel, add="+")
        widget.bind("<Button-5>", self._wheel, add="+")

    def _wheel(self, event) -> None:
        if getattr(event, "delta", 0):
            delta = -1 * int(event.delta / 120)
        elif getattr(event, "num", 0) == 5:
            delta = 1
        else:
            delta = -1
        self.canvas.yview_scroll(delta, "units")
        return "break"

    def bind_children_wheel(self, widget: Optional[tk.Misc] = None) -> None:
        """递归给子控件绑定滚轮（新建子控件后调用）。"""
        root = widget or self.body
        for child in root.winfo_children():
            if id(child) in self._bound_children:
                continue
            self._bound_children.add(id(child))
            self._bind_wheel(child)
            if child.winfo_children():
                self.bind_children_wheel(child)

    def clear(self) -> None:
        for w in list(self.body.winfo_children()):
            w.destroy()
        self._bound_children.clear()
        self.canvas.yview_moveto(0)


# --------------------------------------------------------------------------- #
#                                 卡 片                                       #
# --------------------------------------------------------------------------- #
class Card(ttk.Frame):
    """带标题的分组卡片。使用 .body 作为内容容器。"""

    def __init__(self, master, palette: Palette, title: str = "", subtitle: str = "",
                 padx: int = 16, pady: int = 14, **kw) -> None:
        super().__init__(master, style="Card.TFrame", padding=(padx, pady), **kw)
        self.p = palette
        self.columnconfigure(0, weight=1)
        row = 0
        if title:
            head = ttk.Frame(self, style="Surface.TFrame")
            head.grid(row=row, column=0, sticky="ew", pady=(0, 10))
            ttk.Label(head, text=title, style="H2.TLabel").pack(side="left")
            if subtitle:
                ttk.Label(head, text=subtitle, style="Muted.TLabel").pack(
                    side="left", padx=(10, 0))
            row += 1
        self.body = ttk.Frame(self, style="Surface.TFrame")
        self.body.grid(row=row, column=0, sticky="ew")
        self.body.columnconfigure(1, weight=1)


# --------------------------------------------------------------------------- #
#                                 开 关                                       #
# --------------------------------------------------------------------------- #
class Toggle(tk.Canvas):
    """自绘开关滑块，状态变化时回调 on_change(bool)。"""

    W, H = 46, 24

    def __init__(self, master, palette: Palette, value: bool = False,
                 on_change: Optional[Callable[[bool], None]] = None,
                 disabled: bool = False, **kw) -> None:
        super().__init__(master, width=self.W, height=self.H, highlightthickness=0,
                         bd=0, bg=palette.surface, **kw)
        self.p = palette
        self.value = bool(value)
        self.on_change = on_change
        self.disabled = bool(disabled)
        # 注意：不要占用 self._w / self._h，那是 tkinter 内部使用的属性
        self._width = float(self.W)
        self._height = float(self.H)
        self.bind("<Button-1>", self._click)
        self.bind("<Configure>", self._on_resize)
        self.draw()

    def _on_resize(self, e) -> None:
        if e.width > 1 and e.height > 1:
            self._width, self._height = e.width, e.height
            self.draw()

    def _click(self, _e=None) -> None:
        if self.disabled:
            return
        self.set(not self.value)

    def set(self, value: bool, notify: bool = True) -> None:
        value = bool(value)
        changed = value != self.value
        self.value = value
        self.draw()
        if changed and notify and self.on_change:
            try:
                self.on_change(self.value)
            except Exception:
                pass

    def configure_state(self, disabled: bool) -> None:
        self.disabled = bool(disabled)
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        w, h = self._width, self._height
        p = self.p
        on_color = p.accent if not self.disabled else p.border
        off_color = p.border if not self.disabled else p.surface2
        bg = on_color if self.value else off_color
        r = h / 2
        self._rounded(1, 1, w - 1, h - 1, r, fill=bg, outline="")
        pad = 3
        d = h - pad * 2
        cx = (w - pad - d / 2) if self.value else (pad + d / 2)
        self.create_oval(cx - d / 2, pad, cx + d / 2, h - pad,
                         fill="#FFFFFF", outline="")

    def _rounded(self, x0, y0, x1, y1, r, **kw):
        pts: List[float] = []
        for ox, oy, a0, a1 in ((x0 + r, y0 + r, 180, 270), (x1 - r, y0 + r, 270, 360),
                               (x1 - r, y1 - r, 0, 90), (x0 + r, y1 - r, 90, 180)):
            for i in range(6):
                ang = math.radians(a0 + (a1 - a0) * i / 5)
                pts += [ox + r * math.cos(ang), oy - r * math.sin(ang)]
        return self.create_polygon(pts, smooth=True, **kw)


def labeled_switch(parent, palette: Palette, label: str, value: bool,
                   desc: str = "", on_change=None, disabled: bool = False):
    """左文右开关的一行组件，返回 (frame, toggle)。"""
    row = ttk.Frame(parent, style="Surface.TFrame")
    row.columnconfigure(0, weight=1)
    left = ttk.Frame(row, style="Surface.TFrame")
    left.grid(row=0, column=0, sticky="w")
    ttk.Label(left, text=label, style="H3.TLabel").pack(anchor="w")
    if desc:
        ttk.Label(left, text=desc, style="Muted.TLabel", wraplength=440,
                  justify="left").pack(anchor="w", pady=(2, 0))
    tg = Toggle(row, palette, value=value, on_change=on_change, disabled=disabled)
    tg.grid(row=0, column=1, sticky="e", padx=(12, 0))
    return row, tg


# --------------------------------------------------------------------------- #
#                                侧 边 栏                                     #
# --------------------------------------------------------------------------- #
class Sidebar(tk.Frame):
    """可折叠侧边栏：展开 216px，收起 62px，带平滑动画。"""

    EXPANDED = 216
    COLLAPSED = 62

    def __init__(self, master, palette: Palette, items: Sequence[Tuple[str, str, Callable]],
                 on_toggle: Optional[Callable[[bool], None]] = None,
                 expanded: bool = True) -> None:
        super().__init__(master, bg=palette.surface, width=self.EXPANDED)
        self.p = palette
        self.items = list(items)
        self.on_toggle = on_toggle
        self.expanded = bool(expanded)
        self._target = self.EXPANDED if self.expanded else self.COLLAPSED
        self._current = float(self._target)
        self._buttons: Dict[str, Dict] = {}
        self.active_key: str = ""
        self._alive = True
        self._anim_job = ""

        self.pack_propagate(False)
        self.configure(width=int(self._current))

        self.top = tk.Frame(self, bg=palette.surface, height=58)
        self.top.pack(fill="x", pady=(10, 6))
        self.top.pack_propagate(False)

        self.nav = tk.Frame(self, bg=palette.surface)
        self.nav.pack(fill="both", expand=True)

        self.bottom = tk.Frame(self, bg=palette.surface)
        self.bottom.pack(fill="x", pady=(6, 10))

        for key, label, cmd in self.items:
            self._make_button(key, label, cmd)

        self._animate()

    # ---------- 构建 ----------
    def _make_button(self, key: str, label: str, cmd: Callable) -> None:
        btn = tk.Frame(self.nav, bg=self.p.surface, cursor="hand2", height=42)
        btn.pack(fill="x", padx=8, pady=2)
        btn.pack_propagate(False)
        icon_canvas = tk.Canvas(btn, width=30, height=30, bg=self.p.surface,
                                highlightthickness=0, bd=0)
        icon_canvas.pack(side="left", padx=(6, 0))
        text = tk.Label(btn, text=label, bg=self.p.surface, fg=self.p.muted,
                        font=FONTS["base"], anchor="w")
        text.pack(side="left", padx=(10, 0), fill="x", expand=True)

        for w in (btn, icon_canvas, text):
            w.bind("<Button-1>", lambda _e, k=key: self.select(k, True))
            w.bind("<Enter>", lambda _e, b=btn, t=text: self._hover(b, t, True))
            w.bind("<Leave>", lambda _e, b=btn, t=text: self._hover(b, t, False))

        self._buttons[key] = {
            "frame": btn, "canvas": icon_canvas, "label": text,
            "cmd": cmd, "text": label, "hover": False,
        }
        _draw_nav_icon(icon_canvas, key, self.p.muted, self.p.surface, 30)

    def _hover(self, btn: tk.Frame, text: tk.Label, entering: bool) -> None:
        rec = None
        for v in self._buttons.values():
            if v["frame"] is btn:
                rec = v
                break
        if rec is None:
            return
        rec["hover"] = entering
        self._refresh_btn(rec)

    def _refresh_btn(self, rec: Dict) -> None:
        key_active = rec is self._buttons.get(self.active_key)
        if key_active:
            bg = self.p.elevated
            fg = self.p.text
        elif rec["hover"]:
            bg = self.p.hover
            fg = self.p.text
        else:
            bg = self.p.surface
            fg = self.p.muted
        rec["frame"].configure(bg=bg)
        rec["label"].configure(bg=bg, fg=fg)
        rec["canvas"].configure(bg=bg)
        rec["canvas"].delete("all")
        icon_color = self.p.accent if key_active else fg
        _draw_nav_icon(rec["canvas"], rec["text"], icon_color, bg, 30)

    # ---------- 交互 ----------
    def select(self, key: str, fire: bool = True) -> None:
        if key not in self._buttons:
            return
        self.active_key = key
        for rec in self._buttons.values():
            self._refresh_btn(rec)
        if fire:
            rec = self._buttons[key]
            try:
                rec["cmd"]()
            except Exception:
                import logging

                logging.exception("导航回调失败")

    def toggle(self) -> None:
        self.expanded = not self.expanded
        self._target = self.EXPANDED if self.expanded else self.COLLAPSED
        if self.on_toggle:
            try:
                self.on_toggle(self.expanded)
            except Exception:
                pass

    def destroy(self) -> None:
        self._alive = False
        try:
            if self._anim_job:
                self.winfo_toplevel().after_cancel(self._anim_job)
        except Exception:
            pass
        self._anim_job = ""
        try:
            super().destroy()
        except Exception:
            pass

    def _animate(self) -> None:
        if not self._alive:
            return
        toplevel = self.winfo_toplevel()
        diff = self._target - self._current
        if abs(diff) > 0.6:
            self._current += diff * 0.28
            self.configure(width=int(self._current))
            show_text = self._current > self.COLLAPSED + 46
            for rec in self._buttons.values():
                if show_text:
                    rec["label"].configure(text=rec["text"])
                else:
                    rec["label"].configure(text="")
        elif int(self._current) != int(self._target):
            self._current = float(self._target)
            self.configure(width=int(self._target))
            for rec in self._buttons.values():
                rec["label"].configure(text=rec["text"] if self.expanded else "")
        # 挂在顶层窗口上，避免本控件被销毁后 after 回调失效报错
        try:
            self._anim_job = toplevel.after(16, self._animate)
        except Exception:
            self._anim_job = ""


def _draw_nav_icon(canvas: tk.Canvas, label: str, color: str, bg: str, size: int) -> None:
    """侧栏图标：按导航项名称绘制（纯矢量，不依赖 emoji 字体）。"""
    s = size
    c = canvas
    w = s * 0.62

    def rect(x0, y0, x1, y1, fill=""):
        c.create_rectangle(x0, y0, x1, y1, fill=fill or color, outline="")

    def line(x0, y0, x1, y1, width=2):
        c.create_line(x0, y0, x1, y1, fill=color, width=width)

    def circle(cx, cy, r, fill=""):
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=fill or color, outline="")

    if label == "概览":
        rect(4, 4, w * .45, s * .55)
        rect(w * .55, 4, s - 4, s * .45)
        rect(4, s * .62, w * .45, s - 4)
        rect(w * .55, s * .55, s - 4, s - 4)
    elif label == "热点设置":
        circle(s / 2, s * .70, s * .12)
        for i, (rr, yy) in enumerate(((s * .18, s * .52), (s * .30, s * .38), (s * .42, s * .24))):
            c.create_arc(s / 2 - rr, yy, s / 2 + rr, yy + rr * 1.35,
                         start=200, extent=140, style="arc", outline=color, width=2)
    elif label == "已连接设备":
        rect(3, s * .22, s * .42, s - 5)
        rect(s * .58, s * .22, s - 3, s - 5)
        line(s * .22, s * .22, s * .22, s * .06)
        line(s * .78, s * .22, s * .78, s * .06)
        circle(s * .22, s * .04, s * .05)
        circle(s * .78, s * .04, s * .05)
    elif label == "欢迎页":
        c.create_rectangle(3, 4, s - 3, s - 4, outline=color, width=2)
        line(3, s * .28, s - 3, s * .28, 2)
        line(s * .28, s * .50, s * .72, s * .50, 2)
        line(s * .28, s * .66, s * .58, s * .66, 2)
    elif label == "设置":
        circle(s / 2, s / 2, s * .17, fill="")
        c.create_oval(s / 2 - s * .17, s / 2 - s * .17, s / 2 + s * .17, s / 2 + s * .17,
                      outline=color, width=2)
        for i in range(8):
            import math as _m

            a = _m.radians(i * 45)
            line(s / 2 + _m.cos(a) * s * .22, s / 2 + _m.sin(a) * s * .22,
                 s / 2 + _m.cos(a) * s * .34, s / 2 + _m.sin(a) * s * .34, 2)
    else:
        circle(s / 2, s / 2, s * .28, fill="")
        c.create_oval(s / 2 - s * .28, s / 2 - s * .28, s / 2 + s * .28, s / 2 + s * .28,
                      outline=color, width=2)


# --------------------------------------------------------------------------- #
#                              Toast 提示                                     #
# --------------------------------------------------------------------------- #
class Toast:
    """右下角轻提示。"""

    def __init__(self, root: tk.Misc, palette: Palette) -> None:
        self.root = root
        self.p = palette
        self._win: Optional[tk.Toplevel] = None
        self._job = ""

    def show(self, message: str, kind: str = "info", ms: int = 2600) -> None:
        # 取消上一个尚未触发的自动关闭定时器，避免多个通知互相覆盖而泄漏
        try:
            if self._job:
                self.root.after_cancel(self._job)
        except Exception:
            pass
        self._job = ""
        colors = {
            "info": self.p.accent, "success": self.p.success,
            "warning": self.p.warning, "error": self.p.danger,
        }
        border = colors.get(kind, self.p.accent)
        try:
            if self._win and self._win.winfo_exists():
                self._win.destroy()
            win = tk.Toplevel(self.root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            try:
                win.attributes("-alpha", 0.97)
            except tk.TclError:
                pass
            frame = tk.Frame(win, bg=self.p.elevated, highlightthickness=1,
                             highlightbackground=border)
            frame.pack(fill="both", expand=True)
            tk.Label(frame, text=message, bg=self.p.elevated, fg=self.p.text,
                     font=FONTS["base"], justify="left", wraplength=340,
                     padx=16, pady=12).pack()
            win.update_idletasks()
            rw, rh = win.winfo_reqwidth(), win.winfo_reqheight()
            x = win.winfo_screenwidth() - rw - 24
            y = win.winfo_screenheight() - rh - 64
            win.geometry(f"+{x}+{y}")
            self._win = win
            self._job = self.root.after(ms, self._close)
        except Exception:
            pass

    def cancel(self) -> None:
        """取消待触发的自动关闭定时器（窗口销毁前调用）。"""
        if self._job:
            try:
                self.root.after_cancel(self._job)
            except Exception:
                pass
            self._job = ""

    def _close(self) -> None:
        self._job = ""
        try:
            if self._win is not None and self._win.winfo_exists():
                self._win.destroy()
        except Exception:
            pass
        self._win = None


# --------------------------------------------------------------------------- #
#                              迷你折线图                                     #
# --------------------------------------------------------------------------- #
class Sparkline(tk.Canvas):
    """实时速率折线图（下行 + 上行）。"""

    def __init__(self, master, palette: Palette, height: int = 70, **kw) -> None:
        super().__init__(master, height=height, highlightthickness=0, bd=0,
                         bg=palette.surface, **kw)
        self.p = palette
        self.data: List[Tuple[float, float]] = []
        self.bind("<Configure>", lambda _e: self.draw())

    def set_data(self, data: Sequence[Tuple[float, float]]) -> None:
        self.data = list(data)
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        w = max(10, self.winfo_width())
        h = max(10, self.winfo_height())
        p = self.p
        # 网格线
        for i in range(1, 4):
            y = h * i / 4
            self.create_line(0, y, w, y, fill=p.border, width=1)
        if len(self.data) < 2:
            self.create_text(w / 2, h / 2, text="暂无数据", fill=p.dim, font=FONTS["small"])
            return
        n = len(self.data)
        peak = max((max(d) for d in self.data), default=1) or 1
        pts_down: List[float] = []
        pts_up: List[float] = []
        for i, (down, up) in enumerate(self.data):
            x = w * i / max(1, n - 1)
            pts_down += [x, h - 3 - (down / peak) * (h - 8)]
            pts_up += [x, h - 3 - (up / peak) * (h - 8)]
        if n > 1:
            self.create_line(pts_down, fill=p.accent, width=2, smooth=True)
            self.create_line(pts_up, fill=p.teal, width=1.6, smooth=True)
        # 图例
        self.create_rectangle(8, 6, 20, 12, fill=p.accent, outline="")
        self.create_text(24, 9, text="下行", fill=p.muted, font=FONTS["small"], anchor="w")
        self.create_rectangle(64, 6, 76, 12, fill=p.teal, outline="")
        self.create_text(80, 9, text="上行", fill=p.muted, font=FONTS["small"], anchor="w")


class BarChart(tk.Canvas):
    """每日用量柱状图。"""

    def __init__(self, master, palette: Palette, height: int = 120, **kw) -> None:
        super().__init__(master, height=height, highlightthickness=0, bd=0,
                         bg=palette.surface, **kw)
        self.p = palette
        self.bars: List[Tuple[str, int, int]] = []

    def set_data(self, bars: Sequence[Tuple[str, int, int]]) -> None:
        self.bars = list(bars)
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        w = max(20, self.winfo_width())
        h = max(20, self.winfo_height())
        p = self.p
        if not self.bars:
            self.create_text(w / 2, h / 2, text="暂无数据", fill=p.dim, font=FONTS["small"])
            return
        n = len(self.bars)
        gap = 4
        bw = max(3.0, (w - 12 - gap * (n - 1)) / n)
        peak = max((rx + tx) for _, rx, tx in self.bars) or 1
        base = h - 18
        for i, (day, rx, tx) in enumerate(self.bars):
            x0 = 6 + i * (bw + gap)
            total = rx + tx
            hh = (total / peak) * (base - 8)
            if total > 0:
                self.create_rectangle(x0, base - hh, x0 + bw, base,
                                      fill=p.accent, outline="")
                rh = (rx / total) * hh if total else 0
                self.create_rectangle(x0, base - hh + rh, x0 + bw, base,
                                      fill=p.teal, outline="")
            if n <= 16 and i % max(1, n // 8) == 0:
                self.create_text(x0 + bw / 2, base + 9, text=day[5:], fill=p.dim,
                                 font=("Microsoft YaHei UI", 7))
        self.create_rectangle(8, 4, 18, 10, fill=p.accent, outline="")
        self.create_text(22, 7, text="上行", fill=p.muted, font=FONTS["small"], anchor="w")
        self.create_rectangle(56, 4, 66, 10, fill=p.teal, outline="")
        self.create_text(70, 7, text="下行", fill=p.muted, font=FONTS["small"], anchor="w")
