#!/usr/bin/env python3
"""w4b: Can routing to an object's approach point stall?

Tests proposal section 7 item 4 (docs/planning/semantics-layer-proposal.md:171):
destination = object (x,y) with configured standoff (destination.standoff_m,
e.g. 0.35 m; "obstacle inflation already exists in the planner").

Method (real code, PR tip bec1d91):
  * build an OccupancyGrid, stamp an obstacle disc (object radius 0.30 / 0.40 m)
    the way the perception pipeline stamps not-floor pixels;
  * compute the approach point via semantics.approach_point();
  * probe Planner.plan() toward it;
  * drive Executor.update() in a 15 Hz loop (scene rebuilt from the grid
    exactly like perception.process() does: ray_distances -> summarize_rays ->
    build_scene) with scene.goal = approach point (the wiring m2-design.md:537-540
    proposes; --find itself only resolves+prints on this tip, run.py:247).

Sweep: standoff {0.35, 0.60, 0.70, 0.80} x obj radius {0.30, 0.40}, object
present vs absent from the grid, plus a diagonal approach and a Jev-mode run.

Run from the scratch clone root:
    PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python \
        ../w4b-approach-sim.py
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np

REPO = os.environ.get("REPO", os.path.join(os.path.dirname(os.path.abspath(__file__)), "repo"))
sys.path.insert(0, REPO)

from config import RoomConfig                      # noqa: E402
from control import Executor, Planner              # noqa: E402
from perception import OccupancyGrid               # noqa: E402
from scene import (Destination, GoalInfo, Hardware, PerceptionQuality, Pose,  # noqa: E402
                   TargetObs, Twist, build_scene, summarize_rays, wrap_deg)
from semantics import approach_point               # noqa: E402

CELL = 0.05
W_M, H_M = 4.0, 4.0
ANGLES = np.arange(-90.0, 90.01, 3.0)
CFG = RoomConfig()                                 # defaults; w/h unused by control
BLOCKED_AT = CFG.rover.footprint_radius_m * 2.0 + 0.12      # 0.48 (perception.py:781)
START_OFF = CFG.rover.footprint_radius_m * 1.05             # 0.189 (perception.py:776)
DT = 1.0 / 15.0


# ------------------------------------------------------------------ helpers
def make_grid(obj_xy=None, obj_r=0.0):
    """OccupancyGrid with a wall ring; optionally a disc obstacle stamped the
    way camera pixels would be (dense world points -> occupied cells)."""
    grid = OccupancyGrid(W_M, H_M, CELL)
    wall = np.zeros((grid.h, grid.w), bool)
    wall[[0, -1], :] = True
    wall[:, [0, -1]] = True
    grid.set_occupied(wall, 0.0)
    if obj_xy is not None and obj_r > 0:
        step = 0.01
        xs = np.arange(obj_xy[0] - obj_r, obj_xy[0] + obj_r + 1e-9, step)
        ys = np.arange(obj_xy[1] - obj_r, obj_xy[1] + obj_r + 1e-9, step)
        xx, yy = np.meshgrid(xs, ys)
        m = (xx - obj_xy[0]) ** 2 + (yy - obj_xy[1]) ** 2 <= obj_r ** 2
        pts = np.column_stack([xx[m], yy[m]])
        grid.update(np.zeros((0, 2)), pts, 0.0)
    return grid


def free_near(blocked, ix, iy, g, max_r=6):
    """Exact copy of Planner.plan's inner free_near (control.py:70-81)."""
    if not blocked[iy, ix]:
        return ix, iy
    for r in range(1, max_r + 1):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if max(abs(dx), abs(dy)) != r:
                    continue
                nx, ny = ix + dx, iy + dy
                if 0 <= nx < g.w and 0 <= ny < g.h and not blocked[ny, nx]:
                    return nx, ny
    return None


