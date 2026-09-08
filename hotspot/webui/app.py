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
    from .hotkey import start_global_hotkey

    _dpi_aware()
    api = HotspotBackend()
    api._exit_confirmed = False

    # 全局热键 Ctrl+Alt+H 开关热点（注册失败/被用户关闭时静默降级）
    if api.cfg.hotkey_enabled:
        start_global_hotkey(lambda: api.toggle())

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
    # 上次退出时悬浮窗开着 → 主窗 JS 就绪（前端调 boot_ready）后开浮窗、藏主窗。
    # 坑（探针实证）：
    #   1. 不能用 create_window(hidden=True)：pywebview 实现是 Opacity=0+Show+Hide，
    #      WebView2 控制器在隐藏窗口上初始化直接 E_ABORT，主窗全黑。
    #   2. 主窗 WebView2 初始化完成前并发开第二个控制器会 E_ABORT（浮窗黑屏）；
    #      loaded 事件与 evaluate_js 轮询均不可靠，故由前端 boot() 显式上报就绪。
    api._boot_mini_pending = bool(api.cfg.mini_window.get("show"))
    tray = TrayIcon(
        on_show=lambda: (window.show(), window.restore()),
        on_exit=lambda: (tray.stop(), window.destroy()),
    )
    api.attach_window(window)
    api._tray = tray

    MINI_FILE = UI_DIR / "mini.html"
    MINI_W, MINI_H = 160, 64   # 110% DPI 下 160 逻辑px = 175 整数物理px，右缘无分数缝隙
    MINI_BG = {"dark": "#11151f", "light": "#ffffff"}
    mini_holder = {"win": None}
    cfg = api.cfg      # 浮窗位置记忆读写用（_save_mini_pos / open_mini）

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
                        import ctypes
                        import ctypes.wintypes

                        form.BackColor = sd.ColorTranslator.FromHtml(bg_hex)
                        wb = _find_webview2(form)
                        if wb is not None:
                            try:
                                wb.DefaultBackgroundColor = alpha0
                            except Exception:
                                log.debug("WebView2 底色透明设置失败", exc_info=True)
                        # 圆角：优先 Win11 DWM 圆角（抗锯齿）；Win10 该 API 返回
                        # E_INVALIDARG → 回退 GDI Region。注意两点：
                        #   1. CreateRoundRectRgn 在 gdi32 不在 user32，r 参数是
                        #      椭圆"直径"，真实角半径 = r/2 → 直径取 2×10×dpr
                        #   2. Region 是 1-bit 硬裁剪，半径必须比 html 圆角大
                        #      2px（裁在线外），否则抗锯齿边框弧线被啃掉
                        user32 = ctypes.windll.user32
                        gdi32 = ctypes.windll.gdi32
                        hwnd = int(form.Handle.ToInt64())
                        # 浮窗不进任务栏：加 WS_EX_TOOLWINDOW 扩展样式。
                        # 不能用 form.ShowInTaskbar=False——运行时改会重建窗口句柄，
                        # 把正在进行的 WebView2 控制器初始化连根拔掉（E_ABORT 黑屏）。
                        try:
                            GWL_EXSTYLE, WS_EX_TOOLWINDOW = -20, 0x80
                            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_TOOLWINDOW)
                        except Exception:
                            pass
                        try:
                            dwm = ctypes.windll.dwmapi
                            pref = ctypes.c_int(2)   # DWMWCP_ROUND
                            if dwm.DwmSetWindowAttribute(
                                    hwnd, 33, ctypes.byref(pref), 4) == 0:
                                return               # DWM 圆角设置成功，不需要 Region
                        except Exception:
                            pass
                        dpr = user32.GetDpiForWindow(hwnd) / 96.0
                        d = max(4, int(round(2 * 11 * dpr)))   # 半径11px，比 html 的 10px 大 1px → 裁不到边框
                        rect = ctypes.wintypes.RECT()
                        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                            wp, hp = rect.right - rect.left, rect.bottom - rect.top
                            rgn = gdi32.CreateRoundRectRgn(0, 0, wp + 1, hp + 1, d, d)
                            if rgn:
                                user32.SetWindowRgn(hwnd, rgn, True)
                    except Exception:
                        log.debug("浮窗圆角设置失败", exc_info=True)

                form.BeginInvoke(Action(ui_work))
            except Exception:
                log.debug("浮窗样式设置失败", exc_info=True)
        import threading
        threading.Thread(target=work, daemon=True).start()

    def _save_mini_pos() -> None:
        """记录悬浮窗当前位置到配置。坐标统一存物理像素（GetWindowRect 原样），
        逻辑↔物理换算只在 open_mini 读取时做一次。"""
        try:
            import ctypes
            import ctypes.wintypes
            w = mini_holder["win"]
            if w is None:
                return
            rect = ctypes.wintypes.RECT()
            hwnd = int(w.native.Handle.ToInt64())
            if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                cfg.mini_window["x"] = int(rect.left)
                cfg.mini_window["y"] = int(rect.top)
                cfg.save()
        except Exception:
            log.debug("记录浮窗位置失败", exc_info=True)

    def _mini_dpr() -> float:
        """主屏 DPI 缩放系数（pywebview 对 x/y 的放大倍数）。"""
        try:
            user32 = ctypes.windll.user32
            # 主窗句柄取不到就退回主屏 DC 的 DPI
            w = window.native if getattr(window, "native", None) else None
            hwnd = int(w.Handle.ToInt64()) if w is not None else 0
            if hwnd:
                dpi = user32.GetDpiForWindow(hwnd)
                if dpi > 0:
                    return dpi / 96.0
            hdc = user32.GetDC(0)
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
            user32.ReleaseDC(0, hdc)
            return dpi / 96.0 if dpi > 0 else 1.0
        except Exception:
            return 1.0

    def _default_mini_pos() -> tuple[int, int]:
        """默认位置：右下角。返回逻辑像素（pywebview x/y 的坐标系）。"""
        try:
            import ctypes
            import ctypes.wintypes
            user32 = ctypes.windll.user32
            wa = ctypes.wintypes.RECT()
            # SPI_GETWORKAREA 返回物理像素 → 除以 dpr 换算成逻辑
            if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0):
                dpr = _mini_dpr()
                return (int(wa.right / dpr) - MINI_W - 24,
                        int(wa.bottom / dpr) - MINI_H - 24)
        except Exception:
            log.debug("计算浮窗默认位置失败", exc_info=True)
        return (80, 80)

    def open_mini(x: int | None = None, y: int | None = None) -> None:
        """打开悬浮窗。x/y 提供时放到指定逻辑像素位置；
        未提供时读位置记忆（物理像素 → 除以 dpr 转逻辑），无效或超界则回默认右下角。"""
        theme = getattr(api.cfg, "theme", "dark")
        if mini_holder["win"] is not None:
            try:
                mini_holder["win"].show()
                _apply_mini_style(mini_holder["win"], theme)
                return
            except Exception:
                mini_holder["win"] = None
        if x is None or y is None:
            mx = cfg.mini_window.get("x", -1)
            my = cfg.mini_window.get("y", -1)
            if mx >= 0 and my >= 0:
                dpr = _mini_dpr()
                # 物理像素换算成逻辑，再做屏幕边界防护（换错空间的旧记录必然超界）
                lx, ly = int(mx / dpr), int(my / dpr)
                sw = int(user32.GetSystemMetrics(0) / dpr) if (user32 := ctypes.windll.user32) else 0
                sh = int(user32.GetSystemMetrics(1) / dpr) if user32 else 0
                if sw > 0 and (lx + MINI_W > sw + 60 or ly + MINI_H > sh + 60):
                    lx, ly = _default_mini_pos()
                x, y = lx, ly
            else:
                x, y = _default_mini_pos()
        x = max(0, int(x))
        y = max(0, int(y))
        try:
            mw = webview.create_window(
                title="热点浮窗",
                url=MINI_FILE.as_uri(),
                js_api=api,
                width=MINI_W, height=MINI_H,
                min_size=(MINI_W, MINI_H),  # 默认 (200,100) 会把小窗强制撑大
                x=x, y=y,
                resizable=False,
                frameless=True,
                easy_drag=False,  # 必须 False：pywebview 内置 easy_drag 的全局 mousedown
                                  # 与自定义拖拽互相打架（吞点击、拖动错乱），拖拽全走 move_mini
                on_top=True,
                shadow=False,
                hidden=False,
            )

            def _mini_shown() -> None:
                # 延迟 1.2s：等 WebView2 控制器初始化完再动窗口样式（透明/圆角/工具窗），
                # 否则样式竞争会打断初始化（E_ABORT 黑屏）。延迟只影响样式，不影响功能。
                threading.Timer(1.2, _apply_mini_style,
                                (mw, getattr(api.cfg, "theme", "dark"))).start()

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
            # 打开后立刻记录初始位置（首次=右下角；之后=恢复位），后续拖拽再更新
            threading.Timer(0.6, _save_mini_pos).start()
        except Exception:
            log.exception("迷你浮窗创建失败")

    def _mini_drag_start_impl() -> None:
        """原生窗口拖拽：ReleaseCapture + WM_NCLBUTTONDOWN(HTCAPTION)。
        v9 探针实证：js_api 后台线程直接 SendMessage 完全无效（delta 0,0），
        必须 BeginInvoke 投递到 UI 线程（delta 200,100 精确跟手）——
        NCLBUTTONDOWN 要在窗口所属线程同步进入 DefWindowProc 移动循环。"""
        w = mini_holder["win"]
        if w is None:
            return
        try:
            import ctypes
            from System import Action

            form = w.native
            hwnd = int(form.Handle.ToInt64())

            def ui_drag():
                try:
                    ctypes.windll.user32.ReleaseCapture()
                    ctypes.windll.user32.SendMessageW(hwnd, 0xA1, 2, 0)
                    # SendMessage 返回 = 拖拽循环结束（用户已松手）→ 记录新位置
                    _save_mini_pos()
                except Exception:
                    log.debug("浮窗原生拖拽执行失败", exc_info=True)

            form.BeginInvoke(Action(ui_drag))
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

    # 启动恢复浮窗的接应点：主窗前端 boot() 调 backend.boot_ready() 时触发。
    # 这与"手动点按钮"的前置条件一致（主窗 JS 活跃 → WebView2 初始化完毕），
    # 过早开浮窗会与主窗 WebView2 并发初始化冲突（E_ABORT 黑屏）。
    def _boot_ready_impl() -> None:
        if not getattr(api, "_boot_mini_pending", False):
            return
        api._boot_mini_pending = False
        threading.Timer(0.5, open_mini).start()
        threading.Timer(2.0, window.hide).start()   # 浮窗落定后再藏主窗
    api._boot_ready = _boot_ready_impl
    if getattr(api, "_boot_mini_pending", False):
        # 保底：前端 15s 内没上报（加载失败/异常）→ 放弃恢复，主窗照常显示
        def _boot_give_up() -> None:
            if getattr(api, "_boot_mini_pending", False):
                api._boot_mini_pending = False
                log.warning("主窗 15s 未就绪，放弃恢复浮窗")
        threading.Timer(15.0, _boot_give_up).start()

    _boot_ts = time.time()

    def _record_mini_state(shown: bool) -> None:
        """退出路径统一记录：悬浮窗当前是否开着（show）+ 位置。"""
        try:
            cfg.mini_window["show"] = bool(shown and mini_holder["win"] is not None)
            if mini_holder["win"] is not None:
                _save_mini_pos()
            cfg.save()
        except Exception:
            log.debug("记录浮窗状态失败", exc_info=True)

    def _on_closing() -> bool:
        """窗口关闭请求：close_to_tray 开启且托盘可用 → 隐藏窗口、常驻托盘。"""
        if time.time() - _boot_ts < 5.0:
            log.warning("启动缓冲期内收到关闭请求，已忽略（防启动期闪退）")
            return False
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
        _record_mini_state(False)
        close_mini()
        return True

    def _on_closed() -> None:
        """主窗口真正销毁后：后端与托盘收尾。
        注意：不关闭悬浮窗——用户点 btnMini 的 JS 链是 open_mini 后 hide 主窗，
        悬浮窗必须继续存活（数据采集线程也保留）；真正退出走托盘菜单。
        退出前记录悬浮窗状态（show/位置），下次启动自动恢复。"""
        _record_mini_state(True)
        api.shutdown()
        tray.stop()

    window.events.closing += _on_closing
    window.events.closed += _on_closed
    tray.start()        # 常驻启动；是否隐藏到托盘由 _on_closing 按配置判断
    log.info("界面已启动")
    webview.start(debug=args.debug)
    try:
        tray.stop()   # 兜底销毁，防托盘图标残留
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
