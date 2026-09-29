#!/usr/bin/env python3
"""I2 probe hardening -- build scenario frames + ground truth dump (rule-agnostic).

Run once (against any clone; config/perception/synthetic are identical across
master/PR/prototype):

    cd <clone> && PYTHONDONTWRITEBYTECODE=1 <venv>/python <this>/make_scenarios.py <clone> <dumpdir>

Writes dump/scenarios.json + dump/frames.npz.  Frames are CLEAN (noise=0) so the
scenario table measures the RULE; sensor-noise sensitivity is measured
separately by eval_rules.py (re-rendered with noise, identical in every clone).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

CLONE = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(CLONE))

from config import RoomConfig  # noqa: E402
from perception import Homography  # noqa: E402
from scene import Pose  # noqa: E402
from semantics import FakeVision, WorldFixture  # noqa: E402
from synthetic import SyntheticRoom  # noqa: E402

cfg = RoomConfig.load(CLONE / "config" / "room.synthetic.json")
H = Homography(cfg.homography.image_points_px, cfg.homography.world_points_m)
syn = SyntheticRoom(cfg, seed=7, noise=0.0)
syn.rover = Pose(x=0.6, y=0.6, yaw_deg=0.0)
BASE = syn.render()                                   # clean room, rover out of the way

MAT_BGR = (180, 90, 40)                               # blue mat, from w2a/demo_2.py
MAT_X = (2.6, 3.4)
MAT_Y = (0.8, 1.6)


def lab_of(bgr) -> np.ndarray:
    px = np.uint8([[[int(bgr[0]), int(bgr[1]), int(bgr[2])]]])
    return cv2.cvtColor(px, cv2.COLOR_BGR2LAB)[0, 0].astype(float)


def w2i(x, y):
    return tuple(int(round(v)) for v in H.world_to_img(np.array([[float(x), float(y)]]))[0])


def draw_rect_world(frame, x0, y0, x1, y1, color):
    a = w2i(x0, y1)
    b = w2i(x1, y0)
    cv2.rectangle(frame, a, b, color, -1)


def draw_rect_world_darken(frame, x0, y0, x1, y1, dl):
    """Darken the floor by dl in LAB L, chroma (a,b) preserved -> a shadow."""
    a = w2i(x0, y1)
    b = w2i(x1, y0)
    xa, xb = min(a[0], b[0]), max(a[0], b[0])
    ya, yb = min(a[1], b[1]), max(a[1], b[1])
    roi = frame[ya:yb, xa:xb]
    lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab[..., 0] = np.clip(lab[..., 0] - dl, 0, 255)
    frame[ya:yb, xa:xb] = cv2.cvtColor(lab.astype(np.uint8), cv2.COLOR_LAB2BGR)


def bbox_of(label, x, y, w, h, score=0.9):
    det = FakeVision.from_world([WorldFixture(label, x, y, w, h, score)], H).fixtures[0]
    return det


SCEN = []


def add(name, label, x, y, w, h, expect, cls, frame, note):
    det = bbox_of(label, x, y, w, h)
    SCEN.append({
        "name": name, "label": label, "expect": bool(expect), "cls": cls,
        "bbox_px": [float(v) for v in det.bbox_px], "score": det.score,
        "x": x, "y": y, "w": w, "h": h, "note": note,
    })
    return frame


frames = {}

# ---------------------------------------------------------------- scenarios
# corpus truth: expect False = floor-plane contact IS trustworthy (any FP is a false positive);
# expect True = genuinely elevated/occluded or unverifiable -> suspect is correct.

f = BASE.copy()
add("floor_only", "red box", 5.0, 1.0, 0.3, 0.3, False, "floor", f,
    "control: object on bare floor")
frames["floor_only"] = f

f = BASE.copy()
draw_rect_world(f, MAT_X[0], MAT_Y[0], MAT_X[1], MAT_Y[1], MAT_BGR)
add("mat_thing", "red box", 3.0, 1.2, 0.3, 0.3, False, "floor", f,
    "object resting on a FLAT non-floor-coloured mat (headline FP class)")
frames["mat_thing"] = f

f = BASE.copy()
draw_rect_world(f, MAT_X[0], MAT_Y[0], MAT_X[1], MAT_Y[1], MAT_BGR)
add("mat_itself", "blue mat", 3.0, 1.2, 0.5, 0.6, False, "floor", f,
    "detection of the mat itself (centroid anchor); probe lands on the flat cover")
frames["mat_itself"] = f

f = BASE.copy()
draw_rect_world(f, MAT_X[0], 0.95, MAT_X[1], 1.60, MAT_BGR)       # mat bottom 0.95 m
add("mat_edge_floor", "red box", 3.0, 1.10, 0.3, 0.3, False, "floor", f,
    "mat is behind the base: probe lands on bare floor (control)")
frames["mat_edge_floor"] = f

for nm, dl in (("shadow_mild", 20.0), ("shadow_deep", 40.0), ("shadow_vdeep", 70.0)):
    f = BASE.copy()
    draw_rect_world_darken(f, 4.7, 0.72, 5.3, 1.15, dl)
    add(nm, "red box", 5.0, 1.0, 0.3, 0.3, False, "floor", f,
        f"floor shadow (L-{dl:g}, chroma preserved) under/around the base")
    frames[nm] = f

f = BASE.copy()
draw_rect_world_darken(f, 4.8, 0.72, 5.2, 0.87, 45.0)             # contact shadow band
add("shadow_contact", "red box", 5.0, 1.0, 0.3, 0.3, False, "floor", f,
    "deep contact shadow covering the probe row (wider than the probe ring)")
frames["shadow_contact"] = f

f = BASE.copy()
draw_rect_world_darken(f, 4.95, 0.72, 5.05, 0.87, 45.0)           # narrow shadow band
add("shadow_contact_narrow", "red box", 5.0, 1.0, 0.3, 0.3, False, "floor", f,
    "deep but 20 px-wide contact shadow: sides of the probe row stay bright")
frames["shadow_contact_narrow"] = f

f = BASE.copy()
draw_rect_world(f, 4.8, 0.70, 5.2, 0.85, (60, 60, 60))            # dark neutral card, wide
add("dark_card_wide", "red box", 5.0, 1.0, 0.3, 0.3, False, "floor", f,
    "wide flat dark neutral card in front of the base (shadow/card ambiguity)")
frames["dark_card_wide"] = f

f = BASE.copy()
draw_rect_world(f, 4.96, 0.70, 5.04, 0.85, (60, 60, 60))          # 16 px-wide dark bar
add("occluder_narrow", "red box", 5.0, 1.0, 0.3, 0.3, False, "floor", f,
    "narrow (16 px) dark object in front of the base, ring stays floor")
frames["occluder_narrow"] = f

DARK_MAT_BGR = (105, 105, 105)
f = BASE.copy()
draw_rect_world(f, 4.4, 1.2, 5.2, 2.0, DARK_MAT_BGR)             # dark grey flat mat
add("dark_mat_thing", "red box", 4.8, 1.6, 0.3, 0.3, False, "floor", f,
    "object resting on a flat DARK neutral mat (registered as a cover)")
frames["dark_mat_thing"] = f

f = BASE.copy()
draw_rect_world(f, 4.7, 0.80, 5.3, 0.87, (40, 40, 45))            # speckle band under base
rng = np.random.default_rng(3)
xa, ya = w2i(4.7, 0.87)
xb, yb = w2i(5.3, 0.80)
patch = f[ya:yb, xa:xb]
mask = rng.random(patch.shape[:2]) < 0.45
patch[mask] = (230, 230, 230)
add("tight_bbox_speckle", "red box", 5.0, 1.0, 0.3, 0.3, False, "floor", f,
    "detector bbox 3 px tight: speckled dark object edge sits on the probe row")
frames["tight_bbox_speckle"] = f

f = BASE.copy()
add("on_table", "thing", 3.13, 0.47, 0.5, 0.5, True, "elevated", f,
    "object on the synthetic-room table region (genuine suspect)")
frames["on_table"] = f

f = BASE.copy()
add("edge_polygon", "thing", 0.6, 0.32, 0.3, 0.3, True, "unverifiable", f,
    "probe falls outside the floor polygon (py>690): cannot verify")
frames["edge_polygon"] = f

# ------------------------------------------------------------------- dump
meta = {
    "clone": str(CLONE),
    "camera": {"width": cfg.camera.width, "height": cfg.camera.height},
    "image_points_px": cfg.homography.image_points_px,
    "world_points_m": cfg.homography.world_points_m,
    "polygon_px": np.asarray(cfg.floor.polygon_px, float).tolist(),
    "lab_tolerance": cfg.floor.lab_tolerance,
    "probe_px": cfg.semantics.project.probe_px,
    "point_by_label": cfg.semantics.project.point_by_label,
    "room": {"width_m": cfg.width_m, "height_m": cfg.height_m},
    "px_per_m": cfg.width_m and (cfg.camera.width / cfg.width_m),
    "mat_bgr": list(MAT_BGR),
    "mat_lab": lab_of(MAT_BGR).tolist(),
    "dark_mat_bgr": list(DARK_MAT_BGR),
    "dark_mat_lab": lab_of(DARK_MAT_BGR).tolist(),
    "floor_bgr": [168, 162, 150],
    "floor_lab_nominal": lab_of((168, 162, 150)).tolist(),
    "scenarios": SCEN,
}

# per-scenario floor_lab exactly as FloorModel.classify computes it (median LAB inside polygon)
poly = np.zeros(BASE.shape[:2], np.uint8)
cv2.fillPoly(poly, [np.asarray(cfg.floor.polygon_px, np.int32)], 255)
for s in SCEN:
    fr = frames[s["name"]]
    lab = cv2.cvtColor(fr, cv2.COLOR_BGR2LAB).astype(np.float32)
    s["floor_lab"] = np.median(lab[poly > 0].reshape(-1, 3), axis=0).astype(float).tolist()

np.savez_compressed(OUT / "frames.npz", **{k: v for k, v in frames.items()})
(OUT / "scenarios.json").write_text(json.dumps(meta, indent=1))
print(f"wrote {OUT/'frames.npz'} ({len(frames)} frames) + scenarios.json")
print("floor_lab nominal:", np.round(meta["floor_lab_nominal"], 1).tolist(),
      " mat_lab:", np.round(meta["mat_lab"], 1).tolist())
for s in SCEN:
    bb = [round(v, 1) for v in s["bbox_px"]]
    print(f"{s['name']:22s} expect={s['expect']!s:5s} cls={s['cls']:12s} bbox={bb}")
