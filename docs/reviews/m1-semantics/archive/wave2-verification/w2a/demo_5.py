#!/usr/bin/env python3
"""CLAIM 5 -- a frame whose shape differs from config camera.width/height raises
no error in the semantics path (silently wrong projection).

Real path: Perception.process accepts a 640x360 frame although config says
1280x720 (FloorModel scales its polygon, perception.py:253-259), and
Perception.semantic_context (perception.py:704-722) still hands out the
FULL-RES homography + polygon. semantics.project_detections (semantics.py:
206-235) reads frame.shape only for clipping (L213-216); nothing compares it
with cfg.camera.width/height. A 640-space bbox is therefore interpreted in
1280-space: projection is off by ~2x, silently. Also shows the silent clipping
when a full-res bbox meets the small frame.
"""
from __future__ import annotations

import sys
import time
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

CLONE = Path(__file__).resolve().parent / "clone"
sys.path.insert(0, str(CLONE))
OUT = Path(__file__).resolve().parent / "artifacts" / "demo_5"
import shutil  # noqa: E402
shutil.rmtree(OUT, ignore_errors=True)      # fresh artifacts, deterministic re-runs

from config import RoomConfig                                  # noqa: E402
from perception import Homography, Perception                  # noqa: E402
from semantics import (Detection, FakeVision, SemanticsRunner,  # noqa: E402
                       WorldFixture, project_detections)
from synthetic import SyntheticRoom                            # noqa: E402

fx = WorldFixture("blue mat", 3.0, 1.2, 0.5, 0.6, 0.92)       # true floor centre (3.0, 1.2)
TRUE = (fx.x, fx.y)


def dist(a, b) -> float:
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


print("CLAIM 5: wrong-sized frame -> no error, silently wrong projection")
cfg = RoomConfig.load(CLONE / "config" / "room.synthetic.json")
print(f"config camera: {cfg.camera.width}x{cfg.camera.height}")

perc = Perception(cfg)
frame_half = cv2.resize(SyntheticRoom(cfg).render(), (640, 360), interpolation=cv2.INTER_AREA)
for i in range(6):
    perc.process(frame_half, i / 15.0)                        # real pipeline, wrong-sized frame
ctx = perc.semantic_context(1.0)
H_full, H_half = perc.homography, Homography.for_scale(perc.homography, 0.5)
print(f"frame handed to semantics: {frame_half.shape[1]}x{frame_half.shape[0]}; "
      f"ctx homography is full-res (H[0,0]={float(H_full.H[0, 0]):.5f} -> {1 / float(H_full.H[0, 0]):.0f} px/m)")

det_half = FakeVision.from_world([fx], H_half).fixtures[0]     # what a 640x360 camera sees
det_full = FakeVision.from_world([fx], H_full).fixtures[0]     # what the config-res camera sees
print(f"half-res detection bbox={det_half.bbox_px}   full-res detection bbox={det_full.bbox_px}")

print("\n(a) committed path: half-res bbox + full-res ctx -> no error, wrong world")
raised = None
try:
    objs_bad, rej_bad = project_detections([det_half], frame_half, ctx, cfg.semantics, 1.0)
except Exception as e:                                        # noqa: BLE001
    raised = e
if raised is not None:
    print(f"    raised {type(raised).__name__}: {raised}")
else:
    w = (objs_bad[0].x, objs_bad[0].y)
    print(f"    no exception; objects={len(objs_bad)} rejected={rej_bad} "
          f"world=({w[0]:.3f},{w[1]:.3f}) true=({TRUE[0]},{TRUE[1]}) error={dist(w, TRUE):.2f} m "
          f"height_suspect={objs_bad[0].height_suspect}")

print("\n(b) correct handling for a 640x360 frame: Homography.for_scale(..., 0.5)")
objs_ok, _ = project_detections([det_half], frame_half, replace(ctx, homography=H_half),
                                cfg.semantics, 1.0)
w_ok = (objs_ok[0].x, objs_ok[0].y)
print(f"    world=({w_ok[0]:.3f},{w_ok[1]:.3f}) error={dist(w_ok, TRUE):.3f} m")

print("\n(c) full-res bbox on the small frame -> silent clipping (semantics.py L213-216)")
objs_clip, _ = project_detections([det_full], frame_half, ctx, cfg.semantics, 1.0)
w_clip = (objs_clip[0].x, objs_clip[0].y)
print(f"    bbox {det_full.bbox_px} -> clipped to (550, 359, 639, 359); world=({w_clip[0]:.3f},{w_clip[1]:.3f}) "
      f"true=({TRUE[0]},{TRUE[1]}) error={dist(w_clip, TRUE):.2f} m; no exception")

print("\n(d) end-to-end: SemanticsRunner worker with the mismatched frame/ctx")


class FixedVision:
    name = "fixed-vision"

    def infer(self, frame, *, labels=None):
        return [det_half]


run_cfg = replace(cfg, semantics=replace(cfg.semantics, store_dir=str(OUT / "runner")))
runner = SemanticsRunner(run_cfg, "shapedemo", FixedVision())
try:
    assert runner.maybe_pass(1.0, ctx, frame_half, force=True)
    merged = None
    for _ in range(300):
        merged = runner.poll(1.001)
        if merged is not None:
            break
        time.sleep(0.01)
    o = merged.objects[0]
    print(f"    merged id={o.id} world=({o.x},{o.y}) true=({TRUE[0]},{TRUE[1]}) "
          f"error={dist((o.x, o.y), TRUE):.2f} m; worker.errors={runner.worker.errors} "
          f"last_error={runner.worker.last_error}")
finally:
    runner.close()

ok = (raised is None and dist((objs_bad[0].x, objs_bad[0].y), TRUE) > 1.0
      and runner.worker.errors == 0)
print(f"\nVERDICT claim 5: {'CONFIRMED' if ok else 'REFUTED'}")
print("incident code: semantics.py L209-216 (frame.shape used only for clipping), "
      "perception.py L704-722 (ctx always full-res homography/polygon)")
