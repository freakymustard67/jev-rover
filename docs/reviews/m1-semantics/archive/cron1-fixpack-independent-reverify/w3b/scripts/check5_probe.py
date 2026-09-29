"""W3B independent check #5: the probe patch (patch 0007, '7x3 median instead of
a single pixel').

Verifies the stated intent (single dead pixel / ring artefact no longer flips the
probe), whether an object resting on a non-floor-coloured mat still gets
height_suspect=True (the documented M1 rule), and what behaviour the median
changes.
"""
import os
import sys
from dataclasses import replace

import cv2
import numpy as np

ROOT = os.environ.get("W3B_CLONE", os.getcwd())
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
OUT = os.environ["W3B_OUT"]

from config import RoomConfig
from perception import Perception
from semantics import FakeVision, WorldFixture, height_suspect
from synthetic import SyntheticRoom

cfg = RoomConfig.load("config/room.synthetic.json")
project = cfg.semantics.project
print(f"OBS probe_px={project.probe_px} point={project.point} "
      f"patch_const={getattr(__import__('semantics'), 'PROBE_PATCH_PX', '<absent>')}")

perc = Perception(cfg)
syn = SyntheticRoom(cfg, seed=0)
frame = None
for i in range(6):
    frame = syn.render()
    perc.process(frame, i / 15.0)
ctx = perc.semantic_context(1.0)
h, w = frame.shape[:2]


def bbox_of(label, x, y, ww, hh, score=0.9):
    return FakeVision.from_world([WorldFixture(label, x, y, ww, hh, score)], perc.homography).fixtures[0].bbox_px


def probe_xy(bb, probe_px):
    return int(round((bb[0] + bb[2]) / 2.0)), int(round(bb[3])) + int(probe_px)


# --- A: single dead pixel under the bbox base ---------------------------------
bmat = bbox_of("blue mat", 3.0, 1.2, 0.5, 0.6, 0.92)
bb = tuple(float(v) for v in bmat)
px, py = probe_xy(bmat, project.probe_px)
base = height_suspect(frame, bb, 3.0, 1.2, ctx, project)
noisy = frame.copy()
noisy[py, px] = (0, 0, 0)
hs_dead = height_suspect(noisy, bb, 3.0, 1.2, ctx, project)
print(f"OBS A_probe_at=({px},{py}) A_base={base} A_single_dead_pixel={hs_dead}")

# --- B: object resting on a non-floor mat (documented M1 rule) -----------------
bbox2 = bbox_of("red box", 3.6, 1.4, 0.3, 0.3)
bb2 = tuple(float(v) for v in bbox2)
px2, py2 = probe_xy(bbox2, project.probe_px)
mat_frame = frame.copy()
cv2.rectangle(mat_frame, (bbox2[0] - 60, bbox2[1]), (bbox2[2] + 60, bbox2[3] + 60),
              (160, 60, 40), -1)                     # non-floor coloured mat
hs_mat = height_suspect(mat_frame, bb2, 3.6, 1.4, ctx, project)
print(f"OBS B_probe_at=({px2},{py2}) B_base_on_mat_free_floor={height_suspect(frame, bb2, 3.6, 1.4, ctx, project)} "
      f"B_on_nonfloor_mat={hs_mat}")

# --- C: median footprint change: rows py-1 and py+1 dark, probe row clean ------
wide = frame.copy()
wide[py - 1, px - 3:px + 4] = 0
wide[py + 1, px - 3:px + 4] = 0
hs_wide = height_suspect(wide, bb, 3.0, 1.2, ctx, project)
probe_row_clean = bool(np.all(wide[py, px - 3:px + 4] > 100))
print(f"OBS C_probe_row_clean={probe_row_clean} C_single_pixel_sees={wide[py, px].tolist()} "
      f"C_hs_now={hs_wide}")

# --- D: whole 7x3 patch non-floor -> still suspect -----------------------------
dark = frame.copy()
dark[py - 1:py + 2, px - 3:px + 4] = 0
print(f"OBS D_hs_whole_patch_dark={height_suspect(dark, bb, 3.0, 1.2, ctx, project)}")

# --- E: boundary cases (documented: out-of-frame / outside polygon / unobserved)
probe_far = replace(project, probe_px=200)
print(f"OBS E_hs_probe_off_frame={height_suspect(frame, bb, 3.0, 1.2, ctx, probe_far)}")
print(f"OBS E_hs_outside_polygon={height_suspect(frame, (5.0, 5.0, 15.0, 15.0), 0.05, 3.4, ctx, project)}")
ctx_unobs = replace(ctx, grid_log_odds=np.zeros_like(ctx.grid_log_odds),
                    grid_last_seen=np.zeros_like(ctx.grid_last_seen), grid_t=1000.0)
print(f"OBS E_hs_unobserved_cell={height_suspect(frame, bb, 3.0, 1.2, ctx_unobs, project)}")

# --- F: probe_px still sets the offset (configs unchanged) ---------------------
p0 = replace(project, probe_px=0)
px0, py0 = probe_xy(bmat, 0)
print(f"OBS F_probe_px0_at=({px0},{py0}) F_hs_probe_px0={height_suspect(frame, bb, 3.0, 1.2, ctx, p0)}")

# --- G: exact patch footprint (median over 7x3=21 px; median flips at >= 11) ---
def band(rows, cols, base_frame):
    f = base_frame.copy()
    for r in rows:
        for c in cols:
            f[r, c] = 0
    return f


v1 = band((py - 1, py + 1), range(px - 3, px + 2), frame)      # 10 dark px
v2 = band((py - 1, py + 1), range(px - 3, px + 3), frame)      # 12 dark px
v3 = band((py,), range(px - 3, px + 4), frame)                 # whole probe row dark
v4 = band((py + 2,), range(px - 3, px + 4), frame)             # outside patch (row +2)
v5 = band((py - 1, py + 1), range(px + 4, px + 7), frame)      # outside patch (cols +4..+6)
print(f"OBS G_10px_dark_hs={height_suspect(v1, bb, 3.0, 1.2, ctx, project)} "
      f"G_12px_dark_hs={height_suspect(v2, bb, 3.0, 1.2, ctx, project)}")
print(f"OBS G_probe_row_all_dark_hs={height_suspect(v3, bb, 3.0, 1.2, ctx, project)} "
      f"G_outside_row_hs={height_suspect(v4, bb, 3.0, 1.2, ctx, project)} "
      f"G_outside_cols_hs={height_suspect(v5, bb, 3.0, 1.2, ctx, project)}")

print("EXPECT_AFTER_FIX (0007): A_single_dead_pixel=False (median survives), A_base=False; "
      "B_on_nonfloor_mat=True (M1 rule intact); D=True; E all True; C is the behaviour delta "
      "(see report)")
print("PRE_PATCH_SYMPTOM: A_single_dead_pixel=True (one pixel flips it), C_hs_now=False")
