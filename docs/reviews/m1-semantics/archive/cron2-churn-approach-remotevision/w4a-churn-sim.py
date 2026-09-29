#!/usr/bin/env python3
"""w4a-churn-sim.py — appear/vanished churn of SemanticStore.merge, 10 passes @ 60 s.

Method
------
Drives the REAL `SemanticStore.merge()` (semantics.py, PR tip bec1d91) with
directly constructed `SemanticObject` detections (no FakeVision / no homography
needed: merge() consumes world-metre objects).  Every scenario is 10 passes at
t = 0, 60, ..., 540 s (cfg.semantics.audit_period_s = 60).  Per-pass position
noise sigma is Gaussian per axis; deterministic seeds.

Scenarios
  S1  stationary: 5 objects (distinct labels), sigma 0.03 / 0.10 m
  S2  mover: "teddy" displaces 0.2 / 0.5 / 0.8 / 1.2 m per pass; 2 others static
  S3  one-pass / two-pass miss of a stationary object (vanish timing)
  S4  phantom-alive pinning: pass 0 real (conf 0.9); passes >=1 a sub-threshold
      (conf 0.30 < min_confidence 0.5) detection 0.15 m away EVERY pass
  S5  dropout flicker: stationary object, detector misses with p=0.25/pass
  S6  weak-but-real: conf 0.40 (sub-threshold) every pass from the start

Sweeps: match_radius_m in {0.3, 0.5, 0.75, 1.0}; vanish_passes in {1, 2, 3};
candidate patches (below).

Candidate patches (implemented in `merge_variant`, a line-faithful copy of the
PR-tip merge; copy is asserted identical to the real merge for patch="none"):
  F1  sub-threshold matched hits do NOT refresh last_seen_s / _misses / seen
      (i.e. move the freshness block AFTER the confidence gate)
  F2  confidence decay: on sub-threshold hit prev.confidence *= 0.7; once it
      falls below min_confidence the hit stops refreshing freshness
  H1  carry hysteresis: eff radius = min(max(R, 2*last_raw_step), R+1.0),
      fresh objects carry 0
  H2  H1 but fresh objects start with carry = R (bootstrap hand-off at <= 2R)
  HF  H2 + F2 combined (the recommended policy)

Run from the scratch-clone root:
  PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python \
      /home/freakymustard/jev-rover-research/runs/20260928-2057/w4a-churn-sim.py
"""
from __future__ import annotations

import json
import math
import os
import random
import shutil
import statistics
import sys
import zlib
from dataclasses import replace

sys.path.insert(0, os.getcwd())          # run from the repo root (scratch clone)
from config import SemanticsConfig        # noqa: E402
from scene import SemanticObject, SemanticDiff   # noqa: E402
from semantics import SemanticStore, canonical_label   # noqa: E402

DT = 60.0
NPASSES = 10                             # t = 0 .. 540 s
NSEEDS = 100
STORE_ROOT = "/home/freakymustard/.hermes/cache/scratch/w4a/simstore"
OUT_JSON = "/home/freakymustard/.hermes/cache/scratch/w4a/w4a-churn-results.json"
OUT_TABLES = "/home/freakymustard/.hermes/cache/scratch/w4a/w4a-churn-tables.md"

CARRY_FACTOR = 2.0                       # H1/H2: eff = factor * last raw step
CARRY_CAP = 1.0                          # H1/H2: never more than R + cap
DECAY = 0.7                              # F2: confidence decay per sub-thr. hit


def mkcfg(tag: str, **kw) -> SemanticsConfig:
    kw.setdefault("store_dir", os.path.join(STORE_ROOT, tag))
    return replace(SemanticsConfig(), **kw)


def det(label: str, x: float, y: float, conf: float = 0.9) -> SemanticObject:
    return SemanticObject(id="", label=label, x=round(float(x), 3),
                          y=round(float(y), 3), confidence=round(float(conf), 3))


# --------------------------------------------------------------- merge copy

