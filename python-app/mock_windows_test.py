"""Mock-driven execution of the Windows-only branches (no real Windows needed).

被测对象是当前真实链路：
  * 托盘：原版 hotspot/webui/tray.py::TrayIcon（真 pystray 被 mock 掉）
  * 热键：原版 hotspot/webui/hotkey.py::start_global_hotkey → 回调打 /api/op/toggle
  * 桌面壳：desktop_webview.py 组装托盘/热键并把「退出」接到 window.destroy()
旧的 desktop_native.py（Tk 降级链路的旧壳）已随「删掉旧纯 Python」移除。

    python python-app/mock_windows_test.py
"""
import json
import sys
import types
import unittest.mock as mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

calls = {'hotkey': [], 'tray': [], 'tooltip': [], 'toggle': [], 'destroy': []}


class FakeEvent:
    """pywebview 的 window.events：支持 += 绑定。"""

    def __init__(self):
        self.handlers = []

    def __iadd__(self, f):
        self.handlers.append(f)
        return self

    def set(self):
        pass


class FakeWindow:
    def __init__(self, **kw):
        self.kw = kw
        self.events = types.SimpleNamespace(
            shown=FakeEvent(), closing=FakeEvent(), closed=FakeEvent(),
            loaded=FakeEvent(), resized=FakeEvent())
        self.width, self.height = 1000, 620
        self.x = self.y = 0

    def show(self):
        pass

    def hide(self):
        pass

    def restore(self):
        pass

    def destroy(self):
        calls['destroy'].append('destroy')

    def minimize(self):
        pass

    def maximize(self):
        pass

    def evaluate_js(self, s):
        pass


# ---- fake webview -------------------------------------------------------
fake_webview = types.ModuleType('webview')
_webviews = []


def _create_window(**kw):
    w = FakeWindow(**kw)
    _webviews.append(w)
    return w


fake_webview.create_window = _create_window
def _fake_start(**kw):
    # webview.start() 返回 = 主窗口已关闭。此刻抓托盘状态（之后 tray.stop()
    # 会把 _icon 置 None，那是真实的关闭清理，不是缺陷）。
    calls.setdefault('webview_start', []).append(kw)
    tr = getattr(fake_backend_ref.get('backend'), '_tray', None)
    if tr is not None and tr._icon is not None:
        calls['tray_snapshot'] = (tr.available, list(tr._icon.menu.items))


fake_webview.start = _fake_start
fake_backend_ref = {}
sys.modules['webview'] = fake_webview

# ---- fake pystray / PIL（真实 tray.py 依赖它们） -------------------------
fake_pystray = types.ModuleType('pystray')


class _Icon:
    def __init__(self, name, icon=None, title=None, menu=None):
        self.name, self.icon, self.title, self.menu = name, icon, title, menu
        self.running = False

    def run(self):
        self.running = True

    def stop(self):
        self.running = False


class _MenuItem:
    def __init__(self, text, action=None, default=False):
        self.text, self.action, self.default = text, action, default

    def __call__(self, *a, **k):
        return self


class _Menu:
    def __init__(self, *items):
        self.items = list(items)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


fake_pystray.Icon = _Icon
fake_pystray.Menu = _Menu
fake_pystray.MenuItem = _MenuItem
fake_pystray._base = types.SimpleNamespace(Icon=_Icon)

pil = types.ModuleType('PIL')
pil_image = types.ModuleType('PIL.Image')
pil_draw = types.ModuleType('PIL.ImageDraw')


class _Img:
    size = (64, 64)

    def convert(self, *a):
        return self

    def resize(self, *a, **k):
        return self


pil_image.open = lambda *a, **k: _Img()
pil_image.new = lambda *a, **k: _Img()
pil_draw.Draw = lambda *a, **k: mock.MagicMock()
pil.Image = pil_image
pil.ImageDraw = pil_draw
sys.modules['pystray'] = fake_pystray
sys.modules['PIL'] = pil
sys.modules['PIL.Image'] = pil_image
sys.modules['PIL.ImageDraw'] = pil_draw

