#!/usr/bin/env python3
"""i7 E2E (pipeline arm): destination object through the REAL perception stack.

Adapted from w4b-approach-pipeline.py (SyntheticRoom + real Perception +
Executor.update at 15 Hz). The "blue mat" (0.7 x 0.6 m, r ~ 0.30-0.35) is the
destination; the approach ring uses cfg.destination.standoff_m.

Cases:
  A raw   goal = raw approach point            (pre-fix wiring; on an unpatched
                                               tree this is the only behavior)
  B proj  goal = Planner.project_to_free(approach) or approach  (F1 wiring;
          on an unpatched tree project_to_free does not exist -> falls back)
Modes: jev hold_course and no-Jev baseline.

Deterministic: no RNG; fixed dt; identical start pose and warm-up.
Run:  REPO=<clone> PYTHONDONTWRITEBYTECODE=1 <venv-python> i7-approach-pipeline.py [out.json]
"""
from __future__ import annotations

import json
import math
import os
import sys

import numpy as np

REPO = os.environ.get("REPO", "/home/freakymustard/.hermes/cache/scratch/i7/repo")
sys.path.insert(0, REPO)

from config import RoomConfig                                    # noqa: E402
from control import Executor                                     # noqa: E402
from perception import Perception                                # noqa: E402
from scene import (GoalInfo, Pose, SemanticMap, SemanticObject)  # noqa: E402
from semantics import approach_point, resolve_destination        # noqa: E402
from synthetic import SyntheticRoom                              # noqa: E402

MAT = (2.65, 0.90, 3.35, 1.50)          # x0, y0, x1, y1  (0.7 x 0.6 m "blue mat")
MAT_C = ((MAT[0] + MAT[2]) / 2.0, (MAT[1] + MAT[3]) / 2.0)     # (3.0, 1.2)
START = (1.4, 1.2)
DT = 1.0 / 15.0
BUDGET_S = 40.0


def dist_to_mat(x, y):
    dx = max(MAT[0] - x, 0.0, x - MAT[2])
    dy = max(MAT[1] - y, 0.0, y - MAT[3])
    return math.hypot(dx, dy)