def merge_variant(store: SemanticStore, objects: list[SemanticObject], rejected: int,
                  model: str, t_pass: float, patch: str = "none") -> object:
    """PR-tip merge with the candidate patch hooks. Line-faithful when patch='none'."""
    store.passes += 1
    store.model = model
    store.last_pass_t = t_pass
    store.rejected_total += rejected
    diff = SemanticDiff()
    unmatched = list(objects)
    seen: set[str] = set()
    ema = store.cfg.ema_alpha

    pairs: list[tuple[float, str, int]] = []
    det_labels = [canonical_label(d.label) for d in unmatched]
    for oid, prev in store.objs.items():
        prev_label = canonical_label(prev.label)
        for i, d in enumerate(unmatched):
            if det_labels[i] != prev_label:
                continue
            eff_r = store.cfg.match_radius_m
            if patch in ("H1", "H2", "HF"):
                carry = store._step.get(oid, 0.0)
                eff_r = min(max(store.cfg.match_radius_m, CARRY_FACTOR * carry),
                            store.cfg.match_radius_m + CARRY_CAP)
            dd = math.hypot(d.x - prev.x, d.y - prev.y)
            if dd <= eff_r:
                pairs.append((dd, oid, i))
    pairs.sort(key=lambda p: p[0])

    taken: set[int] = set()
    for _, oid, i in pairs:
        if oid in seen or i in taken:
            continue
        d = unmatched[i]
        taken.add(i)
        prev = store.objs[oid]
        sub = d.confidence < store.cfg.min_confidence
        if sub and patch in ("F1", "HF"):
            # unconfirmed hit: no freshness refresh; object counts as unseen
            continue
        if sub and patch in ("F2", "HF"):
            prev.confidence = round(prev.confidence * DECAY, 3)
            if prev.confidence < store.cfg.min_confidence:
                continue                      # faded: counts as unseen
        prev.last_seen_s = round(t_pass, 2)
        store._misses[oid] = 0
        seen.add(oid)
        if sub:
            continue                          # freshness only (baseline behaviour)
        prev_x, prev_y = prev.x, prev.y
        prev.x = (1.0 - ema) * prev.x + ema * d.x
        prev.y = (1.0 - ema) * prev.y + ema * d.y
        prev.confidence = d.confidence
        hist = store._hs_hist.setdefault(oid, [])
        hist.append(bool(d.height_suspect))
        del hist[:-3]
        if sum(hist) >= 2:
            prev.height_suspect = True
        elif len(hist) - sum(hist) >= 2:
            prev.height_suspect = False
        raw = math.hypot(d.x - prev_x, d.y - prev_y)
        prev.motion = "moved" if raw > store.cfg.move_threshold_m else "static"
        if prev.motion == "moved":
            diff.moved.append(oid)
        if patch in ("H1", "H2", "HF"):
            store._step[oid] = raw

    unmatched = [d for i, d in enumerate(unmatched) if i not in taken]

    for d in unmatched:
        oid = f"obj_{store.next_id:04d}"
        store.next_id += 1
        obj = replace(d, id=oid, first_seen_s=round(t_pass, 2),
                      last_seen_s=round(t_pass, 2), sources=list(d.sources))
        store.objs[oid] = obj
        store._misses[oid] = 0
        store._labels[oid] = obj.label
        if patch in ("H2", "HF"):
            store._step[oid] = store.cfg.match_radius_m      # bootstrap carry
        elif patch in ("H1",):
            store._step[oid] = 0.0
        if obj.confidence >= store.cfg.min_confidence:
            diff.appeared.append(oid)
        seen.add(oid)

    evicted: list[str] = []
    for oid in store.objs:
        if oid in seen:
            continue
        store._misses[oid] = store._misses.get(oid, 0) + 1
        if store._misses[oid] == store.cfg.vanish_passes:
            diff.vanished.append(oid)
        if store._misses[oid] >= store.cfg.max_misses:
            evicted.append(oid)
    for oid in evicted:
        del store.objs[oid]
        store._misses.pop(oid, None)
        store._labels.pop(oid, None)
        store._hs_hist.pop(oid, None)

    store.diff = diff
    return store.snapshot(t_pass)


# ----------------------------------------------------------------- scenarios
# generator: g(k, rng) -> list[(label, x, y, conf)]  (pre-noise base detections)

