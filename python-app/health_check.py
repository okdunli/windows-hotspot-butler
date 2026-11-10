"""综合健康检查：一条命令跑完本移植的所有可自动验证项。

    python python-app/health_check.py            # 全部
    python python-app/health_check.py --skip-win # 跳过 Windows mock 驱动

覆盖：
  1. 1:1 文件等价（PARITY_MANIFEST.json 54 文件 SHA-256）
  2. smoke_exe 的 Windows 逻辑 mock 单测（tasklist/FindWindowW/onefile 阈值）
  3. 全部 Python 模块编译
  5. Windows 分支 mock 驱动（托盘/热键注册 → /api/op/toggle、托盘退出 → WM_CLOSE、
     23 项自检分支含真实网络栈的 UDP 53 / HTTP 80）
  6. 证据判定尺（verify_evidence.py 对当前报告给出明确结论）
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable

CHECKS: list[tuple[str, list[str], bool]] = [
    ("1:1 parity (54 files SHA-256)", ["python-app/check_parity.py"], False),
    ("smoke_exe logic (Windows mock): tasklist/FindWindowW/onefile threshold", ["python-app/test_smoke_exe.py"], False),
    ("verify_evidence logic: VALID/FAILED/NOT_WINDOWS", ["python-app/test_verify_evidence.py"], False),
    ("build_exe cmd (Windows ';' separator / hidden-imports / entry)", ["python-app/test_build_cmd.py"], False),
    ("bat files (CRLF/ASCII/labels, prevents cmd.exe line-splitting)", ["python-app/test_bat_files.py"], False),
    ("startup registry (frozen=exe / source=python run.py / disable)", ["python-app/test_startup_reg.py"], False),
    ("server API contract (/api/state, /api/mini, /api/i18n, unknown-op 404)", ["python-app/test_api_endpoints.py"], False),
    ("UI->backend op contract (every op() resolves to a backend method)", ["python-app/test_ui_ops.py"], False),
    ("original UI 1:1 parity (served HTML/CSS == 原版 byte-for-byte)", ["python-app/test_original_ui.py"], False),
    ("native mini window (2nd pywebview window, not popup)", ["python-app/test_native_mini.py"], False),
    ("window resize/maximize/version enhancement layer", ["python-app/test_window_resize.py"], False),
    ("mock: tray/hotkey -> /api/op/toggle", ["python-app/mock_windows_test.py"], True),
    ("mock: tray exit -> WM_CLOSE/WM_DESTROY", ["python-app/mock_tray_exit_test.py"], True),
    ("mock: 23-item Windows selftest branch", ["python-app/mock_selftest_test.py"], True),
    ("evidence verdict (verify_evidence.py)", ["python-app/verify_evidence.py"], False),
]


def run(name: str, args: list[str]) -> tuple[str, str]:
    """返回 (PASS|FAIL|SKIP, tail)。退出码 2 = verify_evidence.py 的
    NOT_WINDOWS（当前环境无 Windows，属预期，不是失败）。"""
    r = subprocess.run([PY, "-u", *args], cwd=str(HERE.parent),
                       capture_output=True, text=True, timeout=300)
    tail = (r.stdout + r.stderr).strip().split("\n")[-1][:120]
    if r.returncode == 2:
        return "SKIP", tail
    return ("PASS" if r.returncode == 0 else "FAIL"), tail


def main(argv: list[str]) -> int:
    skip_win = "--skip-win" in argv
    print("=" * 66)
    print("Windows Hotspot Butler (Rosezm) - health check")
    print("=" * 66)
    failed = 0
    for name, args, is_win_mock in CHECKS:
        if is_win_mock and skip_win:
            print(f"  [SKIP] {name}")
            continue
        # 干净克隆里没有自检报告（它被 .gitignore 排除），此时不应把 evidence 判 FAIL。
        if name.startswith("evidence verdict") and not (HERE / "windows_selftest_report.json").is_file():
            print(f"  [SKIP] {name}: 暂无 windows_selftest_report.json（跑 WindowsEvidence.bat 后生成）")
            continue
        try:
            status, tail = run(name, args)
        except subprocess.TimeoutExpired:
            status, tail = "FAIL", "timeout"
        print(f"  [{status}] {name}: {tail}")
        if status == "FAIL":
            failed += 1

    # 2. 编译全部 Python
    import py_compile
    compile_bad: list[str] = []
    for p in sorted(HERE.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        try:
            py_compile.compile(str(p), doraise=True)
        except py_compile.PyCompileError as e:
            compile_bad.append(f"{p.relative_to(HERE)}: {e}")
    print(f"  [{'PASS' if not compile_bad else 'FAIL'}] all Python compiles"
          f"{': ' + '; '.join(compile_bad[:3]) if compile_bad else ''}")
    if compile_bad:
        failed += 1

    print("-" * 66)
    print(f"结果：{'ALL GREEN' if failed == 0 else f'{failed} check(s) FAILED'}")
    print("（Windows 专属能力的真实实跑仍需在 Windows 10/11 管理员环境执行")
    print("  WindowsEvidence.bat —— 见 WINDOWS_VERIFY.md）")
    print("=" * 66)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
