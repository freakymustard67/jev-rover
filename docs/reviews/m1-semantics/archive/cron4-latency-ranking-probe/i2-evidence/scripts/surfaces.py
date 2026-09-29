#!/usr/bin/env python3
"""Print probe_surface() per scenario for the prototype clone (evidence detail)."""
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

CLONE = Path(sys.argv[1]).resolve()
DUMP = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(CLONE))

import semantics  # noqa: E402
from config import RoomConfig  # noqa: E402
from perception import Homography, SemanticContext  # noqa: E402

meta = json.loads((DUMP / "scenarios.json").read_text())
frames = np.load(DUMP / "frames.npz")
cfg = RoomConfig.load(CLONE / "config" / "room.synthetic.json")
H = Homography(meta["image_points_px"], meta["world_points_m"])
poly = np.asarray(meta["polygon_px"], np.float32).reshape(-1, 2)
covers = [[float(v) for v in meta["mat_lab"]]]
if os.environ.get("I2_COVERS", "both") == "both":
    covers.append([float(v) for v in meta["dark_mat_lab"]])
proj = replace(cfg.semantics.project, probe_m=0.03, cover_lab=covers, self_match=False)

for s in meta["scenarios"]:
    frame = frames[s["name"]]
    grid = np.zeros((int(3.6 / 0.05), int(6.4 / 0.05)), np.float32)
    ctx = SemanticContext(homography=H, room_w_m=6.4, room_h_m=3.6, cell_m=0.05,
                          polygon_px=poly, floor_lab=np.asarray(s["floor_lab"], float),
                          floor_lab_tolerance=meta["lab_tolerance"], grid_log_odds=grid,
                          grid_last_seen=np.ones_like(grid), grid_t=1.0,
                          occupied_thr=0.42, stale_s=3.0)
    bbox = tuple(float(v) for v in s["bbox_px"])
    got = semantics.probe_surface(frame, bbox, s["x"], s["y"], ctx, proj)
    print(f"{s['name']:22s} surface={got:16s} expect={s['expect']}")
