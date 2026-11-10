"""原生迷你浮窗回归：必须创建第二个 pywebview 窗口（不是 window.open popup）。

与原版 webui/app.py 一致：主窗 + 浮窗共享同一 HotspotBackend 单例（js_api），
toggle_mini 开→第二个 create_window(160×64, on_top, easy_drag=False, frameless)，
再 toggle 关闭销毁；boot_ready 钩子恢复；mini.html 逐字节原版。

    python python-app/test_native_mini.py
"""
from __future__ import annotations

import sys
import types
import unittest.mock as mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import desktop_webview as D  # noqa: E402
import server  # noqa: E402


class _Ev:
    def __iadd__(self, f):
        return self


class _Win:
    def __init__(self, **kw):
        self.kw = kw
        self.events = _Ev()
        self.destroyed = False
        self.shown = False

    def show(self):
        self.shown = True

    def hide(self):
        pass

    def restore(self):
        pass

    def destroy(self):
        self.destroyed = True

    def minimize(self):
        pass

    def evaluate_js(self, s):
        pass

    @property
    def native(self):
        raise AttributeError("mock: no winforms native")


def main() -> int:
    wins: list[_Win] = []
    fake = types.ModuleType("webview")
    fake.create_window = lambda **kw: (wins.append(_Win(**kw)) or wins[-1])
    fake.start = lambda **kw: None
    with mock.patch.dict(sys.modules, {"webview": fake}):
        rc = D.main(["--port", "8811"])
    assert rc == 0 and len(wins) == 1, (rc, len(wins))
    b = server.get_backend()
    assert wins[0].kw.get("js_api") is b, "主窗未挂共享 backend 单例"
    assert wins[0].kw.get("width") == 1000 and wins[0].kw.get("height") == 620
    assert wins[0].kw.get("min_size") == (880, 560)
    print("  [PASS] 主窗 1000×620（min 880×560）+ js_api=共享 backend")

    for h in ("_open_mini", "_close_mini", "_toggle_mini", "_boot_ready", "_app_ref"):
        assert hasattr(b, h), f"缺浮窗钩子 {h}"
    print("  [PASS] 原版 backend 浮窗钩子全部挂上")

    r1 = b.toggle_mini()
    assert r1.get("shown") is True and len(wins) == 2, (r1, len(wins))
    mw = wins[-1]
    assert mw.kw.get("title") == "热点浮窗"
    assert (mw.kw.get("width"), mw.kw.get("height")) == (160, 64)
    assert mw.kw.get("on_top") is True and mw.kw.get("easy_drag") is False
    assert mw.kw.get("frameless") is True and mw.kw.get("js_api") is b
    print("  [PASS] 原生浮窗：160×64 / on_top / easy_drag=False / 共享 js_api")

    r2 = b.toggle_mini()
    assert r2.get("shown") is False and mw.destroyed
    print("  [PASS] 再次点击关闭并销毁浮窗")

    # app.js 不得再用 window.open popup 实现浮窗
    app_js = (HERE.parent / "public" / "app.js").read_text(encoding="utf-8")
    assert 'window.open("/mini"' not in app_js, "浮窗退回 popup 实现了"
    assert "api().toggle_mini()" in app_js and "api().boot_ready()" in app_js
    print("  [PASS] 主窗 app.js 用 toggle_mini + boot_ready（非 popup）")

    # mini.html：原版去掉 web 层圆角/边框（原生窗口已做圆角，避免双层重叠），
    # 其余字节 == 原版；且不再含 border-radius/border
    O = Path("/Users/mac/github/windows-hotspot-butler/hotspot/webui/ui/mini.html")
    if O.is_file():
        orig = O.read_text(encoding="utf-8")
        mine = (HERE.parent / "public" / "mini.html").read_text(encoding="utf-8")
        # 唯一允许的差异：.card 规则去掉 border-radius 与 border 行
        assert "border-radius:13px" not in mine and "border:1px solid rgba(88,166,255,.28)" not in mine
        orig_stripped = orig.replace(
            """.card{
  width:100%;height:100%;border-radius:13px;
  font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;color:var(--text);
  background:linear-gradient(135deg,var(--bg1),var(--bg2));
  border:1px solid rgba(88,166,255,.28);
  display:flex;align-items:center;padding:0 4px 0 5px;gap:8px;cursor:default;
  transition:background .3s ease,color .3s ease,border-color .3s ease;
}
html[data-theme="light"] .card{border-color:rgba(47,111,237,.35)}
.card.on{border-color:rgba(63,185,80,.45)}""",
            """.card{
  width:100%;height:100%;
  font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;color:var(--text);
  background:linear-gradient(135deg,var(--bg1),var(--bg2));
  display:flex;align-items:center;padding:0 4px 0 5px;gap:8px;cursor:default;
  transition:background .3s ease,color .3s ease;
}""").replace(
            "   窗口 Region 11px（app.py）裁在边框线外 → 边框弧线完整 */",
            "   原生窗口已做圆角，web 层不再画圆角/边框，避免双层重叠 */")
        assert mine == orig_stripped, "mini.html 除圆角/边框外应与原版一致"
        print("  [PASS] mini.html == 原版（仅去 web 圆角/边框）")

    print("ALL NATIVE-MINI TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
