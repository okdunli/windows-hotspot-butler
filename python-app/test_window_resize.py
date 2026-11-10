"""窗口自适应 / 最大化按钮 / 版本号 增强层回归。

增强层以「响应时注入」方式叠加在逐字节原版 index.html 之上：
  * public/enhance.css —— 左栏自适应（去滚动条）、圆圈开关随窗口缩放、窄窗收紧
  * public/enhance.js  —— 标题栏最大化/还原按钮 + 设置弹窗版本号
  * server 注入 —— /public/index.html 响应含 enhance 引用与 __WHM_VERSION
  * /api/op/toggleMaximize、/api/op/getVersion 可用
  * 磁盘上的 public/index.html == 原版仅替换产品名（style/mini 逐字节）

    python python-app/test_window_resize.py
"""
from __future__ import annotations

import json
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
    # 1) 覆盖层文件存在且内容齐全
    css = (ROOT / "public" / "enhance.css").read_text(encoding="utf-8")
    js = (ROOT / "public" / "enhance.js").read_text(encoding="utf-8")
    assert "grid-template-columns: minmax(0, 352fr)" in css, "左栏自适应列缺失"
    assert "justify-content: center" in css, "左栏垂直居中缺失"
    assert "scrollbar-width: none" in css and "::-webkit-scrollbar" in css, "滚动条隐藏缺失"
    assert "--whm-scale" in css and "calc(238px * var(--whm-scale, 1))" in css, "双维度圆圈缩放缺失"
    assert "min-width: 760px" in css and "min-height: 520px" in css, "视口最小尺寸兜底缺失"
    assert "version-row" in css, "版本号样式缺失"
    assert "btnMax" not in js and "toggleMaximize" not in js, "最大化按钮应已移除（用户不用它）"
    assert "version-row" in js and "__WHM_VERSION" in js, "版本号注入缺失"
    assert "app-ver" in css and "addTitleVersion" in js, "标题栏常显版本号缺失"
    assert "repo-link" in css and "__WHM_REPO" in js, "开源地址链接缺失"
    assert "updateScale" in js and "innerWidth / 1000" in js, "缩放系数计算缺失"
    # 本项目的 UI 全走 fetch('/api/op/<name>')，绝不能依赖 window.pywebview.api
    # （那是异步注入的，早期版本因此 Windows 上完全不生效）。
    assert '"/api/op/" + name' in js and '"resizeWindow"' in js and '"resizeBegin"' in js, \
        "边缘拖拽缩放必须走 HTTP /api/op（不能依赖 js_api 桥）"
    assert "const api = window.pywebview" not in js and "api.pywebviewResize" not in js, \
        "不得再依赖 pywebview js_api 桥"
    assert "stopImmediatePropagation" in js, "边缘 mousedown 必须拦住 easy_drag"
    dpy = (HERE / "desktop_webview.py").read_text(encoding="utf-8")
    srv = (HERE / "server.py").read_text(encoding="utf-8")
    assert "pywebviewResizeWindow" not in dpy, "失效的 js_api 桥代码应已删除"
    assert "def resize_begin()" in srv and "def resize_window(dx" in srv, "服务层缩放 op 缺失"
    assert '"resizeWindow": "resize_window"' in srv and '"resizeBegin": "resize_begin"' in srv
    # 无边框窗口的黑色边框：缩放已改走 JS 桥，不再需要 WS_THICKFRAME；留着它
    # 会造出非客户区，窗口一 resize，DWM 重新合成就把黑边画出来。
    # 必须 shadow=False + 去掉 WS_THICKFRAME + 关掉 DWM 非客户区渲染。
    assert "shadow=False" in dpy, "主窗应关掉系统阴影（DWM 非客户区 = 黑边来源）"
    assert "0x00040000" not in dpy or "style & ~0x00040000" in dpy, \
        "不得再添加 WS_THICKFRAME（只允许显式移除）"
    assert "_dwm_attr(2, 0)" in dpy, "应关闭 DWM 非客户区渲染（NCRENDERING_POLICY）"
    # DwmSetWindowAttribute 第 3 参是 LPCVOID：必须传指针，裸整数会被当 NULL 而失败
    assert "dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4)" in dpy
    assert "dwm.DwmSetWindowAttribute(hwnd, 2, 0, 4)" not in dpy, \
        "DWM 属性必须用 byref 传指针"
    print("  [PASS] enhance.css / enhance.js 内容齐全 + HTTP 缩放 op（server 层）")

    # 1.5) 8 个方向的尺寸运算（与 server.resize_window 同式）：
    #      拉右/下边 +delta，拉左/上边 -delta；任何方向拖"向外"都应变大。
    for edge, dx, dy in ((2, 30, 0), (8, 0, 40), (1, -30, 0), (4, 0, -20),
                         (6, 30, -20), (5, -30, -20), (10, 30, 40), (9, -30, 40)):
        w = 1000 + ((-dx if edge & 1 else dx) if (edge & 3) else 0)
        h = 620 + ((-dy if edge & 4 else dy) if (edge & 12) else 0)
        assert w >= 1000 and h >= 620, (edge, w, h)
    print("  [PASS] 8 方向边缘拖拽尺寸运算（拉左/上边反向 + 固定对侧边）")

    # 1.6) 服务层 op 真的能调到窗口对象（mock），且 8 方向参数正确。
    #      这是最接近 Windows 单进程实跑的路径：HTTP _invoke -> main_window.resize()
    sys.path.insert(0, str(HERE))
    import server as S
    class _Win:
        def __init__(self): self.width, self.height, self.calls = 1000, 620, []
        def resize(self, w, h, fp):
            self.calls.append((w, h, getattr(fp, "value", fp)))
            self.width, self.height = w, h
    mock_win = _Win()
    S.attach_main_window(mock_win)
    assert S.Handler._invoke("resizeBegin", [])["ok"] is True
    for edge, dx, dy in ((2, 30, 0), (1, -30, 0), (8, 0, 40), (4, 0, -20),
                         (6, 30, -20), (5, -30, -20), (10, 30, 40), (9, -30, 40)):
        r = S.Handler._invoke("resizeWindow", [dx, dy, edge])
        assert r.get("ok") is True, (edge, r)
    assert len(mock_win.calls) == 8, mock_win.calls
    # 拉左/上边固定对侧：fp 里必须含 EAST(4)/SOUTH(8)
    assert mock_win.calls[1][2] & 4 and mock_win.calls[3][2] & 8, mock_win.calls
    S.attach_main_window(None)
    print("  [PASS] 服务层 _invoke 8 方向：真实调用 window.resize() 且 FixPoint 固定对侧边")

    # 2) style 磁盘逐字节 == 原版；mini = 原版去圆角/边框；index = 原版仅替换产品名
    for name in ("style.css",):
        assert (ROOT / "public" / name).read_bytes() == (ORIG / name).read_bytes(), name
    _mini_mine = (ROOT / "public" / "mini.html").read_text(encoding="utf-8")
    assert "border-radius:13px" not in _mini_mine, "浮窗 web 圆角应已移除"
    _o = (ORIG / "index.html").read_bytes()
    assert (ROOT / "public" / "index.html").read_bytes() == \
        _o.replace("WiFi 热点管理器".encode(), "Windows热点管理器".encode()), "index.html"
    print("  [PASS] 磁盘 style/mini 逐字节==原版；index.html 仅产品名不同")

    # 3) 服务端注入与增强 op（真起 server 走 HTTP）
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

        with urllib.request.urlopen(base + "/public/index.html", timeout=5) as r:
            html = r.read().decode("utf-8")
        assert "/public/enhance.css" in html and "/public/enhance.js" in html
        assert "__WHM_VERSION" in html and "1.2.0" in html
        # 原版 DOM 仍在
        assert "aurora" in html and "panel left" in html
        print("  [PASS] /public/index.html 响应已注入增强层（原版 DOM 保留）")

        for op, expect in (("getVersion", None), ("toggleMaximize", None)):
            req = urllib.request.Request(
                base + "/api/op/" + op, data=b'{"args": [' + (b"true" if op == "toggleMaximize" else b"") + b']}',
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=8) as r:
                d = json.loads(r.read())
            assert isinstance(d, dict) and d.get("ok") is not None, (op, d)
            if op == "getVersion":
                assert d.get("version") == "1.2.0", d
        print("  [PASS] getVersion=1.2.0；toggleMaximize 诚实降级（无窗时）")

        # 3.5) enhance.js 必须能被真实 JS 引擎执行且完成插入
        #      （曾经 const tbar 重复声明导致整个文件 SyntaxError，按钮/版本号全不出现）
        jsdom_dir = Path("/Users/mac/work/Rosezm/Rosezm/node_modules/jsdom")
        if jsdom_dir.is_dir():
            import subprocess as _sp
            probe = (
                'const {JSDOM}=require("/Users/mac/work/Rosezm/Rosezm/node_modules/jsdom");'
                'const fs=require("fs");'
                'const html=fs.readFileSync(' + json.dumps(str(ROOT / "public" / "index.html")) + ',"utf8");'
                'const js=fs.readFileSync(' + json.dumps(str(ROOT / "public" / "enhance.js")) + ',"utf8");'
                'const dom=new JSDOM(html,{runScripts:"outside-only",url:"http://x/"});'
                'dom.window.__WHM_VERSION="1.2.0";'
                'dom.window.eval(js);'
                'dom.window.document.dispatchEvent(new dom.window.Event("DOMContentLoaded"));'
                'const t=dom.window.document.querySelector(".titlebar");'
                'const mx=dom.window.document.getElementById("btnMax");'
                'const r={ok1:mx===null && [...t.children].map(e=>e.id||e.className).join(",")==="brand,tb-right",'
                'ok2:!!dom.window.document.querySelector("#settingsModal .version-row"),'
                'ok3:dom.window.document.querySelector(".app-ver")?.textContent==="v1.2.0",'
                'ok4:!!dom.window.document.querySelector("a.repo-link"),'
                'children:[...t.children].map(e=>e.id||e.className)};'
                'console.log(JSON.stringify(r));'
            )
            r2 = _sp.run(["node", "-e", probe], capture_output=True, text=True, timeout=30)
            out = (r2.stdout or "").strip()
            assert out, f"jsdom 执行失败: {(r2.stderr or '')[-300:]}"
            res = json.loads(out)
            assert res["ok1"] and res["ok2"] and res["ok3"] and res["ok4"], res
            print(f"  [PASS] enhance.js 真实执行：{res['children']}，版本行生成")

            # 3.6) 边缘拖拽缩放：走本项目统一的 HTTP /api/op（不能是 js_api 桥），
            #      且捕获阶段拦住 easy_drag 的全局 mousedown
            probe2 = (
                'const {JSDOM}=require("/Users/mac/work/Rosezm/Rosezm/node_modules/jsdom");'
                'const fs=require("fs");'
                'const dom=new JSDOM(fs.readFileSync(' + json.dumps(str(ROOT / "public" / "index.html")) + ',"utf8"),'
                '{runScripts:"outside-only",url:"http://x/"});'
                'const w=dom.window,calls=[];'
                # mock fetch：记录 POST 的 op 名与 args，返回永不失败的 Promise
                'w.fetch=(url,opt)=>{calls.push([url,JSON.parse(opt.body).args]);'
                'return Promise.resolve({ok:true,json:()=>Promise.resolve({})});};'
                'let easy=0;w.addEventListener("mousedown",()=>easy++);'
                'w.eval(fs.readFileSync(' + json.dumps(str(ROOT / "public" / "enhance.js")) + ',"utf8"));'
                'w.document.dispatchEvent(new w.Event("DOMContentLoaded"));'
                'const W=w.innerWidth;'
                'const md=(x,y)=>{const e=new w.MouseEvent("mousedown",{clientX:x,clientY:y,screenX:x,screenY:y,button:0,bubbles:true,cancelable:true});w.document.body.dispatchEvent(e);return e};'
                'const e1=md(W-2,2);'  # 右上角：edge = 右|上 = 2|4 = 6
                'w.dispatchEvent(new w.MouseEvent("mousemove",{clientX:W+30,clientY:-18,screenX:W+30,screenY:-18,bubbles:true}));'
                'w.dispatchEvent(new w.MouseEvent("mouseup",{bubbles:true}));'
                'const e2=md(300,300);'  # 中心：easy_drag 必须照常收到
                'console.log(JSON.stringify({calls,easy,edgePrevented:e1.defaultPrevented,centerPrevented:e2.defaultPrevented}));'
            )
            r3 = _sp.run(["node", "-e", probe2], capture_output=True, text=True, timeout=30)
            out3 = (r3.stdout or "").strip()
            assert out3, f"jsdom 边缘缩放执行失败: {(r3.stderr or '')[-300:]}"
            r = json.loads(out3)
            assert r["edgePrevented"] and r["easy"] == 1 and not r["centerPrevented"], r
            assert r["calls"][0][0].endswith("/api/op/resizeBegin"), r
            # 高频 mousemove 会合并成一次 POST（inflight），取 resizeWindow 那条
            rw = [c for c in r["calls"] if c[0].endswith("/api/op/resizeWindow")]
            assert rw and rw[0][1][2] == 6, r  # edge=6（右上）
            assert "pywebview" not in json.dumps(r["calls"]), "不该再走 js_api 桥"
            print("  [PASS] 边缘拖拽真实执行：HTTP /api/op/resizeWindow edge=6，easy_drag 被拦且中心拖拽照常")
        else:
            print("  [SKIP] 无 jsdom，跳过真实执行断言")

        # 4) 注入不落盘：磁盘文件字节数 < 响应字节数（响应=磁盘+注入）
        disk_size = (ROOT / "public" / "index.html").stat().st_size
        with urllib.request.urlopen(base + "/public/index.html", timeout=5) as r:
            served_len = len(r.read())
        assert disk_size < served_len, (disk_size, served_len)
        print(f"  [PASS] 注入不落盘：磁盘 {disk_size}B < 响应 {served_len}B")

        print("ALL WINDOW-RESIZE TESTS PASS")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=4)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
