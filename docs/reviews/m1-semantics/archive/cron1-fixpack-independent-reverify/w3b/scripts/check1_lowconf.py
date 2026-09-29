"""W3B independent check #1: a sub-min_confidence detection must not move an
object's position/confidence/height_suspect (fork: pre-patch it moved x 3.0->3.12
at conf 0.05).

Drives the real code paths: SemanticStore.merge directly (part A) and
Perception -> project_detections -> merge (part B).
"""
import os
import sys

import numpy as np

ROOT = os.environ.get("W3B_CLONE", os.getcwd())
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
OUT = os.environ["W3B_OUT"]

from config import RoomConfig
from perception import Perception
from scene import SemanticObject
from semantics import FakeVision, SemanticStore, WorldFixture, project_detections
from synthetic import SyntheticRoom

cfg = RoomConfig.load("config/room.synthetic.json")
cfg.semantics.store_dir = os.path.join(OUT, "store_check1")
print(f"OBS min_confidence={cfg.semantics.min_confidence} ema_alpha={cfg.semantics.ema_alpha} "
      f"move_threshold_m={cfg.semantics.move_threshold_m}")


def line(tag, m):
    o = m.objects[0]
    print(f"OBS {tag}.x={o.x:.4f} {tag}.y={o.y:.4f} {tag}.confidence={o.confidence:.4f} "
          f"{tag}.height_suspect={o.height_suspect} {tag}.last_seen_s={o.last_seen_s} "
          f"{tag}.motion={o.motion} {tag}.appeared={m.diff.appeared} "
          f"{tag}.moved={m.diff.moved} {tag}.vanished={m.diff.vanished}")


# ---------------- part A: hand-built objects straight into the store ----------
st = SemanticStore(cfg.semantics, "w3b1a")
st.merge([SemanticObject(id="", label="blue mat", x=3.0, y=1.2, confidence=0.9,
                         height_suspect=True)], 0, "fake", 0.0)
mA = st.merge([SemanticObject(id="", label="blue mat", x=3.12, y=1.2, confidence=0.05,
                              height_suspect=False)], 0, "fake", 1.0)
line("A_after_lowconf", mA)

# ---------------- part B: full projection pipeline ----------------------------
perc = Perception(cfg)
syn = SyntheticRoom(cfg, seed=0)
frame = None
for i in range(6):
    frame = syn.render()
    perc.process(frame, i / 15.0)
ctx = perc.semantic_context(1.0)
print(f"OBS B_frame_shape={frame.shape} B_polygon_camera_w={getattr(ctx, 'camera_w', '<absent>')}")

st2 = SemanticStore(cfg.semantics, "w3b1b")
v1 = FakeVision.from_world([WorldFixture("blue mat", 3.0, 1.2, 0.5, 0.6, 0.92)], perc.homography)
o1, r1 = project_detections(v1.fixtures, frame, ctx, cfg.semantics, 1.0)
print(f"OBS B_pass1_projected={[(o.label, o.x, o.y, o.confidence, o.height_suspect) for o in o1]} rejected={r1}")
st2.merge(o1, r1, v1.name, 1.0)

v2 = FakeVision.from_world([WorldFixture("blue mat", 3.12, 1.2, 0.5, 0.6, 0.05)], perc.homography)
o2, r2 = project_detections(v2.fixtures, frame, ctx, cfg.semantics, 2.0)
print(f"OBS B_pass2_projected={[(o.label, o.x, o.y, o.confidence, o.height_suspect) for o in o2]} rejected={r2}")
mB = st2.merge(o2, r2, v2.name, 2.0)
line("B_after_lowconf", mB)

print("EXPECT_AFTER_FIX A: x=3.0 confidence=0.9 height_suspect=True last_seen_s=1.0; "
      "B: x=3.0 (EMA not applied), confidence=0.9")
print("PRE_PATCH_SYMPTOM: x==3.048 (A) / 3.048 (B), confidence=0.05, height_suspect=False")
