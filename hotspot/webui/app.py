"""新界面入口：PyWebView 承载本地 HTML/CSS/JS，Python 只做后端。

窗口无边框，标题栏用 .pywebview-drag-region 拖动（pywebview 内置 easy_drag）。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

UI_DIR = Path(__file__).resolve().parent / "ui"
INDEX_FILE = UI_DIR / "index.html"


def _dpi_aware() -> None:
    if sys.platform != "win32":
        return
    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="WiFi 热点管理器 · 新界面")
    parser.add_argument("--verbose", action="store_true", help="输出详细日志")
    parser.add_argument("--debug", action="store_true", help="开启 WebView 调试")
    args = parser.parse_args(argv)

    from ..core.paths import setup_logging

    setup_logging(verbose=args.verbose)
    log = logging.getLogger("webui")

    if sys.platform != "win32":
        print("本工具仅支持 Windows 10 / 11。")
        return 1

    try:
        import webview
    except ImportError:
        print("缺少 pywebview，请先执行：pip install pywebview")
        return 2

    if not INDEX_FILE.exists():
        print(f"界面文件缺失：{INDEX_FILE}")
        return 3

    from .backend import HotspotBackend

    _dpi_aware()
    api = HotspotBackend()

    window = webview.create_window(
        title="WiFi 热点管理器",
        url=INDEX_FILE.as_uri(),
        js_api=api,
        width=1000,
        height=680,
        min_size=(880, 600),
        frameless=True,
        easy_drag=True,
        background_color="#0b0e14",
    )
    api.attach_window(window)

    def _on_closed() -> None:
        api.shutdown()

    window.events.closed += _on_closed
    log.info("界面已启动")
    webview.start(debug=args.debug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
