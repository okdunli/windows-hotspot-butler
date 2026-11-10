"""业务服务：把原版 HotspotBackend 暴露成 HTTP API。

这个进程承载两组端点：

  1. 业务 API（UI 的实际功能入口，形状与 Web 版完全一致，UI 一份代码两用）：
       GET  /api/state            -> backend.get_state()
       GET  /api/mini             -> backend.mini_state()
       POST /api/op/<name>        -> getattr(backend, name)(*args)
                                     body: {"args": [...]}   name 用 camelCase
                                     （与 Web 版 /api/op/<name> 逐字相同）

  2. Rosezm 导出器的两个协议端点（rose_fetch / $data / server action 的底层）：
       GET  /__rosezm/data?path=X -> {"state": {...}}   data channel
       POST <path>  __action=N    -> {"state": {...}}   server-action protocol

业务逻辑零重写：全部来自原版 hotspot/webui/backend.py 的 HotspotBackend
（pywebview 时代的 js_api 类），此处仅做 HTTP 打包。

    python server.py [--port 8787]
"""
from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# 允许从项目根目录或本目录启动（Windows 用 \，macOS/Linux 用 /，统一用 pathlib）
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# 桌面载体 pywebview 加载的界面：原版 index.html/style.css/app.js 的逐字节副本
# 放在项目 public/（framework build 会把非 html 资源复制到 dist/public/，
# 但 html 被框架当路由文档跳过，故静态托管需要能回退到源码 public/）。
# 与原版一样是「桌面窗口里装网页」，界面由原版 HTML/CSS 渲染，不是 Tk 控件。
WEB_ROOT: Path | None = None
PUBLIC_ROOT: Path | None = None


def _dist_root() -> Path | None:
    global WEB_ROOT
    if WEB_ROOT is not None:
        return WEB_ROOT
    candidates = [
        Path(__file__).resolve().parent.parent / "dist",
        Path(getattr(sys, "_MEIPASS", "")) / "dist" if getattr(sys, "frozen", False) else Path(""),
    ]
    for p in candidates:
        if p.is_dir() and (p / "index.html").is_file():
            WEB_ROOT = p
            return p
    return None


def _public_root() -> Path | None:
    global PUBLIC_ROOT
    if PUBLIC_ROOT is not None:
        return PUBLIC_ROOT
    candidates = [
        Path(__file__).resolve().parent.parent / "public",
        Path(getattr(sys, "_MEIPASS", "")) / "public" if getattr(sys, "frozen", False) else Path(""),
    ]
    for p in candidates:
        if p.is_dir() and (p / "index.html").is_file():
            PUBLIC_ROOT = p
            return p
    return None

from hotspot.webui.backend import HotspotBackend  # noqa: E402

# ---- 本机适配（不改原版 backend.py 一个字节）----
# 原版 _apply_start_with_windows 把 HKCU\...\Run 指向 <项目>/main.py。
# 本机入口是 <python-app>/run.py，原路径不存在；在服务层重定向一次，
# 保持"设置页勾开机自启 → 写注册表"的 1:1 行为。
def _whm_apply_startup(self, enabled: bool) -> None:
    if sys.platform != "win32":
        return
    exe = sys.executable
    # 源码形态：run.py 与 server.py 同目录；打包形态：exe 自身即入口。
    if getattr(sys, "frozen", False):
        script = ""  # 直接启动 exe（无需脚本路径）
    else:
        script = str(Path(__file__).resolve().parent / "run.py")
    value = f'"{exe}"' + (f' "{script}"' if script else "")
    key = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
    args = ["reg", "add", key, "/v", "WifiHotspotManager", "/t", "REG_SZ", "/d", value, "/f"] \
        if enabled else \
        ["reg", "delete", key, "/v", "WifiHotspotManager", "/f"]
    try:
        subprocess.run(args, timeout=15, creationflags=0x08000000)
    except Exception:
        pass


HotspotBackend._apply_start_with_windows = _whm_apply_startup


# 原版 tray.py::_icon_image 使用了未导入的 Path（上游潜伏 bug：托盘一初始化
# 即 NameError，图标永远画不出来）。在服务层打补丁（tray.py 保持 parity
# 逐字节不动）：注入 Path 到该模块命名空间，行为即与注释意图一致。
def _patch_tray_path() -> None:
    try:
        from hotspot.webui import tray as _tray
        from pathlib import Path as _P
        if not hasattr(_tray, "Path"):
            _tray.Path = _P
    except Exception:
        pass


_patch_tray_path()

