"""Windows Hotspot Butler 启动器（单进程）。

一条命令在**同一个进程**里拉起两层：
  1. 业务服务 server.py（原版 hotspot/ 包，零改动）-> 127.0.0.1:8787（后台 daemon 线程）
  2. 桌面界面：pywebview 桌面窗口加载 `rosezm build` 生成的 dist/index.html
     （原版同款 HTML/CSS/JS 视觉），主线程。

    python python-app/run.py            # 默认 8787 + 桌面窗口
    python python-app/run.py --port 9000
    python python-app/run.py --no-ui    # 只起服务（自检 / 调试用）
    python python-app/run.py --selftest [--apply]   # Windows 实跑自检

为什么不走子进程：PyInstaller 打包后 sys.executable 就是 exe 自身，一旦用
[exe, "server.py"] 重新拉起子进程，子进程会**再次完整启动整个 app**，形成无限递归
（表现为 banner 被打印几十次、窗口永远出不来）。因此我们**完全不派生子进程**——
服务跑在后台线程、桌面窗口跑在主线程、托盘/热键也各自是 daemon 线程，整个程序只有一个
进程，从根本上消除递归。这与原版 pywebview 把后端放在同一进程里是同一套路。

UI 通过 fetch 打 http://127.0.0.1:<port> 的 loopback，与 Rosezm Web 版共用同一份
/api/state、/api/op/<name> 协议；server.py 同时静态托管 dist/ 的 HTML/CSS/JS。
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


class _NullStream(io.TextIOBase):
    """windowed exe 的哑 stdout/stderr。

    PyInstaller --windowed 下 sys.stdout/sys.stderr 是 None，任何 print()
    都会 AttributeError 闪退。替换成哑流后所有下游 print/logging 原样工作；
    真正排障用 --console 版或 %APPDATA%\\Windows热点管理器\\logs\\ 文件日志。
    """

    def write(self, s):  # noqa: D102
        return len(s)

    def flush(self):  # noqa: D102
        pass


def _guard_windowed_streams() -> None:
    if sys.stdout is None:
        sys.stdout = _NullStream()
    if sys.stderr is None:
        sys.stderr = _NullStream()


_guard_windowed_streams()


def _bypass_proxy_for_localhost() -> None:
    """本机回环请求必须绕过系统 HTTP 代理。

    装了 Clash/VPN/公司代理的机器上，urllib 会把 http://127.0.0.1:8787 也交给
    代理（表现为 502 Bad Gateway），导致界面永远连不上后端。这里只对 localhost
    绕过；外部请求（测速等）仍走系统代理，保持原行为。
    """
    try:
        _orig = urllib.request.proxy_bypass

        def _bp(host: str) -> bool:
            h = (host or "").split(":")[0].strip("[]").lower()
            if h in ("127.0.0.1", "localhost", "::1"):
                return True
            return _orig(host)

        urllib.request.proxy_bypass = _bp
    except Exception:
        pass


_bypass_proxy_for_localhost()
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost,::1")
os.environ.setdefault("no_proxy", "127.0.0.1,localhost,::1")
# 让 server / desktop_native（python-app 根）与 rosezm / pages / styles（desktop/）都能被导入。
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(HERE / "desktop") not in sys.path:
    sys.path.insert(0, str(HERE / "desktop"))


def wait_ready(base: str, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/__health", timeout=1.5) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def _run_backend(port: int) -> None:
    """后台线程里的服务主循环；绑定失败（端口被占）给清晰提示而非裸 traceback。"""
    from server import main as server_main
    try:
        server_main(["--port", str(port)])
    except OSError as exc:
        print(f"[后端] 无法监听 127.0.0.1:{port}：{exc}")
        print(f"       端口可能被占用，请用 --port <其它端口> 重试。")
    except Exception:
        import traceback
        traceback.print_exc()


def _start_backend(port: int) -> None:
    """业务服务在后台 daemon 线程里跑（serve_forever 会阻塞，所以必须线程化）。"""
    threading.Thread(
        target=_run_backend,
        args=(port,),
        name="whm-backend",
        daemon=True,
    ).start()


def _parse_port(argv: list[str]) -> int:
    port = 8787
    if "--port" in argv:
        try:
            port = int(argv[argv.index("--port") + 1])
        except (IndexError, ValueError):
            pass
    return port


def _passthrough_args(argv: list[str]) -> list[str]:
    """把除 --selftest / --port [val] 之外的参数透传给自检。"""
    out: list[str] = []
    skip = False
    for a in argv:
        if skip:
            skip = False
            continue
        if a == "--selftest":
            continue
        if a == "--port":
            skip = True
            continue
        out.append(a)
    return out


def run_selftest(port: int, rest: list[str]) -> int:
    """业务服务已在后台线程启动，这里直接在同进程内跑 Windows 实跑自检。"""
    os.environ["ROSEZM_API"] = f"http://127.0.0.1:{port}"
    # windows_selftest 在导入时依据 sys.argv 解析 --port/--apply，故先备好 argv 再导入。
    sys.argv = [sys.argv[0], "--port", str(port), *rest]
    from windows_selftest import main as selftest_main
    return selftest_main()


def probe_ui() -> int:
    """`--probe-ui`：核对桌面界面资源是否齐备，并写出 JSON 报告（不进 mainloop）。

    桌面界面是 pywebview 加载的 1:1 原版 HTML（public/index.html），没有 Tk
    控件树可数；这里改为核对界面资源齐备 + 记录报告，作为「exe 到底出不出
    界面」的静态证据（verify_evidence.py 读 desktop_probe_report.json）。
    """
    report = {"probe": "desktop_ui", "files": {}, "missing": [], "ok": False}
    out = HERE / "desktop_probe_report.json"
    try:
        pub = HERE.parent / "public"
        need = ("index.html", "style.css", "app.js", "enhance.js", "enhance.css")
        missing = [n for n in need if not (pub / n).is_file()]
        report["missing"] = missing
        for n in need:
            f = pub / n
            if f.is_file():
                report["files"][n] = f.stat().st_size
        report["ok"] = not missing
        if missing:
            print(f"UI-PROBE FAIL missing={missing}")
        else:
            print(f"UI-PROBE files={len(need)} index.html={report['files']['index.html']}B")
        return 0 if report["ok"] else 1
    except Exception as exc:  # 资源读不出来就是失败
        import traceback
        traceback.print_exc()
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(f"UI-PROBE FAIL: {type(exc).__name__}: {exc}")
        return 1
    finally:
        try:
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    port = _parse_port(argv)
    no_ui = "--no-ui" in argv
    selftest = "--selftest" in argv
    probe = "--probe-ui" in argv
    base = f"http://127.0.0.1:{port}"

    # 与原版 main.py 一致：非管理员时给提示（不强制 UAC，保持 1:1）。
    if sys.platform == "win32" and "--no-elevate" not in argv:
        try:
            from hotspot.core import pshell
            if not pshell.is_admin():
                print("提示：当前不是管理员，热点开关 / ICS / 防火墙可能失败。")
                print("      请右键「以管理员身份运行」本程序。")
        except Exception:
            pass

    # 给导出的 UI / 服务 / 自检统一指后端地址（rosezm.py 在导入时读取该变量）。
    os.environ.setdefault("ROSEZM_API", base)

    print("=" * 62)
    print("Windows Hotspot Butler")
    print(f"  业务服务: {base}  (同进程后台线程)")
    print(f"  桌面界面: 窗口")
    print("=" * 62)

    # 0) 只探测界面能否建成（不进 mainloop、不起后端）
    if probe:
        return probe_ui()

    # 1) 业务服务（后台线程）
    _start_backend(port)
    if not wait_ready(base):
        print("业务服务启动超时，详见上方输出")
        return 1
    print(f"业务服务就绪: {base}/api/state")

    # 2) 自检：服务已在后台，同进程直接跑
    if selftest:
        rest = _passthrough_args(argv)
        return run_selftest(port, rest)

    # 3) 只起服务（调试 / 远端界面）：阻塞直到 Ctrl+C
    if no_ui:
        print("按 Ctrl+C 停止服务")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        return 0

    # 4) 桌面窗口：默认 pywebview 加载 Rosezm 生成的 HTML/CSS（与原版同款界面）。
    #    托盘/热键由 desktop_webview 内部负责（曾在这里另起 desktop_native
    #    旧架构，它自带一个 TrayIcon —— Windows 上出现两个托盘图标，已移除）。
    from desktop_webview import main as desktop_main
    # --port 已解析；剔除它和它的值，其余参数（--debug/--resize-seq 等）透传
    skip = {"--no-ui", "--selftest", "--apply", "--no-elevate", "--no-native"}
    extra: list[str] = []
    skip_next = False
    for a in argv:
        if skip_next:
            skip_next = False
            continue
        if a == "--port":
            skip_next = True
            continue
        if a in skip or a.startswith("--resize-seq="):
            continue
        extra.append(a)
    return desktop_main(["--port", str(port)] + extra)


if __name__ == "__main__":
    sys.exit(main())
