"""复验 1:1 文件等价清单：重算 PARITY_MANIFEST.json 里每个文件的 SHA-256。

    python python-app/check_parity.py

对照源（原版业务包）从 **git 对象**里取：`git show <ref>:<path>`，
ref 写在清单的 `source_ref` 字段。这样本机与 CI 都不依赖任何绝对路径
（CI 上 checkout 完整历史即可）。

退出码 0 = 全部一致；1 = 有缺失/差异/多余文件。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MANIFEST = ROOT / "PARITY_MANIFEST.json"


def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha(p: Path) -> str:
    return sha_bytes(p.read_bytes())


def git_show(ref: str, path: str) -> bytes | None:
    """从 git 对象读原版文件；读不到返回 None。"""
    try:
        r = subprocess.run(["git", "show", f"{ref}:{path}"],
                           cwd=str(ROOT), capture_output=True)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def main(argv: list[str]) -> int:
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    ref = m.get("source_ref", "")
    port_root = Path(argv[argv.index("--port-root") + 1]) if "--port-root" in argv else ROOT
    pkg = port_root / "python-app/hotspot"

    bad: list[str] = []
    checked = 0
    for row in m["files"]:
        rel = row["file"]
        dst = pkg / rel
        src = git_show(ref, f"{m.get('source_prefix', 'hotspot/')}{rel}")
        if src is None:
            bad.append(f"MISSING-SRC {rel}")
            continue
        if not dst.is_file():
            bad.append(f"MISSING {rel}")
            continue
        if sha_bytes(src) != sha(dst):
            bad.append(f"DIFFERENT {rel}")
        checked += 1

    # 包内不应有清单之外的新文件
    expected = {r["file"] for r in m["files"]}
    for p in sorted(pkg.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            rel = str(p.relative_to(pkg))
            if rel not in expected:
                bad.append(f"EXTRA {rel}")

    for row in m.get("relocated_assets", []):
        # relocated rows' `file` is relative to the PROJECT root (the original
        # hotspot/templates and hotspot/scripts live under hotspot/, which the
        # package-internal rows above already include as their prefix).
        # relocated_assets 的 file 以 hotspot/ 开头；对照源的根前缀可能不同
        asset_rel = row["file"]
        prefix = m.get("source_prefix", "hotspot/")
        if prefix != "hotspot/" and asset_rel.startswith("hotspot/"):
            asset_rel = prefix + asset_rel[len("hotspot/"):]
        src = git_show(ref, asset_rel)
        dst = port_root / row["port"]
        if src is None:
            bad.append(f"ASSET-MISSING-SRC {row['file']}")
        elif not dst.is_file() or sha_bytes(src) != sha(dst):
            bad.append(f"ASSET {row['file']}")
        checked += 1

    print(f"1:1 parity: {checked} files checked against git ref {ref!r}, "
          f"{len(bad)} problem(s)")
    for b in bad:
        print("  -", b)
    if bad:
        print("FAIL: the port is not byte-identical to the original business package")
        return 1
    print("PASS: hotspot/ (48 files) + templates/scripts (6 files) are byte-identical")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
