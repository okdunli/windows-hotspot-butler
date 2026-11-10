"""verify_evidence.py 的判定逻辑单测（用合成报告，无需真 Windows）。

锁定用户最后一步 `python python-app/verify_evidence.py <zip|dir>` 的三种结局：
  - win32 + admin + apply + 22 PASS + exe_smoke PASS -> 0 (VALID)
  - 同上但 exe_smoke FAIL                              -> 1 (FAILED)
  - 非 win32                                            -> 2 (NOT_WINDOWS)

    python python-app/test_verify_evidence.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
VER = HERE / "verify_evidence.py"


def _write(d: Path, platform: str, exe_verdict: str) -> None:
    results = [{"name": f"check{i}", "status": "PASS", "detail": "ok"} for i in range(22)]
    (d / "selftest_apply_report.json").write_text(json.dumps({
        "platform": platform, "admin": platform == "win32",
        "apply": platform == "win32", "results": results}))
    (d / "exe_smoke.json").write_text(json.dumps({
        "platform": platform, "verdict": exe_verdict,
        "checks": [{"name": "界面渲染", "status": exe_verdict, "detail": "widgets=92"}]}))


def _run(target: Path) -> int:
    return subprocess.run([sys.executable, str(VER), str(target)],
                          capture_output=True, text=True).returncode


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write(d, "win32", "PASS")
        assert _run(d) == 0, "win32+exe PASS 应为 VALID(0)"
        print("  [PASS] win32 + exe_smoke PASS -> VALID (0)")

        _write(d, "win32", "FAIL")
        assert _run(d) == 1, "exe smoke FAIL 应为 FAILED(1)"
        print("  [PASS] win32 + exe_smoke FAIL -> FAILED (1)")

        _write(d, "darwin", "PASS")
        assert _run(d) == 2, "非 win32 应为 NOT_WINDOWS(2)"
        print("  [PASS] darwin -> NOT_WINDOWS (2)")
    print("ALL 3 VERIFY-EVIDENCE TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
