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
    from .tray import TrayIcon

    _dpi_aware()
    api = HotspotBackend()
    api._exit_confirmed = False

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
    # ---- 托盘（close_to_tray 开启时：关闭窗口 = 隐藏到托盘） ----
    tray = TrayIcon(
        on_show=lambda: (window.show(), window.restore()),
        on_exit=lambda: (tray.stop(), window.destroy()),
    )
    api.attach_window(window)
    api._tray = tray

    # ---- 迷你悬浮窗（第二个 frameless 小窗，置顶、可拖动） ----
    MINI_FILE = UI_DIR / "mini.html"
    mini_holder = {"win": None}

    def open_mini() -> None:
        if mini_holder["win"] is not None:
            try:
                mini_holder["win"].show()
                return
            except Exception:
                mini_holder["win"] = None
        try:
            mw = webview.create_window(
                title="热点浮窗",
                url=MINI_FILE.as_uri(),
                js_api=api,
                width=190, height=64, resizable=False,
                frameless=True, easy_drag=True, on_top=True,
                hidden=False, background_color="#11151f",
            )
            mini_holder["win"] = mw
            api._close_mini = close_mini

            def _mini_closed() -> None:
                mini_holder["win"] = None
            mw.events.closed += _mini_closed
        except Exception:
            log.exception("迷你浮窗创建失败")

    def close_mini() -> None:
        w = mini_holder["win"]
        if w is not None:
            try:
                w.destroy()
            except Exception:
                pass
            mini_holder["win"] = None
    api._open_mini = open_mini
    api._close_mini = close_mini

    def _on_closing() -> bool:
        """窗口关闭请求：close_to_tray 开启且托盘可用 → 隐藏窗口、常驻托盘。"""
        if api.cfg.close_to_tray and tray.available:
            window.hide()
            tray.notify("已最小化到托盘，点击图标可恢复显示")
            return False        # 阻止真正的关闭
        # 热点还开着 → 确认（浏览器原生 confirm 不适用于 frameless，用 JS 弹层由后端二次调用）
        if api.cfg.confirm_exit_hotspot and api.controller.last_status.active \
                and not api._exit_confirmed:
            api._exit_confirmed = True   # 第二次点 ✕ 视为确认
            try:
                window.evaluate_js("window.__confirmExit && window.__confirmExit()")
            except Exception:
                pass
            return False
        close_mini()
        return True

    def _on_closed() -> None:
        api.shutdown()
        tray.stop()
        close_mini()

    window.events.closing += _on_closing
    window.events.closed += _on_closed
    tray.start()        # 常驻启动；是否隐藏到托盘由 _on_closing 按配置判断
    log.info("界面已启动")
    webview.start(debug=args.debug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
