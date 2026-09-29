"""I1 probe 1: reproduce knife-edge + morphology/typo failures on UNPATCHED code.

Runs inside the scratch clone. Read-only w.r.t. the real repo.
"""
import sys
from difflib import SequenceMatcher

sys.path.insert(0, ".")
from config import SemanticsConfig
from scene import SemanticMap, SemanticObject
from semantics import _fold, _tokens, rank_candidates, resolve_destination

OBJS = [
    SemanticObject(id="obj_0001", label="blue mat", x=3.2, y=1.1, confidence=0.9),
    SemanticObject(id="obj_0002", label="red mat", x=1.0, y=2.0, confidence=0.8),
    SemanticObject(id="obj_0003", label="blue box", x=4.5, y=2.3, confidence=0.85),
    SemanticObject(id="obj_0004", label="charger", x=5.0, y=3.0, confidence=0.7),
    SemanticObject(id="obj_0005", label="plastic water bottle", x=6.0, y=1.0, confidence=0.75),
    SemanticObject(id="obj_0006", label="yoga mat extra large", x=2.0, y=4.0, confidence=0.6),
    SemanticObject(id="obj_0007", label="battery", x=1.5, y=1.5, confidence=0.8),
    SemanticObject(id="obj_0008", label="cardboard box", x=7.0, y=2.0, confidence=0.7),
    SemanticObject(id="obj_0009", label="wine glass", x=8.0, y=0.5, confidence=0.6),
]
SEM = SemanticMap(passes=1, model="probe", objects=OBJS)

print("== 1) knife-edge: Jaccard vs min_label_score=0.34 ==")
for q in ["bottle", "bring me the bottle", "mat", "box", "glass", "water bottle"]:
    ranked = rank_candidates(q, SEM.objects, SemanticsConfig())
    print(f"  query={q!r:26} -> {[(c.obj.label, round(c.score,3)) for c in ranked] or 'NO CANDIDATES -> None'}")

print()
print("== 2) raw Jaccard numbers for the knife-edge pair ==")
q, w = _tokens("bottle"), _tokens("plastic water bottle")
print(f"  tokens('bottle')={sorted(q)} tokens('plastic water bottle')={sorted(w)}")
print(f"  |q&w|={len(q & w)}  |q|w|={len(q | w)}  Jaccard={len(q & w)/len(q | w):.4f}  < 0.34 -> dropped")
q2, w2 = _tokens("mat"), _tokens("yoga mat extra large")
print(f"  tokens('mat')={sorted(q2)} tokens('yoga mat extra large')={sorted(w2)} Jaccard={len(q2 & w2)/len(q2 | w2):.4f}")

print()
print("== 3) morphology failures (_fold on unpatched code) ==")
for word in ["boxes", "batteries", "glasses", "chargers", "dishes", "mats", "kites"]:
    print(f"  fold({word!r}) = {_fold(word)!r}")

print()
print("== 4) typo queries -> no candidates (require difflib fallback) ==")
for q in ["chargr", "chager", "chargrer", "batery", "battry", "bateries", "watter bottle", "plasic water bottle", "waterbottle"]:
    ranked = rank_candidates(q, SEM.objects, SemanticsConfig())
    print(f"  query={q!r:24} -> {[(c.obj.label, round(c.score,3)) for c in ranked] or 'NO CANDIDATES -> None'}")

print()
print("== 5) difflib.SequenceMatcher ratios: positive typo pairs ==")
pos = [("chargr", "charger"), ("chager", "charger"), ("chargrer", "charger"),
       ("batery", "battery"), ("battry", "battery"), ("bateries", "battery"),
       ("watter", "water"), ("plasic", "plastic"), ("boxe", "box"), ("socks", "sock")]
for a, b in pos:
    r = SequenceMatcher(None, a, b).ratio()
    print(f"  ratio({a!r}, {b!r}) = {r:.4f}  {'>=0.80' if r >= 0.80 else ('>=0.75' if r >= 0.75 else '< 0.75')}")

print()
print("== 6) difflib ratios: negative pairs (must stay below cutoff) ==")
neg = [("garage", "charger"), ("garage", "charge"), ("llama", "mat"), ("llama", "blue"),
       ("umbrella", "blue"), ("helicopter", "workbench"), ("helicopter", "charger"),
       ("swimming", "sink"), ("pool", "toolbox"), ("robot", "workbench"), ("robot", "toolbox"),
       ("tidy", "table"), ("floor", "toolbox"), ("charge", "charger"), ("hallway", "glass")]
for a, b in neg:
    r = SequenceMatcher(None, a, b).ratio()
    print(f"  ratio({a!r}, {b!r}) = {r:.4f}  {'!! ABOVE 0.75' if r >= 0.75 else 'ok (<0.75)'}")

print()
print("== 7) resolve_destination end-to-end on unpatched code ==")
for q in ["go to the blue mat", "find the llama", "the blue box", "where is the charger",
          "mat", "tidy the garage", "grab the bottle", "go to the boxes", "where are the batteries"]:
    d = resolve_destination(q, SEM, jev=None, cfg=SemanticsConfig())
    print(f"  resolve({q!r:28}) -> {d.label if d else None}")
