"""把 evidence\ 的全部输出 + 两份 JSON 报告打成一个 zip，方便贴回。

    python python-app/pack_evidence.py [目录]

默认读 ROOT/evidence，产物写 ROOT/whm-evidence.zip。
"""
from __future__ import annotations

import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def main(argv: list[str]) -> int:
    ev = Path(argv[1]).resolve() if len(argv) > 1 else ROOT / "evidence"
    if not ev.is_dir():
        print(f"[ERROR] {ev} 不存在 —— 请先运行 WindowsEvidence.bat 的探测步骤")
        return 1
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = ROOT / f"whm-evidence-{stamp}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(ev.rglob("*")):
            if p.is_file():
                z.write(p, f"evidence/{p.relative_to(ev)}")
        for j in ("windows_selftest_report.json", "desktop_probe_report.json",
                  "windows_selftest_report.json.bak"):
            f = HERE / j
            if f.is_file():
                z.write(f, f"python-app/{j}")
    print(f"[OK] {out} ({out.stat().st_size} bytes)")
    print("把这个 zip 文件贴回即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
