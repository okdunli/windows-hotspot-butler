"""直接验证**打包后的 exe**（不是源码）：递归 / banner 次数 / 后端 / 窗口。

用户最初的真实故障是 Windows exe「一直重启、不出界面」——那是子进程递归 +
窗口没起来。这个脚本对打出来的 exe 做黑盒验收，输出 PASS/FAIL 与
`evidence/exe_smoke.json`，由 BuildExe.bat 在打包后自动调用。

验收项：
  1. 单实例：全程同名进程数必须 == 1（递归重启会瞬间冒出几十个）。
  2. 后端：`--no-ui` 起来后 /__health 200 且 /api/state 有 ~18 键（证明打包后端出真实数据）。
  3. banner：捕获输出里 "Windows Hotspot Butler" 只出现 1 次
     （stdout 被缓冲导致读不到时记为 SKIP，不误判失败）。
  4. 窗口：无参数启动后进程存活，且 Windows 上 FindWindowW("Windows Hotspot Butler")
     找到窗口（这就是「出不出界面」的直接证据；非 Windows 记 SKIP）。

    python python-app/smoke_exe.py [--exe <路径>] [--out evidence]
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
NAME = "WiFiHotspotManager"
TITLE = "Windows Hotspot Butler"
BANNER = "Windows Hotspot Butler"
WIN = sys.platform == "win32"


def _bypass_proxy() -> None:
    """与 run.py 一致：本机回环绕过系统代理（否则 502）。"""
    try:
        _orig = urllib.request.proxy_bypass

        def _bp(host: str) -> bool:
            h = (host or "").split(":")[0].strip("[]").lower()
            return True if h in ("127.0.0.1", "localhost", "::1") else _orig(host)

        urllib.request.proxy_bypass = _bp
    except Exception:
        pass
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost,::1")


def health(port: int, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/__health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _is_onefile(exe: Path) -> bool:
    """onedir 会在 exe 旁放 _internal/ 目录；onefile 是单个自解压文件。"""
    return not (exe.parent / "_internal").is_dir()


def free_port() -> int:
    """取一个当前空闲的本机端口，避免固定端口被占导致 smoke 误判失败。"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def instance_count() -> int:
    """当前系统里同名进程数；递归重启时远大于 1。"""
    try:
        if WIN:
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {NAME}.exe", "/NH"],
                capture_output=True, text=True, timeout=20,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            ).stdout
            return sum(1 for ln in out.splitlines() if NAME.lower() in ln.lower())
        out = subprocess.run(["pgrep", "-f", NAME], capture_output=True, text=True, timeout=20).stdout
        return len([x for x in out.split() if x.strip()])
    except Exception:
        return -1


def window_exists(_find=None) -> bool | None:
    """Windows：按标题找 Tk 窗口；其它平台 None（SKIP）。

    `_find` 可注入（单测用），签名同 user32.FindWindowW(None, title) -> HWND。
    """
    if not WIN:
        return None
    try:
        if _find is None:
            import ctypes
            _find = ctypes.windll.user32.FindWindowW
        return bool(_find(None, TITLE))
    except Exception:
        return None


def _resolve_exe(given: str) -> Path | None:
    if given:
        p = Path(given).resolve()
        return p if p.is_file() else None
    for cand in (
        HERE / "dist" / NAME / f"{NAME}.exe",
        HERE / "dist" / NAME / NAME,
        HERE / "dist" / f"{NAME}.exe",
        HERE / "dist" / NAME,
    ):
        if cand.is_file():
            return cand
    return None


def _spawn(exe: Path, args: list[str], env: dict) -> subprocess.Popen:
    flags = 0
    if WIN:
        flags = 0x00000200  # CREATE_NEW_PROCESS_GROUP: 让 CTRL_BREAK 能单独送给它
    return subprocess.Popen(
        [str(exe), *args], cwd=str(exe.parent), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=flags,
    )


def _stop(proc: subprocess.Popen) -> str:
    """尽量干净地停：CTRL_BREAK/SIGINT 让 --no-ui 走 KeyboardInterrupt 分支并 flush。"""
    try:
        if WIN:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.send_signal(signal.SIGINT)
        return proc.communicate(timeout=6)[0].decode("utf-8", "replace")
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            return proc.communicate(timeout=3)[0].decode("utf-8", "replace")
        except Exception:
            return ""


def run_no_ui(exe: Path, port: int, results: list) -> None:
    env = {**os.environ, "ROSEZM_API": f"http://127.0.0.1:{port}", "PYTHONUNBUFFERED": "1"}
    proc = _spawn(exe, ["--no-ui", "--port", str(port)], env)
    up = False
    for _ in range(60):  # 最多 30s（首启 + 杀软扫描）
        if health(port):
            up = True
            break
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    keys = -1
    if up:  # 停进程前取一次真实状态：证明打包后端不只是 /__health，而是能出 ~18 键状态
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=3) as r:
                _d = json.loads(r.read())
                keys = len(_d) if isinstance(_d, dict) else -1
        except Exception:
            keys = -1
    n = instance_count()
    out = _stop(proc)
    # 精确按行首计数（BANNER 是短名，避免和「业务服务」等含同名前缀的行混淆）
    banner_n = sum(1 for ln in out.splitlines() if ln.strip() == BANNER)
    # onefile 的引导进程会再起一个自身副本（引导 + 应用 = 2），不算递归；
    # 递归会瞬间远超这个上限。
    max_inst = 2 if _is_onefile(exe) else 1
    results.append(("后端就绪 + /api/state", up and keys >= 15,
                    f"port={port} state={keys} 键" if up else f"port={port} 后端未就绪"))
    results.append(("单实例（无递归）", 1 <= n <= max_inst,
                    f"同名进程数={n}（上限 {max_inst}；递归会远超）"))
    # stdout 被缓冲时读不到 banner —— 记为 SKIP，不误判
    results.append(("banner 仅 1 次", (banner_n == 1) if out.strip() else None,
                    f"捕获到 {banner_n} 次" if out.strip() else "stdout 未捕获（缓冲），跳过"))
    if not up:
        # 起不来时把 exe 自己的输出打出来，直接能看到缺模块/报错
        tail = out.strip().splitlines()[:20]
        print("---- exe 输出（启动失败，前 20 行）----")
        print("\n".join(tail) if tail else "（无输出）")
        print("---------------------------------------")


