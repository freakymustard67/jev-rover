"""I1 eval: current (master Jaccard) vs proposed (asymmetric + difflib + fold v2).

Baseline ranking is imported from `i1_baseline_semantics.py` — a verbatim copy
of master 9c33ec0 semantics.py (`git show 9c33ec0:semantics.py`). Proposed is
the patched `semantics.py` in this clone. Run: cwd = clone.
"""
import argparse
import importlib.util
import json
import sys

sys.path.insert(0, ".")
from config import SemanticsConfig
from scene import SemanticMap, SemanticObject


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


import semantics as proposed  # noqa: E402  (patched)
baseline = _load("i1_baseline_semantics.py", "i1_baseline_semantics")


def objects_of(scenario):
    return [SemanticObject(id=o["id"], label=o["label"], x=0.0, y=0.0,
                           confidence=o["confidence"]) for o in scenario]


def top1(ranked):
    return ranked[0].obj.label if ranked else None


def run(rank_fn, cfg, cases, scenarios):
    rows = []
    for case in cases:
        sem = SemanticMap(passes=1, model="corpus",
                          objects=objects_of(scenarios[case["scenario"]]))
        ranked = rank_fn(case["query"], sem.objects, cfg)
        labels = [c.obj.label for c in ranked]
        scores = [round(c.score, 4) for c in ranked]
        if case.get("expect_none"):
            ok = len(ranked) == 0
            t1 = None
        else:
            t1 = top1(ranked)
            ok = t1 in case["expected"]
        margin = round(scores[0] - scores[1], 4) if len(scores) >= 2 else None
        rows.append({"id": case["id"], "query": case["query"],
                     "category": case["category"], "expected": case.get("expected"),
                     "expect_none": bool(case.get("expect_none")),
                     "top1": t1, "labels": labels, "scores": scores,
                     "margin": margin, "ok": ok})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    data = json.load(open(args.corpus))
    cases, scenarios = data["cases"], data["scenarios"]
    cfg = SemanticsConfig()

    base_rows = run(baseline.rank_candidates, cfg, cases, scenarios)
    prop_rows = run(proposed.rank_candidates, cfg, cases, scenarios)

    n = len(cases)
    b_acc = sum(r["ok"] for r in base_rows)
    p_acc = sum(r["ok"] for r in prop_rows)
    print(f"corpus: {n} cases ({sum(1 for c in cases if c.get('expect_none'))} negatives), "
          f"min_label_score={cfg.min_label_score}, fuzzy_token_cutoff={cfg.fuzzy_token_cutoff}")
    print(f"top-1 accuracy  baseline(Jaccard) = {b_acc}/{n} = {b_acc/n:.3f}")
    print(f"top-1 accuracy  proposed(0.7/0.3+difflib+foldv2) = {p_acc}/{n} = {p_acc/n:.3f}")
    print()

    hdr = f"{'id':6} {'cat':22} {'query':26} {'baseline':>10} {'proposed':>24} {'b_ok':>4} {'p_ok':>4} {'margin_b':>8} {'margin_p':>8} {'delta'}"
    print(hdr)
    print("-" * len(hdr))
    regressions, improvements = [], []
    for b, p in zip(base_rows, prop_rows):
        tag = ""
        if not b["ok"] and p["ok"]:
            tag = "FIXED"
            improvements.append((p["id"], p["query"], b["top1"], p["top1"]))
        elif b["ok"] and not p["ok"]:
            tag = "REGRESSION"
            regressions.append((p["id"], p["query"], b["top1"], p["top1"]))
        elif not b["ok"] and not p["ok"]:
            tag = "STILL-BAD"
        b_top = "None" if b["expect_none"] else str(b["top1"])
        p_top = "None" if p["expect_none"] else str(p["top1"])
        print(f"{p['id']:6} {p['category']:22} {p['query']:26} {b_top:>10} {p_top:>24} "
              f"{str(b['ok']):>4} {str(p['ok']):>4} {str(b['margin']):>8} {str(p['margin']):>8} {tag}")

    print()
    print(f"improvements (baseline wrong -> proposed right): {len(improvements)}")
    for i in improvements:
        print(f"  + {i[0]} {i[1]!r}: {i[2]} -> {i[3]}")
    print(f"regressions (baseline right -> proposed wrong): {len(regressions)}")
    for r in regressions:
        print(f"  - {r[0]} {r[1]!r}: {r[2]} -> {r[3]}")

    print()
    print("separation margin (top1-top2), correct non-negative cases only:")
    for label, rows in (("baseline", base_rows), ("proposed", prop_rows)):
        ms = [r["margin"] for r in rows if r["ok"] and not r["expect_none"] and r["margin"] is not None]
        if ms:
            print(f"  {label}: n={len(ms)} mean={sum(ms)/len(ms):.4f} min={min(ms):.4f} "
                  f"max={max(ms):.4f}")

    # B1-05 ambiguity contract: bare 'mat' must keep top2 = blue/red mat, |d|<=0.15
    print()
    print("ambiguity contract (S1-05 'mat'), proposed:")
    pr = [r for r in prop_rows if r["id"] == "S1-05"][0]
    print(f"  top1={pr['top1']} labels={pr['labels']} scores={pr['scores']} margin={pr['margin']} "
          f"within_eps={abs(pr['scores'][0]-pr['scores'][1]) <= cfg.ambiguity_epsilon}")

    # end-to-end resolve checks replicating the shipped test assertions
    print()
    print("shipped-test queries end-to-end (patched resolve_destination, jev=None):")
    sem = SemanticMap(passes=1, model="fake", objects=objects_of(scenarios["S1_living"]))
    # S1 has 6 objects incl. yoga mat / bottle extras; shipped test fixture is the first 4 + charger
    shipped = [o for o in sem.objects if o.id in ("obj_0001", "obj_0002", "obj_0003", "obj_0004")]
    if not any(o.label == "plastic water bottle" for o in shipped):
        shipped.append(SemanticObject(id="obj_0005", label="plastic water bottle", x=6.0, y=1.0, confidence=0.75))
    sem2 = SemanticMap(passes=1, model="fake", objects=shipped)
    for q, want in [("go to the blue mat", "blue mat"), ("find the llama", None),
                    ("the blue box", "blue box"), ("where is the charger", "charger"),
                    ("tidy the garage", None)]:
        d = proposed.resolve_destination(q, sem2, jev=None, cfg=cfg)
        got = d.label if d else None
        print(f"  resolve({q!r:26}) -> {got!r:>12}  want={want!r:>12}  {'OK' if got == want else 'MISMATCH'}")

    if args.json_out:
        json.dump({"baseline": base_rows, "proposed": prop_rows,
                   "summary": {"n": n, "baseline_accuracy": b_acc / n,
                               "proposed_accuracy": p_acc / n,
                               "improvements": [i[0] for i in improvements],
                               "regressions": [r[0] for r in regressions]}},
                  open(args.json_out, "w"), indent=1)
        print(f"\njson written: {args.json_out}")


if __name__ == "__main__":
    main()
