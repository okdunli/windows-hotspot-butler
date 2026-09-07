"""设备图标：默认用 emoji 渲染（直观、跨主题一致），自定义图片仍走 PhotoImage。

说明：现代 Windows(10/11) + Tk 8.6.9+ 已能通过 "Segoe UI Emoji" 正常渲染彩色
emoji；旧环境若只显示单色/方块也不影响功能。
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List

from ..core.oui import DEVICE_TYPES

# 图标 key -> 中文名（与 core.oui.DEVICE_TYPES 对齐，外加 unknown）
ICON_KEYS: List[str] = [
    "phone", "tablet", "laptop", "desktop", "tv", "console", "watch",
    "speaker", "camera", "printer", "router", "nas", "iot", "car", "unknown",
]

ICON_LABELS: Dict[str, str] = dict(DEVICE_TYPES)

# 设备类型 -> emoji（直观、跨主题一致）
DEVICE_EMOJI: Dict[str, str] = {
    "phone": "📱", "tablet": "📟", "laptop": "💻", "desktop": "🖥️",
    "tv": "📺", "console": "🎮", "watch": "⌚", "speaker": "🔊",
    "camera": "📷", "printer": "🖨️", "router": "📡", "nas": "🗄️",
    "iot": "💡", "car": "🚗", "unknown": "❓",
}

# 侧边栏导航项 -> emoji
NAV_EMOJI: Dict[str, str] = {
    "概览": "📊", "热点设置": "📶", "已连接设备": "📱",
    "欢迎页": "🌐", "设置": "⚙️",
}


def emoji_font(size: int):
    """返回能渲染 emoji 的字体（优先 Segoe UI Emoji，缺失时由系统回退）。"""
    return ("Segoe UI Emoji", max(8, int(size)))


def _r(c, x0, y0, x1, y1, r, **kw) -> int:
    """圆角矩形（多边形近似，兼容性最好）。"""
    r = max(0.0, min(float(r), (x1 - x0) / 2, (y1 - y0) / 2))
    pts: List[float] = []
    corners = (
        (x0 + r, y0 + r, 180, 270),
        (x1 - r, y0 + r, 270, 360),
        (x1 - r, y1 - r, 0, 90),
        (x0 + r, y1 - r, 90, 180),
    )
    for ox, oy, a0, a1 in corners:
        for i in range(6):
            ang = math.radians(a0 + (a1 - a0) * i / 5)
            pts += [ox + r * math.cos(ang), oy - r * math.sin(ang)]
    return c.create_polygon(pts, smooth=True, **kw)


def _circle(c, cx, cy, r, **kw) -> int:
    return c.create_oval(cx - r, cy - r, cx + r, cy + r, **kw)


# --------------------------------------------------------------------------- #
#                              各设备图标绘制                                 #
# --------------------------------------------------------------------------- #
def _phone(c, x, y, s, fill, bg) -> None:
    _r(c, x + .30 * s, y + .05 * s, x + .70 * s, y + .95 * s, .08 * s, fill=fill, outline=bg, width=1)
    c.create_rectangle(x + .43 * s, y + .91 * s, x + .57 * s, y + .935 * s, fill=bg, outline="")


def _tablet(c, x, y, s, fill, bg) -> None:
    _r(c, x + .20 * s, y + .08 * s, x + .80 * s, y + .92 * s, .06 * s, fill=fill, outline=bg, width=1)
    _circle(c, x + .50 * s, y + .89 * s, .022 * s, fill=bg, outline="")
    _circle(c, x + .70 * s, y + .12 * s, .018 * s, fill=bg, outline="")


def _laptop(c, x, y, s, fill, bg) -> None:
    _r(c, x + .14 * s, y + .16 * s, x + .86 * s, y + .66 * s, .04 * s, fill=fill, outline=bg, width=1)
    c.create_polygon(
        x + .06 * s, y + .82 * s, x + .18 * s, y + .68 * s,
        x + .82 * s, y + .68 * s, x + .94 * s, y + .82 * s,
        fill=fill, outline=bg, width=1,
    )


def _desktop(c, x, y, s, fill, bg) -> None:
    _r(c, x + .08 * s, y + .12 * s, x + .92 * s, y + .64 * s, .04 * s, fill=fill, outline=bg, width=1)
    c.create_rectangle(x + .44 * s, y + .64 * s, x + .56 * s, y + .76 * s, fill=fill, outline="")
    c.create_rectangle(x + .28 * s, y + .76 * s, x + .72 * s, y + .84 * s, fill=fill, outline="")


def _tv(c, x, y, s, fill, bg) -> None:
    _r(c, x + .05 * s, y + .14 * s, x + .95 * s, y + .70 * s, .04 * s, fill=fill, outline=bg, width=1)
    c.create_rectangle(x + .44 * s, y + .70 * s, x + .56 * s, y + .80 * s, fill=fill, outline="")
    c.create_rectangle(x + .26 * s, y + .80 * s, x + .74 * s, y + .86 * s, fill=fill, outline="")


def _console(c, x, y, s, fill, bg) -> None:
    _r(c, x + .12 * s, y + .34 * s, x + .88 * s, y + .74 * s, .14 * s, fill=fill, outline=bg, width=1)
    _circle(c, x + .34 * s, y + .54 * s, .055 * s, fill=bg, outline="")
    _circle(c, x + .60 * s, y + .52 * s, .045 * s, fill=bg, outline="")
    _circle(c, x + .68 * s, y + .60 * s, .045 * s, fill=bg, outline="")


def _watch(c, x, y, s, fill, bg) -> None:
    c.create_rectangle(x + .38 * s, y + .04 * s, x + .62 * s, y + .24 * s, fill=fill, outline="")
    c.create_rectangle(x + .38 * s, y + .76 * s, x + .62 * s, y + .96 * s, fill=fill, outline="")
    _r(c, x + .28 * s, y + .22 * s, x + .72 * s, y + .78 * s, .10 * s, fill=fill, outline=bg, width=1)


def _speaker(c, x, y, s, fill, bg) -> None:
    _r(c, x + .30 * s, y + .08 * s, x + .70 * s, y + .92 * s, .07 * s, fill=fill, outline=bg, width=1)
    _circle(c, x + .50 * s, y + .34 * s, .10 * s, fill=bg, outline="")
    _circle(c, x + .50 * s, y + .66 * s, .065 * s, fill=bg, outline="")


def _camera(c, x, y, s, fill, bg) -> None:
    _r(c, x + .10 * s, y + .28 * s, x + .90 * s, y + .78 * s, .06 * s, fill=fill, outline=bg, width=1)
    c.create_polygon(
        x + .40 * s, y + .28 * s, x + .48 * s, y + .17 * s,
        x + .76 * s, y + .17 * s, x + .84 * s, y + .28 * s,
        fill=fill, outline="",
    )
    _circle(c, x + .50 * s, y + .53 * s, .13 * s, fill=bg, outline="")
    _circle(c, x + .78 * s, y + .38 * s, .028 * s, fill=fill, outline="")


def _printer(c, x, y, s, fill, bg) -> None:
    c.create_rectangle(x + .28 * s, y + .06 * s, x + .72 * s, y + .26 * s, fill=fill, outline=bg, width=1)
    _r(c, x + .10 * s, y + .26 * s, x + .90 * s, y + .62 * s, .05 * s, fill=fill, outline=bg, width=1)
    c.create_rectangle(x + .22 * s, y + .62 * s, x + .78 * s, y + .88 * s, fill=fill, outline=bg, width=1)


def _router(c, x, y, s, fill, bg) -> None:
    c.create_line(x + .30 * s, y + .36 * s, x + .18 * s, y + .12 * s,
                  fill=fill, width=max(2, int(.045 * s)))
    c.create_line(x + .70 * s, y + .36 * s, x + .82 * s, y + .12 * s,
                  fill=fill, width=max(2, int(.045 * s)))
    _r(c, x + .10 * s, y + .36 * s, x + .90 * s, y + .74 * s, .06 * s, fill=fill, outline=bg, width=1)
    for i in range(3):
        _circle(c, x + (.30 + i * .20) * s, y + .55 * s, .035 * s, fill=bg, outline="")


def _nas(c, x, y, s, fill, bg) -> None:
    _r(c, x + .20 * s, y + .10 * s, x + .80 * s, y + .90 * s, .06 * s, fill=fill, outline=bg, width=1)
    for i in range(3):
        cy = y + (.28 + i * .22) * s
        c.create_rectangle(x + .30 * s, cy, x + .70 * s, cy + .12 * s, fill=bg, outline="")
        _circle(c, x + .66 * s, cy + .06 * s, .022 * s, fill=fill, outline="")


def _iot(c, x, y, s, fill, bg) -> None:
    _r(c, x + .26 * s, y + .26 * s, x + .74 * s, y + .74 * s, .05 * s, fill=fill, outline=bg, width=1)
    c.create_rectangle(x + .38 * s, y + .40 * s, x + .62 * s, y + .60 * s, fill=bg, outline="")
    for i in range(3):
        oy = y + (.36 + i * .14) * s
        c.create_line(x + .12 * s, oy, x + .26 * s, oy, fill=fill, width=max(1, int(.035 * s)))
        c.create_line(x + .74 * s, oy, x + .88 * s, oy, fill=fill, width=max(1, int(.035 * s)))


def _car(c, x, y, s, fill, bg) -> None:
    c.create_polygon(
        x + .08 * s, y + .70 * s, x + .18 * s, y + .44 * s, x + .36 * s, y + .38 * s,
        x + .64 * s, y + .38 * s, x + .84 * s, y + .46 * s, x + .94 * s, y + .70 * s,
        fill=fill, outline=bg, width=1,
    )
    _circle(c, x + .30 * s, y + .74 * s, .09 * s, fill=bg, outline="")
    _circle(c, x + .72 * s, y + .74 * s, .09 * s, fill=bg, outline="")


def _unknown(c, x, y, s, fill, bg) -> None:
    _r(c, x + .18 * s, y + .18 * s, x + .82 * s, y + .82 * s, .06 * s, fill=fill, outline=bg, width=1)
    c.create_text(
        x + .50 * s, y + .50 * s, text="?", fill=bg,
        font=("Segoe UI", max(9, int(.40 * s)), "bold"),
    )


DRAWERS: Dict[str, Callable] = {
    "phone": _phone, "tablet": _tablet, "laptop": _laptop, "desktop": _desktop,
    "tv": _tv, "console": _console, "watch": _watch, "speaker": _speaker,
    "camera": _camera, "printer": _printer, "router": _router, "nas": _nas,
    "iot": _iot, "car": _car, "unknown": _unknown,
}


def draw_icon(canvas, kind: str, x: float, y: float, size: int,
              color: str = "#79C0FF", bg: str = "#0D1117") -> None:
    """在 canvas 的 (x,y,size,size) 区域内绘制设备图标（emoji）。

    自定义图片由调用方单独处理（见 devices_page 的 custom 分支）。
    """
    emoji = DEVICE_EMOJI.get(kind if kind in DEVICE_EMOJI else "unknown", "❓")
    try:
        canvas.create_text(
            x + size / 2, y + size / 2, text=emoji, anchor="center",
            font=emoji_font(size * 0.8),
        )
    except Exception:
        try:
            canvas.create_text(x + size / 2, y + size / 2, text="?", anchor="center")
        except Exception:
            pass


def load_custom_icon(path: str, size: int = 28):
    """加载用户自定义图片为 PhotoImage（优先 PIL，否则用内置 PhotoImage 缩放）。

    失败返回 None。返回的 PhotoImage 需由调用方保持引用，否则会被垃圾回收。
    """
    try:
        from PIL import Image, ImageTk  # type: ignore

        img = Image.open(path).convert("RGBA")
        img.thumbnail((size, size))
        return ImageTk.PhotoImage(img)
    except Exception:
        pass
    try:
        from tkinter import PhotoImage

        img = PhotoImage(file=path)
        w, h = img.width(), img.height()
        factor = max(1, int(max(w / size, h / size)))
        if factor > 1:
            img = img.subsample(factor, factor)
        return img
    except Exception:
        return None
