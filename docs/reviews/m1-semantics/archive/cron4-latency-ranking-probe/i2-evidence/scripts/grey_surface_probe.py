#!/usr/bin/env python3
"""Supplementary: does the shadow split accept a NEUTRAL-GREY surface under the
base (which, if it were an elevated grey sideboard/step rather than a shadow,
would be a false negative)?  Painted with the floor's own chroma, L-50/L-30."""
import json
import sys
from pathlib import Path

import cv2
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
s = next(x for x in meta["scenarios"] if x["name"] == "floor_only")
frame = frames["floor_only"].copy()
bbox = tuple(float(v) for v in s["bbox_px"])
grid = np.zeros((int(3.6 / 0.05), int(6.4 / 0.05)), np.float32)
ctx = SemanticContext(homography=H, room_w_m=6.4, room_h_m=3.6, cell_m=0.05,
                      polygon_px=np.asarray(meta["polygon_px"], np.float32).reshape(-1, 2),
                      floor_lab=np.asarray(s["floor_lab"], float),
                      floor_lab_tolerance=meta["lab_tolerance"], grid_log_odds=grid,
                      grid_last_seen=np.ones_like(grid), grid_t=1.0,
                      occupied_thr=0.42, stale_s=3.0)
px = int(round((bbox[0] + bbox[2]) / 2.0))
py = int(round(bbox[3])) + int(meta["probe_px"])
floor_lab = np.asarray(s["floor_lab"], float)
print(f"floor LAB {floor_lab.tolist()}  probe pixel ({px},{py})")
for dl in (30.0, 50.0):
    lab = np.array([[floor_lab[0] - dl, floor_lab[1], floor_lab[2]]], np.float32)
    bgr = cv2.cvtColor(lab.reshape(1, 1, 3).astype(np.uint8), cv2.COLOR_LAB2BGR)[0, 0]
    f2 = frame.copy()
    f2[py - 25:py + 25, px - 40:px + 40] = bgr          # wide neutral-grey surface
    dec = semantics.height_suspect(f2, bbox, s["x"], s["y"], ctx, cfg.semantics.project)
    extra = ""
    if hasattr(semantics, "probe_surface"):
        extra = f" surface={semantics.probe_surface(f2, bbox, s['x'], s['y'], ctx, cfg.semantics.project)}"
    print(f"grey surface L-{dl:g} (BGR {bgr.tolist()}): height_suspect={dec}{extra}")
