#!/usr/bin/env python3
"""CLAIM 4 -- a below-min_confidence detection (score 0.05) inside the match
radius still moves the object via EMA and overwrites confidence/height_suspect.

Real path: semantics.SemanticStore.merge (semantics.py:259-319). The match loop
(L271-280) has NO confidence filter; min_confidence is consulted only to gate
diff.appeared for brand-new objects (L304-305). The matched update copies
d.confidence and d.height_suspect verbatim (L284-285) and applies the EMA
(L282-283).
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

CLONE = Path(__file__).resolve().parent / "clone"
sys.path.insert(0, str(CLONE))
OUT = Path(__file__).resolve().parent / "artifacts" / "demo_4"
import shutil  # noqa: E402
shutil.rmtree(OUT, ignore_errors=True)      # fresh artifacts, deterministic re-runs

from config import SemanticsConfig          # noqa: E402
from scene import SemanticObject            # noqa: E402
from semantics import SemanticStore         # noqa: E402


def obj(label: str, x: float, y: float, score: float = 0.9, hs: bool = False) -> SemanticObject:
    return SemanticObject(id="", label=label, x=x, y=y, confidence=score, height_suspect=hs)


cfg = replace(SemanticsConfig(), store_dir=str(OUT / "lowconf"))
st = SemanticStore(cfg, "lowconf")
print("CLAIM 4: score-0.05 detection within match radius still updates the object")
print(f"config: min_confidence={cfg.min_confidence} match_radius_m={cfg.match_radius_m} "
      f"ema_alpha={cfg.ema_alpha} move_threshold_m={cfg.move_threshold_m}\n")

st.merge([obj("red box", 3.0, 1.0, 0.9)], 0, "demo", 0.0)
print("pass 1: obj_0001 'red box' at (3.0, 1.0) conf=0.90 height_suspect=False")

d = obj("red box", 3.3, 1.0, 0.05, hs=True)
print(f"pass 2 detection: 'red box' at (3.3, 1.0) score=0.05 height_suspect=True "
      f"(distance {abs(3.3 - 3.0):.1f} m <= match radius {cfg.match_radius_m})")
m = st.merge([d], 0, "demo", 1.0)
o = m.objects[0]
expected_x = (1.0 - cfg.ema_alpha) * 3.0 + cfg.ema_alpha * 3.3
print(f"    result: x={o.x} (EMA from 3.0: expected {expected_x}) conf={o.confidence} "
      f"height_suspect={o.height_suspect} motion={o.motion} diff.moved={m.diff.moved} "
      f"diff.appeared={m.diff.appeared}")
print(f"    -> position moved {abs(o.x - 3.0):.2f} m by the 0.05-score detection; confidence "
      f"overwritten 0.90 -> {o.confidence}; height_suspect overwritten False -> {o.height_suspect}")

print("\n[related] an UNMATCHED 0.05-score detection creates a new object silently "
      "(min_confidence only gates diff.appeared):")
st2 = SemanticStore(replace(SemanticsConfig(), store_dir=str(OUT / "silent")), "silent")
m2 = st2.merge([obj("ghost", 2.0, 2.0, 0.05)], 0, "demo", 0.0)
print(f"    objects_in_map={len(m2.objects)} conf={m2.objects[0].confidence} "
      f"diff.appeared={m2.diff.appeared}")

ok = (abs(o.x - expected_x) < 1e-9 and o.confidence == 0.05 and o.height_suspect is True
      and o.motion == "moved")
print(f"\nVERDICT claim 4: {'CONFIRMED' if ok else 'REFUTED'}")
print("incident code: semantics.py L271-280 (no min_confidence filter on matching), L284-285 (verbatim overwrite)")
