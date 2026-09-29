"""W3B independent check #3: label case-folding.

'blue mat' object + 'Blue Mat' detection must merge into ONE object (pre-patch a
second obj_0002 was created). Also checks the dedupe + adapter-filter halves.
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
from semantics import (Detection, FakeVision, SemanticStore, WorldFixture,
                       dedupe_detections, project_detections)
from synthetic import SyntheticRoom

cfg = RoomConfig.load("config/room.synthetic.json")
cfg.semantics.store_dir = os.path.join(OUT, "store_check3")


def det(label, x, y, conf=0.9):
    return SemanticObject(id="", label=label, x=x, y=y, confidence=conf)


# --- part A: store merge -------------------------------------------------------
st = SemanticStore(cfg.semantics, "w3b3")
st.merge([det("blue mat", 3.0, 1.2)], 0, "fake", 0.0)
m = st.merge([det("Blue Mat", 3.02, 1.19)], 0, "fake", 1.0)
print(f"OBS A_objects={[(o.id, o.label, round(o.x, 3), round(o.y, 3)) for o in m.objects]}")
print(f"OBS A_n_objects={len(m.objects)} A_appeared={m.diff.appeared} "
      f"A_vanished={m.diff.vanished} A_moved={m.diff.moved}")
m2 = st.merge([det("blue mats", 3.0, 1.2)], 0, "fake", 2.0)
print(f"OBS A_plural_n_objects={len(m2.objects)} A_plural_appeared={m2.diff.appeared} "
      f"A_plural_labels={[o.label for o in m2.objects]}")

# --- part B: dedupe ------------------------------------------------------------
d1 = Detection("Blue Mat", (100, 100, 200, 200), 0.7)
d2 = Detection("blue mats", (102, 101, 201, 199), 0.9)
kept = dedupe_detections([d1, d2], 0.5)
print(f"OBS B_dedupe_n_kept={len(kept)} B_dedupe_labels={[d.label for d in kept]} "
      f"B_dedupe_scores={[d.score for d in kept]}")

# --- part C: adapter filter ----------------------------------------------------
v = FakeVision([Detection("Blue Mat", (0, 0, 10, 10))])
got = v.infer(np.zeros((20, 20, 3), np.uint8), labels=["blue mats"])
print(f"OBS C_infer_plural_label={[d.label for d in got]}")

# --- part D: projection pipeline, two differently-cased duplicates -------------
perc = Perception(cfg)
syn = SyntheticRoom(cfg, seed=0)
frame = None
for i in range(6):
    frame = syn.render()
    perc.process(frame, i / 15.0)
ctx = perc.semantic_context(1.0)
v2 = FakeVision.from_world([WorldFixture("blue mat", 3.0, 1.2, 0.5, 0.6, 0.9),
                            WorldFixture("Blue Mat", 3.0, 1.2, 0.5, 0.6, 0.95)],
                           perc.homography)
objs, rej = project_detections(v2.fixtures, frame, ctx, cfg.semantics, 1.0)
print(f"OBS D_projected={[(o.label, o.x, o.y, o.confidence) for o in objs]} D_rejected={rej} "
      f"D_detections={len(v2.fixtures)}")
print("EXPECT_AFTER_FIX: A_n_objects=1 appeared=[] label='blue mat'; "
      "B_dedupe_n_kept=1; C=1 detection; D_projected has 1 entry (score 0.95)")
print("PRE_PATCH_SYMPTOM: A_n_objects=2 appeared=['obj_0002']; B=2 kept; C=0 detections; D=2 entries")
