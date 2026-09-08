"""新界面入口：PyWebView 承载本地 HTML/CSS/JS，Python 只做后端。

窗口无边框，标题栏用 .pywebview-drag-region 拖动（pywebview 内置 easy_drag）。
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

import ctypes.wintypes  # noqa: F401  (圆角 Region 用)

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

    # ---- 迷你悬浮窗（第二个 frameless 小窗，置顶、可拖动、色键透明圆角） ----
    MINI_FILE = UI_DIR / "mini.html"
    MINI_W, MINI_H = 192, 64
    # 抗锯齿圆角方案（SetWindowRgn 是 1-bit 硬裁剪必有锯齿，已废弃）：
    #   1. WebView2 DefaultBackgroundColor = alpha 0（页面圆角外像素不画）
    #   2. Form TransparencyKey/BackColor = 色键（Form 表面整面抠成透明）
    #   3. mini.html 圆角外透明 → 角落透出桌面，圆弧边缘保留 HTML 抗锯齿
    # 色键取卡片描边邻近色，不能与卡片内任何颜色相同，否则被抠洞。
    MINI_KEY_HEX = "#1e2d49"
    mini_holder = {"win": None}

    def _find_webview2(ctrl):
        for c in ctrl.Controls:
            try:
                name = str(type(c))
            except Exception:
                name = ""
            if "WebView2" in name or "WebBrowser" in name:
                return c
            hit = _find_webview2(c)
            if hit is not None:
                return hit
        return None

    def _apply_colorkey(w, stage: str = "shown") -> None:
        """色键透明。WebView2 COM 属性必须投递到 UI 线程设置（后台线程直接调
        会抛 CoreWebView2Controller members can only be accessed from the UI thread）。
        file:// 导航会重置 WebView2 底色 → shown 和 loaded 各设一次（loaded 晚于 shown）。"""

        def work() -> None:
            try:
                import System.Drawing as sd
                from System import Action

                key = sd.ColorTranslator.FromHtml(MINI_KEY_HEX)
                alpha0 = sd.Color.FromArgb(0, 0, 0, 0)
                form = w.native

                def ui_work():
                    form.TransparencyKey = key
                    form.BackColor = key
                    wb = _find_webview2(form)
                    if wb is not None:
                        try:
                            wb.DefaultBackgroundColor = alpha0
                            if stage == "loaded":
                                print("[mini] colorkey applied at loaded", flush=True)
                        except Exception:
                            log.debug("WebView2 底色透明设置失败", exc_info=True)

                form.BeginInvoke(Action(ui_work))
            except Exception:
                log.debug("色键透明失败", exc_info=True)
        import threading
        threading.Thread(target=work, daemon=True).start()

    def open_mini() -> None:
        if mini_holder["win"] is not None:
            try:
                mini_holder["win"].show()
                _apply_colorkey(mini_holder["win"])
                return
            except Exception:
                mini_holder["win"] = None
        try:
            mw = webview.create_window(
                title="热点浮窗",
                url=MINI_FILE.as_uri(),
                js_api=api,
                width=MINI_W, height=MINI_H,
                min_size=(MINI_W, MINI_H),  # 默认 (200,100) 会把小窗强制撑大
                resizable=False,
                frameless=True, easy_drag=True, on_top=True,
                shadow=False,
                hidden=False,
            )

            def _mini_shown() -> None:
                _apply_colorkey(mw, "shown")

            def _mini_loaded() -> None:
                # file:// 导航完成后 WebView2 会重置底色，必须补一刀
                _apply_colorkey(mw, "loaded")
            mw.events.shown += _mini_shown
            mw.events.loaded += _mini_loaded
            # 兜底：2s 后再补一次（loaded 可能早于我们订阅，或渲染树晚完成）
            def _mini_late() -> None:
                time.sleep(2.0)
                try:
                    _apply_colorkey(mw, "loaded")
                except Exception:
                    pass
            def _mini_late() -> None:
                time.sleep(2.0)
                try:
                    _apply_colorkey(mw, "loaded")
                except Exception:
                    pass
            threading.Thread(target=_mini_late, daemon=True).start()
            mini_holder["win"] = mw
            api._mini_window = mw
            api._close_mini = close_mini

            def _mini_closed() -> None:
                mini_holder["win"] = None
            mw.events.closed += _mini_closed
        except Exception:
            log.exception("迷你浮窗创建失败")

    def _move_mini_impl(dx: float, dy: float) -> None:
        """增量移动浮窗：读物理位置 → SetWindowPos。JS 屏幕坐标=物理像素。"""
        w = mini_holder["win"]
        if w is None:
            return
        try:
            import ctypes
            import ctypes.wintypes

            hwnd = int(w.native.Handle.ToInt64())
            user32 = ctypes.windll.user32
            rect = ctypes.wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            # JS screenX/screenY 是物理像素；窗口物理位置 + 增量
            user32.SetWindowPos(
                hwnd, None,
                int(rect.left + dx), int(rect.top + dy),
                None, None, 0x0001 | 0x0004 | 0x0040,  # NOSIZE|NOZORDER|SHOWWINDOW
            )
        except Exception:
            log.debug("浮窗增量移动失败", exc_info=True)

    api._app_ref = {"move_mini": _move_mini_impl}

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