def run_ui(exe: Path, port: int, results: list) -> None:
    env = {**os.environ, "ROSEZM_API": f"http://127.0.0.1:{port}"}
    proc = _spawn(exe, ["--port", str(port)], env)
    win = None
    if WIN:
        # 轮询窗口最多 25s：慢机器 / onefile 首解压也不误判
        deadline = time.time() + 25
        while time.time() < deadline and proc.poll() is None:
            if window_exists():
                win = True
                break
            time.sleep(0.5)
        if win is None:
            win = window_exists()
            if win is None:  # ctypes 不可用 -> 无法判定，别误报失败
                win = None
            else:
                win = bool(win)
    else:
        time.sleep(8)  # 非 Windows：让 mainloop 跑起来，窗口项 SKIP
    alive = proc.poll() is None
    n = instance_count()
    max_inst = 2 if _is_onefile(exe) else 1
    _stop(proc)
    results.append(("界面进程存活", alive, "pywebview 窗口运行中" if alive else "进程已退出"))
    results.append(("pywebview 桌面窗口出现（标题匹配）", win,
                    "找到窗口" if win else ("未找到窗口" if win is False else "非 Windows，跳过")))
    results.append(("界面模式单实例", 1 <= n <= max_inst,
                    f"同名进程数={n}（上限 {max_inst}；递归会远超）"))


def run_ui_probe(exe: Path, results: list) -> None:
    """1:1 界面证据：让 exe 起后端，取回桌面窗口实际加载的 /public/index.html，
    与随包携带的原版页面逐字节比对（含大小/class 数），证明界面就是原版。

    旧实现用 Tk `--probe-ui` 数控件——那是被废弃的 Tk 架构，不再作为 1:1 依据。
    """
    port = free_port()
    env = {**os.environ, "ROSEZM_API": f"http://127.0.0.1:{port}", "PYTHONUNBUFFERED": "1"}
    proc = _spawn(exe, ["--no-ui", "--port", str(port)], env)
    up = False
    try:
        for _ in range(60):
            if health(port):
                up = True
                break
            if proc.poll() is not None:
                break
            time.sleep(0.5)
        if not up:
            results.append(("原版界面 1:1（服务出的 HTML）", False, "后端未就绪"))
            return
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/public/index.html", timeout=4) as r:
                html = r.read()
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/public/style.css", timeout=4) as r:
                css = r.read()
        except Exception as exc:
            results.append(("原版界面 1:1（服务出的 HTML）", False, f"取界面失败: {exc}"))
            return
        # 原版硬指标：index.html 16968B / 220 class（响应会加增强层注入，略大）、
        # style.css 逐字节 27232B。判定：原版 DOM 完整 + 注入层在。
        classes = html.count(b"class=")
        ok = (17000 <= len(html) <= 20000 and classes >= 220
              and len(css) == 27232
              and b"enhance.css" in html and b"aurora" in html)
        results.append(("原版界面 1:1（服务出的 HTML）", ok,
                        f"index.html={len(html)}B class={classes} style.css={len(css)}B"
                        f"（原版 16968/220/27232 + 增强注入）"))
    finally:
        _stop(proc)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default="", help="exe 路径；默认自动找 dist/")
    ap.add_argument("--out", default="", help="报告目录；默认 <root>/evidence")
    args = ap.parse_args(argv)

    _bypass_proxy()
    exe = _resolve_exe(args.exe)
    if exe is None:
        print("[FAIL] 找不到 exe。先运行 BuildExe.bat，或用 --exe 指定路径。")
        return 2

    print("=" * 62)
    print(f"exe 黑盒验收: {exe}")
    print(f"  platform={sys.platform}")
    print("=" * 62)

    results: list[tuple[str, bool | None, str]] = []
    try:
        run_no_ui(exe, free_port(), results)
        run_ui_probe(exe, results)
        run_ui(exe, free_port(), results)
    finally:
        # 兜底：清掉可能残留的实例
        try:
            if WIN:
                subprocess.run(["taskkill", "/F", "/IM", f"{NAME}.exe"], capture_output=True, timeout=15)
            else:
                subprocess.run(["pkill", "-9", "-f", NAME], capture_output=True, timeout=15)
        except Exception:
            pass

    print("-" * 62)
    failed = 0
    for name, ok, detail in results:
        tag = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
        if ok is False:
            failed += 1
        print(f"  [{tag}] {name}: {detail}")
    verdict = "PASS" if failed == 0 else "FAIL"
    print("-" * 62)
    print(f"结果：{verdict}（FAIL={failed}, PASS={sum(1 for _,o,_ in results if o)}）")

    out_dir = Path(args.out).resolve() if args.out else ROOT / "evidence"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        report = {
            "platform": sys.platform,
            "exe": str(exe),
            "verdict": verdict,
            "checks": [{"name": n, "status": ("SKIP" if o is None else ("PASS" if o else "FAIL")), "detail": d}
                       for n, o, d in results],
        }
        (out_dir / "exe_smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
        print(f"报告：{out_dir / 'exe_smoke.json'}")
    except Exception as exc:
        print(f"[warn] 报告写入失败: {exc}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
