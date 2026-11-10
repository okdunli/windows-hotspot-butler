"""托盘「显示主界面 / 退出」的语义回归（mock 驱动，无需 Windows）。

当前真实链路（desktop_webview.py 装配原版 tray.py）：
  * 托盘「显示主界面」→ window.show() + window.restore()（pywebview 线程安全封装）
  * 托盘「退出」      → tray.stop() + window.destroy()
核心防线：**不得 spawn 子进程** —— 旧实现 subprocess.Popen([exe, ...]) 在
PyInstaller 下会递归重启 exe（用户最早遇到的「一直重启」故障）。

旧的 desktop_native.py（Tk 降级链路的旧壳）已随「删掉旧纯 Python」移除。

    python python-app/mock_tray_exit_test.py
"""
import sys
import types
import unittest.mock as mock
from pathlib import Path

R = Path(__file__).resolve().parent
sys.path.insert(0, str(R))
sys.path.insert(0, str(R.parent))

calls = {'show': 0, 'restore': 0, 'destroy': 0, 'tray_stop': 0}


class FakeEvent:
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
        self.width, self.height, self.x, self.y = 1000, 620, 0, 0

    def show(self):
        calls['show'] += 1

    def restore(self):
        calls['restore'] += 1

    def destroy(self):
        calls['destroy'] += 1

    def hide(self):
        pass

    def minimize(self):
        pass

    def maximize(self):
        pass

    def evaluate_js(self, s):
        pass


fake_webview = types.ModuleType('webview')
_win = []


def _create_window(**kw):
    w = FakeWindow(**kw)
    _win.append(w)
    return w


def _start(**kw):
    # 窗口「关闭」= webview.start() 返回。此刻托盘仍可用；main() 随后的
    # tray.stop() 会清掉 _icon（正常清理），所以菜单项要在这里先抓下来。
    tr = getattr(fake_backend_ref.get('backend'), '_tray', None)
    if tr is not None and tr._icon is not None:
        calls['menu_items'] = list(tr._icon.menu.items)


fake_backend_ref = {}
fake_webview.create_window = _create_window
fake_webview.start = _start
sys.modules['webview'] = fake_webview

# 假 pystray / PIL（真实 tray.py 依赖）
fake_pystray = types.ModuleType('pystray')


class _Icon:
    def __init__(self, name, icon=None, title=None, menu=None):
        self.name, self.icon, self.title, self.menu = name, icon, title, menu

    def run(self):
        pass

    def stop(self):
        pass


class _MenuItem:
    def __init__(self, text, action=None, default=False):
        self.text, self.action, self.default = text, action, default

    def __call__(self, *a, **k):
        return self


class _Menu:
    def __init__(self, *items):
        self.items = list(items)


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


class FakeBackend:
    def __init__(self):
        self.cfg = types.SimpleNamespace(
            mini_window={'show': False}, close_to_tray=False,
            confirm_exit_hotspot=False, hotkey_enabled=False, window={})
        self._exit_confirmed = False
        self._tray = None
        self.controller = types.SimpleNamespace(last_status=types.SimpleNamespace(active=False))

    def attach_window(self, w):
        pass

    def shutdown(self):
        pass

    def save_config(self, *a, **k):
        pass


import desktop_webview
import server

fake_backend = FakeBackend()
fake_backend_ref['backend'] = fake_backend
with mock.patch.object(server, 'get_backend', lambda: fake_backend), \
     mock.patch.object(sys, 'platform', 'win32'):
    rc = desktop_webview.main(['--port', '8899'])

print('desktop_webview.main() rc =', rc)

tray = fake_backend._tray
assert tray is not None, 'tray not created'
menu_items = calls['menu_items']
assert len(menu_items) == 2, f'托盘菜单应有 2 项：{menu_items}'

# ---- 托盘「显示主界面」→ window.show() + window.restore() -------------
calls['show'] = calls['restore'] = 0
menu_items[0].action()
print('  show ->', calls)
assert calls['show'] == 1 and calls['restore'] == 1, '托盘「显示主界面」未 show/restore 窗口'
print('PASS: tray show brings the existing window back (show + restore)')

# ---- 托盘「退出」→ tray.stop() + window.destroy() ----------------------
calls['destroy'] = 0
menu_items[1].action()
print('  exit ->', calls)
assert calls['destroy'] == 1, '托盘「退出」未 destroy 窗口'
print('PASS: tray exit destroys the window')

# ---- 核心防线：不得 spawn 子进程（PyInstaller 下会递归重启 exe） -------
src = (R / 'desktop_webview.py').read_text(encoding='utf-8')
assert 'import subprocess' not in src, 'desktop_webview 不应 import subprocess（防 exe 递归重启）'
assert 'subprocess' not in src.replace('import subprocess', ''), \
    'desktop_webview 不应再出现 subprocess 调用'
print('PASS: no subprocess spawn in the desktop shell (no exe recursion)')