# js_api 原版方法名（snake_case）+ Web 版曾用的 camelCase 别名，
# 使同一份 UI 代码既能打 Web 版（camel）也能打本服务（两者都收）。
ALIASES = {
    "startHotspot": "start",
    "stopHotspot": "stop",
    "saveConfigPatch": "save_config",
    "applyNow": "apply_now",
    "getState": "get_state",
    "miniState": "mini_state",
    "miniToggle": "mini_toggle",
    "toggleMini": "toggle_mini",
    "wifiQrcode": "wifi_qrcode",
    "getStatsReport": "get_stats_report",
    "setDeviceName": "set_device_name",
    "setDeviceType": "set_device_type",
    "forgetDevice": "forget_device",
    "blockDevice": "block_device",
    "unblockDevice": "unblock_device",
    "portalStart": "portal_start",
    "portalStop": "portal_stop",
    "portalAllow": "portal_allow",
    "portalRevoke": "portal_revoke",
    "getDiagnose": "get_diagnose",
    "scheduleStop": "schedule_stop",
    "stopStatus": "stop_status",
    "tempPasswordStart": "temp_password_start",
    "tempPasswordStop": "temp_password_stop",
    "tempPasswordStatus": "temp_password_status",
    "exportConfig": "export_config",
    "exportCsv": "export_csv",
    "importConfig": "import_config",
    "setTheme": "set_theme",
    "getTheme": "get_theme",
    "setLanguage": "set_language",
    "getLanguage": "get_language",
    "getI18n": "get_i18n",
    # 迷你浮窗 / 启动恢复（原版 mini.html 与主窗 boot() 用的 snake_case API）
    "bootReady": "boot_ready",
    "hideMain": "hide_main",
    "miniRestoreMain": "mini_restore_main",
    "miniDragStart": "mini_drag_start",
    "miniClosePinned": "mini_close_pinned",
    "shareStart": "share_start",
    "shareStop": "share_stop",
    "shareOpenFolder": "share_open_folder",
    "pfAdd": "pf_add",
    "pfRemove": "pf_remove",
    "speedTest": "speed_test",
    "confirmExit": "confirm_exit",
    "cancelExit": "cancel_exit",
    # 端口增强：最大化/还原（pywebview winforms 的 maximize/restore 经
    # Invoke 投递到 UI 线程，HTTP 线程调用线程安全——与原版 minimize 同套路）
    "toggleMaximize": "toggle_maximize",
    "getVersion": "get_version",
    # 无边框窗口边缘拖拽缩放（服务层，前端走 HTTP，不依赖 pywebview 桥）
    "resizeBegin": "resize_begin",
    "resizeWindow": "resize_window",
    "setTrafficMode": None,  # 原版没有，返回错误即可
}

# 端口增强层版本（与 package.json 一致）；原版 hotspot/core/paths.py 的
# APP_VERSION 是 1:1 parity 锁定文件，不在其上改版本，这里由服务层提供。
PORT_VERSION = "1.2.0"
PORT_REPO_URL = "https://github.com/okdunli/windows-hotspot-butler"

# 桌面窗口引用（desktop_webview 挂上后 toggle_maximize 才有目标）
main_window = None
main_window_lock = threading.Lock()


def attach_main_window(w) -> None:
    global main_window
    with main_window_lock:
        main_window = w


def toggle_maximize(maxed: bool) -> dict:
    """主窗 最大化↔还原。pywebview 的 winforms 后端用 WinForms Control.Invoke
    投递到 UI 线程（线程安全）；非 Windows / 无窗口时诚实降级。"""
    w = main_window
    if w is None:
        return {"ok": False, "msg": "窗口不可用"}
    try:
        if maxed:
            w.maximize()
        else:
            w.restore()
        return {"ok": True, "maximized": bool(maxed)}
    except Exception as exc:
        return {"ok": False, "msg": str(exc)}


def get_version() -> dict:
    return {"ok": True, "version": PORT_VERSION}


# ---- 无边框窗口边缘拖拽缩放（服务层，走 HTTP /api/op/*）----------------
# 这个项目的 UI 全部走 fetch('/api/op/<name>')，并不使用 pywebview 的
# window.pywebview.api；早期把缩放挂到 js_api 上，Windows 里前端拿不到该
# 对象（注入时序 + 本项目桥实现），边缘拖拽直接不生效。这里与
# toggle_maximize 同套路：挂在服务层，前端用和其它操作完全一样的 HTTP 调用。
# 尺寸基准在 mousedown 时取一次，之后传"相对按下点的总位移"，避免每次
# 轮询 resizing 时读 window.width 的乱序。
_resize_drag = {"w": 1000, "h": 620}
_resize_lock = threading.Lock()


