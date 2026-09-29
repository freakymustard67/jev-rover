#!/usr/bin/env python3
"""i7 probe: blocked-region geometry per obj_r + projection landing points."""
import math
import os
import sys

import numpy as np

REPO = os.environ.get("REPO", "/home/freakymustard/.hermes/cache/scratch/i7/repo")
sys.path.insert(0, REPO)

from config import RoomConfig                      # noqa: E402
from control import Planner                        # noqa: E402
from perception import OccupancyGrid               # noqa: E402
from scene import Destination                      # noqa: E402
from semantics import approach_point               # noqa: E402

CELL = 0.05
CFG = RoomConfig()
OBJ = (2.0, 2.0)
START = (0.7, 2.0)


def make_grid(obj_r):
    grid = OccupancyGrid(4.0, 4.0, CELL)
    wall = np.zeros((grid.h, grid.w), bool)
    wall[[0, -1], :] = True
    wall[:, [0, -1]] = True
    grid.set_occupied(wall, 0.0)
    step = 0.01
    xs = np.arange(OBJ[0] - obj_r, OBJ[0] + obj_r + 1e-9, step)
    ys = np.arange(OBJ[1] - obj_r, OBJ[1] + obj_r + 1e-9, step)
    xx, yy = np.meshgrid(xs, ys)
    m = (xx - OBJ[0]) ** 2 + (yy - OBJ[1]) ** 2 <= obj_r ** 2
    grid.update(np.zeros((0, 2)), np.column_stack([xx[m], yy[m]]), 0.0)
    return grid


for obj_r in (0.15, 0.30, 0.40):
    grid = make_grid(obj_r)
    planner = Planner(grid, CFG.grid.inflation_m, CFG)
    blocked = planner.blocked()
    occ = grid.occupied()
    # blocked radius: max distance from object centre of a blocked cell
    d = np.hypot(grid.cell_cx - OBJ[0], grid.cell_cy - OBJ[1])
    rb = float(d[blocked & (d <= 0.95)].max())
    # occupied radius (raw footprint in grid)
    ro = float(d[occ & (d <= 0.95)].max()) if (occ & (d <= 0.95)).any() else 0.0
    dest = Destination(label="o", x=OBJ[0], y=OBJ[1], confidence=0.9)
    ax, ay = approach_point(dest, START, 0.35)
    cell = (min(int(ax / CELL), grid.w - 1), min(int(ay / CELL), grid.h - 1))
    ab = bool(blocked[cell[1], cell[0]])
    proj = planner.project_to_free((ax, ay)) if hasattr(planner, "project_to_free") else None
    print(f"r={obj_r}: occ_radius={ro:.3f} blocked_radius={rb:.3f} "
          f"approach=({ax:.2f},{ay:.2f}) cell={cell} blocked={ab} "
          f"proj={None if proj is None else (round(proj[0], 3), round(proj[1], 3))} "
          f"proj_dist_to_obj={None if proj is None else round(math.hypot(proj[0]-OBJ[0], proj[1]-OBJ[1]), 3)} "
          f"d(approach,obj)={math.hypot(ax-OBJ[0], ay-OBJ[1]):.3f}")