def gen_s1(k, rng):
    return [("mat", 1.0, 1.0, 0.9), ("ball", 3.5, 1.0, 0.9), ("box", 1.0, 3.0, 0.9),
            ("cup", 3.5, 3.0, 0.9), ("charger", 2.25, 2.0, 0.9)]


def gen_s2(d):
    def g(k, rng):
        return [("teddy", 0.6 + d * k, 2.5, 0.9), ("mat", 1.0, 1.0, 0.9),
                ("box", 1.0, 3.0, 0.9)]
    return g


def gen_s3(miss_passes):
    def g(k, rng):
        if k in miss_passes:
            return []
        return [("mat", 2.0, 2.0, 0.9)]
    return g


def gen_s4(k, rng):
    """Pass 0: the real object. Later: only a sub-threshold ghost 0.15 m away."""
    if k == 0:
        return [("mat", 2.0, 2.0, 0.9)]
    return [("mat", 2.15, 2.0, 0.30)]


def gen_s5(k, rng):
    """Detector recall noise: present with p=0.75."""
    if rng.random() < 0.75:
        return [("mat", 2.0, 2.0, 0.9)]
    return []


def gen_s6(k, rng):
    """A genuinely present object the model only ever detects at conf 0.40."""
    return [("mat", 2.0, 2.0, 0.40)]


# --------------------------------------------------------------------- runner

def seed_for(name: str, s: int) -> int:
    return (zlib.crc32(name.encode()) ^ (s * 2654435761)) & 0xFFFFFFFF


def run_once(name, gen, sigma, s, *, radius=0.5, vp=2, patch="none",
             validate: bool = False):
    """One 10-pass run. Returns metrics dict (+validation of the copy if asked)."""
    rng = random.Random(seed_for(name, s))
    cfg = mkcfg(f"{name}_r{radius}_v{vp}_{patch}",
                match_radius_m=radius, vanish_passes=vp)
    st = SemanticStore(cfg, "sim")
    st._step = {}

    st_val = None
    if validate:
        st_val = SemanticStore(cfg, "sim")
        st_val._step = {}

    vanished_ids = set()
    resurrection_ids = set()
    per_pass = []                        # (appeared, vanished, moved) counts
    life = {}                            # oid -> [birth, last_seen_pass, death, evicted]
    created_label = {}                   # oid -> label at creation (survives eviction)
    deaths = {}                          # oid -> pass index where vanished fired
    max_objs = 0

    for k in range(NPASSES):
        t = k * DT
        dets = []
        for label, x, y, conf in gen(k, rng):
            if sigma > 0.0:
                x += rng.gauss(0.0, sigma)
                y += rng.gauss(0.0, sigma)
            d = det(label, x, y, conf)
            dets.append(d)

        if patch == "none":
            m = st.merge(dets, 0, "sim", t)
            if validate:
                m_val = merge_variant(st_val, [replace(d) for d in dets], 0, "sim", t, "none")
                assert (list(m.diff.appeared), list(m.diff.vanished), list(m.diff.moved)) == \
                       (list(m_val.diff.appeared), list(m_val.diff.vanished), list(m_val.diff.moved)), \
                    f"copy diverged (diff) {name} seed {s} pass {k}"
                a = {o.id: (o.x, o.y, o.confidence, o.last_seen_s) for o in m.objects}
                b = {o.id: (o.x, o.y, o.confidence, o.last_seen_s) for o in m_val.objects}
                assert a == b, f"copy diverged (objects) {name} seed {s} pass {k}: {a} != {b}"
        else:
            m = merge_variant(st, dets, 0, "sim", t, patch)

        for oid in m.diff.appeared:
            life.setdefault(oid, [k, k, None, False])
        for oid in m.diff.vanished:
            if oid in life:
                life[oid][2] = k
            deaths.setdefault(oid, k)
            vanished_ids.add(oid)
        for oid, o in st.objs.items():
            rec = life.setdefault(oid, [k, k, None, False])
            created_label.setdefault(oid, o.label)
            if o.last_seen_s == round(t, 2):
                rec[1] = k
                if oid in vanished_ids and oid not in resurrection_ids:
                    resurrection_ids.add(oid)
        per_pass.append((len(m.diff.appeared), len(m.diff.vanished), len(m.diff.moved)))
        max_objs = max(max_objs, len(st.objs))

    mover_label = canonical_label("teddy")
    mover_ids = {oid for oid, lbl in created_label.items()
                 if canonical_label(lbl) == mover_label}

    alive_end = len(st.objs)
    new_ids = st.next_id - 1
    lifetimes = []
    for oid, (birth, last, death, ev) in life.items():
        end = death if death is not None else NPASSES - 1
        lifetimes.append(max(0, end - birth))
    max_last_seen_end = max((o.last_seen_s for o in st.objs.values()), default=0.0)

    return {
        "name": name, "patch": patch, "radius": radius, "vp": vp, "sigma": sigma,
        "seed": s,
        "new_ids": new_ids,
        "appeared": sum(p[0] for p in per_pass),
        "vanished": sum(p[1] for p in per_pass),
        "moved": sum(p[2] for p in per_pass),
        "per_pass": per_pass,
        "first_vanish_pass": (min(deaths.values()) if deaths else None),
        "vanish_passes_seen": sorted({p for p in deaths.values()}),
        "deaths": deaths,
        "alive_end": alive_end,
        "silent_resurrections": len(resurrection_ids),
        "mover_label_ids": len(mover_ids),
        "id_lifetimes": lifetimes,
        "max_objs": max_objs,
        "max_last_seen_end": max_last_seen_end,
        "vanished_ids_total": len(vanished_ids),
    }


