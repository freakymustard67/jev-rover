#!/usr/bin/env python3
"""CLAIM 3 -- no eviction: an object unmatched for 50 passes is never removed and
can resurrect with the same id.

Real path: semantics.SemanticStore.merge (semantics.py:259-319): misses are
counted (L308-313) and a 'vanished' diff event fires exactly once at
vanish_passes, but nothing is ever deleted from self.objs. The stale object also
stays visible in snapshots and resolvable as a destination.
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

CLONE = Path(__file__).resolve().parent / "clone"
sys.path.insert(0, str(CLONE))
OUT = Path(__file__).resolve().parent / "artifacts" / "demo_3"
import shutil  # noqa: E402
shutil.rmtree(OUT, ignore_errors=True)      # events.jsonl is append-only; start clean

from config import SemanticsConfig                     # noqa: E402
from scene import SemanticObject                       # noqa: E402
from semantics import SemanticStore, resolve_destination  # noqa: E402


def obj(label: str, x: float, y: float, score: float = 0.9) -> SemanticObject:
    return SemanticObject(id="", label=label, x=x, y=y, confidence=score)


cfg = replace(SemanticsConfig(), store_dir=str(OUT / "ghost"))
st = SemanticStore(cfg, "ghost")
print("CLAIM 3: no eviction; unmatched for 50 passes -> still present, can resurrect with same id")
print(f"config: vanish_passes={cfg.vanish_passes}\n")

st.merge([obj("blue mat", 3.0, 1.2)], 0, "demo", 0.0)
print("pass 1: object obj_0001 'blue mat' at (3.0, 1.2)")
for i in range(1, 51):
    st.merge([], 0, "demo", float(i))          # 50 consecutive passes with no detections

snap = st.snapshot(51.0)
print(f"after 50 empty passes: passes={snap.passes} objects_in_map={len(snap.objects)}")
o = snap.objects[0]
print(f"    {o.id} label={o.label!r} pos=({o.x},{o.y}) last_seen_s={o.last_seen_s} "
      f"misses={st._misses[o.id]} next_id={st.next_id}")

events = [json.loads(line) for line in (OUT / "ghost" / "events.jsonl").read_text().splitlines()]
van = [(e["t"], e["vanished"]) for e in events if e["vanished"]]
print(f"    events.jsonl: vanished fired {len(van)} time(s): {van} "
      f"(fires once at miss #{cfg.vanish_passes}, never again; object never removed)")

dest = resolve_destination("blue mat", snap)
print(f"    resolve_destination('blue mat') still resolves the 50-pass-stale object: "
      f"label={dest.label!r} at ({dest.x},{dest.y}) object_id={dest.object_id}")

m = st.merge([obj("blue mat", 3.05, 1.2)], 0, "demo", 52.0)
o2 = m.objects[0]
print(f"\npass 52: detection at (3.05, 1.2) -> matched object id={o2.id} "
      f"(same as the original: {o2.id == 'obj_0001'}), appeared={m.diff.appeared} moved={m.diff.moved} "
      f"misses_reset={st._misses[o2.id]} next_id={st.next_id}")
print("    -> the id was never retired, so the 'vanished' object silently came back under the same id")

ok = (len(snap.objects) == 1 and o.id == "obj_0001" and o2.id == "obj_0001"
      and len(van) == 1 and st.next_id == 2)
print(f"\nVERDICT claim 3: {'CONFIRMED' if ok else 'REFUTED'}")
print("incident code: semantics.py L308-313 (misses/diff only), no eviction anywhere in SemanticStore")
