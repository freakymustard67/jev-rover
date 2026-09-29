"""I1: weight-blend sensitivity on the corpus + determinism + unintended-ambiguity check.

justify 0.7/0.3 vs plain recall (1/0) and precision (0/1). Uses the patched
semantics.py tokenisation/pairing; only the blend weights change.
"""
import json
import sys

sys.path.insert(0, ".")
from config import SemanticsConfig
from scene import SemanticMap, SemanticObject
from semantics import _match_tokens, _tokens

CORPUS = "/home/freakymustard/jev-rover-research/runs/20260928-2342/i1-evidence/corpus.json"
data = json.load(open(CORPUS))
cfg = SemanticsConfig()


def rank(query, objects, wr, wp):
    q = _tokens(query)
    out = []
    for o in objects:
        w = _tokens(o.label)
        m, p = _match_tokens(q, w, cfg.fuzzy_token_cutoff)
        s = wr * (m / len(q)) + wp * (p / len(w))
        if s >= cfg.min_label_score:
            out.append((o.label, round(s, 4)))
    return sorted(out, key=lambda t: -t[1])


print("weight blend sweep (top-1 accuracy on 51-case corpus, threshold 0.34, cutoff 0.8):")
for wr, wp in [(1.0, 0.0), (0.0, 1.0), (0.5, 0.5), (0.6, 0.4), (0.7, 0.3), (0.8, 0.2), (0.9, 0.1)]:
    ok = 0
    fails = []
    for case in data["cases"]:
        objs = [SemanticObject(id=o["id"], label=o["label"], x=0, y=0, confidence=o["confidence"])
                for o in data["scenarios"][case["scenario"]]]
        ranked = rank(case["query"], objs, wr, wp)
        if case.get("expect_none"):
            good = len(ranked) == 0
        else:
            good = bool(ranked) and ranked[0][0] in case["expected"]
        ok += good
        if not good:
            fails.append(f"{case['id']}:{case['query']!r}->{ranked[0][0] if ranked else None}")
    print(f"  w_recall={wr:4} w_precision={wp:4}  acc={ok}/51={ok/51:.3f}  fails={fails if fails else 'none'}")
    if wr == 1.0:
        print("    ^ plain recall note: with a token-pair gate negatives still hold, but every")
        print("      recall=1 tie (all labels containing the token) loses the length signal;")
        print("      check specific cases: 'mat' vs 'yoga mat extra large' -> 1.0 both,")

print()
print("unintended-ambiguity check (proposed scores, expected-set size 1, >=2 candidates):")
rows = json.load(open("/home/freakymustard/jev-rover-research/runs/20260928-2342/i1-evidence/eval-results.json"))
for r in rows["proposed"]:
    if r["expect_none"] or len(r["scores"]) < 2:
        continue
    if len(r["expected"]) != 1:
        continue
    m = r["scores"][0] - r["scores"][1]
    flag = "WOULD ASK JEV" if m <= cfg.ambiguity_epsilon else "ok"
    print(f"  {r['id']:6} {r['query']!r:28} top={r['top1']!r:22} margin={m:.4f}  {flag}")

print()
print("determinism under different hash seeds (top-1 lists must be identical):")
import subprocess
script = (
    "import sys; sys.path.insert(0,'.');"
    "import json; from scene import *; from semantics import rank_candidates; from config import SemanticsConfig;"
    f"d=json.load(open({CORPUS!r}));"
    "out=[];\n"
    "for c in d['cases']:\n"
    "    objs=[SemanticObject(id=o['id'],label=o['label'],x=0,y=0,confidence=o['confidence']) for o in d['scenarios'][c['scenario']]]\n"
    "    out.append([x.obj.label for x in rank_candidates(c['query'],objs,SemanticsConfig())])\n"
    "print(json.dumps(out))"
)
outs = []
for seed in ("0", "1", "42"):
    r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                       env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin",
                            "PYTHONDONTWRITEBYTECODE": "1"})
    outs.append(r.stdout.strip())
    if r.returncode != 0:
        print(r.stderr[-500:])
        break
print(f"  3 seeds x 51 cases identical: {len(set(outs)) == 1}")
