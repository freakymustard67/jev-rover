#!/usr/bin/env python3
"""i7 E2E: approach-standoff fix (F1 projection + F2 no-splice) vs the w4b stall.

Deterministic, noise-free: stamps an obstacle disc (obj_r in {0.15, 0.30, 0.40})
into an OccupancyGrid the way the perception pipeline stamps occupied cells,
builds the scene from grid rays exactly like perception.process(), and drives
Executor.update() at 15 Hz with scene.goal = the value the M2 routing wiring
would set.

Cases per obj_r (start (0.7, 2.0) yaw 0, object at (2.0, 2.0)):
  raw@0.35      goal = raw approach point at cfg standoff 0.35 (pre-fix wiring)
  proj@0.35     goal = Planner.project_to_free(approach)   (F1 wiring; falls back
                to the raw point when the method does not exist -> unpatched tree)
  rule          goal = approach at cfg.required_standoff_m(obj_r)  (fallback r+0.35)
  rec           goal = approach at obj_r + 0.40            (w4b recommendation)
Modes: jev hold_course and no-Jev baseline.

Run from any cwd; REPO must point at the scratch clone:
  REPO=/home/freakymustard/.hermes/cache/scratch/i7/repo \
    PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python \
    i7-approach-e2e.py [out.json]
"""
from __future__ import annotations

import json
import math
import os
import sys

import numpy as np

REPO = os.environ["REPO"] if "REPO" in os.environ else "/home/freakymustard/.hermes/cache/scratch/i7/repo"
sys.path.insert(0, REPO)

from config import RoomConfig                       # noqa: E402
from control import Executor, Planner               # noqa: E402
from perception import OccupancyGrid                # noqa: E402
from scene import (Destination, GoalInfo, Hardware, PerceptionQuality, Pose,  # noqa: E402
                   TargetObs, Twist, build_scene, summarize_rays, wrap_deg)
from semantics import approach_point                # noqa: E402

CELL = 0.05
W_M, H_M = 4.0, 4.0
ANGLES = np.arange(-90.0, 90.01, 3.0)
CFG = RoomConfig()
BLOCKED_AT = CFG.rover.footprint_radius_m * 2.0 + 0.12
START_OFF = CFG.rover.footprint_radius_m * 1.05
DT = 1.0 / 15.0
OBJ = (2.0, 2.0)
START = (0.7, 2.0)
BUDGET_S = 45.0

HAS_PROJECT = hasattr(Planner, "project_to_free")


def make_grid(obj_r):
    grid = OccupancyGrid(W_M, H_M, CELL)
    wall = np.zeros((grid.h, grid.w), bool)
    wall[[0, -1], :] = True
    wall[:, [0, -1]] = True
    grid.set_occupied(wall, 0.0)
    if obj_r > 0:
        step = 0.01
        xs = np.arange(OBJ[0] - obj_r, OBJ[0] + obj_r + 1e-9, step)
        ys = np.arange(OBJ[1] - obj_r, OBJ[1] + obj_r + 1e-9, step)
        xx, yy = np.meshgrid(xs, ys)
        m = (xx - OBJ[0]) ** 2 + (yy - OBJ[1]) ** 2 <= obj_r ** 2
        grid.update(np.zeros((0, 2)), np.column_stack([xx[m], yy[m]]), 0.0)
    return grid


def blocked_radius(grid, probe_r=0.95):
    d = np.hypot(grid.cell_cx - OBJ[0], grid.cell_cy - OBJ[1])
    m = grid.inflated_occupied(CFG.grid.inflation_m) & (d <= probe_r)
    return float(d[m].max()) if m.any() else 0.0


def make_scene(grid, t, pose, v, w, goal):
    dists, limits = grid.ray_distances(
        pose.x, pose.y, pose.yaw_deg, ANGLES, t, CFG.grid.max_range_m,
        CFG.grid.stale_s, start_offset_m=START_OFF)
    rays = summarize_rays(ANGLES.tolist(), dists.tolist(), limits=limits,
                          free_threshold_m=BLOCKED_AT + 0.10, blocked_at_m=BLOCKED_AT)
    scene = build_scene(
        rays, t=round(t, 2), pose=Pose(round(pose.x, 4), round(pose.y, 4), round(pose.yaw_deg, 2)),
        twist=Twist(v, w), quality=PerceptionQuality(), tracks=[], nogo_hit=False,
        target=TargetObs(), hardware=Hardware())
    scene.goal = goal
    return scene