# ---- fake backend (真实 server.get_backend 的替身) ----------------------
class FakeBackend:
    def __init__(self):
        # 真实 cfg 是属性式对象（cfg.mini_window.get(...)），子项才是 dict
        self.cfg = types.SimpleNamespace(
            mini_window={'show': False},
            close_to_tray=False,
            confirm_exit_hotspot=False,
            hotkey_enabled=True,
            window={},
        )
        self._exit_confirmed = False
        self._tray = None
        self.controller = types.SimpleNamespace(last_status=types.SimpleNamespace(active=False))

    def attach_window(self, w):
        self._window = w

    def toggle(self):
        import urllib.request
        urllib.request.urlopen(
            urllib.request.Request('http://127.0.0.1:1/api/op/toggle', data=b'{}'))
        return {'ok': True}

    def shutdown(self):
        pass

    def save_config(self, *a, **k):
        pass


# ---- fake service (urllib) ---------------------------------------------
class FakeResp:
    status = 200

    def __init__(self, data):
        self._d = json.dumps(data).encode()

    def read(self):
        return self._d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=8):
    url = req.full_url if hasattr(req, 'full_url') else str(req)
    if url.endswith('/api/state'):
        return FakeResp({'hotspot': {'active': True, 'ssid': 'MockAP'},
                         'stats': {'online': 3}, 'config': {'ssid': 'MockAP'}})
    if '/api/op/' in url:
        calls['toggle'].append('op:' + url.split('/api/op/')[-1])
        return FakeResp({'ok': True, 'started': True})
    return FakeResp({})


# ---- drive desktop_webview.main() ---------------------------------------
import hotspot.webui.tray as _t
import desktop_webview
import hotspot.webui.hotkey as hotkey
import server

fake_backend = FakeBackend()
fake_backend_ref['backend'] = fake_backend


def fake_start_global_hotkey(on_toggle):
    calls['hotkey'].append('registered')
    calls['toggle'].append(on_toggle)   # capture the callback
    return True


with mock.patch.object(server, 'get_backend', lambda: fake_backend), \
     mock.patch.object(hotkey, 'start_global_hotkey', fake_start_global_hotkey), \
     mock.patch.object(sys, 'platform', 'win32'), \
     mock.patch('urllib.request.urlopen', fake_urlopen):
    rc = desktop_webview.main(['--port', '8899'])

print('desktop_webview.main() rc =', rc)
print('  tray calls:', calls['tray'])
print('  hotkey registered:', calls['hotkey'])

# the captured hotkey callback must really call /api/op/toggle
cb = [c for c in calls['toggle'] if callable(c)][0]
before = len(calls['toggle'])
with mock.patch('urllib.request.urlopen', fake_urlopen):
    cb()   # simulate Ctrl+Alt+H while the fake service is in place
new_calls = [c for c in calls['toggle'][before:] if isinstance(c, str)]
print('  hotkey callback ->', new_calls)

assert rc == 0
assert 'registered' in calls['hotkey'], 'hotkey not registered'
assert any(c.startswith('op:toggle') for c in new_calls), 'hotkey did not toggle'

# 托盘：真实 TrayIcon 已构造并可用。快照取自 webview.start() 返回之前——
# 之后 main() 会调 tray.stop() 清理图标（窗口已关闭，属正常清理，不是缺陷）。
avail, menu_items = calls['tray_snapshot']
assert avail is True, 'tray not available (mock pystray)'
assert len(menu_items) == 2, f"托盘菜单应有 2 项（显示主界面 / 退出）：{menu_items}"
# 托盘「退出」→ 窗口 destroy()
menu_items[1].action()
assert 'destroy' in calls['destroy'], 'tray exit did not destroy the window'

print('PASS: Windows tray/hotkey branch executes and calls /api/op/toggle')
