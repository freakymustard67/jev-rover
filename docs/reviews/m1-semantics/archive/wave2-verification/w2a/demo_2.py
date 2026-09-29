#!/usr/bin/env python3
"""CLAIM 2 -- an object resting on a non-floor-coloured mat is flagged height_suspect=True.

Constructs a frame with the synthetic room's floor colour plus a blue-mat
rectangle, an object bbox whose base sits on the mat, and drives the real probe
path: semantics.project_detections (semantics.py:206-235) -> height_suspect
(semantics.py:176-203, probe at bbox bottom-centre + project.probe_px, L191-197),
both directly and end-to-end through SemanticsRunner/SemanticsWorker.

The object is FLAT ON THE FLOOR (the mat is floor-level): the flag is therefore
a false positive for "elevated", though by design ("projection untrustworthy"
until confirmed). Control: same object on bare floor -> False.
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
OUT = Path(__file__).resolve().parent / "artifacts" / "demo_2"
import shutil  # noqa: E402
shutil.rmtree(OUT, ignore_errors=True)      # fresh artifacts, deterministic re-runs

from config import RoomConfig                                   # noqa: E402
from perception import Homography, SemanticContext              # noqa: E402
from semantics import (Detection, FakeVision, SemanticsRunner,  # noqa: E402
                       WorldFixture, project_detections)
from synthetic import FLOOR_BGR                                 # noqa: E402

MAT_BGR = (180, 90, 40)      # a blue mat, clearly not floor-coloured
W, H = 6.4, 3.6              # room metres (config)
MAT_W = (2.6, 3.4)           # mat x range in metres
MAT_Y = (0.8, 1.6)           # mat y range in metres


def lab(bgr: tuple[int, int, int]) -> np.ndarray:
    px = np.uint8([[[*bgr]]])
    return cv2.cvtColor(px, cv2.COLOR_BGR2LAB)[0, 0].astype(float)


def make_ctx(cfg: RoomConfig, homography: Homography) -> SemanticContext:
    grid = np.zeros((int(H / cfg.grid.cell_m), int(W / cfg.grid.cell_m)), np.float32)
    return SemanticContext(
        homography=homography, room_w_m=W, room_h_m=H, cell_m=cfg.grid.cell_m,
        polygon_px=np.asarray(cfg.floor.polygon_px, np.float32).reshape(-1, 2),
        floor_lab=lab(FLOOR_BGR), floor_lab_tolerance=cfg.floor.lab_tolerance,
        grid_log_odds=grid, grid_last_seen=np.full_like(grid, 1.0),
        grid_t=1.0, occupied_thr=0.42, stale_s=3.0)


def make_frame(homography: Homography, mat: bool) -> np.ndarray:
    frame = np.full((720, 1280, 3), FLOOR_BGR, np.uint8)
    if mat:
        x0, y0 = homography.world_to_img([[MAT_W[0], MAT_Y[1]]])[0]
        x1, y1 = homography.world_to_img([[MAT_W[1], MAT_Y[0]]])[0]
        cv2.rectangle(frame, (round(x0), round(y0)), (round(x1), round(y1)), MAT_BGR, -1)
    return frame


def bbox_for(homography: Homography, fx: WorldFixture) -> Detection:
    return FakeVision.from_world([fx], homography).fixtures[0]


def drain(runner: SemanticsRunner, t: float, tries: int = 300):
    for _ in range(tries):
        res = runner.poll(t)
        if res is not None:
            return res
        time.sleep(0.01)
    return None


class FixedVision:
    name = "fixed-vision"

    def __init__(self, dets: list[Detection]):
        self.dets = dets

    def infer(self, frame, *, labels=None):
        return list(self.dets)


print("CLAIM 2: object on a non-floor-coloured mat -> height_suspect=True")
cfg = RoomConfig.load(CLONE / "config" / "room.synthetic.json")
homography = Homography(cfg.homography.image_points_px, cfg.homography.world_points_m)
ctx = make_ctx(cfg, homography)
print(f"config: floor.lab_tolerance={cfg.floor.lab_tolerance} project.probe_px={cfg.semantics.project.probe_px} "
      f"project.point_by_label={cfg.semantics.project.point_by_label}")
print(f"floor LAB={np.round(lab(FLOOR_BGR), 1).tolist()}  mat LAB={np.round(lab(MAT_BGR), 1).tolist()}  "
      f"distance={np.linalg.norm(lab(FLOOR_BGR) - lab(MAT_BGR)):.1f}")

mat_frame = make_frame(homography, mat=True)
floor_frame = make_frame(homography, mat=False)

on_mat = bbox_for(homography, WorldFixture("blue mat", 3.0, 1.2, 0.3, 0.3, 0.9))
on_floor = bbox_for(homography, WorldFixture("red box", 5.0, 1.0, 0.3, 0.3, 0.9))
for name, det, frame in (("on mat", on_mat, mat_frame), ("on bare floor", on_floor, floor_frame)):
    b = det.bbox_px
    probe = (int(round((b[0] + b[2]) / 2.0)), int(round(b[3])) + cfg.semantics.project.probe_px)
    d = float(np.linalg.norm(lab(frame[probe[1], probe[0]]) - lab(FLOOR_BGR)))
    print(f"\n[{name}] bbox_px={b} probe_px={probe} probe LAB distance from floor={d:.1f} "
          f"(>= tol {cfg.floor.lab_tolerance} means 'not floor')")

print("\n[direct probe path: project_detections -> height_suspect]")
objs_mat, rej_mat = project_detections([on_mat], mat_frame, ctx, cfg.semantics, 1.0)
objs_floor, rej_floor = project_detections([on_floor], floor_frame, ctx, cfg.semantics, 1.0)
print(f"    on mat:   rejected={rej_mat} world=({objs_mat[0].x},{objs_mat[0].y}) "
      f"height_suspect={objs_mat[0].height_suspect}")
print(f"    on floor: rejected={rej_floor} world=({objs_floor[0].x},{objs_floor[0].y}) "
      f"height_suspect={objs_floor[0].height_suspect}")

print("\n[end-to-end path: SemanticsRunner worker with the mat frame]")
run_cfg = replace(cfg, semantics=replace(cfg.semantics, store_dir=str(OUT / "runner")))
runner = SemanticsRunner(run_cfg, "matdemo", FixedVision([on_mat]))
try:
    assert runner.maybe_pass(1.0, ctx, mat_frame, force=True)
    merged = drain(runner, 1.001)
    o = merged.objects[0]
    print(f"    merged: id={o.id} label={o.label} world=({o.x},{o.y}) "
          f"height_suspect={o.height_suspect} conf={o.confidence} worker_errors={runner.worker.errors}")
finally:
    runner.close()

ok = objs_mat[0].height_suspect is True and objs_floor[0].height_suspect is False
print(f"\nVERDICT claim 2: {'CONFIRMED' if ok else 'REFUTED'}")
print("incident code: semantics.py L176-203 (probe below bbox base vs floor colour; no mat/furniture distinction)")
