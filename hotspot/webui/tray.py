"""系统托盘：关闭窗口时最小化到托盘（可选），托盘菜单可显示/退出。

pystray 为软依赖：未安装时 close_to_tray 自动降级为直接退出。
托盘线程为 daemon，主窗口 destroy 后由 pywebview 的退出逻辑自然收尾。
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

log = logging.getLogger(__name__)

try:
    import pystray
    from PIL import Image, ImageDraw

    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False


def _icon_image():
    """64x64 的 📶 风格托盘图标（信号弧线），避免依赖外部图片文件。"""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy = 32, 54
    for i, r in enumerate((10, 20, 30)):
        bbox = [cx - r, cy - r, cx + r, cy + r]
        d.arc(bbox, start=225, end=315, fill=(88, 166, 255, 255), width=6)
    d.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill=(88, 166, 255, 255))
    return img


class TrayIcon:
    """托盘图标。show/hide 通过回调驱动主窗口（pywebview 线程安全封装见 app.py）。"""

    def __init__(self, on_show: Callable[[], None], on_exit: Callable[[], None],
                 tooltip: str = "WiFi 热点管理器") -> None:
        self._on_show = on_show
        self._on_exit = on_exit
        self._icon: Optional["pystray.Icon"] = None
        if not HAS_DEPS:
            log.info("pystray/Pillow 未安装，托盘功能不可用（pip install pystray pillow）")
            return
        self._icon = pystray.Icon(
            "WifiHotspotManager", _icon_image(), tooltip,
            menu=pystray.Menu(
                pystray.MenuItem("显示主界面", lambda: self._safe(on_show), default=True),
                pystray.MenuItem("退出", lambda: self._safe(on_exit)),
            ),
        )

    @staticmethod
    def _safe(fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception:
            log.exception("托盘回调异常")

    @property
    def available(self) -> bool:
        return self._icon is not None

    def start(self) -> None:
        if self._icon is not None:
            threading.Thread(target=self._icon.run, name="whm-tray", daemon=True).start()

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                log.debug("托盘停止异常", exc_info=True)
            self._icon = None

    def notify(self, text: str) -> None:
        if self._icon is not None:
            try:
                self._icon.notify(text, "WiFi 热点管理器")
            except Exception:
                log.debug("托盘通知异常", exc_info=True)

    def set_tooltip(self, text: str) -> None:
        if self._icon is not None:
            try:
                self._icon.title = text
            except Exception:
                log.debug("托盘提示更新失败", exc_info=True)
