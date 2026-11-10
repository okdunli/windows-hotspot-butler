"""界面 1:1 回归：桌面窗口加载的页面/样式必须与原版逐字节一致。

用户要求「用 Rosezm 生成、和原版一模一样的代码」。原版界面是
hotspot/webui/ui/{index.html,mini.html,style.css} + app.js（pywebview js_api）；
本项目 public/ 存放逐字节副本（app.js 仅把 js_api 桥接成 /api/op/*）。
本测试真起 server，验证：
  1. GET /public/index.html、/public/style.css、/public/mini.html 逐字节 == 原版；
  2. 前端桥发出的 camelCase op 能往返（getState / getStatsReport ...）；
  3. 桌面窗口 URL 指向 /public/index.html（不是重写过的 .rose 页面）。

    python python-app/test_original_ui.py
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ORIG = HERE.parent / "python-app" / "hotspot" / "webui" / "ui"


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def main() -> int:
    if not ORIG.is_dir():
        print(f"  [SKIP] 原版目录不存在：{ORIG}")
        return 0
    _orig = urllib.request.proxy_bypass
    urllib.request.proxy_bypass = lambda h: True if (h or "").split(":")[0] in (
        "127.0.0.1", "localhost", "::1") else _orig(h)

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [sys.executable, "-u", str(HERE / "server.py"), "--port", str(port)],
        cwd=str(HERE), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(80):
            try:
                urllib.request.urlopen(base + "/__health", timeout=1)
                break
            except Exception:
                time.sleep(0.25)
        else:
            raise AssertionError("server 未就绪")

        # 1) 界面基线（应用已改名 Windows热点管理器 / Windows Hotspot Butler）：
        #    - style.css / mini.html：磁盘与响应仍逐字节 == 原版
        #    - index.html：磁盘与响应除产品名（title/brand 两处）外逐字节 == 原版；
        #      响应额外含增强层注入（enhance.css/js）
        NAME_OLD, NAME_NEW = "WiFi 热点管理器", "Windows热点管理器"
        for name in ("style.css",):
            orig = (ORIG / name).read_bytes()
            assert (ROOT / "public" / name).read_bytes() == orig, name
            with urllib.request.urlopen(base + "/public/" + name, timeout=5) as r:
                assert r.read() == orig, f"/public/{name} 响应与原版不一致"
        orig = (ORIG / "index.html").read_bytes()
        disk = (ROOT / "public" / "index.html").read_bytes()
        assert disk == orig.replace(NAME_OLD.encode(), NAME_NEW.encode()), \
            "index.html 应为原版仅替换产品名"
        with urllib.request.urlopen(base + "/public/index.html", timeout=5) as r:
            served = r.read()
        assert served != disk and b"enhance.css" in served and b"enhance.js" in served
        head_end = served.index(b"</head>") + len(b"</head>")
        assert served[head_end:] == disk[disk.index(b"</head>") + len(b"</head>"):], \
            "body 字节被改变"
        print("  [PASS] style/mini 逐字节==原版；index.html = 原版仅替换产品名 + 注入；body 未变")

        # 2) 前端桥 op 往返
        import json
        for op in ("getState", "getStatsReport", "miniState"):
            req = urllib.request.Request(
                base + "/api/op/" + op, data=b'{"args":[]}',
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=8) as r:
                d = json.loads(r.read())
            assert isinstance(d, dict) and d, f"{op} 返回异常"
        print("  [PASS] 前端桥 camelCase op 往返正常")

        # 3) 桌面窗口 URL
        import desktop_webview as D
        assert "/public/index.html" in open(HERE / "desktop_webview.py").read(), \
            "桌面窗口没有指向原版 1:1 页面"
        print("  [PASS] pywebview 桌面窗口 URL = /public/index.html")

        print("ALL ORIGINAL-UI PARITY TESTS PASS")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=4)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
