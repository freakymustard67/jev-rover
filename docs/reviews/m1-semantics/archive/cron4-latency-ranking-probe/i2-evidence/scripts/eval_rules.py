#!/usr/bin/env python3
"""I2 probe hardening -- evaluate one clone's height_suspect rule against the
scenario dump.  Rule-agnostic: run once per clone (master / PR tip / prototype).

    cd <clone> && PYTHONDONTWRITEBYTECODE=1 <venv>/python <this>/eval_rules.py <clone> <dumpdir> <out.json> [flake_n]

Prints raw numbers (probe pixels, patch LAB, deltas, decisions) and writes JSON.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

CLONE = Path(sys.argv[1]).resolve()
DUMP = Path(sys.argv[2]).resolve()
OUTJ = Path(sys.argv[3]).resolve()
FLAKE_N = int(sys.argv[4]) if len(sys.argv) > 4 else 0
sys.path.insert(0, str(CLONE))

import semantics  # noqa: E402
from config import RoomConfig  # noqa: E402
from perception import Homography, SemanticContext  # noqa: E402
from semantics import Detection  # noqa: E402

meta = json.loads((DUMP / "scenarios.json").read_text())
frames = np.load(DUMP / "frames.npz")
cfg = RoomConfig.load(CLONE / "config" / "room.synthetic.json")
H = Homography(meta["image_points_px"], meta["world_points_m"])
poly = np.asarray(meta["polygon_px"], np.float32).reshape(-1, 2)
MAT_LAB = np.asarray(meta["mat_lab"], float)

commit = subprocess.run(["git", "-C", str(CLONE), "rev-parse", "--short", "HEAD"],
                        capture_output=True, text=True).stdout.strip()


def make_ctx(s, t=1.0):
    grid = np.zeros((int(3.6 / 0.05), int(6.4 / 0.05)), np.float32)
    return SemanticContext(homography=H, room_w_m=6.4, room_h_m=3.6, cell_m=0.05,
                           polygon_px=poly, floor_lab=np.asarray(s["floor_lab"], float),
                           floor_lab_tolerance=meta["lab_tolerance"],
                           grid_log_odds=grid, grid_last_seen=np.ones_like(grid),
                           grid_t=t, occupied_thr=0.42, stale_s=3.0)


def tune(proj, s):
    """Best-effort knob injection for the prototype; no-op on master/PR."""
    kw = {}
    if hasattr(proj, "probe_m"):
        kw["probe_m"] = 0.03
    if hasattr(proj, "cover_lab"):
        covers = [[float(v) for v in MAT_LAB]]
        if __import__("os").environ.get("I2_COVERS", "both") == "both":
            covers.append([float(v) for v in np.asarray(meta.get("dark_mat_lab", MAT_LAB), float)])
        kw["cover_lab"] = covers
    if hasattr(proj, "self_match"):
        kw["self_match"] = __import__("os").environ.get("I2_SELF_MATCH", "0") == "1"
    if not kw:
        return proj
    try:
        return replace(proj, **kw)
    except TypeError:
        return proj


def lab_patch(frame, x, y, pw=7, ph=3):
    h, w = frame.shape[:2]
    x0, x1 = max(0, x - pw // 2), min(w, x + pw // 2 + 1)
    y0, y1 = max(0, y - ph // 2), min(h, y + ph // 2 + 1)
    return np.median(cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2LAB)
                     .reshape(-1, 3).astype(float), axis=0)


def metres_per_px(frame_pts, px, py, probe_m):
    """Local metres per pixel along the image-down direction, via H."""
    p0 = np.array([[px, py]], float)
    p1 = np.array([[px, py + 1]], float)
    w0, w1 = H.img_to_world(p0)[0], H.img_to_world(p1)[0]
    d = w1 - w0
    n = float(np.linalg.norm(d))
    return n, (w0 + d / n * probe_m if n > 0 else w0)


results = []
fp = fn = 0
fp_cases, fn_cases = [], []
for s in meta["scenarios"]:
    frame = frames[s["name"]]
    ctx = make_ctx(s)
    proj = tune(cfg.semantics.project, s)
    bbox = tuple(float(v) for v in s["bbox_px"])
    x0, y0, x1, y1 = bbox

    # ---- diagnostics (rule-independent) ----------------------------------
    px = int(round((x0 + x1) / 2.0))
    py = int(round(y1)) + int(meta["probe_px"])
    mpp, probe_world = metres_per_px(frame, px, int(round(y1)), 0.03)
    diag = {
        "legacy_probe_px": [px, py],
        "legacy_pixel_lab": [float(v) for v in lab_patch(frame, px, py, 1, 1)],
        "legacy_patch7x3_lab": [float(v) for v in lab_patch(frame, px, py)],
        "floor_lab": [round(float(v), 1) for v in s["floor_lab"]],
        "legacy_delta_norm": None, "dL": None, "dchroma": None,
        "metres_per_px_at_base_row": round(mpp, 6),
        "probe_world_0.03m": [round(float(v), 4) for v in probe_world],
        "m_per_6px": round(mpp * 6, 4),
        "ring_lab_l": [float(v) for v in lab_patch(frame, px - 11, py, 1, 1)],
        "ring_lab_r": [float(v) for v in lab_patch(frame, px + 11, py, 1, 1)],
    }
    pl = np.asarray(diag["legacy_patch7x3_lab"], float)
    fl = np.asarray(s["floor_lab"], float)
    dl = fl - pl
    diag["legacy_delta_norm"] = round(float(np.linalg.norm(dl)), 2)
    diag["dL"] = round(float(dl[0]), 2)
    diag["dchroma"] = round(float(np.linalg.norm(dl[1:3])), 2)

    # ---- the rule under test --------------------------------------------
    dec = bool(semantics.height_suspect(frame, bbox, s["x"], s["y"], ctx, proj))
    # end-to-end projection path (flag + world coords through the real code)
    sem_cfg = replace(cfg.semantics, project=proj)
    det = Detection(s["label"], tuple(int(v) for v in s["bbox_px"]), s["score"])
    objs, rej = semantics.project_detections([det], frame, ctx, sem_cfg, 1.0)
    e2e = None
    if objs:
        e2e = {"wx": objs[0].x, "wy": objs[0].y, "height_suspect": bool(objs[0].height_suspect)}

    ok = (dec == s["expect"])
    if not ok and s["expect"] is False:
        fp += 1
        fp_cases.append(s["name"])
    if not ok and s["expect"] is True:
        fn += 1
        fn_cases.append(s["name"])
    results.append({**{k: s[k] for k in ("name", "expect", "cls", "note", "label")},
                    "decision": dec, "ok": ok, "diag": diag, "e2e": e2e})
    print(f"{s['name']:22s} expect={s['expect']!s:5s} got={dec!s:5s} {'OK ' if ok else 'XX '} "
          f"cls={s['cls']:12s} probe={px},{py} patchLAB={np.round(pl,1).tolist()} "
          f"floorLAB={np.round(fl,1).tolist()} |d|={diag['legacy_delta_norm']:6.1f} "
          f"dL={diag['dL']:6.1f} dC={diag['dchroma']:5.1f} m/6px={diag['m_per_6px']:.4f}")

print(f"\nclone={CLONE.name} commit={commit}  FP={fp} FN={fn}  fp_cases={fp_cases} fn_cases={fn_cases}")

# ---- sensor-noise flake measurement (identical frames in every clone) -------
flake = {}
if FLAKE_N:
    rng = np.random.default_rng(4242)
    for name in ("floor_only", "mat_thing", "shadow_contact", "tight_bbox_speckle"):
        s = next(x for x in meta["scenarios"] if x["name"] == name)
        frame0 = frames[name]
        bbox = tuple(float(v) for v in s["bbox_px"])
        px = int(round((bbox[0] + bbox[2]) / 2.0))
        py = int(round(bbox[3])) + int(meta["probe_px"])
        ctx = make_ctx(s)
        proj = tune(cfg.semantics.project, s)
        h, w = frame0.shape[:2]
        flags = 0
        flags_clean = 0
        n_clean = 0
        flags_dead = 0
        n_dead = 0
        for i in range(FLAKE_N):
            small = rng.integers(-3, 4, size=(h // 2, w // 2, 3), dtype=np.int16)
            up = cv2.resize(small.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
            fr = np.clip(frame0.astype(np.int16) + np.rint(up).astype(np.int16), 0, 255).astype(np.uint8)
            injected = False
            if i % 10 == 0:
                fr[py, px] = (0, 0, 0)          # dead pixel on the probe pixel
                injected = True
            if i % 25 == 0:
                fr[py, px] = (255, 255, 255)    # hot pixel
                injected = True
            hit = bool(semantics.height_suspect(fr, bbox, s["x"], s["y"], ctx, proj))
            flags += hit
            if injected:
                n_dead += 1
                flags_dead += hit
            else:
                n_clean += 1
                flags_clean += hit
        flake[name] = {"n": FLAKE_N, "suspect_rate": flags / FLAKE_N, "flags": flags,
                       "noise_only": {"n": n_clean, "flags": flags_clean,
                                      "rate": round(flags_clean / max(1, n_clean), 4)},
                       "injected": {"n": n_dead, "flags": flags_dead,
                                    "rate": round(flags_dead / max(1, n_dead), 4)}}
        print(f"flake {name:22s} suspect {flags}/{FLAKE_N}  "
              f"noise-only {flags_clean}/{n_clean}  injected {flags_dead}/{n_dead}")

json.dump({"clone": CLONE.name, "commit": commit, "fp": fp, "fn": fn,
           "fp_cases": fp_cases, "fn_cases": fn_cases, "scenarios": results,
           "flake": flake}, open(OUTJ, "w"), indent=1)
print("wrote", OUTJ)