def resize_begin() -> dict:
    w = main_window
    if w is None:
        return {"ok": False, "msg": "窗口不可用"}
    try:
        with _resize_lock:
            _resize_drag["w"], _resize_drag["h"] = int(w.width), int(w.height)
        return {"ok": True, "w": _resize_drag["w"], "h": _resize_drag["h"]}
    except Exception as exc:
        return {"ok": False, "msg": str(exc)}


def resize_window(dx: int, dy: int, edge: int) -> dict:
    """edge 位掩码：1=左 2=右 4=上 8=下；dx/dy 为相对按下点的总位移。
    右/下拖 +delta，左/上拖 -delta；FixPoint 固定住对侧边。"""
    w = main_window
    if w is None:
        return {"ok": False, "msg": "窗口不可用"}
    try:
        with _resize_lock:
            w0, h0 = _resize_drag["w"], _resize_drag["h"]
        width = max(880, w0 + ((-dx if edge & 1 else dx) if (edge & 3) else 0))
        height = max(560, h0 + ((-dy if edge & 4 else dy) if (edge & 12) else 0))
        try:
            from webview.window import FixPoint
            fp = FixPoint(0)
            fp |= FixPoint.EAST if edge & 1 else FixPoint.WEST
            fp |= FixPoint.SOUTH if edge & 4 else FixPoint.NORTH
        except Exception:
            # FixPoint 是 Flag：NORTH=1 WEST=2 EAST=4 SOUTH=8
            fp = (4 if edge & 1 else 2) | (8 if edge & 4 else 1)
        # Windows + pywebview winforms：HTTP 线程不能碰 WinForms 控件，
        # 用 Control.BeginInvoke 把 resize 投递到 UI 线程（与 _enable_resize
        # 里补窗口样式同一套路）。其它平台直接调用。
        def _do_resize() -> None:
            w.resize(int(width), int(height), fp)
            # resize 会让 DWM 重新合成非客户区 → 顶部/四周冒出黑色边框。
            # 窗口本来就没有非客户区（shadow=False、无 WS_THICKFRAME），
            # 这里再显式把 frame 收回客户区并让样式生效一次，兜住合成时序。
            try:
                if sys.platform == "win32":
                    import ctypes
                    hwnd = int(w.native.Handle.ToInt64())
                    class _M(ctypes.Structure):
                        _fields_ = [(n, ctypes.c_int) for n in
                                    ("cxLeftWidth", "cxRightWidth",
                                     "cyTopHeight", "cyBottomHeight")]
                    dwm = ctypes.windll.dwmapi
                    dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(_M()))
                    user32 = ctypes.windll.user32
                    user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x2 | 0x1 | 0x4 | 0x20)
            except Exception:
                pass

        try:
            if sys.platform == "win32":
                from System import Action
                w.native.BeginInvoke(Action(_do_resize))
            else:
                _do_resize()
        except Exception:
            w.resize(int(width), int(height), fp)
        return {"ok": True, "w": int(width), "h": int(height)}
    except Exception as exc:
        return {"ok": False, "msg": str(exc)}

backend: HotspotBackend | None = None
backend_lock = threading.Lock()


def get_backend() -> HotspotBackend:
    """后端单例：一次进程一份（等价 pywebview 时代 js_api 单实例）。"""
    global backend
    with backend_lock:
        if backend is None:
            backend = HotspotBackend()
    return backend


