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

    # ---- 迷你悬浮窗（第二个 frameless 小窗，置顶、可拖动） ----
    MINI_FILE = UI_DIR / "mini.html"
    MINI_W, MINI_H = 160, 64   # 110% DPI 下 160 逻辑px = 175 整数物理px，右缘无分数缝隙
    # 圆角/点击实验结论（SendInput 真实鼠标矩阵测试 + v7 探针）：
    #   - TransparencyKey（任何配置）→ layered hit-test 全窗口穿透（点击落到下层）
    #   - SetWindowRgn 圆角裁剪 + alpha0 透明 html → 同样破坏子窗口鼠标消息
    #   - 最终方案（v7 探针验证 CLICK YES）：html 铺不透明渐变 +
    #     SetWindowRgn 圆角裁剪 → 圆角外直接透出桌面（真圆角），点击正常，
    #     且拖拽走 Windows 原生 WM_NCLBUTTONDOWN/HTCAPTION（与主窗口顶栏同机制）
    MINI_BG = {"dark": "#11151f", "light": "#ffffff"}
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

    def _apply_mini_style(w, theme: str = "dark") -> None:
        """WebView2 COM 属性必须投递到 UI 线程设置（后台线程直接调
        会抛 CoreWebView2Controller members can only be accessed from the UI thread）。"""

        def work() -> None:
            try:
                import System.Drawing as sd
                from System import Action

                bg_hex = MINI_BG.get(theme, MINI_BG["dark"])
                alpha0 = sd.Color.FromArgb(0, 0, 0, 0)
                form = w.native

                def ui_work():
                    try:
                        form.BackColor = sd.ColorTranslator.FromHtml(bg_hex)
                    except Exception:
                        pass
                    wb = _find_webview2(form)
                    if wb is not None:
                        try:
                            wb.DefaultBackgroundColor = alpha0
                        except Exception:
                            log.debug("WebView2 底色透明设置失败", exc_info=True)

                form.BeginInvoke(Action(ui_work))
            except Exception:
                log.debug("浮窗样式设置失败", exc_info=True)
        import threading
        threading.Thread(target=work, daemon=True).start()

    def open_mini() -> None:
        theme = getattr(api.cfg, "theme", "dark")
        if mini_holder["win"] is not None:
            try:
                mini_holder["win"].show()
                _apply_mini_style(mini_holder["win"], theme)
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
                frameless=True,
                easy_drag=False,  # 必须 False：pywebview 内置 easy_drag 的全局 mousedown
                                  # 与自定义拖拽互相打架（吞点击、拖动错乱），拖拽全走 move_mini
                on_top=True,
                shadow=False,
                hidden=False,
            )

            def _mini_shown() -> None:
                _apply_mini_style(mw, getattr(api.cfg, "theme", "dark"))

            def _mini_loaded() -> None:
                # file:// 导航完成后 WebView2 可能重置底色，补一刀
                _apply_mini_style(mw, getattr(api.cfg, "theme", "dark"))
            mw.events.shown += _mini_shown
            mw.events.loaded += _mini_loaded
            mini_holder["win"] = mw
            api._mini_window = mw
            api._close_mini = close_mini

            def _mini_closed() -> None:
                mini_holder["win"] = None
            mw.events.closed += _mini_closed
        except Exception:
            log.exception("迷你浮窗创建失败")

    def _mini_drag_start_impl() -> None:
        """原生窗口拖拽：ReleaseCapture + WM_NCLBUTTONDOWN(HTCAPTION)。
        交还控制权给 Windows DefWindowProc 的移动循环，拖动轨迹与鼠标
        完全一致（和主窗口顶栏拖拽同机制），无任何坐标换算偏差。"""
        w = mini_holder["win"]
        if w is None:
            return
        try:
            import ctypes

            hwnd = int(w.native.Handle.ToInt64())
            user32 = ctypes.windll.user32
            user32.ReleaseCapture()
            user32.SendMessageW(hwnd, 0xA1, 2, 0)   # WM_NCLBUTTONDOWN, HTCAPTION
        except Exception:
            log.debug("浮窗原生拖拽失败", exc_info=True)

    api._app_ref = {"mini_drag_start": _mini_drag_start_impl}
    api._mini_style = _apply_mini_style

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
    # 兜底：无论从哪条路径退出（窗口 destroy / 托盘菜单退出 / 确认退出），
    # webview.start 返回即主事件循环结束，托盘若还活着必须销毁，
    # 否则 pystray daemon 线程虽死但 Explorer 托盘区图标残留。
    try:
        tray.stop()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
