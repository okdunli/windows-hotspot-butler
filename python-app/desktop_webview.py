"""桌面外壳：pywebview 主窗 + **原生迷你悬浮窗**（原版 webui/app.py 同款）。

原版 `hotspot/webui/app.py` 是「无边框 pywebview 主窗 + 原生第二个 pywebview
浮窗」，主窗与浮窗都挂 `js_api=HotspotBackend`。本移植：
  * 主窗加载逐字节原版 `public/index.html`（app.js 把 pywebview api 桥成 /api/*，
    因为主窗跑在 http:// 上才能 fetch 后端）。
  * 浮窗加载逐字节原版 `public/mini.html`（自包含、只调 window.pywebview.api.*，
    用 file:// 即可）——因此 **mini.html 一个字节都不用改**。
  * 两个窗口共享 `server.get_backend()` 这同一个后端单例：HTTP 请求与
    pywebview js_api 调用落到同一个 HotspotBackend，状态/配置/采集完全一致。

浮窗逻辑（位置记忆、DPI、右下角默认位、圆角/工具窗样式、原生拖拽、
boot_ready 恢复、toggle/close 钩子）从原版 app.py 移植，非 Windows 平台
优雅降级（不做 WinForms 专属样式/拖拽，但窗口仍能创建）。

    python python-app/desktop_webview.py --port 8787
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

log = logging.getLogger("webui")

MINI_W, MINI_H = 160, 64   # 110% DPI 下 160 逻辑px = 175 整数物理px
MINI_BG = {"dark": "#11151f", "light": "#ffffff"}


def _public_root() -> Path | None:
    # 源码形态 <root>/public；PyInstaller 形态随包携带的 public/
    candidates = [
        ROOT / "public",
        Path(getattr(sys, "_MEIPASS", "")) / "public" if getattr(sys, "frozen", False) else Path(""),
    ]
    for p in candidates:
        if p.is_dir() and (p / "index.html").is_file():
            return p
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Windows Hotspot Butler")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--debug", action="store_true", help="WebView 调试")
    ap.add_argument("--no-frameless", action="store_true", help="退回系统边框（排障用）")
    ap.add_argument("--resize-seq", default="", help="自适应验证：880x600,1200x900 顺序缩放")
    args = ap.parse_args(argv)

    # ---- DPI 感知（原版 app.py::_dpi_aware，必须在创建窗口前调用）----
    if sys.platform == "win32":
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    try:
        import webview
    except ImportError:
        print("缺少 pywebview。请执行：pip install pywebview")
        print("（或 python python-app/run.py --tk 使用纯 Tk 降级界面）")
        return 2

    pub = _public_root()
    if pub is None:
        print("界面文件缺失：未找到 public/index.html。")
        print("请先在项目根运行：node rosezm-cli.mjs build .")
        return 3

    from server import get_backend, attach_main_window
    backend = get_backend()           # 与 HTTP 服务同一个后端单例（零改动原版类）
    base = os.environ.get("ROSEZM_API") or f"http://127.0.0.1:{args.port}"
    main_url = base.rstrip("/") + "/public/index.html"
    MINI_FILE = pub / "mini.html"
    # 原版同款图标：hotspot/assets/icon.ico（窗口/任务栏）——原版
    # webview.start(icon=ICON_ICO) 同参数。
    ICON_ICO = Path(__file__).resolve().parent / "hotspot" / "assets" / "icon.ico"

    # ---- 主窗：参数与原版 webui/app.py 的 create_window 一致 ----
    # shadow=False：pywebview 默认 shadow=True 会 ExtendFrameIntoClientArea(1px)
    # + Enable DWM 非客户区渲染，窗口一 resize 就合成出黑色边框（顶部黑边）。
    # 无边框窗口不需要系统阴影，关掉即可，resize 后也不会再有黑条。
    window = webview.create_window(
        title="Windows Hotspot Butler",
        url=main_url,
        js_api=backend,
        width=1000,
        height=620,
        min_size=(880, 560),
        frameless=not args.no_frameless,
        easy_drag=True,
        shadow=False,
        background_color="#0b0e14",
    )
    backend.attach_window(window)
    backend._exit_confirmed = False
    attach_main_window(window)   # 最大化/还原 + 边缘缩放（server 层 /api/op/*）

    # 上次退出时浮窗开着 → 主窗 JS 就绪（前端调 boot_ready）后再开浮窗，
    # 避免主窗 WebView2 初始化期并发开第二个控制器 E_ABORT 黑屏。
    backend._boot_mini_pending = bool(backend.cfg.mini_window.get("show"))

    # ---- 托盘（原版 tray.py 零改动）：显示主界面 / 退出 ----
    from hotspot.webui.tray import TrayIcon
    tray = TrayIcon(
        on_show=lambda: (window.show(), window.restore()),
        on_exit=lambda: (tray.stop(), window.destroy()),
    )
    backend._tray = tray

    # ---- 全局热键 Ctrl+Alt+H（原版 app.py 同款，注册失败静默降级）----
    if backend.cfg.hotkey_enabled:
        try:
            from hotspot.webui.hotkey import start_global_hotkey
            ok = start_global_hotkey(lambda: backend.toggle())
            print(f"hotkey: {'registered Ctrl+Alt+H' if ok else 'unavailable'}")
        except Exception as exc:
            print(f"hotkey: unavailable ({exc})")

    mini_holder: dict = {"win": None}
    cfg = backend.cfg

    # ------------------------------------------------------------------
    # 以下浮窗实现移植自原版 hotspot/webui/app.py（win32 专属处已 guard）
    # ------------------------------------------------------------------
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
        """WebView2 COM 属性必须投递到 UI 线程（pythonnet/WinForms，仅 Windows）。"""
        if w is None or sys.platform != "win32":
            return

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
                                pass
                        user32 = ctypes.windll.user32
                        gdi32 = ctypes.windll.gdi32
                        hwnd = int(form.Handle.ToInt64())
                        try:
                            GWL_EXSTYLE = -20
                            WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x80, 0x40000
                            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                            user32.SetWindowLongW(
                                hwnd, GWL_EXSTYLE,
                                (ex & ~WS_EX_APPWINDOW) | WS_EX_TOOLWINDOW)
                            form.Hide()
                            user32.ShowWindow(hwnd, 4)   # SW_SHOWNOACTIVATE
                        except Exception:
                            pass
                        try:
                            dwm = ctypes.windll.dwmapi
                            pref = ctypes.c_int(2)   # DWMWCP_ROUND
                            if dwm.DwmSetWindowAttribute(
                                    hwnd, 33, ctypes.byref(pref), 4) == 0:
                                return
                        except Exception:
                            pass
                        dpr = user32.GetDpiForWindow(hwnd) / 96.0
                        d = max(4, int(round(2 * 11 * dpr)))
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
        threading.Thread(target=work, daemon=True).start()

    def _save_mini_pos() -> None:
        """记录浮窗位置（物理像素 GetWindowRect 原样），仅 Windows。"""
        if sys.platform != "win32":
            return
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
        if sys.platform != "win32":
            return 1.0
        try:
            import ctypes
            user32 = ctypes.windll.user32
            w = window.native if getattr(window, "native", None) else None
            hwnd = int(w.Handle.ToInt64()) if w is not None else 0
            if hwnd:
                dpi = user32.GetDpiForWindow(hwnd)
                if dpi > 0:
                    return dpi / 96.0
            hdc = user32.GetDC(0)
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)
            user32.ReleaseDC(0, hdc)
            return dpi / 96.0 if dpi > 0 else 1.0
        except Exception:
            return 1.0

    def _default_mini_pos() -> tuple[int, int]:
        if sys.platform != "win32":
            return (80, 80)
        try:
            import ctypes
            import ctypes.wintypes
            user32 = ctypes.windll.user32
            wa = ctypes.wintypes.RECT()
            if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0):
                dpr = _mini_dpr()
                return (int(wa.right / dpr) - MINI_W - 24,
                        int(wa.bottom / dpr) - MINI_H - 24)
        except Exception:
            pass
        return (80, 80)

    def open_mini(x: int | None = None, y: int | None = None) -> None:
        """打开原生浮窗（第二个 pywebview 窗口，js_api 共享同一 backend）。"""
        theme = getattr(cfg, "theme", "dark")
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
                lx, ly = int(mx / dpr), int(my / dpr)
                sw = sh = 0
                if sys.platform == "win32":
                    try:
                        import ctypes
                        u = ctypes.windll.user32
                        sw = int(u.GetSystemMetrics(0) / dpr)
                        sh = int(u.GetSystemMetrics(1) / dpr)
                    except Exception:
                        pass
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
                js_api=backend,
                width=MINI_W, height=MINI_H,
                min_size=(MINI_W, MINI_H),
                x=x, y=y,
                resizable=False,
                frameless=True,
                easy_drag=False,
                on_top=True,
                shadow=False,
                hidden=False,
            )

            def _mini_shown() -> None:
                threading.Timer(1.2, _apply_mini_style,
                                (mw, getattr(cfg, "theme", "dark"))).start()

            def _mini_loaded() -> None:
                _apply_mini_style(mw, getattr(cfg, "theme", "dark"))

            try:
                mw.events.shown += _mini_shown
                mw.events.loaded += _mini_loaded
                mw.events.closed += lambda: mini_holder.update(win=None)
            except Exception:
                pass
            mini_holder["win"] = mw
            backend._mini_window = mw
            backend._close_mini = close_mini
            threading.Timer(0.6, _save_mini_pos).start()
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
        backend._mini_window = None

    def _mini_drag_start_impl() -> None:
        """原生窗口拖拽：ReleaseCapture + WM_NCLBUTTONDOWN(HTCAPTION)，仅 Windows。"""
        if sys.platform != "win32":
            return
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
                    _save_mini_pos()
                except Exception:
                    log.debug("浮窗原生拖拽执行失败", exc_info=True)

            form.BeginInvoke(Action(ui_drag))
        except Exception:
            log.debug("浮窗原生拖拽失败", exc_info=True)

    def _toggle_mini_impl() -> dict:
        """主窗「迷你悬浮窗」按钮：开↔关，并记住 show 状态（与原版一致）。"""
        shown = mini_holder["win"] is not None
        if shown:
            cfg.mini_window["show"] = False
            cfg.save()
            close_mini()
            return {"ok": True, "shown": False}
        cfg.mini_window["show"] = True
        cfg.save()
        open_mini()
        return {"ok": True, "shown": True}

    def _boot_ready_impl() -> None:
        """主窗前端 boot() 上报 JS 就绪 → 延迟开浮窗（避开 WebView2 并发初始化）。"""
        if not getattr(backend, "_boot_mini_pending", False):
            return
        backend._boot_mini_pending = False
        threading.Timer(0.5, open_mini).start()

    # 挂到原版 backend 的回调点（backend.py 零改动，用这些 hook 接应）
    backend._app_ref = {"mini_drag_start": _mini_drag_start_impl}
    backend._mini_style = _apply_mini_style
    backend._open_mini = open_mini
    backend._close_mini = close_mini
    backend._toggle_mini = _toggle_mini_impl
    backend._boot_ready = _boot_ready_impl

    if getattr(backend, "_boot_mini_pending", False):
        def _boot_give_up() -> None:
            if getattr(backend, "_boot_mini_pending", False):
                backend._boot_mini_pending = False
                log.warning("主窗 15s 未就绪，放弃恢复浮窗")
        threading.Timer(15.0, _boot_give_up).start()

    _boot_ts = time.time()

    def _record_mini_state(shown: bool) -> None:
        try:
            cfg.mini_window["show"] = bool(shown and mini_holder["win"] is not None)
            if mini_holder["win"] is not None:
                _save_mini_pos()
            cfg.save()
        except Exception:
            log.debug("记录浮窗状态失败", exc_info=True)

    def _on_closing() -> bool:
        if time.time() - _boot_ts < 5.0:
            return False  # 启动缓冲期防闪退
        tray = getattr(backend, "_tray", None)
        if cfg.close_to_tray and tray is not None and getattr(tray, "available", False):
            window.hide()
            return False
        if cfg.confirm_exit_hotspot and backend.controller.last_status.active \
                and not backend._exit_confirmed:
            backend._exit_confirmed = True
            try:
                window.evaluate_js("window.__confirmExit && window.__confirmExit()")
            except Exception:
                pass
            return False
        _record_mini_state(False)
        close_mini()
        return True

    def _on_closed() -> None:
        _record_mini_state(True)
        attach_main_window(None)   # 窗口销毁后 toggle_maximize 诚实降级
        backend.shutdown()
        tray.stop()

    try:
        window.events.closing += _on_closing
        window.events.closed += _on_closed
    except Exception:
        pass

    # 主窗圆角（Windows 11 DWM；失败静默）——与浮窗同一做法，投递 UI 线程。
    def _apply_main_style() -> None:
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from System import Action
            form = window.native
            hwnd = int(form.Handle.ToInt64())

            def ui_round():
                try:
                    dwm = ctypes.windll.dwmapi
                    pref = ctypes.c_int(2)   # DWMWCP_ROUND
                    dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
                except Exception:
                    pass

            form.BeginInvoke(Action(ui_round))
        except Exception:
            pass

    try:
        window.events.shown += lambda: threading.Timer(1.0, _apply_main_style).start()
    except Exception:
        pass

    # ---- 黑色边框抑制（纯视觉，与缩放无关） ----
    # 缩放现在走前端 /api/op/resizeWindow → window.resize()（SetWindowPos），
    # 不再需要 WS_THICKFRAME 提供的原生缩放命中；留着它反而会造出非客户区，
    # 窗口一 resize，DWM 重新合成就把那条黑边画出来。
    # 这里做三件事：
    #   1) 确保样式里没有 WS_THICKFRAME / WS_CAPTION；
    #   2) DWMWA_NCRENDERING_POLICY = DWMNCRP_DISABLED(0)：DWM 不再接管
    #      非客户区绘制（pywebview 默认 shadow=True 时会 Enable，正是黑边来源）；
    #   3) 把 frame 收回客户区（margins 全 0），彻底没有非客户区。
    def _enable_resize() -> None:
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from System import Action
        except Exception:
            return

        def ui_style():
            try:
                user32, dwm = ctypes.windll.user32, ctypes.windll.dwmapi
                hwnd = int(window.native.Handle.ToInt64())
                style = user32.GetWindowLongW(hwnd, -16)
                # 去掉 WS_THICKFRAME(0x40000)/WS_CAPTION(0xC0000)，保留最小化按钮
                user32.SetWindowLongW(hwnd, -16, style & ~0x00040000 & ~0x00C00000)
                # DWM 不再渲染非客户区。
                # 注意 DwmSetWindowAttribute 的第 3 参是 LPCVOID（指针），
                # 传裸整数会被当成 NULL 指针而调用失败——必须传 byref 的 int。
                def _dwm_attr(attr: int, value: int) -> None:
                    v = ctypes.c_int(value)
                    dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4)
                _dwm_attr(2, 0)              # DWMWA_NCRENDERING_POLICY = DISABLED
                _dwm_attr(37, 0)             # DWMWA_VISIBLE_FRAME_BORDER_THICKNESS = 0
                _dwm_attr(34, 0xFFFFFFFE)    # DWMWA_BORDER_COLOR = DWMWA_COLOR_NONE
                # frame 收回客户区（无 1px 边框）
                class _MARGINS(ctypes.Structure):
                    _fields_ = [(n, ctypes.c_int) for n in
                                ("cxLeftWidth", "cxRightWidth",
                                 "cyTopHeight", "cyBottomHeight")]
                m = _MARGINS()
                dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(m))
                # SWP_FRAMECHANGED：让样式生效（不改窗口大小/位置）
                user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x2 | 0x1 | 0x4 | 0x20)
            except Exception:
                pass

        try:
            window.native.BeginInvoke(Action(ui_style))
        except Exception:
            pass

    try:
        window.events.shown += lambda: threading.Timer(1.2, _enable_resize).start()
    except Exception:
        pass

    # 托盘 tooltip 轮询（原版 desktop_native 的行为并入：热点状态 + 在线数）
    def _poll_tooltip() -> None:
        import json as _json
        import urllib.request as _u
        while True:
            try:
                st = _json.loads(_u.urlopen(
                    base + "/api/state", timeout=8).read().decode("utf-8", "replace"))
                hs = st.get("hotspot", {})
                if tray.available:
                    if hs.get("active"):
                        tray.set_tooltip(
                            f"{hs.get('ssid') or '热点'} · 运行中 · 在线 "
                            f"{st.get('stats', {}).get('online', 0)} 台")
                    else:
                        tray.set_tooltip("Windows Hotspot Butler · 热点未开启")
            except Exception:
                pass
            time.sleep(8)

    threading.Thread(target=_poll_tooltip, name="whm-tray-poll", daemon=True).start()

    tray.start()   # 常驻启动；是否隐藏到托盘由 _on_closing 按配置判断
    print("tray: started" if tray.available else "tray: dependencies unavailable (pip install pystray pillow)")

    # --resize-seq W1xH1,W2xH2：按顺序缩放窗口（自适应验证辅助；pywebview
    # resize 内部经 callAfter/Invoke 投递 GUI 线程，从 daemon 线程调用安全）
    if args.resize_seq:
        def _resize_worker() -> None:
            import time as _t
            _t.sleep(6)  # 等页面加载完
            for wh in args.resize_seq.split(","):
                try:
                    w_, h_ = (int(x) for x in wh.strip().lower().split("x"))
                    window.resize(w_, h_)
                    print(f"[resize-seq] -> {w_}x{h_}")
                except Exception as exc:
                    print(f"[resize-seq] {wh} 失败: {exc}")
                _t.sleep(7)
        threading.Thread(target=_resize_worker, name="whm-resize-seq", daemon=True).start()

    # 单进程：server.py 已在后台 daemon 线程里跑，窗口在主线程阻塞到关闭。
    # icon：窗口/任务栏图标（原版 webview.start(icon=ICON_ICO) 同参数）。
    webview.start(debug=args.debug, icon=str(ICON_ICO) if ICON_ICO.is_file() else None)
    try:
        tray.stop()   # 兜底销毁，防托盘图标残留
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
