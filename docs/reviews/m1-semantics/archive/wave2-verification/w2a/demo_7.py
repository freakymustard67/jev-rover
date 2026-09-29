#!/usr/bin/env python3
"""CLAIM 7 -- two consecutive passes displaced 0.2 m each leave motion='static'
although total drift is 0.4 m.

Real path: semantics.SemanticStore.merge (semantics.py:281-291): the EMA update
happens first, then `raw = |detection - previous SMOOTHED position|` (L290) is
compared with move_threshold_m (L291, strict >). The claim's literal reading is
a straight-line drift of 0.2 m per pass; the script also runs the two readings
that CAN stay 'static' (oscillation; steps measured from the current estimate).

Config: ema_alpha=0.4, move_threshold_m=0.25 (defaults).
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

CLONE = Path(__file__).resolve().parent / "clone"
sys.path.insert(0, str(CLONE))
OUT = Path(__file__).resolve().parent / "artifacts" / "demo_7"
import shutil  # noqa: E402
shutil.rmtree(OUT, ignore_errors=True)      # fresh artifacts, deterministic re-runs

from config import SemanticsConfig          # noqa: E402
from scene import SemanticObject            # noqa: E402
from semantics import SemanticStore         # noqa: E402

ALPHA, THR = SemanticsConfig().ema_alpha, SemanticsConfig().move_threshold_m


def obj(x: float, y: float = 1.0) -> SemanticObject:
    return SemanticObject(id="", label="red box", x=x, y=y, confidence=0.9)


def store(name: str) -> SemanticStore:
    return SemanticStore(replace(SemanticsConfig(), store_dir=str(OUT / name)), name)


def run(tag: str, dets: list[float]) -> list[tuple[float, float, float, str]]:
    st = store(tag)
    st.merge([obj(0.0)], 0, "demo", 0.0)
    rows = []
    for i, d in enumerate(dets):
        prev = st.objs["obj_0001"].x                       # smoothed position the code compares against
        m = st.merge([obj(d)], 0, "demo", float(i + 1))
        o = m.objects[0]
        rows.append((d, abs(d - prev), o.x, o.motion))
        print(f"    pass {i + 1}: detection x={d:.2f}  derived raw=|{d:.2f}-{prev:.3f}|={abs(d - prev):.3f} m "
              f"-> pos={o.x:.3f} motion={o.motion} diff.moved={m.diff.moved}")
    return rows


print("CLAIM 7: two consecutive 0.2 m passes leave motion='static' (total drift 0.4 m)")
print(f"config: ema_alpha={ALPHA} move_threshold_m={THR}\n")

print("[A] literal reading: straight-line drift, 0.2 m per pass (total drift 0.4 m)")
rows = run("straight", [0.2, 0.4])
static_both = all(r[3] == "static" for r in rows[:2])
print(f"    -> motions={[r[3] for r in rows[:2]]}; total drift from start = 0.4 m; "
      f"both passes static? {static_both}")

print("\n[B] oscillation reading: +0.2 m then -0.2 m (0.4 m travelled, net drift 0)")
rows_b = run("osc", [0.2, 0.0])
print(f"    -> motions={[r[3] for r in rows_b]}; 0.4 m travelled, net drift 0.0 m")

print("\n[C] estimate-relative reading: each detection sits 0.2 m ahead of the current estimate")
rows_c = run("estrel", [0.2, 0.28])
print(f"    -> motions={[r[3] for r in rows_c]}; detection stream walked 0.28 m net over two passes")

verdict = "REFUTED" if not static_both else "CONFIRMED"
print(f"\nVERDICT claim 7: {verdict} (as literally stated: net 0.4 m straight drift does NOT stay static)")
print("    - the 1st 0.2 m step reads 'static' by design (0.2 < threshold 0.25); the 2nd step reads "
      f"raw 0.320 m > 0.25 and is flagged 'moved', because raw is measured against the LAGGING "
      "smoothed estimate (semantics.py L281-291).")
print("    - 'static/static' holds only for oscillating or estimate-relative steps (cases B/C), where "
      "net drift is not 0.4 m.")
print("incident code: semantics.py L281-291 (raw vs previous smoothed position, per-pass threshold)")
