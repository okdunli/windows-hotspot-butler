"""主题配色与 ttk 样式（冷色调：蓝 / 绿 / 紫）。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Dict

# 冷色主色板（GitHub Dark 风格）
BG_DARK = "#0D1117"
BG_SURFACE = "#161B22"
BG_SURFACE2 = "#1C2129"
BG_ELEVATED = "#21262D"
BORDER = "#30363D"
TEXT = "#E6EDF3"
TEXT_MUTED = "#8B949E"
TEXT_DIM = "#6E7681"

ACCENT = "#58A6FF"
ACCENT_HOVER = "#79C0FF"
SUCCESS = "#3FB950"
WARNING = "#D29922"
DANGER = "#F85149"
PURPLE = "#BC8CFF"
TEAL = "#39C5CF"

# 亮色
L_BG = "#F5F7FA"
L_SURFACE = "#FFFFFF"
L_SURFACE2 = "#F0F3F7"
L_ELEVATED = "#FFFFFF"
L_BORDER = "#D8DEE7"
L_TEXT = "#1F2937"
L_TEXT_MUTED = "#5B6675"
L_TEXT_DIM = "#8892A0"
L_ACCENT = "#1F6FEB"


class Palette:
    def __init__(self, dark: bool = True) -> None:
        self.dark = dark
        if dark:
            self.bg = BG_DARK
            self.surface = BG_SURFACE
            self.surface2 = BG_SURFACE2
            self.elevated = BG_ELEVATED
            self.border = BORDER
            self.text = TEXT
            self.muted = TEXT_MUTED
            self.dim = TEXT_DIM
            self.accent = ACCENT
            self.accent_hover = ACCENT_HOVER
            self.input_bg = "#0B0F14"
            self.selected = "#1F6FEB"
            self.hover = "#1C2430"
        else:
            self.bg = L_BG
            self.surface = L_SURFACE
            self.surface2 = L_SURFACE2
            self.elevated = L_ELEVATED
            self.border = L_BORDER
            self.text = L_TEXT
            self.muted = L_TEXT_MUTED
            self.dim = L_TEXT_DIM
            self.accent = L_ACCENT
            self.accent_hover = "#2A7FF0"
            self.input_bg = "#FFFFFF"
            self.selected = "#DDEAFF"
            self.hover = "#EDF2F9"

    @property
    def success(self) -> str:
        return SUCCESS if self.dark else "#1A7F37"

    @property
    def warning(self) -> str:
        return WARNING if self.dark else "#9A6700"

    @property
    def danger(self) -> str:
        return DANGER if self.dark else "#CF222E"

    @property
    def purple(self) -> str:
        return PURPLE if self.dark else "#8250DF"

    @property
    def teal(self) -> str:
        return TEAL if self.dark else "#1B7C83"

    @property
    def icon_fg(self) -> str:
        return self.accent

    @property
    def icon_bg(self) -> str:
        return self.surface


def apply_ttk_style(root: tk.Misc, p: Palette) -> ttk.Style:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=p.bg, foreground=p.text,
                    fieldbackground=p.input_bg, bordercolor=p.border,
                    troughcolor=p.surface2, focuscolor=p.accent,
                    font=("Microsoft YaHei UI", 10))
    style.configure("TFrame", background=p.bg)
    style.configure("Surface.TFrame", background=p.surface)
    style.configure("Card.TFrame", background=p.surface, relief="flat",
                    borderwidth=1, bordercolor=p.border)
    style.configure("TLabel", background=p.bg, foreground=p.text,
                    font=("Microsoft YaHei UI", 10))
    style.configure("Surface.TLabel", background=p.surface, foreground=p.text)
    style.configure("Muted.TLabel", background=p.surface, foreground=p.muted,
                    font=("Microsoft YaHei UI", 9))
    style.configure("Dim.TLabel", background=p.surface, foreground=p.dim,
                    font=("Microsoft YaHei UI", 9))
    style.configure("Title.TLabel", background=p.bg, foreground=p.text,
                    font=("Microsoft YaHei UI", 17, "bold"))
    style.configure("H2.TLabel", background=p.surface, foreground=p.text,
                    font=("Microsoft YaHei UI", 12, "bold"))
    style.configure("H3.TLabel", background=p.surface, foreground=p.text,
                    font=("Microsoft YaHei UI", 10, "bold"))
    style.configure("Accent.TLabel", background=p.surface, foreground=p.accent,
                    font=("Microsoft YaHei UI", 10, "bold"))
    style.configure("Success.TLabel", background=p.surface, foreground=p.success)
    style.configure("Danger.TLabel", background=p.surface, foreground=p.danger)
    style.configure("Warn.TLabel", background=p.surface, foreground=p.warning)

    style.configure("TButton", background=p.surface2, foreground=p.text,
                    bordercolor=p.border, borderwidth=1, padding=(12, 6),
                    font=("Microsoft YaHei UI", 10))
    style.map("TButton",
              background=[("active", p.hover), ("pressed", p.surface2),
                          ("disabled", p.surface)],
              foreground=[("disabled", p.dim)],
              bordercolor=[("focus", p.accent)])
    style.configure("Primary.TButton", background=p.accent, foreground="#FFFFFF",
                    bordercolor=p.accent, padding=(14, 7),
                    font=("Microsoft YaHei UI", 10, "bold"))
    style.map("Primary.TButton",
              background=[("active", p.accent_hover), ("pressed", p.accent),
                          ("disabled", p.surface2)],
              foreground=[("disabled", p.dim)])
    style.configure("Danger.TButton", background=p.surface2, foreground=p.danger,
                    bordercolor=p.danger, padding=(12, 6))
    style.map("Danger.TButton", background=[("active", p.danger), ("pressed", p.danger)],
              foreground=[("active", "#FFFFFF")])
    style.configure("Ghost.TButton", background=p.surface, foreground=p.muted,
                    bordercolor=p.border, padding=(8, 4),
                    font=("Microsoft YaHei UI", 9))

    style.configure("TEntry", fieldbackground=p.input_bg, foreground=p.text,
                    bordercolor=p.border, insertcolor=p.text, padding=(8, 6),
                    borderwidth=1, relief="flat")
    style.map("TEntry", bordercolor=[("focus", p.accent)],
              fieldbackground=[("disabled", p.surface2)],
              foreground=[("disabled", p.dim)])

    style.configure("TCombobox", fieldbackground=p.input_bg, foreground=p.text,
                    background=p.surface2, bordercolor=p.border,
                    arrowcolor=p.muted, padding=(8, 5))
    style.map("TCombobox", fieldbackground=[("readonly", p.input_bg)],
              foreground=[("readonly", p.text)],
              bordercolor=[("focus", p.accent)],
              arrowcolor=[("disabled", p.dim)])
    style.configure("TSpinbox", fieldbackground=p.input_bg, foreground=p.text,
                    bordercolor=p.border, arrowcolor=p.muted, padding=(6, 5))
    style.map("TSpinbox", bordercolor=[("focus", p.accent)])

    style.configure("TCheckbutton", background=p.surface, foreground=p.text,
                    indicatorcolor=p.input_bg, bordercolor=p.border,
                    indicatorbackground=p.input_bg)
    style.map("TCheckbutton",
              background=[("active", p.surface)],
              indicatorcolor=[("selected", p.accent), ("active", p.accent_hover)],
              foreground=[("disabled", p.dim)])
    style.configure("TRadiobutton", background=p.surface, foreground=p.text,
                    indicatorcolor=p.input_bg, indicatorbackground=p.input_bg)
    style.map("TRadiobutton",
              background=[("active", p.surface)],
              indicatorcolor=[("selected", p.accent), ("active", p.accent_hover)],
              foreground=[("disabled", p.dim)])

    style.configure("TNotebook", background=p.bg, bordercolor=p.border, tabmargins=(0, 2, 0, 0))
    style.configure("TNotebook.Tab", background=p.surface2, foreground=p.muted,
                    padding=(14, 8), bordercolor=p.border)
    style.map("TNotebook.Tab",
              background=[("selected", p.surface), ("active", p.hover)],
              foreground=[("selected", p.text)])

    style.configure("TProgressbar", background=p.accent, troughcolor=p.surface2,
                    bordercolor=p.border, thickness=6)
    style.configure("TScrollbar", background=p.surface2, troughcolor=p.bg,
                    bordercolor=p.border, arrowcolor=p.muted, width=10)
    style.map("TScrollbar", background=[("active", p.border), ("pressed", p.border)])
    style.configure("TSeparator", background=p.border)
    style.configure("Treeview", background=p.surface, fieldbackground=p.surface,
                    foreground=p.text, bordercolor=p.border)
    style.map("Treeview", background=[("selected", p.selected)])

    root.option_add("*TCombobox*Listbox.background", p.surface)
    root.option_add("*TCombobox*Listbox.foreground", p.text)
    root.option_add("*TCombobox*Listbox.selectBackground", p.accent)
    root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
    root.option_add("*TCombobox*Listbox.font", ("Microsoft YaHei UI", 10))
    return style


FONTS: Dict[str, tuple] = {
    "base": ("Microsoft YaHei UI", 10),
    "bold": ("Microsoft YaHei UI", 10, "bold"),
    "small": ("Microsoft YaHei UI", 9),
    "title": ("Microsoft YaHei UI", 17, "bold"),
    "h2": ("Microsoft YaHei UI", 12, "bold"),
    "h3": ("Microsoft YaHei UI", 10, "bold"),
    "mono": ("Cascadia Mono", 10),
    "mono_small": ("Cascadia Mono", 9),
    "big": ("Microsoft YaHei UI", 22, "bold"),
}