def run_seeds(name, gen, sigma, *, seeds=NSEEDS, radius=0.5, vp=2, patch="none",
              validate=False):
    runs = [run_once(name, gen, sigma, s, radius=radius, vp=vp, patch=patch,
                     validate=(validate and s < 5))
            for s in range(seeds)]
    def mn(key):
        return statistics.fmean(r[key] for r in runs)
    def mx(key):
        return max(r[key] for r in runs)
    agg = {
        "name": name, "patch": patch, "radius": radius, "vp": vp, "sigma": sigma,
        "seeds": seeds,
        "new_ids_mean": mn("new_ids"), "new_ids_max": mx("new_ids"),
        "appeared_mean": mn("appeared"), "vanished_mean": mn("vanished"),
        "moved_mean": mn("moved"), "moved_max": mx("moved"),
        "alive_end_mean": mn("alive_end"),
        "resurrections_mean": mn("silent_resurrections"),
        "lifetime_mean": (statistics.fmean(l for r in runs for l in r["id_lifetimes"])
                          if any(r["id_lifetimes"] for r in runs) else None),
        "lifetime_min": (min(l for r in runs for l in r["id_lifetimes"])
                         if any(r["id_lifetimes"] for r in runs) else None),
        "lifetime_max": (max(l for r in runs for l in r["id_lifetimes"])
                         if any(r["id_lifetimes"] for r in runs) else None),
        "first_vanish_pass_hist": {},
        "mover_ids_mean": mn("mover_label_ids"),
        "max_objs_mean": mn("max_objs"),
        "max_last_seen_end_mean": mn("max_last_seen_end"),
    }
    for r in runs:
        if r["first_vanish_pass"] is not None:
            agg["first_vanish_pass_hist"][r["first_vanish_pass"]] = \
                agg["first_vanish_pass_hist"].get(r["first_vanish_pass"], 0) + 1
    # per-pass event counts summed across seeds (mean)
    agg["per_pass_mean"] = [
        [statistics.fmean(r["per_pass"][k][j] for r in runs) for j in range(3)]
        for k in range(NPASSES)]
    return agg, runs


# ----------------------------------------------------------------- reporting

def fmt(x, nd=2):
    return "n/a" if x is None else f"{x:.{nd}f}"


