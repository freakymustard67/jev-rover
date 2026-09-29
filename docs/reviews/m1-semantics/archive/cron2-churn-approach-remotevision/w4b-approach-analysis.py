#!/usr/bin/env python3
"""w4b analysis: stall-boundary sweep + trace signatures.

Reuses w4b-approach-sim.py via importlib; runs boundary cases in Jev mode and
baseline mode; summarizes the collected traces.
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("w4b_sim", os.path.join(HERE, "w4b-approach-sim.py"))
sim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sim)


def summarize_case(r):
    tr = r["trace_3hz"]
    tail = [x for x in tr if x[0] >= tr[-1][0] - 20.0]
    xs = [x[1] for x in tail]
    ys = [x[2] for x in tail]
    yaws = [x[3] for x in tail]
    vs = [abs(x[4]) for x in tail]
    return dict(min_goal=r["min_dist_goal_m"], reach030=r["reached_0.30"],
                blocked=r["approach_cell_blocked"], plan=r["plan_found"],
                pos_span=(round(max(xs) - min(xs), 3), round(max(ys) - min(ys), 3)),
                yaw_minmax=(min(yaws), max(yaws)), vmax_tail=round(max(vs), 2),
                still6=r["still_last6s_s"], tail_src=r["tail_sources"],
                reflexes=r["reflexes"], end_nearest=r["end_nearest_m"])


print("== boundary sweep, Jev hold_course, object in grid ==")
rows = []
for r_ in (0.30, 0.40):
    for s in (0.40, 0.45, 0.50, 0.55, 0.60):
        res = sim.run_case(r_, s, jev_mode=True)
        rows.append(res)
        d = summarize_case(res)
        print(f"r={r_} s={s}: blocked={d['blocked']} min_goal={d['min_goal']} "
              f"reach0.30={d['reach030']} still6={d['still6']} tail_pos_span={d['pos_span']} "
              f"yaw=[{d['yaw_minmax'][0]},{d['yaw_minmax'][1]}] vmax={d['vmax_tail']} "
              f"src={d['tail_src']} refx={d['reflexes']}")

print("\n== boundary sweep, baseline (no Jev), object in grid ==")
for r_ in (0.30, 0.40):
    for s in (0.40, 0.45, 0.50, 0.60):
        res = sim.run_case(r_, s, jev_mode=False)
        rows.append(res)
        d = summarize_case(res)
        print(f"r={r_} s={s}: blocked={d['blocked']} min_goal={d['min_goal']} "
              f"reach0.30={d['reach030']} tail_pos_span={d['pos_span']} "
              f"vmax={d['vmax_tail']} src={d['tail_src']} refx={d['reflexes']}")

print("\n== stall signatures from earlier results (standoff 0.35 cases) ==")
with open(os.path.join(HERE, "w4b-approach-results.json")) as f:
    earlier = json.load(f)
for r in earlier:
    if r["standoff"] == 0.35 and r["obj_in_grid"]:
        d = summarize_case(r)
        print(f"{r['label']}: min_goal={d['min_goal']} blocked={d['blocked']} "
              f"tail_pos_span={d['pos_span']} yaw=[{d['yaw_minmax'][0]},{d['yaw_minmax'][1]}] "
              f"vmax_tail={d['vmax_tail']} still6={d['still6']} src={d['tail_src']} "
              f"refx={d['reflexes']} end_nearest={d['end_nearest']}")
        print("  tail(1 Hz, last 12 s): t,x,y,yaw,v,w,src,d_goal,nearest,no_prog:")
        for row in r["trace_3hz"][-36::3]:
            print("   ", row)

print("\n== pipeline E2E numbers ==")
with open(os.path.join(HERE, "w4b-pipeline-results.json")) as f:
    pipe = json.load(f)
for c in pipe:
    print(c["label"], "min_goal", c["min_goal"], "min_mat", c["min_mat"],
          "still6", c["still6"], "end_src", c["end_src"], "reflexes", c["reflexes"])

print("\n== blocked radius, pure geometry (no noise), formula check ==")
for r_ in (0.20, 0.30, 0.35, 0.40, 0.50):
    grid = sim.make_grid((2.0, 2.0), r_)
    rb = sim.blocked_radius(grid, (2.0, 2.0))
    print(f"obj_r={r_}: blocked_radius={rb:.3f} = obj_r + {rb - r_:.3f}")