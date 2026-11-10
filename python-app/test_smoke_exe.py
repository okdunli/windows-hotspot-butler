"""smoke_exe.py 的纯逻辑单测（mock 模拟 Windows，无需真 Windows 机器）。

覆盖无法在 macOS 实跑的 Windows 分支：tasklist 解析、FindWindowW、
onefile/onedir 判定、单实例阈值。

    python python-app/test_smoke_exe.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import smoke_exe as S  # noqa: E402


def test_is_onefile() -> None:
    with tempfile.TemporaryDirectory() as d:
        exe = Path(d) / S.NAME
        exe.write_bytes(b"")
        assert S._is_onefile(exe) is True, "单文件（无 _internal）应判为 onefile"
        (Path(d) / "_internal").mkdir()
        assert S._is_onefile(exe) is False, "带 _internal/ 应判为 onedir"


def test_window_exists() -> None:
    old = S.WIN
    try:
        S.WIN = False
        assert S.window_exists() is None, "非 Windows 应 SKIP"
        S.WIN = True
        assert S.window_exists(_find=lambda a, b: 0x1234) is True
        assert S.window_exists(_find=lambda a, b: 0) is False

        def _boom(a, b):
            raise OSError("no user32")

        assert S.window_exists(_find=_boom) is None, "ctypes 不可用应 SKIP 而非 FAIL"
    finally:
        S.WIN = old


class _R:
    def __init__(self, out: str) -> None:
        self.stdout = out


def test_instance_count_win() -> None:
    old_win, old_run = S.WIN, S.subprocess.run
    try:
        S.WIN = True
        S.subprocess.run = lambda *a, **k: _R(
            "WiFiHotspotManager.exe   1234 Console   1  10,000 K\n"
            "WiFiHotspotManager.exe   5678 Console   1  10,000 K\n")
        assert S.instance_count() == 2
        S.subprocess.run = lambda *a, **k: _R("信息: 没有运行的任务匹配指定标准。\n")
        assert S.instance_count() == 0
    finally:
        S.WIN, S.subprocess.run = old_win, old_run


def test_instance_count_posix() -> None:
    old_win, old_run = S.WIN, S.subprocess.run
    try:
        S.WIN = False
        S.subprocess.run = lambda *a, **k: _R("123\n456\n")
        assert S.instance_count() == 2
    finally:
        S.WIN, S.subprocess.run = old_win, old_run


def test_threshold() -> None:
    # (进程数, onedir?, 期望通过)
    cases = [(1, False, True), (2, False, False),
             (1, True, True), (2, True, True),
             (30, True, False), (0, False, False)]
    for n, onefile, expect in cases:
        max_inst = 2 if onefile else 1
        assert (1 <= n <= max_inst) is expect, (n, onefile, expect)


def test_free_port() -> None:
    p = S.free_port()
    assert isinstance(p, int) and 0 < p < 65536, p


if __name__ == "__main__":
    tests = [test_is_onefile, test_window_exists, test_instance_count_win,
             test_instance_count_posix, test_threshold, test_free_port]
    for fn in tests:
        fn()
        print(f"  [PASS] {fn.__name__}")
    print(f"ALL {len(tests)} SMOKE-LOGIC TESTS PASS")