def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def trace_lines(name, gen, sigma, seed=0, *, radius=0.5, vp=2, patch="none"):
    rng = random.Random(seed_for(name, seed))
    cfg = mkcfg(f"trace_{name}", match_radius_m=radius, vanish_passes=vp)
    st = SemanticStore(cfg, "sim")
    st._step = {}
    out = [f"\n### trace {name} patch={patch} R={radius} vp={vp} sigma={sigma} seed={seed}\n",
           "| pass | t (s) | appeared | vanished | moved | objects (id label@x,y conf last_seen) |",
           "|---|---|---|---|---|---|"]
    for k in range(NPASSES):
        t = k * DT
        dets = []
        for label, x, y, conf in gen(k, rng):
            if sigma > 0.0:
                x += rng.gauss(0.0, sigma)
                y += rng.gauss(0.0, sigma)
            dets.append(det(label, x, y, conf))
        m = (st.merge(dets, 0, "sim", t) if patch == "none"
             else merge_variant(st, dets, 0, "sim", t, patch))
        objs = " ".join(
            f"{o.id}:{o.label}@{o.x:.2f},{o.y:.2f} c{o.confidence:.2f} ls{o.last_seen_s:.0f}"
            for o in sorted(m.objects, key=lambda o: o.id))
        out.append(f"| {k} | {t:.0f} | {m.diff.appeared} | {m.diff.vanished} | "
                   f"{m.diff.moved} | {objs} |")
    return out