class Handler(BaseHTTPRequestHandler):
    server_version = "WhmPythonServer/1.0"
    protocol_version = "HTTP/1.1"

    # ---- 基础 ----
    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _body_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def log_message(self, fmt, *args):  # 静音默认日志（原版 backend 已有滚动日志）
        pass

    # ---- 路由 ----
    def do_OPTIONS(self) -> None:
        self._send(204, b"", "text/plain")

    def do_GET(self) -> None:
        url = urlparse(self.path)
        path = url.path

        if path == "/__health":
            self._json(200, {"ok": True, "app": "WifiHotspotManager", "mode": "python"})
            return

        # Rosezm data channel：页面 $data 的状态
        if path == "/__rosezm/data":
            self._json(200, {"state": {}})
            return

        if path == "/api/state":
            self._json(200, get_backend().get_state())
            return

        if path == "/api/mini":
            self._json(200, get_backend().mini_state())
            return

        if path == "/api/i18n":
            self._json(200, get_backend().get_i18n())
            return

        # Rosezm Web 前端（桌面 pywebview 窗口加载它）。只服务 dist/ 下的静态文件，
        # 拒绝 .. 穿越；未知路径回首页 SPA 交给前端路由。
        if self._serve_static(path):
            return

        self._json(404, {"error": "not found"})

    def _serve_static(self, path: str) -> bool:
        dist = _dist_root()
        pub = _public_root()
        if dist is None and pub is None:
            return False

        rel = path.lstrip("/")

        # /public/* → 源码 public/ 优先（唯一真源，随编辑即时生效）；
        # dist/public/ 仅作 frozen 回退（build 时的拷贝可能落后于源码）。
        if rel.startswith("public/"):
            sub = rel[len("public/"):] or "index.html"
            for base in (pub, dist / "public" if dist else None):
                if base is None:
                    continue
                cand = (base / sub).resolve()
                try:
                    cand.relative_to(base.resolve())
                except ValueError:
                    continue
                if cand.is_file():
                    return self._send_file(cand)

        # 其它静态路径（/client.js、/chunks/*、/ 等）走 dist/
        if dist is not None:
            target = (dist / (rel or "index.html")).resolve()
            try:
                target.relative_to(dist.resolve())
            except ValueError:
                return False
            if target.is_dir():
                target = target / "index.html"
            if not target.is_file() and not rel.startswith(("api/", "__")):
                target = dist / "index.html"   # SPA 回退
            if target.is_file():
                return self._send_file(target)
        return False

    def _send_file(self, target: Path) -> bool:
        try:
            data = target.read_bytes()
        except OSError:
            return False
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        # 端口增强层：给主窗 HTML 注入自适应 CSS + 边缘缩放/版本号脚本。
        # 原版 index.html 保持在 public/ 逐字节不动，注入只发生在响应里。
        if target.name == "index.html" and b"</head>" in data:
            pub = _public_root()
            if pub is not None and (pub / "enhance.css").is_file():
                inject = (
                    '<link rel="stylesheet" href="/public/enhance.css">'
                    '<script>window.__WHM_VERSION="' + PORT_VERSION + '";'
                    'window.__WHM_REPO="' + PORT_REPO_URL + '";</script>'
                    '<script src="/public/enhance.js" defer></script>'
                ).encode("utf-8")
                data = data.replace(b"</head>", inject + b"</head>", 1)
        self._send(200, data, ctype)
        return True

    def do_POST(self) -> None:
        url = urlparse(self.path)
        path = url.path

        # 业务 API：POST /api/op/<name>
        if path.startswith("/api/op/"):
            name = path[len("/api/op/"):]
            payload = self._body_json()
            args = payload.get("args") or []
            if not isinstance(args, list):
                args = []
            try:
                result = self._invoke(name, args)
                self._json(200, result)
            except AttributeError:
                self._json(404, {"ok": False, "msg": f"未知操作：{name}"})
            except Exception as exc:  # 业务异常不炸连接
                import traceback
                traceback.print_exc()
                self._json(500, {"ok": False, "msg": str(exc)})
            return

        # Rosezm server-action protocol：POST <route> 带 __action=<name>
        if path.startswith("/api/"):
            self._json(404, {"error": "not found"})
            return
        fields = self._form_fields()
        action = fields.pop("__action", None)
        # 本项目的 UI 不使用 server action（业务全走 /api/op），返回空 patch
        self._json(200, {"state": {}, "action": action, "fields": fields})

        self._json(404, {"error": "not found"})

    # ---- helpers ----
    @staticmethod
    def _invoke(name: str, args: list):
        # 端口增强 op：不落在原版 backend 上（零改动），由服务层提供
        port_ops = {
            "toggle_maximize": toggle_maximize,
            "get_version": get_version,
            "resize_begin": resize_begin,
            "resize_window": resize_window,
        }
        fn = port_ops.get(ALIASES.get(name, name))
        if fn is not None:
            return fn(*args)
        b = get_backend()
        method_name = ALIASES.get(name, name)
        if method_name is None:
            raise AttributeError(name)
        method = getattr(b, method_name, None)
        if method is None:
            raise AttributeError(name)
        import inspect
        if inspect.iscoroutinefunction(method):
            import asyncio
            return asyncio.run(method(*args))
        result = method(*args)
        # 原版部分方法返回 awaitable（async def）
        if inspect.isawaitable(result):
            import asyncio
            return asyncio.run(result)
        return result if result is not None else {"ok": True}

    def _form_fields(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length else b""
        parsed = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
        return {k: v[0] if v else "" for k, v in parsed.items()}


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    port = 8787
    if "--port" in argv:
        try:
            port = int(argv[argv.index("--port") + 1])
        except (IndexError, ValueError):
            pass

    print("=" * 62)
    print("Windows Hotspot Butler — 业务服务")
    print(f"  http://127.0.0.1:{port}/api/state")
    print("  Ctrl+C 停止")
    print("=" * 62)
    get_backend()  # 立即启动采集线程，与原版 app 启动行为一致

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n停止中…")
    finally:
        try:
            backend and backend.shutdown()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