def drive(label, goal_xy, jev_mode=False, budget_s=BUDGET_S):
    cfg = RoomConfig.load(os.path.join(REPO, "config", "room.synthetic.json"))
    syn = SyntheticRoom(cfg)
    syn.furniture = [("blue mat", MAT[0], MAT[1], MAT[2], MAT[3], (200, 80, 60))]
    syn.rover = Pose(START[0], START[1], 0.0)
    perc = Perception(cfg)
    scene = None
    for i in range(10):                                   # warm: floor sample + grid
        scene = perc.process(syn.render(), i * DT)

    ex = Executor(cfg, perc.grid)
    goal = GoalInfo(type="goto", name="dest", x=goal_xy[0], y=goal_xy[1])
    judg = ({"source": "jev", "age_s": 0.1, "maneuver": "hold_course", "risk": 0.2,
             "path_still_good": 1.0, "observation_unreliable": 0.0}
            if jev_mode else {"source": "none"})

    t, last_v, last_w = 10 * DT, 0.0, 0.0
    min_goal, min_mat = 1e9, 1e9
    trace = []
    for _ in range(int(budget_s / DT)):
        scene = perc.process(syn.render(), t, last_v, last_w)
        scene.goal = goal
        cmd = ex.update(scene, judg, t, DT)
        last_v, last_w = cmd.v_mps, cmd.w_deg_s
        syn.step(cmd.v_mps, cmd.w_deg_s, DT)
        d_goal = math.hypot(syn.rover.x - goal_xy[0], syn.rover.y - goal_xy[1])
        d_mat = dist_to_mat(syn.rover.x, syn.rover.y)
        min_goal, min_mat = min(min_goal, d_goal), min(min_mat, d_mat)
        trace.append((round(t, 2), round(syn.rover.x, 3), round(syn.rover.y, 3),
                      round(cmd.v_mps, 2), round(cmd.w_deg_s, 0), cmd.source,
                      round(d_goal, 3), round(d_mat, 3)))
        t += DT
        if d_goal < 0.05:
            break
    tail = [q for q in trace if q[0] >= trace[-1][0] - 6.0]
    still = sum(1 for q in tail if abs(q[3]) < 0.02 and abs(q[4]) < 5.0) * DT
    xs = [q[1] for q in tail]
    ys = [q[2] for q in tail]
    span = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    res = {"case": label, "jev": jev_mode, "goal": (round(goal_xy[0], 3), round(goal_xy[1], 3)),
           "min_goal_m": round(min_goal, 3), "latch_0.30": min_goal < 0.30,
           "arrival_0.10": min_goal < 0.10, "min_mat_gap_m": round(min_mat, 3),
           "still_last6s_s": round(still, 2), "frozen_last6s": span < 0.05,
           "end_source": trace[-1][5], "tail_sources": sorted({q[5] for q in tail}),
           "reflexes": dict(ex.stats["reflexes"]), "replans": ex.stats["replans"],
           "end_pose": (trace[-1][1], trace[-1][2]), "trace_5s": trace[::75][:9]}
    print(f"[{label}] goal={res['goal']} min_goal={res['min_goal_m']} "
          f"latch={int(res['latch_0.30'])} arr={int(res['arrival_0.10'])} "
          f"min_mat_gap={res['min_mat_gap_m']} still6={res['still_last6s_s']} "
          f"frozen={int(res['frozen_last6s'])} end={res['end_source']} "
          f"reflexes={res['reflexes'] or '{}'}")
    return res


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else None
    has_proj = hasattr(Executor(RoomConfig.load(os.path.join(REPO, "config", "room.synthetic.json")),
                                Perception(RoomConfig.load(os.path.join(REPO, "config", "room.synthetic.json"))).grid).planner,
                      "project_to_free")
    cfg = RoomConfig.load(os.path.join(REPO, "config", "room.synthetic.json"))
    syn = SyntheticRoom(cfg)
    syn.furniture = [("blue mat", MAT[0], MAT[1], MAT[2], MAT[3], (200, 80, 60))]
    syn.rover = Pose(START[0], START[1], 0.0)
    perc = Perception(cfg)
    for i in range(10):
        scene = perc.process(syn.render(), i * DT)
    g = perc.grid
    occ = g.occupied()
    d = np.hypot(g.cell_cx - MAT_C[0], g.cell_cy - MAT_C[1])
    blocked = g.inflated_occupied(cfg.grid.inflation_m)
    sem = SemanticMap(passes=1, model="fake-v0", objects=[
        SemanticObject(id="obj_0001", label="blue mat", x=MAT_C[0], y=MAT_C[1], confidence=0.92)])
    dest = resolve_destination("blue mat", sem, cfg=cfg.semantics)
    ax, ay = approach_point(dest, START, cfg.destination.standoff_m)
    proj = None
    if has_proj:
        ex = Executor(cfg, g)
        proj = ex.planner.project_to_free((ax, ay))
    print(f"# repo {REPO}; project_to_free available={has_proj}; standoff={cfg.destination.standoff_m}")
    print(f"grid: mat-area occupied cells {int((occ & (d <= 0.75)).sum())}; "
          f"blocked radius around dest {float(d[blocked & (d <= 0.95)].max()):.3f} m; "
          f"approach ({ax:.2f},{ay:.2f}) blocked="
          f"{bool(blocked[min(int(ay / g.cell_m), g.h - 1), min(int(ax / g.cell_m), g.w - 1)])}")
    print(f"projected goal: {None if proj is None else (round(proj[0], 3), round(proj[1], 3))}"
          + (f" = {math.hypot(proj[0]-ax, proj[1]-ay):.3f} m from approach; effective standoff "
             f"{math.hypot(proj[0]-MAT_C[0], proj[1]-MAT_C[1]):.3f} m" if proj else ""))
    results = [drive("A raw (jev)", (ax, ay), True), drive("A raw (base)", (ax, ay), False)]
    goal_b = proj if proj is not None else (ax, ay)
    results += [drive("B proj (jev)", goal_b, True), drive("B proj (base)", goal_b, False)]
    if out_path:
        with open(out_path, "w") as f:
            json.dump({"repo": REPO, "project_to_free": has_proj,
                       "approach": (ax, ay), "projected": proj, "results": results}, f, indent=2)
        print(f"# results -> {out_path}")


if __name__ == "__main__":
    main()
