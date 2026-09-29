#!/usr/bin/env python3
"""Fill the remaining Jev-mode cells of the standoff matrix."""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("w4b_sim", os.path.join(HERE, "w4b-approach-sim.py"))
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)

for r_, st in ((0.30, 0.35), (0.30, 0.55), (0.40, 0.70)):
    res = s.run_case(r_, st, jev_mode=True)
    tr = res["trace_3hz"]
    tail = [x for x in tr if x[0] >= tr[-1][0] - 20]
    span = (round(max(x[1] for x in tail) - min(x[1] for x in tail), 3),
            round(max(x[2] for x in tail) - min(x[2] for x in tail), 3))
    print(f'JEV r={r_} s={st}: blocked={res["approach_cell_blocked"]} '
          f'min_goal={res["min_dist_goal_m"]} reach0.30={res["reached_0.30"]} '
          f'still6={res["still_last6s_s"]} tail_span={span} '
          f'src={res["tail_sources"]} refx={res["reflexes"]} end_nearest={res["end_nearest_m"]}')