def main():
    shutil.rmtree(STORE_ROOT, ignore_errors=True)
    os.makedirs(STORE_ROOT, exist_ok=True)
    results = []
    by_key = {}

    def rec(name, gen, sigma, *, radius=0.5, vp=2, patch="none", seeds=NSEEDS,
            validate=False, label=""):
        agg, runs = run_seeds(name, gen, sigma, seeds=seeds, radius=radius,
                              vp=vp, patch=patch, validate=validate)
        agg["label"] = label or name
        results.append(agg)
        by_key[(name, patch, radius, vp, sigma)] = agg
        return agg, runs

    # ---- headline baseline (R=0.5, vp=2), copy validated against real merge
    base = []
    for sigma in (0.03, 0.10):
        base.append(rec(f"S1-stationary-s{sigma:.2f}", gen_s1, sigma, validate=True,
                        label=f"S1 stationary sigma={sigma:.2f}")[0])
    for d in (0.2, 0.5, 0.8, 1.2):
        for sigma in (0.03, 0.10):
            base.append(rec(f"S2-mover-d{d}-s{sigma:.2f}", gen_s2(d), sigma,
                            validate=True,
                            label=f"S2 mover {d} m/pass sigma={sigma:.2f}")[0])
    base.append(rec("S3a-onepass-miss", gen_s3({3}), 0.03, validate=True,
                    label="S3a one-pass miss @pass 3")[0])
    base.append(rec("S3b-twopass-miss", gen_s3({3, 4}), 0.03, validate=True,
                    label="S3b two-pass miss @pass 3-4")[0])
    base.append(rec("S4a-phantom-alive", gen_s4, 0.03, validate=True,
                    label="S4a phantom-alive (ghost 0.30)")[0])
    base.append(rec("S4b-phantom-alive-s0.10", gen_s4, 0.10, validate=True,
                    label="S4b phantom-alive, sigma=0.10")[0])
    for vp in (1, 2, 3):
        rec(f"S5-dropout-vp{vp}", gen_s5, 0.03, vp=vp,
            label=f"S5 detector dropout p=.25, vp={vp}")
    rec("S6-weak-real", gen_s6, 0.03, validate=True,
        label="S6 weak-but-real conf 0.40")

    # ---- radius sweep {0.3, 0.75, 1.0} on S1 / S2 / S4 (0.5 already in base)
    for radius in (0.3, 0.75, 1.0):
        rec(f"S1-stationary-s0.10-R{radius}", gen_s1, 0.10, radius=radius,
            label=f"S1 sigma=0.10 R={radius}")
        for d in (0.2, 0.5, 0.8, 1.2):
            for sigma in (0.03, 0.10):
                rec(f"S2-mover-d{d}-s{sigma:.2f}-R{radius}", gen_s2(d), sigma,
                    radius=radius, label=f"S2 d={d} sigma={sigma:.2f} R={radius}")
        rec(f"S4a-phantom-R{radius}", gen_s4, 0.03, radius=radius,
            label=f"S4a phantom R={radius}")

    # ---- vanish_passes sweep {1, 3} on S3a / S3b (vp=2 in base)
    for vp in (1, 3):
        rec(f"S3a-onepass-miss-vp{vp}", gen_s3({3}), 0.03, vp=vp,
            label=f"S3a one-pass miss vp={vp}")
        rec(f"S3b-twopass-miss-vp{vp}", gen_s3({3, 4}), 0.03, vp=vp,
            label=f"S3b two-pass miss vp={vp}")

    # ---- candidate patches
    for patch, scen, sigmas in (
        ("F1", "S1", (0.10,)),
        ("F1", "S2", (0.03,)),
        ("F1", "S4a", (0.03,)),
        ("F2", "S2", (0.03,)),
        ("F2", "S4a", (0.03,)),
        ("F2", "S6", (0.03,)),
        ("H1", "S1", (0.10,)),
        ("H1", "S2", (0.03, 0.10)),
        ("H2", "S2", (0.03, 0.10)),
        ("HF", "S1", (0.03, 0.10)),
        ("HF", "S2", (0.03, 0.10)),
        ("HF", "S3a", (0.03,)),
        ("HF", "S4a", (0.03,)),
        ("HF", "S4b", (0.10,)),
        ("HF", "S6", (0.03,)),
    ):
        if scen == "S1":
            genf = gen_s1
        elif scen == "S2":
            for d in (0.2, 0.5, 0.8, 1.2):
                for sigma in sigmas:
                    rec(f"S2-mover-d{d}-s{sigma:.2f}-{patch}", gen_s2(d), sigma,
                        patch=patch, label=f"S2 d={d} sigma={sigma:.2f} {patch}")
            continue
        elif scen == "S3a":
            genf = gen_s3({3})
        elif scen == "S6":
            genf = gen_s6
        else:
            genf = gen_s4
        for sigma in sigmas:
            rec(f"{scen}-s{sigma:.2f}-{patch}", genf, sigma, patch=patch,
                label=f"{scen} sigma={sigma:.2f} {patch}")

    with open(OUT_JSON, "w") as fh:
        json.dump(results, fh, indent=1, sort_keys=True)

    # ------------------------------------------------------------- tables
    lines = []

    def emit(s):
        print(s)
        lines.append(s)

    emit("## T1 — baseline (R=0.5 m, vp=2, 100 seeds, 10 passes @60 s)\n")
    rows = []
    for name, scenario, sigma in [
        ("S1-stationary-s0.03", "S1 stationary, sigma=0.03", 0.03),
        ("S1-stationary-s0.10", "S1 stationary, sigma=0.10", 0.10),
        ("S2-mover-d0.2-s0.03", "S2 mover d=0.2, sigma=0.03", 0.03),
        ("S2-mover-d0.2-s0.10", "S2 mover d=0.2, sigma=0.10", 0.10),
        ("S2-mover-d0.5-s0.03", "S2 mover d=0.5, sigma=0.03", 0.03),
        ("S2-mover-d0.5-s0.10", "S2 mover d=0.5, sigma=0.10", 0.10),
        ("S2-mover-d0.8-s0.03", "S2 mover d=0.8, sigma=0.03", 0.03),
        ("S2-mover-d0.8-s0.10", "S2 mover d=0.8, sigma=0.10", 0.10),
        ("S2-mover-d1.2-s0.03", "S2 mover d=1.2, sigma=0.03", 0.03),
        ("S2-mover-d1.2-s0.10", "S2 mover d=1.2, sigma=0.10", 0.10),
        ("S3a-onepass-miss", "S3a one-pass miss (pass 3)", 0.03),
        ("S3b-twopass-miss", "S3b two-pass miss (pass 3-4)", 0.03),
        ("S4a-phantom-alive", "S4a phantom-alive (sigma=0.03)", 0.03),
        ("S4b-phantom-alive-s0.10", "S4b phantom-alive (sigma=0.10)", 0.10),
        ("S6-weak-real", "S6 weak-but-real conf=0.40", 0.03),
    ]:
        a = by_key[(name, "none", 0.5, 2, sigma)]
        rows.append([
            scenario, fmt(a["appeared_mean"]), fmt(a["vanished_mean"]),
            fmt(a["moved_mean"]), fmt(a["new_ids_mean"], 1),
            str(a["first_vanish_pass_hist"] or "—"),
            fmt(a["alive_end_mean"], 1), fmt(a["lifetime_mean"], 1),
            fmt(a["max_last_seen_end_mean"], 0),
        ])
    emit(table(["scenario", "appeared", "vanished", "moved", "new IDs",
                "1st-vanish pass histogram", "objs alive@end", "id lifetime (passes)",
                "newest last_seen (s)"], rows))

    emit("\n## T2 — mover ID continuity (S2, R=0.5)\n")
    rows = []
    for d in (0.2, 0.5, 0.8, 1.2):
        for sigma in (0.03, 0.10):
            a = by_key[(f"S2-mover-d{d}-s{sigma:.2f}", "none", 0.5, 2, sigma)]
            rows.append([f"d={d}", f"sigma={sigma:.2f}", fmt(a["new_ids_mean"], 1),
                         fmt(a["new_ids_max"], 0), fmt(a["mover_ids_mean"], 1),
                         fmt(a["vanished_mean"], 1),
                         " ".join(f"{fmt(p[0],1)}/{fmt(p[1],1)}/{fmt(p[2],1)}"
                                  for p in a["per_pass_mean"][1:])])
    emit(table(["per-pass step", "sigma", "new IDs mean", "new IDs max",
                "IDs on mover", "vanished", "per-pass appeared/vanished/moved (passes 1-9, mean)"], rows))

    emit("\n## T3 — match_radius sweep (100 seeds)\n")
    rows = []
    for radius in (0.3, 0.5, 0.75, 1.0):
        a = by_key[("S1-stationary-s0.10" if radius == 0.5 else f"S1-stationary-s0.10-R{radius}",
                    "none", radius, 2, 0.10)]
        cells = [f"newIDs {fmt(a['new_ids_mean'], 1)} van {fmt(a['vanished_mean'], 1)}"]
        for d in (0.2, 0.5, 0.8, 1.2):
            a = by_key[(f"S2-mover-d{d}-s0.03" if radius == 0.5 else f"S2-mover-d{d}-s0.03-R{radius}",
                        "none", radius, 2, 0.03)]
            cells.append(f"d{d}: newIDs {fmt(a['new_ids_mean'], 1)}, van {fmt(a['vanished_mean'], 1)}")
        a = by_key[("S4a-phantom-alive" if radius == 0.5 else f"S4a-phantom-R{radius}",
                    "none", radius, 2, 0.03)]
        cells.append(f"S4a alive@end {fmt(a['alive_end_mean'], 1)}, van {fmt(a['vanished_mean'], 1)}")
        rows.append([f"R={radius}"] + cells)
    emit(table(["radius", "S1 sig.10", "S2 d=0.2", "S2 d=0.5", "S2 d=0.8", "S2 d=1.2",
                "S4a phantom"], rows))

    emit("\n## T4 — vanish_passes sweep (flicker; 100 seeds)\n")
    rows = []
    for vp in (1, 2, 3):
        for tag, label, nm in (
            ("S3a-onepass-miss", "S3a 1-pass miss",
             "S3a-onepass-miss" if vp == 2 else f"S3a-onepass-miss-vp{vp}"),
            ("S3b-twopass-miss", "S3b 2-pass miss",
             "S3b-twopass-miss" if vp == 2 else f"S3b-twopass-miss-vp{vp}"),
            ("S5-dropout", "S5 dropout p=.25", f"S5-dropout-vp{vp}"),
        ):
            a = by_key[(nm, "none", 0.5, vp, 0.03)]
            rows.append([f"vp={vp}", label, fmt(a["vanished_mean"]),
                         str(a["first_vanish_pass_hist"] or "—"),
                         fmt(a["resurrections_mean"])])
    emit(table(["vanish_passes", "scenario", "vanished events", "1st-vanish hist",
                "silent resurrections"], rows))

    emit("\n## T5a — sub-threshold policies on S4a / S6 (100 seeds, R=0.5, vp=2)\n")
    rows = []
    for scen, sigma, getkey in (
        ("S4a phantom-alive", 0.03,
         lambda patch: ("S4a-phantom-alive" if patch == "none" else f"S4a-s0.03-{patch}")),
        ("S6 weak-but-real conf .40", 0.03,
         lambda patch: ("S6-weak-real" if patch == "none" else f"S6-s0.03-{patch}")),
    ):
        for policy in ("none", "F1", "F2", "HF"):
            key = (getkey(policy), "none" if policy == "none" else policy, 0.5, 2, sigma)
            if key not in by_key:
                continue
            a = by_key[key]
            rows.append([scen, "baseline" if policy == "none" else policy,
                         fmt(a["vanished_mean"]), str(a["first_vanish_pass_hist"] or "—"),
                         fmt(a["alive_end_mean"], 1), fmt(a["max_last_seen_end_mean"], 0)])
    emit(table(["scenario", "policy", "vanished events", "1st-vanish hist",
                "objs alive@end", "newest last_seen (s)"], rows))

    emit("\n## T5b — mover policies on S2 (100 seeds, R=0.5, vp=2)\n")
    rows = []
    for d in (0.2, 0.5, 0.8, 1.2):
        for sigma in (0.03, 0.10):
            for policy in ("none", "H1", "H2", "HF"):
                nm = (f"S2-mover-d{d}-s{sigma:.2f}" if policy == "none"
                      else f"S2-mover-d{d}-s{sigma:.2f}-{policy}")
                key = (nm, "none" if policy == "none" else policy, 0.5, 2, sigma)
                if key not in by_key:
                    continue
                a = by_key[key]
                rows.append([f"d={d}", f"sig={sigma:.2f}",
                             "baseline" if policy == "none" else policy,
                             fmt(a["new_ids_mean"], 1), fmt(a["new_ids_max"], 0),
                             fmt(a["mover_ids_mean"], 1), fmt(a["vanished_mean"], 1),
                             fmt(a["moved_mean"], 1)])
    emit(table(["per-pass step", "sigma", "policy", "new IDs mean", "new IDs max",
                "IDs on mover", "vanished", "moved"], rows))

    emit("\n## T5c — noise side-effects on S1 (100 seeds, R=0.5, vp=2)\n")
    rows = []
    for sigma in (0.03, 0.10):
        for policy in ("none", "H1", "HF"):
            nm = (f"S1-stationary-s{sigma:.2f}" if policy == "none"
                  else f"S1-s{sigma:.2f}-{policy}")
            key = (nm, "none" if policy == "none" else policy, 0.5, 2, sigma)
            if key not in by_key:
                continue
            a = by_key[key]
            rows.append([f"sigma={sigma:.2f}", "baseline" if policy == "none" else policy,
                         fmt(a["appeared_mean"]), fmt(a["vanished_mean"]),
                         fmt(a["moved_mean"]), fmt(a["new_ids_mean"], 1)])
    emit(table(["noise", "policy", "appeared", "vanished", "moved", "new IDs"], rows))

    for ln in trace_lines("S2-mover-d0.5", gen_s2(0.5), 0.03, seed=0):
        emit(ln)
    for ln in trace_lines("S2-mover-d0.5-HF", gen_s2(0.5), 0.03, seed=0, patch="HF"):
        emit(ln)
    for ln in trace_lines("S4a-phantom-alive", gen_s4, 0.03, seed=0):
        emit(ln)
    for ln in trace_lines("S4a-phantom-alive-F2", gen_s4, 0.03, seed=0, patch="F2"):
        emit(ln)
    for ln in trace_lines("S3b-twopass-miss", gen_s3({3, 4}), 0.03, seed=0):
        emit(ln)

    with open(OUT_TABLES, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"\n[tables] {OUT_TABLES}\n[raw]    {OUT_JSON}")


if __name__ == "__main__":
    main()