#!/usr/bin/env python3
"""Compare per-seed metrics of a spot-check driver run against the full run."""
import json
import sys

full = json.load(open(sys.argv[1]))
sub = json.load(open(sys.argv[2]))
mismatch = 0
checked = 0
for name, runs in sub["runs"].items():
    for r in runs:
        f = full["runs"][name][r["seed"]]
        for k in ("new_ids", "appeared", "vanished", "moved", "first_vanish_pass",
                  "alive_end", "resurrections", "resurrections_announced", "per_pass"):
            checked += 1
            if f[k] != r[k]:
                mismatch += 1
                print("MISMATCH", name, r["seed"], k, f[k], r[k])
print(f"checked {checked} fields across {sum(len(v) for v in sub['runs'].values())} per-seed runs; mismatches={mismatch}")
