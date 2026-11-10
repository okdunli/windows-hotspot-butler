"""Mock-drive windows_selftest.py's win32 branch (no real Windows needed).

Replaces the Windows-only probes with deterministic fakes and EXECUTES the
whole self-test module, so every branch (WinRT / netsh / ICS / firewall / UDP 53
/ registry / tray / hotkey / Tk / service round-trip / --apply toggle) runs.
Fails loudly if the script itself would crash on Windows.
"""
import importlib.util, json, sys, types, unittest.mock as mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# fake Win32 ctypes + WinRT/netsh/ICS/firewall/reg outputs
fake_ctypes = types.ModuleType('ctypes')
fake_ctypes.windll = types.SimpleNamespace(
    user32=mock.MagicMock(), kernel32=mock.MagicMock(), shell32=mock.MagicMock())

fake_tray = types.ModuleType('hotspot.webui.tray')
fake_tray.HAS_DEPS = True
fake_tray.TrayIcon = object
fake_hotkey = types.ModuleType('hotspot.webui.hotkey')
fake_hotkey.sys_check = lambda: True
fake_hotkey.start_global_hotkey = lambda cb: True
# Keep the REAL hotspot.core importable: only replace the two webui modules
# that need pystray/Win32. Don't shadow the package itself.
import hotspot.webui as _real_webui
sys.modules['ctypes'] = fake_ctypes
sys.modules['hotspot.webui.tray'] = fake_tray
sys.modules['hotspot.webui.hotkey'] = fake_hotkey
_real_webui.tray = fake_tray
_real_webui.hotkey = fake_hotkey

# load the real self-test module
spec = importlib.util.spec_from_file_location('wst', ROOT / 'windows_selftest.py')
wst = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wst)

# The mocked main() overwrites the REAL evidence report at
# python-app/windows_selftest_report.json (windows_selftest.py builds the
# path inline from its own HERE). Snapshot it before the run and restore
# after, so the genuine darwin report is never replaced by a fake win32 one.

# deterministic fakes for the core probes
caps = wst.wificaps.WlanCapabilities()
fake_results = {
    'admin': True,
    'winrt': {'ok': True, 'state': 'Off', 'error': '', 'errorType': ''},
    'netsh': 'Hosted network supported : Yes\n',
    'caps': caps,
    'gw': '192.168.137.1',
    'arp': [('192.168.137.24', 'aa:bb:cc:dd:ee:ff')],
    'ics_pub': 'Wi-Fi',
    'firewall': 'Rule Name: WHM-BLOCK-TEST\n',
    'reg': 'HKEY_CURRENT_USER\\...\\Run\n',
}

class FakeResp:
    status = 200
    def __init__(self, d): self._d = json.dumps(d).encode()
    def read(self): return self._d
    def __enter__(self): return self
    def __exit__(self, *a): return False

# fake netsh/reg/powershell COM outputs for the new side-effect probes
_firewall_added = {'v': False}
def fake_run(cmd, timeout=25):
    c = list(cmd)
    if c[:2] == ['netsh', 'advfirewall']:
        if 'add' in c: _firewall_added['v'] = True
        if 'delete' in c: _firewall_added['v'] = False
        return 0, 'Ok.', ''
    if c and c[0] == 'reg':
        if 'query' in c:
            return 0, ('HKEY_CURRENT_USER\\...\\Run    WifiHotspotManager    REG_SZ    '
                       '"python.exe" "python-app\\run.py"'), ''
        return 0, 'The operation completed successfully.', ''
    if 'powershell' in c[0]:
        return 0, 'HNETSHARE_OK\n', ''
    return 0, '', ''

def fake_console(cmd, timeout=20):
    c = list(cmd)
    if 'show' in c and 'rule' in c:
        return ('Rule Name: WHM-SELFTEST-FIREWALL\nEnabled: Yes\n'
                if _firewall_added['v'] else '')
    if c and c[0] == 'reg' and 'query' in c:
        return 'HKEY_CURRENT_USER\\...\\Run    WifiHotspotManager    REG_SZ    "python.exe" "python-app\\run.py"\n'
    if 'drivers' in c:
        return 'Hosted network supported : Yes\n'
    return 'mock output\n'