def blocked_radius(grid, obj_xy, infl=0.22, probe_r=0.95):
    """Max distance from the object centre at which a cell is planner-blocked."""
    blocked = grid.inflated_occupied(infl)
    cx, cy = grid.cell_cx, grid.cell_cy
    d = np.hypot(cx - obj_xy[0], cy - obj_xy[1])
    m = blocked & (d <= probe_r)
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


def run_case(obj_r, standoff, obj_xy=(2.0, 2.0), start=(0.7, 2.0), start_yaw=0.0,
             obj_in_grid=True, jev_mode=False, budget_s=45.0, label=""):
    grid = make_grid(obj_xy if obj_in_grid else None, obj_r if obj_in_grid else 0.0)
    ex = Executor(CFG, grid)
    dest = Destination(label="object", x=obj_xy[0], y=obj_xy[1], confidence=0.9)
    ax, ay = approach_point(dest, (start[0], start[1]), standoff)
    goal = GoalInfo(type="goto", name="dest", x=ax, y=ay)

    # --- geometry probe -------------------------------------------------
    blocked = ex.planner.blocked()
    ix = min(max(int(ax / CELL), 0), grid.w - 1)
    iy = min(max(int(ay / CELL), 0), grid.h - 1)
    approach_blocked = bool(blocked[iy, ix])
    fn = free_near(blocked, ix, iy, grid)
    path_probe, through = ex.planner.plan((start[0], start[1]), (ax, ay), 0.0)
    rb = blocked_radius(grid, obj_xy)

    # --- drive loop -----------------------------------------------------
    pose = Pose(start[0], start[1], start_yaw)
    judg = ({"source": "jev", "age_s": 0.1, "maneuver": "hold_course", "risk": 0.2,
             "path_still_good": 1.0, "observation_unreliable": 0.0}
            if jev_mode else {"source": "none"})
    t = 0.0
    last_v, last_w = 0.0, 0.0               # achieved velocity = previous command
    min_d_goal = 1e9
    min_d_center = 1e9
    last_motion_t = 0.0
    trace = []
    n = int(budget_s / DT)
    for _ in range(n):
        grid.last_seen[:, :] = t            # room fully observed each frame
        scene = make_scene(grid, t, pose, last_v, last_w, goal)
        cmd = ex.update(scene, judg, t, DT)
        last_v, last_w = cmd.v_mps, cmd.w_deg_s
        pose.yaw_deg = wrap_deg(pose.yaw_deg + cmd.w_deg_s * DT)
        r = math.radians(pose.yaw_deg)
        pose.x += cmd.v_mps * math.cos(r) * DT
        pose.y += cmd.v_mps * math.sin(r) * DT
        d_goal = math.hypot(pose.x - ax, pose.y - ay)
        d_center = math.hypot(pose.x - obj_xy[0], pose.y - obj_xy[1])
        min_d_goal = min(min_d_goal, d_goal)
        min_d_center = min(min_d_center, d_center)
        if abs(cmd.v_mps) > 0.02 or abs(cmd.w_deg_s) > 5.0:
            last_motion_t = t
        trace.append((round(t, 2), round(pose.x, 2), round(pose.y, 2), round(pose.yaw_deg, 1),
                      round(cmd.v_mps, 2), round(cmd.w_deg_s, 0), cmd.source,
                      round(d_goal, 2), scene.nearest_m, ex.no_progress_s))
        t += DT
        if d_goal < 0.05:                    # parked on the point (out-grid cases can)
            break
    tail = [r for r in trace if r[0] >= max(0.0, trace[-1][0] - 6.0)]
    still_s = sum(1 for r in tail if abs(r[4]) < 0.02 and abs(r[5]) < 5.0) * DT
    result = {
        "label": label or f"r={obj_r} standoff={standoff} in_grid={obj_in_grid}",
        "obj_r": obj_r, "standoff": standoff, "obj_in_grid": obj_in_grid,
        "approach": (round(ax, 2), round(ay, 2)),
        "approach_cell_blocked": approach_blocked,
        "blocked_radius_m": round(rb, 3),
        "free_near_goal_cell": None if fn is None else (fn[0] * CELL + CELL / 2, fn[1] * CELL + CELL / 2),
        "plan_found": bool(path_probe),
        "plan_end": None if not path_probe else tuple(round(v, 2) for v in path_probe[-1]),
        "min_dist_goal_m": round(min_d_goal, 3),
        "min_dist_center_m": round(min_d_center, 3),
        "min_surface_gap_m": round(min_d_center - obj_r, 3),
        "reached_0.10": min_d_goal < 0.10, "reached_0.30": min_d_goal < 0.30,
        "end_pose": (round(pose.x, 2), round(pose.y, 2)),
        "end_source": trace[-1][6], "end_nearest_m": trace[-1][8],
        "still_last6s_s": round(still_s, 2),
        "tail_sources": sorted({r[6] for r in tail}),
        "reflexes": dict(ex.stats["reflexes"]), "modes": dict(ex.stats["modes"]),
        "replans": ex.stats["replans"],
        "trace_head": trace[:3], "trace_tail": trace[-3:],
        "trace_3hz": trace[::5],
    }
    return result


