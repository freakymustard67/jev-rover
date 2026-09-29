#!/usr/bin/env python3
"""Aggregate the three rule evaluations into one markdown table (evidence)."""
import json
import sys
from pathlib import Path

D = Path(sys.argv[1])
runs = {n: json.loads((D / f"out-{n}.json").read_text()) for n in ("master", "pr", "proto")}
rows = []
for s in runs["master"]["scenarios"]:
    name = s["name"]
    cell = lambda n: ("**T**" if runs[n]["scenarios"][[x["name"] for x in runs[n]["scenarios"]].index(name)]["decision"] else "F")
    d = s["diag"]
    mark = lambda n: cell(n)
    exp = "F" if s["expect"] is False else "T"
    good = lambda n: d and None
    rows.append((name, s["cls"], exp, mark("master"), mark("pr"), mark("proto"),
                 d["legacy_delta_norm"], d["dL"], d["dchroma"],
                 ",".join(str(v) for v in d["legacy_probe_px"])))
print("| scenario | class | truth | master | PR | proto | |d| | dL | dC | probe px |")
print("|---|---|---|---|---|---|---|---|---|---|")
for r in rows:
    print(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {r[6]} | {r[7]} | {r[8]} | {r[9]} |")
print()
for n in runs:
    rr = runs[n]
    print(f"{n}: FP={rr['fp']} FN={rr['fn']} fp_cases={rr['fp_cases']}")
    for k, v in rr["flake"].items():
        print(f"   flake {k}: noise-only {v['noise_only']['flags']}/{v['noise_only']['n']} "
              f"injected {v['injected']['flags']}/{v['injected']['n']} "
              f"total {v['flags']}/{v['n']}")
