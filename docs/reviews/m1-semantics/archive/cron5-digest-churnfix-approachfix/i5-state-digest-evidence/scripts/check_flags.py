"""List digest entries carrying the optional flags (height_suspect / motion),
and any absent-by-default keys, for one recorded payload.

Usage: python check_flags.py [states/post_20.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "states" / "post_20.json"
sem = json.loads(path.read_text())["observed"]["semantics"]
flagged = [o for o in sem["objects"] if set(o) - {"label", "x", "y", "confidence"}]
print(f"# {path}: {len(sem['objects'])} entries, {len(flagged)} with optional flags")
for o in flagged:
    print(json.dumps(o))
print("diff:", json.dumps(sem["diff"]))
if sem.get("objects_more"):
    print("objects_more:", sem["objects_more"])
