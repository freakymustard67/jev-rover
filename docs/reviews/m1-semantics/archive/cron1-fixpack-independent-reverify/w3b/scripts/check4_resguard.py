"""W3B independent check #4: frame-resolution guard.

Feeding a 640x360 frame under a 1280x720 camera config must be refused/warned in
the semantics path. The synthetic config IS 1280x720, so the mismatch scenario is
exactly: half-scale frame (or half-scale detector boxes) + full-scale homography.
"""
import contextlib
import io
import os
import sys
import time
from dataclasses import replace

import cv2
import numpy as np

ROOT = os.environ.get("W3B_CLONE", os.getcwd())
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
OUT = os.environ["W3B_OUT"]

from config import RoomConfig
from perception import Homography, Perception
from semantics import FakeVision, SemanticsRunner, WorldFixture, project_detections
from synthetic import SyntheticRoom

cfg = RoomConfig.load("config/room.synthetic.json")
cfg.semantics.store_dir = os.path.join(OUT, "store_check4")
print(f"OBS configured_camera={cfg.camera.width}x{cfg.camera.height}")

perc = Perception(cfg)
syn = SyntheticRoom(cfg, seed=0)
frame = None
for i in range(6):
    frame = syn.render()
    perc.process(frame, i / 15.0)
ctx = perc.semantic_context(1.0)
print(f"OBS real_frame_shape={frame.shape} ctx_camera_w={getattr(ctx, 'camera_w', '<absent>')} "
      f"ctx_camera_h={getattr(ctx, 'camera_h', '<absent>')}")

small = cv2.resize(frame, (frame.shape[1] // 2, frame.shape[0] // 2),
                   interpolation=cv2.INTER_AREA)
print(f"OBS mismatched_frame_shape={small.shape}")

# --- A: pure geometry: where a world point lands when read at half scale -------
H = perc.homography
H_half = Homography.for_scale(H, 0.5)
for (wx, wy) in [(3.0, 1.2), (4.4, 1.0), (1.5, 0.8)]:
    full = H.world_to_img(np.array([[wx, wy]]))[0]
    half = full / 2.0                      # where the point sits in the 640x360 frame
    got = H.img_to_world(half.reshape(1, 2))[0]
    err = float(np.hypot(got[0] - wx, got[1] - wy))
    print(f"OBS A_world({wx},{wy}) full_img=({full[0]:.1f},{full[1]:.1f}) "
          f"half_img=({half[0]:.1f},{half[1]:.1f}) full_H_projection=({got[0]:.3f},{got[1]:.3f}) "
          f"silent_error_m={err:.3f}")

# --- B: a 640x360-space adapter's boxes run through the full-res homography ----
v_half = FakeVision.from_world([WorldFixture("blue mat", 3.0, 1.2, 0.5, 0.6, 0.92)], H_half)
objs, rej = project_detections(v_half.fixtures, small, ctx, cfg.semantics, 1.0)
print(f"OBS B_halfspace_bbox={v_half.fixtures[0].bbox_px} "
      f"B_projected={[(o.label, o.x, o.y) for o in objs]} rejected={rej}")

# --- C: SemanticsRunner guard --------------------------------------------------
def drain(runner, t):
    for _ in range(300):
        runner.poll(t)
        if runner._inflight is None:
            return
        time.sleep(0.01)


runner = SemanticsRunner(cfg, "w3b4", v_half)
buf = io.StringIO()
with contextlib.redirect_stderr(buf):
    r1 = runner.maybe_pass(1.0, ctx, small, kind="full", force=True)
    r2 = runner.maybe_pass(2.0, ctx, small, kind="full", force=True)
warn = buf.getvalue().strip().splitlines()
print(f"OBS C_accept1={r1} C_accept2={r2} C_skipped={dict(runner.skipped)}")
print(f"OBS C_warning_lines={len(warn)} C_warning={warn[0] if warn else '<none>'}")
drain(runner, 2.1)
snap = runner.snapshot(2.2)
print(f"OBS C_store_objects={[(o.label, getattr(o, 'x', None), getattr(o, 'y', None)) for o in (snap.objects if snap else [])]} "
      f"C_passes={runner.store.passes}")
runner.close()

# --- D: matching frame is accepted ---------------------------------------------
v_full = FakeVision.from_world([WorldFixture("blue mat", 3.0, 1.2, 0.5, 0.6, 0.92)], H)
runner2 = SemanticsRunner(cfg, "w3b4b", v_full)
r3 = runner2.maybe_pass(1.0, ctx, frame, kind="full", force=True)
drain(runner2, 1.1)
snap2 = runner2.snapshot(1.2)
print(f"OBS D_accept_matched={r3} D_skipped={dict(runner2.skipped)} "
      f"D_store_objects={[(o.label, o.x, o.y) for o in (snap2.objects if snap2 else [])]}")

# --- E: camera fields absent/unknown -> no check (post-patch design) -----------
if hasattr(ctx, "camera_w"):
    ctx0 = replace(ctx, camera_w=0, camera_h=0)
    r4 = runner2.maybe_pass(5.0, ctx0, small, kind="full", force=True)
    drain(runner2, 5.1)
    print(f"OBS E_accept_unknown_camera={r4} E_skipped={dict(runner2.skipped)}")
else:
    print("OBS E_accept_unknown_camera=<no camera_w field pre-patch>")
runner2.close()

print("EXPECT_AFTER_FIX: C_accept1=False C_accept2=False, 1 warning line, "
      "C_skipped['resolution']=2, C_passes=0; D accepts (skipped resolution=0) and stores ~(3.0,1.2); "
      "E accepts (0 = unknown)")
print("PRE_PATCH_SYMPTOM: C_accept1/2 accept (no 'resolution' key), passes=1, store object at the "
      "misprojected half-scale coordinates")