def run_case(obj_r, standoff, kind, jev_mode, label=""):
    grid = make_grid(obj_r)
    ex = Executor(CFG, grid)
    dest = Destination(label="object", x=OBJ[0], y=OBJ[1], confidence=0.9)
    ax, ay = approach_point(dest, START, standoff)
    projected = None
    if kind == "proj" and HAS_PROJECT:
        projected = ex.planner.project_to_free((ax, ay))
    gx, gy = projected if projected is not None else (ax, ay)
    goal = GoalInfo(type="goto", name="dest", x=gx, y=gy)

    blocked = ex.planner.blocked()
    ab = bool(blocked[min(int(ay / CELL), grid.h - 1), min(int(ax / CELL), grid.w - 1)])
    path_probe, _ = ex.planner.plan(START, (gx, gy), 0.0)
    rb = blocked_radius(grid)

    pose = Pose(START[0], START[1], 0.0)
    judg = ({"source": "jev", "age_s": 0.1, "maneuver": "hold_course", "risk": 0.2,
             "path_still_good": 1.0, "observation_unreliable": 0.0}
            if jev_mode else {"source": "none"})
    t, last_v, last_w = 0.0, 0.0, 0.0
    min_d_goal, min_d_center, min_d_ram = 1e9, 1e9, 1e9
    trace = []
    for _ in range(int(BUDGET_S / DT)):
        grid.last_seen[:, :] = t                      # room fully observed each frame
        scene = make_scene(grid, t, pose, last_v, last_w, goal)
        cmd = ex.update(scene, judg, t, DT)
        last_v, last_w = cmd.v_mps, cmd.w_deg_s
        pose.yaw_deg = wrap_deg(pose.yaw_deg + cmd.w_deg_s * DT)
        r = math.radians(pose.yaw_deg)
        pose.x += cmd.v_mps * math.cos(r) * DT
        pose.y += cmd.v_mps * math.sin(r) * DT
        d_goal = math.hypot(pose.x - gx, pose.y - gy)
        d_center = math.hypot(pose.x - OBJ[0], pose.y - OBJ[1])
        min_d_goal = min(min_d_goal, d_goal)
        min_d_center = min(min_d_center, d_center)
        min_d_ram = min(min_d_ram, d_center - obj_r)
        trace.append((round(t, 2), round(pose.x, 3), round(pose.y, 3), round(pose.yaw_deg, 1),
                      round(cmd.v_mps, 2), round(cmd.w_deg_s, 0), cmd.source,
                      round(d_goal, 3), round(d_center, 3)))
        t += DT
        if d_goal < 0.05:
            break

    tail = [q for q in trace if q[0] >= max(0.0, trace[-1][0] - 6.0)]
    still_s = sum(1 for q in tail if abs(q[4]) < 0.02 and abs(q[5]) < 5.0) * DT
    xs = [q[1] for q in tail]
    ys = [q[2] for q in tail]
    span = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    return {
        "case": label or f"r={obj_r} {kind} standoff={standoff} jev={jev_mode}",
        "obj_r": obj_r, "kind": kind, "standoff": standoff,
        "standoff_used": round(standoff, 3), "jev": jev_mode,
        "approach": (round(ax, 3), round(ay, 3)),
        "approach_cell_blocked": ab,
        "blocked_radius_m": round(rb, 3),
        "goal": (round(gx, 3), round(gy, 3)),
        "goal_projected": projected is not None,
        "goal_cell_free": (not blocked[min(int(gy / CELL), grid.h - 1),
                                        min(int(gx / CELL), grid.w - 1)]),
        "plan_found": bool(path_probe),
        "plan_end": None if not path_probe else tuple(round(v, 3) for v in path_probe[-1]),
        "min_dist_goal_m": round(min_d_goal, 3),
        "latch_0.30": min_d_goal < 0.30,
        "arrival_0.10": min_d_goal < 0.10,
        "min_dist_center_m": round(min_d_center, 3),
        "min_surface_gap_m": round(min_d_ram, 3),
        "end_pose": (round(pose.x, 3), round(pose.y, 3)),
        "end_source": trace[-1][6],
        "tail_sources": sorted({q[6] for q in tail}),
        "still_last6s_s": round(still_s, 2),
        "frozen_last6s": span < 0.05,          # parked OR chatter: position frozen
        "tail_span_m": round(span, 3),
        "reflexes": dict(ex.stats["reflexes"]),
        "replans": ex.stats["replans"],
        "trace_5s": trace[::75][:10],
    }


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else None
    if not HAS_PROJECT:
        print("# NOTE: Planner.project_to_free absent (unpatched tree); "
              "'proj' cases fall back to the raw approach point")
    print(f"# repo {REPO}; obj {OBJ} r in (0.15, 0.30, 0.40); start {START}; "
          f"cfg standoff {CFG.destination.standoff_m}")
    results = []
    for obj_r in (0.15, 0.30, 0.40):
        rule = CFG.required_standoff_m(obj_r) if hasattr(CFG, "required_standoff_m") \
            else math.ceil((obj_r + 0.35) / 0.05 - 1e-9) * 0.05
        rec = math.ceil((obj_r + 0.40) / 0.05 - 1e-9) * 0.05
        for kind, standoff in (("raw", CFG.destination.standoff_m),
                               ("proj", CFG.destination.standoff_m),
                               ("rule", rule), ("rec", rec)):
            for jev in (True, False):
                r = run_case(obj_r, standoff, kind, jev)
                results.append(r)
                print(f"[{('jev ' if jev else 'base')} r={obj_r:.2f} {kind:4s} "
                      f"so={standoff:.2f}] approach_blk={int(r['approach_cell_blocked'])} "
                      f"goal={r['goal']} proj={int(r['goal_projected'])} "
                      f"plan_end={r['plan_end']} min_goal={r['min_dist_goal_m']:.3f} "
                      f"min_gap={r['min_surface_gap_m']:.3f} "
                      f"latch={int(r['latch_0.30'])} arr={int(r['arrival_0.10'])} "
                      f"still6={r['still_last6s_s']:.1f} frozen={int(r['frozen_last6s'])} "
                      f"src={r['end_source']} reflexes={r['reflexes'] or '{}'}")
    if out_path:
        with open(out_path, "w") as f:
            json.dump({"repo": REPO, "has_project_to_free": HAS_PROJECT,
                       "results": results}, f, indent=2)
        print(f"# results -> {out_path}")


if __name__ == "__main__":
    main()
