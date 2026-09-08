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
    #   - SetWindowRgn 圆角裁剪 → 1-bit 硬裁剪有锯齿，且半径参数是椭圆直径易算错
    #   - 最终方案：Win11 DWM 圆角（DWMWA_WINDOW_CORNER_PREFERENCE=DWMWCP_ROUND）
    #     → DWM 合成器渲染，自带抗锯齿，系统固定 ~8px 半径；
    #     Win10 无此 API 时回退 GDI Region（直径=2×半径），html radius 同步 8px
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
                        # E_INVALIDARG → 回退 GDI Region。注意两点（v9 探针实证）：
                        #   1. CreateRoundRectRgn 在 gdi32 不在 user32，r 参数是
                        #      椭圆"直径"，真实角半径 = r/2 → 直径取 2×8×dpr
                        #   2. Region + 透明 html 会杀死点击；mini.html 是不透明
                        #      渐变，无此问题（v7/v9 对照）
                        user32 = ctypes.windll.user32
                        gdi32 = ctypes.windll.gdi32
                        # 浮窗不进任务栏。注意：运行时改 ShowInTaskbar 会重建
                        # 窗口句柄 → hwnd 必须在此之后重新读取（下方重新取）
                        try:
                            form.ShowInTaskbar = False
                        except Exception:
                            pass
                        hwnd = int(form.Handle.ToInt64())
                        try:
                            dwm = ctypes.windll.dwmapi
                            pref = ctypes.c_int(2)   # DWMWCP_ROUND
                            if dwm.DwmSetWindowAttribute(
                                    hwnd, 33, ctypes.byref(pref), 4) == 0:
                                return               # DWM 圆角设置成功，不需要 Region
                        except Exception:
                            pass
                        # ---- Win10 回退路径：GDI Region ----
                        dpr = user32.GetDpiForWindow(hwnd) / 96.0
                        d = max(4, int(round(2 * 8 * dpr)))   # 直径 = 2×半径8px，对齐 html border-radius:8px
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
        """记录悬浮窗当前位置到配置（物理像素）。位置获取失败则保留旧值。"""
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

    def open_mini(x: int | None = None, y: int | None = None) -> None:
        """打开悬浮窗。x/y 提供时放到指定物理像素位置（位置记忆恢复）；
        未提供且无记录时默认放右下角（留 24px 边距）。"""
        theme = getattr(api.cfg, "theme", "dark")
        if mini_holder["win"] is not None:
            try:
                mini_holder["win"].show()
                _apply_mini_style(mini_holder["win"], theme)
                return
            except Exception:
                mini_holder["win"] = None
        # 首次无记录 → 默认右下角（主屏工作区，避开任务栏）
        if x is None or y is None:
            mx = cfg.mini_window.get("x", -1)
            my = cfg.mini_window.get("y", -1)
            if mx >= 0 and my >= 0:
                x, y = mx, my
            else:
                try:
                    user32 = ctypes.windll.user32
                    # SPI_GETWORKAREA → 任务栏以外的桌面区域
                    wa = ctypes.wintypes.RECT()
                    if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0):
                        x = wa.right - MINI_W - 24
                        y = wa.bottom - MINI_H - 24
                    else:
                        x = y = 80
                except Exception:
                    x = y = 80
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
        # 启动头 5 秒内的关闭请求一律忽略并留痕：曾出现过启动 ~9s 静默退出
        # （无 Python 异常、无原生崩溃记录），疑似启动期状态未就绪时被意外
        # close 信号穿透 _on_closing 放行。缓冲期 + 日志便于再发生时定位。
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
    # 上次退出时悬浮窗开着 → 本次启动自动恢复（延迟到界面就绪后；位置走记忆，
    # 无记录则首次默认右下角）。窗口仍在主界面显示，用户可手动再收进浮窗。
    if cfg.mini_window.get("show"):
        threading.Timer(2.0, open_mini).start()
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