def main():
    print(f"# repo: {REPO}")
    out = []
    print("\n== planner geometry probe (no driving) ==")
    for obj_r in (0.30, 0.40):
        grid = make_grid((2.0, 2.0), obj_r)
        rb = blocked_radius(grid, (2.0, 2.0))
        ax, ay = approach_point(Destination("o", 2.0, 2.0, 0.9), (0.7, 2.0), 0.35)
        ix, iy = int(ax / CELL), int(ay / CELL)
        blocked = grid.inflated_occupied(0.22)
        print(f"obj_r={obj_r:.2f}: blocked_radius={rb:.3f} m; approach (0.35) cell "
              f"blocked={bool(blocked[iy, ix])}; free_near-> {free_near(blocked, ix, iy, grid)}")

    print("\n== sweep: standoff x object radius, object in grid ==")
    for obj_r in (0.30, 0.40):
        for standoff in (0.35, 0.60, 0.70, 0.80):
            r = run_case(obj_r, standoff)
            out.append(r)
            print(f"[IN ] r={obj_r} standoff={standoff}: blocked={r['approach_cell_blocked']} "
                  f"plan={r['plan_found']} plan_end={r['plan_end']} "
                  f"min_goal={r['min_dist_goal_m']} reach0.30={r['reached_0.30']} "
                  f"still6s={r['still_last6s_s']}s tail={r['tail_sources']} "
                  f"reflexes={r['reflexes']} replans={r['replans']}")

    print("\n== object NOT in grid (flat/ignored objects) ==")
    for obj_r in (0.30, 0.40):
        r = run_case(obj_r, 0.35, obj_in_grid=False)
        out.append(r)
        print(f"[OUT] r={obj_r} standoff=0.35: min_goal={r['min_dist_goal_m']} "
              f"reach0.30={r['reached_0.30']} end_source={r['end_source']}")

    print("\n== diagonal approach (r=0.40, standoff=0.35) ==")
    r = run_case(0.40, 0.35, start=(1.0, 1.0), start_yaw=45.0)
    out.append(r)
    print(f"[DIAG] min_goal={r['min_dist_goal_m']} blocked={r['approach_cell_blocked']} "
          f"free_near={r['free_near_goal_cell']} reflexes={r['reflexes']}")

    print("\n== Jev mode hold_course (r=0.40, standoff=0.35) ==")
    r = run_case(0.40, 0.35, jev_mode=True)
    out.append(r)
    print(f"[JEV] min_goal={r['min_dist_goal_m']} still6s={r['still_last6s_s']} "
          f"end_source={r['end_source']} reflexes={r['reflexes']} modes={r['modes']}")

    # dump full results as JSON next to this script
    import json
    dst = os.path.join(os.path.dirname(os.path.abspath(__file__)), "w4b-approach-results.json")
    with open(dst, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n# results -> {dst}")


if __name__ == "__main__":
    main()