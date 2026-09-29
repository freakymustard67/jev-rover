#!/usr/bin/env python3
"""w4b E2E: destination object through the REAL perception pipeline.

Does a mat-sized destination object enter the occupancy grid, does the
approach point end up inside the planner's inflated region, and does the
Executor then stall trying to reach it? Uses SyntheticRoom + Perception
(the same code path as the real camera, tests/test_perception_synthetic.py
preamble), then drives Executor.update at 15 Hz.

Cases:
  A. baseline executor (no Jev), goal = approach point (2.65, 1.2)
  B. Jev hold_course, same goal
  C. FIX probe: goal projected to the nearest free cell (blocked map), Jev mode

Run from the scratch clone root:
  PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python \
      ../w4b-approach-pipeline.py
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np

REPO = os.environ.get("REPO", os.path.join(os.path.dirname(os.path.abspath(__file__)), "repo"))
sys.path.insert(0, REPO)

from config import RoomConfig                                    # noqa: E402
from control import Executor                                     # noqa: E402
from perception import Perception                                # noqa: E402
from scene import (GoalInfo, Pose, SemanticMap, SemanticObject,  # noqa: E402
                   wrap_deg)
from semantics import approach_point, resolve_destination        # noqa: E402
from synthetic import SyntheticRoom                              # noqa: E402

MAT = (2.65, 0.90, 3.35, 1.50)          # x0, y0, x1, y1  (0.7 x 0.6 m "blue mat")
MAT_C = ((MAT[0] + MAT[2]) / 2.0, (MAT[1] + MAT[3]) / 2.0)       # (3.0, 1.2)
START = (1.4, 1.2)
DT = 1.0 / 15.0


def dist_to_mat(x, y):
    """Euclidean distance from (x, y) to the mat rectangle."""
    dx = max(MAT[0] - x, 0.0, x - MAT[2])
    dy = max(MAT[1] - y, 0.0, y - MAT[3])
    return math.hypot(dx, dy)


def drive(label, goal_xy, jev_mode=False, budget_s=40.0):
    cfg = RoomConfig.load(os.path.join(REPO, "config", "room.synthetic.json"))
    syn = SyntheticRoom(cfg)
    syn.furniture = [("blue mat", MAT[0], MAT[1], MAT[2], MAT[3], (200, 80, 60))]
    syn.rover = Pose(START[0], START[1], 0.0)
    perc = Perception(cfg)

    scene = None
    for i in range(10):                                  # warm: floor sample + grid
        scene = perc.process(syn.render(), i * DT)

    ex = Executor(cfg, perc.grid)
    goal = GoalInfo(type="goto", name="dest", x=goal_xy[0], y=goal_xy[1])
    judg = ({"source": "jev", "age_s": 0.1, "maneuver": "hold_course", "risk": 0.2,
             "path_still_good": 1.0, "observation_unreliable": 0.0}
            if jev_mode else {"source": "none"})

    t, last_v, last_w = 10 * DT, 0.0, 0.0
    min_goal, min_mat, last_move = 1e9, 1e9, t
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
        if abs(cmd.v_mps) > 0.02 or abs(cmd.w_deg_s) > 5.0:
            last_move = t
        trace.append((round(t, 2), round(syn.rover.x, 2), round(syn.rover.y, 2),
                      round(syn.rover.yaw_deg, 0), round(cmd.v_mps, 2),
                      round(cmd.w_deg_s, 0), cmd.source, round(d_goal, 2),
                      round(d_mat, 2), scene.nearest_m))
        t += DT
        if d_goal < 0.05:
            break
    tail = [r for r in trace if r[0] >= trace[-1][0] - 6.0]
    still = sum(1 for r in tail if abs(r[4]) < 0.02 and abs(r[5]) < 5.0) * DT
    print(f"\n[{label}] goal={goal_xy} jev={jev_mode}")
    print(f"  min dist to goal = {min_goal:.3f} m (goal_tol 0.30 -> "
          f"{'REACHED' if min_goal < 0.30 else 'NOT reached'})")
    print(f"  min dist rover->mat surface = {min_mat:.3f} m; "
          f"end still-6s = {still:.2f}s; end src = {trace[-1][6]}; "
          f"reflexes={dict(ex.stats['reflexes'])}")
    print("  trace every 2 s (t, x, y, yaw, v, w, src, d_goal, d_mat, nearest):")
    for r in trace[::30]:
        print("   ", r)
    return {"label": label, "min_goal": round(min_goal, 3), "min_mat": round(min_mat, 3),
            "still6": round(still, 2), "end_src": trace[-1][6],
            "reflexes": dict(ex.stats["reflexes"]),
            "trace_2s": trace[::30], "trace_tail": trace[-4:]}


def main():
    cfg = RoomConfig.load(os.path.join(REPO, "config", "room.synthetic.json"))
    syn = SyntheticRoom(cfg)
    syn.furniture = [("blue mat", MAT[0], MAT[1], MAT[2], MAT[3], (200, 80, 60))]
    syn.rover = Pose(START[0], START[1], 0.0)
    perc = Perception(cfg)
    for i in range(10):
        scene = perc.process(syn.render(), i * DT)

    # --- 1. does the mat enter the occupancy grid? --------------------------
    g = perc.grid
    occ = g.occupied()
    masked = np.zeros_like(occ)
    for iy in range(g.h):
        for ix in range(g.w):
            cx, cy = (ix + 0.5) * g.cell_m, (iy + 0.5) * g.cell_m
            if MAT[0] - 0.1 <= cx <= MAT[2] + 0.1 and MAT[1] - 0.1 <= cy <= MAT[3] + 0.1:
                masked[iy, ix] = True
    n_occ_mat = int((occ & masked).sum())
    n_cells_mat = int(masked.sum())
    print(f"grid: mat-adjacent cells occupied {n_occ_mat}/{n_cells_mat}; "
          f"total occupied {int(occ.sum())}")
    blocked = g.inflated_occupied(cfg.grid.inflation_m)
    # blocked radius around the destination point (centroid), probe 0.95 m
    d = np.hypot(g.cell_cx - MAT_C[0], g.cell_cy - MAT_C[1])
    m = blocked & (d <= 0.95)
    print(f"planner-blocked radius around dest {MAT_C} = {float(d[m].max()):.3f} m "
          f"(inflation {cfg.grid.inflation_m})")

    # --- 2. semantics chain: resolve -> approach point ----------------------
    sem = SemanticMap(passes=1, model="fake-v0", objects=[
        SemanticObject(id="obj_0001", label="blue mat", x=MAT_C[0], y=MAT_C[1], confidence=0.92)])
    dest = resolve_destination("blue mat", sem, cfg=cfg.semantics)
    ax, ay = approach_point(dest, START, cfg.destination.standoff_m)
    ix, iy = int(ax / g.cell_m), int(ay / g.cell_m)
    print(f"resolve -> {dest.label} at ({dest.x},{dest.y}); approach ({ax:.2f},{ay:.2f}) "
          f"standoff={cfg.destination.standoff_m}; approach cell blocked={bool(blocked[iy, ix])}")
    path, through = Executor(cfg, g).planner.plan(START, (ax, ay), 10 * DT)
    print(f"Planner.plan -> found={bool(path)} through_unknown={through} "
          f"end={None if not path else tuple(round(v, 2) for v in path[-1])}")

    results = [drive("A baseline", (ax, ay), jev_mode=False),
               drive("B jev hold_course", (ax, ay), jev_mode=True)]

    # --- 3. FIX probe: project the goal to the nearest free cell ------------
    bx, by = blocked, (ax, ay)
    gx0, gy0 = int(ax / g.cell_m), int(ay / g.cell_m)
    proj = None
    for r in range(0, 40):                       # up to 2.0 m of search
        found = None
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if max(abs(dx), abs(dy)) != r:
                    continue
                nx, ny = gx0 + dx, gy0 + dy
                if 0 <= nx < g.w and 0 <= ny < g.h and not bx[ny, nx]:
                    found = (nx, ny)
                    break
            if found:
                break
        if found:
            proj = ((found[0] + 0.5) * g.cell_m, (found[1] + 0.5) * g.cell_m)
            break
    print(f"\nFIX probe: nearest free cell to approach point = "
          f"({proj[0]:.2f},{proj[1]:.2f}) = {math.hypot(proj[0]-ax, proj[1]-ay):.3f} m away; "
          f"effective standoff to dest = {math.hypot(proj[0]-MAT_C[0], proj[1]-MAT_C[1]):.2f} m"
          if proj else "FIX probe: no free cell found")
    if proj:
        results.append(drive("C fix: projected free goal (jev)", proj, jev_mode=True))

    import json
    dst = os.path.join(os.path.dirname(os.path.abspath(__file__)), "w4b-pipeline-results.json")
    with open(dst, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n# results -> {dst}")


if __name__ == "__main__":
    main()