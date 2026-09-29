#!/usr/bin/env python3
"""i6-churn-driver.py — I6 validation harness: drive the REAL SemanticStore.merge().

Adapted from runs/20260928-2057/w4a-churn-sim.py (scenario generators, seed
derivation and metric definitions are kept identical so baseline numbers are
directly comparable to the w4a report §3 tables), but this script never
re-implements merge(): it imports the module from the checkout named by
$I6_REPO (default: cwd) so the patched-vs-baseline comparison runs against
whatever code is checked out at tip.

Scenarios (names exactly as in the w4a sim => same RNG streams per seed):
  S1 stationary sigma=0.03 / 0.10        S3a one-pass miss @pass 3
  S2 mover d=0.2/0.5/0.8/1.2 sigma=0.03  S3b two-pass miss @pass 3-4
  S2 mover d=0.5/0.8 sigma=0.10          S4a phantom (sigma 0.03)
  S4b phantom (sigma 0.10)

Extra metrics vs the sim: `resurrections_announced` — a resurrection that also
reports the returning id in diff.appeared that pass (the S3 event-log gap).

Deterministic: fixed seeds via seed_for(name, s); 100 seeds default.
Run from anywhere:
  PYTHONDONTWRITEBYTECODE=1 <venv-python> i6-churn-driver.py \
      --repo /path/to/scratch-clone --tag patched --out results-patched.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys
import zlib
from dataclasses import replace

DT = 60.0
NPASSES = 10
NSEEDS = 100


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.environ.get("I6_REPO", os.getcwd()),
                    help="checkout to import config/scene/semantics from")
    ap.add_argument("--tag", default="run", help="label for this run (baseline|patched)")
    ap.add_argument("--out", default="", help="write full JSON results here")
    ap.add_argument("--seeds", type=int, default=NSEEDS)
    ap.add_argument("--store-root",
                    default=os.environ.get("I6_STORE_ROOT",
                                           "/home/freakymustard/.hermes/cache/scratch/i6/simstore"))
    args = ap.parse_args()

    sys.path.insert(0, args.repo)
    from config import SemanticsConfig                      # noqa: E402
    from scene import SemanticObject                        # noqa: E402
    from semantics import SemanticStore, canonical_label    # noqa: E402

    def mkcfg(tag: str, **kw) -> SemanticsConfig:
        kw.setdefault("store_dir", os.path.join(args.store_root, args.tag, tag))
        return replace(SemanticsConfig(), **kw)

    def det(label: str, x: float, y: float, conf: float = 0.9) -> SemanticObject:
        return SemanticObject(id="", label=label, x=round(float(x), 3),
                              y=round(float(y), 3), confidence=round(float(conf), 3))

    # ------------------------------------------------------------- scenarios
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
        if k == 0:
            return [("mat", 2.0, 2.0, 0.9)]
        return [("mat", 2.15, 2.0, 0.30)]

    def gen_s6(k, rng):
        """A genuinely present object the model only ever detects at conf 0.40."""
        return [("mat", 2.0, 2.0, 0.40)]

    def seed_for(name: str, s: int) -> int:
        return (zlib.crc32(name.encode()) ^ (s * 2654435761)) & 0xFFFFFFFF

    # ----------------------------------------------------------------- runs
    def run_once(name, gen, sigma, s, *, radius=0.5, vp=2):
        rng = random.Random(seed_for(name, s))
        cfg = mkcfg(f"{name}_r{radius}_v{vp}", match_radius_m=radius, vanish_passes=vp)
        st = SemanticStore(cfg, "sim")

        vanished_ids: set[str] = set()
        resurrections: set[str] = set()          # last_seen refreshed after a vanish
        announced_resurrections: set[str] = set()  # ...and reported in diff.appeared
        per_pass = []
        life: dict[str, list] = {}
        created_label: dict[str, str] = {}
        deaths: dict[str, int] = {}

        for k in range(NPASSES):
            t = k * DT
            dets = []
            for label, x, y, conf in gen(k, rng):
                if sigma > 0.0:
                    x += rng.gauss(0.0, sigma)
                    y += rng.gauss(0.0, sigma)
                dets.append(det(label, x, y, conf))
            m = st.merge(dets, 0, "sim", t)

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
                    if oid in vanished_ids and oid not in resurrections:
                        resurrections.add(oid)
                        if oid in m.diff.appeared:
                            announced_resurrections.add(oid)
            per_pass.append((len(m.diff.appeared), len(m.diff.vanished), len(m.diff.moved)))

        mover_label = canonical_label("teddy")
        mover_ids = {oid for oid, lbl in created_label.items()
                     if canonical_label(lbl) == mover_label}
        lifetimes = []
        for oid, (birth, last, death, ev) in life.items():
            end = death if death is not None else NPASSES - 1
            lifetimes.append(max(0, end - birth))
        max_last_seen_end = max((o.last_seen_s for o in st.objs.values()), default=0.0)
        return {
            "name": name, "sigma": sigma, "seed": s,
            "new_ids": st.next_id - 1,
            "appeared": sum(p[0] for p in per_pass),
            "vanished": sum(p[1] for p in per_pass),
            "moved": sum(p[2] for p in per_pass),
            "per_pass": per_pass,
            "first_vanish_pass": (min(deaths.values()) if deaths else None),
            "alive_end": len(st.objs),
            "resurrections": len(resurrections),
            "resurrections_announced": len(announced_resurrections),
            "mover_label_ids": len(mover_ids),
            "id_lifetimes": lifetimes,
            "max_last_seen_end": max_last_seen_end,
        }

    def run_seeds(name, gen, sigma, *, seeds, radius=0.5, vp=2):
        runs = [run_once(name, gen, sigma, s, radius=radius, vp=vp) for s in range(seeds)]
        agg = {
            "name": name, "sigma": sigma, "seeds": seeds,
            "new_ids_mean": statistics.fmean(r["new_ids"] for r in runs),
            "new_ids_max": max(r["new_ids"] for r in runs),
            "appeared_mean": statistics.fmean(r["appeared"] for r in runs),
            "vanished_mean": statistics.fmean(r["vanished"] for r in runs),
            "moved_mean": statistics.fmean(r["moved"] for r in runs),
            "alive_end_mean": statistics.fmean(r["alive_end"] for r in runs),
            "resurrections_mean": statistics.fmean(r["resurrections"] for r in runs),
            "resurrections_announced_mean": statistics.fmean(
                r["resurrections_announced"] for r in runs),
            "mover_ids_mean": statistics.fmean(r["mover_label_ids"] for r in runs),
            "max_last_seen_end_mean": statistics.fmean(r["max_last_seen_end"] for r in runs),
            "first_vanish_pass_hist": {},
        }
        for r in runs:
            if r["first_vanish_pass"] is not None:
                h = agg["first_vanish_pass_hist"]
                h[r["first_vanish_pass"]] = h.get(r["first_vanish_pass"], 0) + 1
        return agg, runs

    cfglist = [
        ("S1-stationary-s0.03", gen_s1, 0.03),
        ("S1-stationary-s0.10", gen_s1, 0.10),
        ("S2-mover-d0.2-s0.03", gen_s2(0.2), 0.03),
        ("S2-mover-d0.5-s0.03", gen_s2(0.5), 0.03),
        ("S2-mover-d0.5-s0.10", gen_s2(0.5), 0.10),
        ("S2-mover-d0.8-s0.03", gen_s2(0.8), 0.03),
        ("S2-mover-d0.8-s0.10", gen_s2(0.8), 0.10),
        ("S2-mover-d1.2-s0.03", gen_s2(1.2), 0.03),
        ("S3a-onepass-miss", gen_s3({3}), 0.03),
        ("S3b-twopass-miss", gen_s3({3, 4}), 0.03),
        ("S4a-phantom-alive", gen_s4, 0.03),
        ("S4b-phantom-alive-s0.10", gen_s4, 0.10),
        ("S6-weak-but-real", gen_s6, 0.03),
    ]

    results, runs_all = [], {}
    for name, gen, sigma in cfglist:
        agg, runs = run_seeds(name, gen, sigma, seeds=args.seeds)
        results.append(agg)
        runs_all[name] = runs

    hdr = (f"{'scenario':26} {'newIDs(mean/max)':>16} {'appeared':>9} {'vanished':>9} "
           f"{'moved':>7} {'alive@end':>9} {'resurr/announced':>17} {'moverIDs':>9} "
           f"{'maxLS@end':>9} firstvanish")
    print(f"== I6 churn driver — tag={args.tag} repo={args.repo} ==")
    print(hdr)
    for a in results:
        print(f"{a['name']:26} {a['new_ids_mean']:7.2f}/{a['new_ids_max']:<2d}         "
              f"{a['appeared_mean']:9.2f} {a['vanished_mean']:9.2f} {a['moved_mean']:7.2f} "
              f"{a['alive_end_mean']:9.2f} {a['resurrections_mean']:8.2f}/{a['resurrections_announced_mean']:<6.2f} "
              f"{a['mover_ids_mean']:9.2f} {a['max_last_seen_end_mean']:9.1f} "
              f"{a['first_vanish_pass_hist']}")

    out = {"tag": args.tag, "repo": args.repo, "seeds": args.seeds,
           "configs": results, "runs": runs_all}
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(out, fh, indent=1, sort_keys=True)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
