"""server.py 的 HTTP 契约集成测试（Tk UI 实际依赖的端点形状）。

覆盖 selftest 没单独断言的两个端点：`/api/mini`（迷你浮窗）、`/api/i18n`（中英文），
外加 `/api/state` 的关键键与「未知 op → 404」。真起一个 server 子进程，走真实 HTTP。

    python python-app/test_api_endpoints.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _get(base: str, path: str, timeout: float = 6.0):
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        return r.status, json.loads(r.read())


def main() -> int:
    # 本机回环绕过系统代理（与 run.py 一致）
    _orig = urllib.request.proxy_bypass
    urllib.request.proxy_bypass = lambda h: True if (h or "").split(":")[0] in (
        "127.0.0.1", "localhost", "::1") else _orig(h)

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [sys.executable, str(HERE / "server.py"), "--port", str(port)],
        cwd=str(HERE), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        ready = False
        for _ in range(80):
            try:
                if _get(base, "/__health", 1.0)[0] == 200:
                    ready = True
                    break
            except Exception:
                time.sleep(0.25)
        assert ready, "server 未在 20s 内就绪"

        st, state = _get(base, "/api/state")
        assert st == 200 and isinstance(state, dict) and len(state) >= 15, f"state 形状异常: {type(state)} len={len(state) if isinstance(state, dict) else '?'}"
        for k in ("hotspot", "config", "stats", "devices", "caps"):
            assert k in state, f"/api/state 缺键 {k}"
        print(f"  [PASS] /api/state：{len(state)} 键，含 hotspot/config/stats/devices/caps")

        st, mini = _get(base, "/api/mini")
        assert st == 200 and isinstance(mini, dict), f"mini 形状异常: {type(mini)}"
        for k in ("active", "online"):
            assert k in mini, f"/api/mini 缺键 {k}"
        print(f"  [PASS] /api/mini：dict，含 active/online（迷你浮窗契约）")

        st, i18n = _get(base, "/api/i18n")
        # zh 模式返回空表是设计如此（原文即中文）；en 模式才是整表。这里只验「是 JSON 对象、不报错」。
        assert st == 200 and isinstance(i18n, dict), f"i18n 应为 dict，实际 {type(i18n)}"
        print(f"  [PASS] /api/i18n：dict（zh 空表 / en 整表均合法；当前 {len(i18n)} 条）")

        req = urllib.request.Request(
            base + "/api/op/__nope__", data=b'{"args":[]}',
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=6)
            raise AssertionError("未知 op 应返回 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404, f"未知 op 返回 {e.code}，应为 404"
        print("  [PASS] /api/op/<unknown> → 404")

        # 无边框窗口边缘缩放端点：必须路由到服务层（不是 404），
        # 无窗口时诚实降级（HTTP 200 + ok:false），前端才不会静默失败。
        def op(name, args):
            r = urllib.request.Request(
                base + "/api/op/" + name,
                data=json.dumps({"args": args}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(r, timeout=6) as resp:
                return resp.status, json.loads(resp.read())

        for name, args in (("resizeBegin", []), ("resizeWindow", [30, 0, 2]),
                           ("resize_begin", []), ("resize_window", [0, -20, 4])):
            st2, d = op(name, args)
            assert st2 == 200 and isinstance(d, dict) and "ok" in d, (name, st2, d)
        print("  [PASS] /api/op/resizeBegin|resizeWindow（含 camel 别名）→ 服务层 op（非 404）")

        print("ALL 5 API-ENDPOINT TESTS PASS")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=4)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
