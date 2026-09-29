#!/usr/bin/env python3
"""Cron spot-check: count I1 eval results from the independently re-run JSON."""
import json
from pathlib import Path

p = Path("/home/freakymustard/jev-rover-research/runs/20260928-2342/i1-evidence/eval-cron-check.json")
d = json.loads(p.read_text())
print("top-level keys:", list(d.keys()) if isinstance(d, dict) else f"list[{len(d)}]")

def summarize(rows, name):
    n = len(rows)
    ok = sum(1 for r in rows if r.get("ok"))
    print(f"{name}: {ok}/{n} = {ok/n:.3f}")
    return ok, n

if isinstance(d, dict):
    for k, v in d.items():
        if isinstance(v, list) and v and isinstance(v[0], dict) and "ok" in v[0]:
            summarize(v, f"  {k}")
    # any summary field
    for k, v in d.items():
        if not isinstance(v, list):
            print(f"  {k}: {v}")
else:
    summarize(d, "all")
    # split by baseline/proposed flag if present
    if d and "baseline_ok" in d[0]:
        summarize([{"ok": r["baseline_ok"]} for r in d], "baseline")
        summarize([{"ok": r["proposed_ok"]} for r in d], "proposed")
