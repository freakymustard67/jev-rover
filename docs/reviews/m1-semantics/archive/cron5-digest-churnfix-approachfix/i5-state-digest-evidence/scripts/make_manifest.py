"""Write sha256sums.txt for every file in the evidence dir (except the manifest
itself). Deterministic: sorted paths, standard sha256sum output format.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE.parent
out = EVIDENCE / "sha256sums.txt"
lines = []
for p in sorted(EVIDENCE.rglob("*")):
    if not p.is_file() or p == out or p.name == "__pycache__":
        continue
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    lines.append(f"{digest}  {p.relative_to(EVIDENCE)}")
out.write_text("\n".join(lines) + "\n")
print(f"{len(lines)} files -> {out}")
for line in lines:
    print(line)
