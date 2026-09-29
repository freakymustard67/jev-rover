#!/usr/bin/env python3
"""CLAIM 6 -- label matching is case-sensitive: object 'blue mat' + detection
'Blue Mat' create a duplicate.

Real path: semantics.SemanticStore.merge compares labels with `d.label !=
prev.label` (semantics.py:273) -- raw string equality, no normalisation. The
same exact-compare appears in dedupe_detections (L161). Yet label lookups
elsewhere ARE case-folded: resolve_anchor_kind (L129-130), FakeVision filtering
(L93), build_vision filtering (L120-121), and query tokenisation (L592), so
case-only variants produce duplicate objects that queries cannot tell apart.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

CLONE = Path(__file__).resolve().parent / "clone"
sys.path.insert(0, str(CLONE))
OUT = Path(__file__).resolve().parent / "artifacts" / "demo_6"
import shutil  # noqa: E402
shutil.rmtree(OUT, ignore_errors=True)      # fresh artifacts, deterministic re-runs

from config import SemanticsConfig                        # noqa: E402
from scene import SemanticObject                          # noqa: E402
from semantics import (Detection, SemanticStore, dedupe_detections,  # noqa: E402
                       rank_candidates, resolve_destination)


def obj(label: str, x: float, y: float, score: float = 0.9) -> SemanticObject:
    return SemanticObject(id="", label=label, x=x, y=y, confidence=score)


cfg = replace(SemanticsConfig(), store_dir=str(OUT / "case"))
st = SemanticStore(cfg, "case")
print("CLAIM 6: 'blue mat' vs 'Blue Mat' -> duplicate object")

st.merge([obj("blue mat", 3.0, 1.2, 0.9)], 0, "demo", 0.0)
m = st.merge([obj("Blue Mat", 3.05, 1.2, 0.9)], 0, "demo", 1.0)
print(f"pass 1: 'blue mat'  -> {st.objs['obj_0001'].id} label={st.objs['obj_0001'].label!r}")
print(f"pass 2: 'Blue Mat' -> ids={[o.id for o in m.objects]} labels={[o.label for o in m.objects]}")
print(f"    diff.appeared={m.diff.appeared} (the case-variant was announced as a NEW object), "
      f"diff.moved={m.diff.moved}")

kept = dedupe_detections([Detection("blue mat", (100, 100, 200, 200), 0.7),
                          Detection("Blue Mat", (102, 101, 201, 199), 0.9)], 0.5)
print(f"\n[dedupe] same pass, overlapping bboxes, labels differing only by case -> kept={len(kept)} "
      f"labels={[d.label for d in kept]} (semantics.py L161 exact-compares labels)")

snap = st.snapshot(2.0)
ranked = rank_candidates("go to the blue mat", snap.objects)
print(f"\n[query] rank_candidates('go to the blue mat') on the duplicated map:")
for c in ranked:
    print(f"    {c.obj.label!r} id={c.obj.id} score={c.score:.2f} conf={c.obj.confidence}")
dest = resolve_destination("go to the blue mat", snap)
print(f"    resolve_destination -> {dest.label!r} object_id={dest.object_id} at ({dest.x},{dest.y}) "
      f"(two labels tie at score 1.00; the pick falls to the deterministic fallback)")

ok = (len(m.objects) == 2 and m.diff.appeared == ["obj_0002"] and len(kept) == 2
      and len(ranked) == 2)
print(f"\nVERDICT claim 6: {'CONFIRMED' if ok else 'REFUTED'}")
print("incident code: semantics.py L273 (raw != compare), L161 (dedupe), vs case-folded lookups at L93/L120/L129/L592")
