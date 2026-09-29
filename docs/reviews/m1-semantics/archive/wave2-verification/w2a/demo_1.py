#!/usr/bin/env python3
"""CLAIM 1 -- merge assignment is insertion-order greedy -> same-label identity swap.

Scenario: two 'blue mat' objects, older A = obj_0001 at x=0.00, newer B =
obj_0002 at x=0.30. One pass arrives where B moved toward A (B's detection at
x=0.28) and A moved away (A's detection at x=0.45). The detection that belongs
to the NEWER object (0.28) is closer to the OLDER object's last position
(0.00) than A's own detection (0.45) is.

Real path: semantics.SemanticStore.merge (semantics.py:259-319); iteration over
self.objs.items() in insertion order (L270); nearest-unmatched pick (L271-280).
Detection scores are used as identity tags (merge copies d.confidence onto the
matched object, L284), so the consumed detection is observable without
reimplementing the matcher.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

CLONE = Path(__file__).resolve().parent / "clone"
sys.path.insert(0, str(CLONE))
OUT = Path(__file__).resolve().parent / "artifacts" / "demo_1"
import shutil  # noqa: E402
shutil.rmtree(OUT, ignore_errors=True)      # fresh artifacts, deterministic re-runs

from config import SemanticsConfig          # noqa: E402
from scene import SemanticObject            # noqa: E402
from semantics import SemanticStore         # noqa: E402

B_SCORE, A_SCORE = 0.11, 0.99               # identity tags on the detections


def obj(label: str, x: float, y: float, score: float = 0.9) -> SemanticObject:
    return SemanticObject(id="", label=label, x=x, y=y, confidence=score)


def store(name: str) -> SemanticStore:
    cfg = replace(SemanticsConfig(), store_dir=str(OUT / name))
    return SemanticStore(cfg, name)


def report(st: SemanticStore, tag: str) -> None:
    for o in st.snapshot(0.0).objects:
        print(f"    {tag}: {o.id} x={o.x:.3f} conf={o.confidence:.2f} motion={o.motion}")


print("CLAIM 1: insertion-order greedy merge -> same-label objects can swap identities")
print(f"config: match_radius_m={SemanticsConfig().match_radius_m} "
      f"ema_alpha={SemanticsConfig().ema_alpha}\n")

print("[main scenario] older A at 0.00 (obj_0001), newer B at 0.30 (obj_0002), label 'blue mat'")
st = store("swap")
st.merge([obj("blue mat", 0.00, 1.0), obj("blue mat", 0.30, 1.0)], 0, "demo", 0.0)
report(st, "pass1")

print("pass2 detections: B moved to 0.28 (score tag 0.11), A moved to 0.45 (score tag 0.99)")
d_b, d_a = obj("blue mat", 0.28, 1.0, B_SCORE), obj("blue mat", 0.45, 1.0, A_SCORE)
print(f"    dist(B's detection 0.28 -> older A 0.00) = {abs(0.28 - 0.00):.2f} m")
print(f"    dist(A's detection 0.45 -> older A 0.00) = {abs(0.45 - 0.00):.2f} m   <- B's detection is closer")
st.merge([d_b, d_a], 0, "demo", 1.0)
report(st, "pass2")
o1, o2 = st.objs["obj_0001"], st.objs["obj_0002"]
swapped = o1.confidence == B_SCORE and o2.confidence == A_SCORE
print(f"    obj_0001 consumed {'B' if o1.confidence == B_SCORE else 'A'}'s detection; "
      f"obj_0002 consumed {'A' if o2.confidence == A_SCORE else 'B'}'s detection -> "
      f"{'SWAPPED' if swapped else 'not swapped'}")

print("\n[continuation] pass3 detections: B at 0.30 (0.11), A at 0.60 (0.99)")
st.merge([obj("blue mat", 0.30, 1.0, B_SCORE), obj("blue mat", 0.60, 1.0, A_SCORE)], 0, "demo", 2.0)
report(st, "pass3")
o1, o2 = st.objs["obj_0001"], st.objs["obj_0002"]
near1 = "B(0.30)" if abs(o1.x - 0.30) < abs(o1.x - 0.60) else "A(0.60)"
near2 = "B(0.30)" if abs(o2.x - 0.30) < abs(o2.x - 0.60) else "A(0.60)"
print(f"    truth: A at 0.60, B at 0.30. obj_0001 tracks {near1}, obj_0002 tracks {near2} "
      f"-> id/object association stays swapped across passes")

print("\n[insertion order is decisive] same detections, but B created first (B=obj_0001, A=obj_0002)")
st2 = store("swap_reversed")
st2.merge([obj("blue mat", 0.30, 1.0), obj("blue mat", 0.00, 1.0)], 0, "demo", 0.0)
st2.merge([obj("blue mat", 0.28, 1.0, B_SCORE), obj("blue mat", 0.45, 1.0, A_SCORE)], 0, "demo", 1.0)
report(st2, "pass2")
r1, r2 = st2.objs["obj_0001"], st2.objs["obj_0002"]
print(f"    obj_0001 (B, created first) consumed {'B' if r1.confidence == B_SCORE else 'A'}'s detection; "
      f"obj_0002 consumed {'A' if r2.confidence == A_SCORE else 'B'}'s detection -> no swap")

own = abs(0.45 - 0.00) + abs(0.28 - 0.30)
swap = abs(0.28 - 0.00) + abs(0.45 - 0.30)
print(f"\n[note] nearest-to-estimate cost: own pairing={own:.2f} m, swapped pairing={swap:.2f} m; "
      f"in this scenario the swapped pairing is also the min-cost one, so greediness alone is not "
      f"the only factor -- the association metric (detection vs lagging smoothed estimate) is.")

verdict = "CONFIRMED" if swapped else "REFUTED"
print(f"\nVERDICT claim 1: {verdict}")
print("incident code: semantics.py L270-280 (insertion-order loop, nearest unmatched pick, no per-pass cost check)")