def fake_urlopen(req, timeout=15):
    url = req.full_url if hasattr(req, 'full_url') else str(req)
    if url.endswith('/api/state'):
        return FakeResp({'config': {'ssid': 'MockAP'}, 'admin': True,
                         'hotspot': {'active': False}, 'stats': {'online': 0}})
    if url.endswith('/api/op/getStatsReport'):
        return FakeResp({'ok': True, 'daily': [{'day': '2026-11-04'}]})
    if url.endswith('/api/op/saveConfigPatch'):
        return FakeResp({'ok': True, 'msg': '已保存'})
    if '/api/op/' in url:
        return FakeResp({'ok': True, 'started': True, 'msg': 'ok'})
    return FakeResp({})

# snapshot the REAL report so the mocked main() write can be rolled back.
# A clean clone has no report (it is gitignored) - tolerate that.
_real_report = ROOT / 'windows_selftest_report.json'
_real_report_bytes = _real_report.read_bytes() if _real_report.is_file() else None

# patch platform + every Windows-only entry point + the network
with mock.patch.object(sys, 'platform', 'win32'), \
     mock.patch.object(wst.pshell, 'is_admin', lambda: True), \
     mock.patch.object(wst.pshell, 'ps_script', lambda *a, **k: fake_results['winrt']), \
     mock.patch.object(wst.pshell, 'run', fake_run), \
     mock.patch.object(wst.pshell, 'run_console', fake_console), \
     mock.patch.object(wst.wificaps, 'probe_drivers', lambda: caps), \
     mock.patch.object(wst.netinfo, 'hotspot_gateway_ip', lambda: fake_results['gw']), \
     mock.patch.object(wst.netinfo, 'arp_entries', lambda *a: fake_results['arp']), \
     mock.patch.object(wst.ics, 'internet_adapter_name', lambda: fake_results['ics_pub']), \
     mock.patch.object(wst.urllib.request, 'urlopen', fake_urlopen), \
     mock.patch('time.sleep', lambda s: None), \
     mock.patch.object(wst, 'APPLY', True), \
     mock.patch.object(sys, 'argv', ['windows_selftest.py']):
    # 真实 socket：只替换 Windows 系统命令，不替换网络栈（否则 recvfrom /
    # server_address 变成 MagicMock，解包会得到 got 0 的假 FAIL）。
    # macOS 非 root 绑 53 会失败 -> 该项诚实 SKIP，属预期。
    rc = wst.main()

# Read what the MOCK wrote (still at the real path), THEN roll back so the
# genuine darwin report is never replaced by a fake win32 one.
report = json.loads((ROOT / 'windows_selftest_report.json').read_text(encoding='utf-8'))
if _real_report_bytes is not None:
    (ROOT / 'windows_selftest_report.json').write_bytes(_real_report_bytes)
else:
    (ROOT / 'windows_selftest_report.json').unlink(missing_ok=True)
statuses = [r['status'] for r in report['results']]
print('rc =', rc, '| PASS =', statuses.count('PASS'),
      '| FAIL =', statuses.count('FAIL'), '| SKIP =', statuses.count('SKIP'))
for r in report['results']:
    if r['status'] == 'FAIL':
        print('  FAIL:', r['name'], '-', r['detail'][:200])
assert rc == 0, 'self-test reported a FAIL under mocked Windows'
skips = [r['name'] for r in report['results'] if r['status'] == 'SKIP']
assert all('UDP 53' in n or 'DNS 劫持' in n for n in skips), f'unexpected SKIPs: {skips}'
print('SKIP (expected on non-root macOS):', skips)
print('PASS: all Windows-only branches execute, report generated, no FAIL/SKIP')
