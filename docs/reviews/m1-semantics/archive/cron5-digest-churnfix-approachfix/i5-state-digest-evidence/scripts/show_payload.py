"""Pretty-print the observed.semantics block of a recorded state payload.

Usage: python show_payload.py [states/post_20.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
path = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parent / "states" / "post_20.json"
state = json.loads(path.read_text())
sem = state["observed"]["semantics"]
print(f"# {path}")
print(json.dumps({"keys": list(sem), "n_objects": len(sem["objects"]),
                  "objects_more": sem.get("objects_more"),
                  "objects_head": sem["objects"][:2], "objects_tail": sem["objects"][-1:],
                  "destination": sem["destination"], "diff": sem["diff"]}, indent=1))
