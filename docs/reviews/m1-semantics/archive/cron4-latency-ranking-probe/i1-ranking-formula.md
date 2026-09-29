# I1 — Destination-ranking formula: asymmetric overlap + difflib fallback + plural-fold v2

**Task:** prototype in a scratch clone, quantify against the shipped Jaccard matcher on a label
corpus, produce a validated patch diff. **Master:** `9c33ec0`. **Patch:**
`i1-evidence/i1-ranking-formula.patch` (sha256 `521f996f61367eeb812655ad1afb0068a571ee1ab2b11419e388bf0a7430481c`,
61 insertions / 10 deletions across `semantics.py`, `config.py`, `README.md`).

**Result in one line:** corpus top-1 accuracy **34/51 (0.667) → 51/51 (1.000)**, 17 fixed cases,
**0 regressions**, ambiguity contract intact, **78/78 tests pass** with no test changes, patch applies
cleanly to a fresh clone.

---

## 1. Reproduced failures (executable, unpatched 9c33ec0)

Run: `cd clone && PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python i1_probe_repro.py`
(full log: `i1-evidence/repro-unpatched.txt`).

| failure | raw number | cause |
|---|---|---|
| `"bottle"` vs label `"plastic water bottle"` | Jaccard `1/3 = 0.3333 < 0.34` → **dropped** (`semantics.py:619-621` pre-patch) | symmetric Jaccard punishes verbose detector labels |
| `"mat"` vs `"yoga mat extra large"` | `1/4 = 0.25 < 0.34` → dropped | same |
| `_fold("boxes")` | `"boxe"` (`semantics.py:584-588` pre-patch) | naive trailing-`s` strip |
| `_fold("batteries")` | `"batterie"` | same |
| `_fold("glasses")` | `"glasse"` (note: the task brief said "unchanged"; the probe shows the `ss` guard tests the **whole word**, `glasses` ends `es`, so it strips) | same |
| `"chargr"`, `"chager"`, `"chargrer"`, `"batery"`, `"battry"`, `"bateries"`, `"watter bottle"` | `NO CANDIDATES → None` | no typo tolerance; difflib ratios 0.91–0.93 (probe §5) |
| `"bring me the coffee"` (bonus knife-edge found while building the corpus) | Jaccard `{bring,coffee}` vs `{coffee,mug}` = `1/3 = 0.333` → dropped | "bring" is not in `_STOP_WORDS` (`semantics.py:574-581` has `go/find/near/...` but not `bring/fetch/grab`) |

End-to-end on the unpatched clone: `resolve_destination("grab the bottle") → None`,
`("go to the boxes") → None`, `("where are the batteries") → None` (probe §7).

## 2. The proposed formula (exact semantics)

Patch hunks: `semantics.py:585-598` (fold v2), `semantics.py:616-617` (`_W_RECALL=0.7`,
`_W_PRECISION=0.3`), `semantics.py:620-643` (`_match_tokens`), `semantics.py:646-676`
(`rank_candidates` scoring), `config.py:154` + `config.py:330` (`fuzzy_token_cutoff: float = 0.80`,
validated in `[0,1]`), `README.md:146-149`.

1. **Fold v2** (deterministic, no deps): `-ies → -y` for `len>4` (`batteries→battery`); sibilant
   `-es → stem` when the stem ends in `s/x/z/ch/sh` (`boxes→box`, `glasses→glass`, `dishes→dish`);
   else trailing `-s` for `len>3`, `ss` guard kept (`mats→mat`, `kites→kite`).
2. **Asymmetric score:** `0.7·m/|q| + 0.3·p/|w|` where `m` = matched query tokens, `p` = matched
   label tokens — i.e. the plan's §4 formula `0.7·m/|q| + 0.3·m/|l|` (the `+0.2 substring` term is
   deliberately omitted; the review's recommended variant had dropped it too). Recall-dominant, so
   a verbose label is not punished; the precision term keeps a length signal.
3. **Typo fallback:** exact token matches first; remaining query tokens are paired greedily (sorted
   order, best `difflib.SequenceMatcher.ratio()`, ties → lexicographically smallest label token, a
   label token claimed at most once) against label tokens with `ratio ≥ 0.80`. Deterministic: no
   hash-order dependence (verified identical across `PYTHONHASHSEED` 0/1/42).
4. **Thresholds:** `min_label_score=0.34` and `ambiguity_epsilon=0.15` **unchanged** (new
   `fuzzy_token_cutoff=0.80` added, default config value, so existing configs keep working).

### Why 0.7/0.3 and not plain recall (`m/|q|` only)
- Plain recall ties every label containing the query token at 1.0 — `"mat"` → `blue mat 1.0`,
  `red mat 1.0`, `yoga mat extra large 1.0` (0.7/0.3 gives `0.85, 0.85, 0.775`); the rank loses all
  label-length signal and the Jev path is invoked on flat ties.
- It cannot punish verbose labels *relative to crisp ones* at equal recall: `"bottle"` → `1.0` for
  any label containing `bottle`, whatever else it says.
- Negatives stay safe under 0.7/0.3 because matching is token-pair-gated (non-matching tokens score
  nothing) — corpus sweep of `0.5/0.5 … 0.9/0.1` all give 51/51, so the blend is not
  knife-edge-sensitive; we keep the plan's own coefficients rather than invent new ones.
- **Precision-only** (`0/1`) drops to **47/51 = 0.922** (fails `grab the bottle`, `bring me the
  bottle`, `find the bottle`, `yoga mat` — verbose-label misses again), confirming recall must lead.
  Raw sweep: `i1-evidence/weights-run.txt`.

## 3. Corpus — `i1-evidence/corpus.json` (51 cases, 9 negatives)

Built from the shipped tests, the M1 plan examples (`m1-plan.md:263,407`), the REVIEW-m1 matcher
findings, realistic phrasings, morphology variants and typos; 4 scenarios (`S1_living` 6 labels,
`S2_kitchen` 6, `S3_garage` 6, `S4_typos` 4), categories: exact (14), verbose-label incl.
knife-edge (10), morphology (5), typo (9), ambiguity (1), negatives (9, incl. one typo-negative).

### Quantified (full table: `i1-evidence/eval-run.txt`, raw per-case: `eval-results.json`)

| matcher | top-1 accuracy | improvements | regressions |
|---|---|---|---|
| baseline (master Jaccard) | **34/51 = 0.667** | — | — |
| proposed | **51/51 = 1.000** | 17 | **0** |

All 17 improvements (baseline → proposed, all previously *no candidate → None*):

| id | query | proposed top-1 |
|---|---|---|
| S1-07/08/14 | grab/bring/find … "the bottle" | plastic water bottle (knife-edge, was 0.333) |
| S2-05 | "glasses" | wine glass (fold v2) |
| S2-10 | "bring me the coffee" | coffee mug (2nd knife-edge found: 0.333) |
| S3-01 | "where are the batteries" | battery (fold v2) |
| S3-02 | "go to the boxes" | cardboard box (fold v2) |
| S3-12 | "battries" | battery (fold+typo) |
| S4-01/02/03/05/07/09 | chargrer, batery, bateries, chager, battry, chargre | charger / battery (typos) |
| S4-04 | "watter bottle" | plastic water bottle (typo+verbose) |
| S4-08 | "the matt" | blue mat (typo) |

**Separation margin** (top1−top2, proposed, cases with ≥2 candidates and a single expected label):
`S1-01 0.50, S1-03 0.50, S1-11 0.35, S1-12 0.50, S1-13 0.50` — all **> 0.15**, so none would
spuriously invoke the Jev Choice. The one designed ambiguity `"mat"` keeps margin `0.0` ≤ 0.15 with
top-2 `[blue mat, red mat]` exactly as the shipped test demands. (Baseline margin stats are n=1:
Jaccard leaves most correct cases with a single candidate.)

Key score movements: `"bottle"` → `plastic water bottle` **0.80** (was 0.33, dropped);
bare `"mat"` → `0.85/0.85` (was `0.50/0.50`), `yoga mat extra large` now `0.775` (was 0.25, dropped
— see residual R4); `"chargr"` → `charger` **1.00** via fuzzy pairing; the 2nd knife-edge
`"bring me the coffee"` → `coffee mug` 0.50 (was `1/3 = 0.333`, dropped). Note: `"go to the blue mat"`
scores `1.00` under **both** matchers on this fixture — the `0.67` in the old `rank_candidates`
docstring came from the prototype's fixture and does not reproduce against the shipped code.

## 4. Tests — no changes needed

- Patched clone: `cd clone && PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python -m pytest -q -p no:cacheprovider`
  → **78 passed** (`i1-evidence/pytest-patched.txt`).
- Fresh clone of `9c33ec0` + `git apply --check` + `git apply` → **78 passed**
  (`i1-evidence/pytest-fresh-clone-patched.txt`). **Zero test updates required**; all six
  destination assertions (`tests/test_semantics_destination.py:36-70`) hold end-to-end
  (also re-verified independently in `eval-run.txt`, "shipped-test queries" section).

## 5. Patch (unified diff, `git diff` vs master)

```diff
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -143,7 +143,9 @@ the whole pipeline is testable offline; real adapters are M2.
   projection may be biased, which is the honest flag rather than a guess.
 * **Motion**: computed from the RAW per-pass displacement, never the EMA step
   (smoothing turns a 0.35 m move into 0.14 m and would miss it).
-* **Destination**: Jaccard token overlap over labels; ties within
+* **Destination**: asymmetric token overlap over labels (`0.7*recall +
+  0.3*precision`, the plan's formula) with a `difflib` fallback for typo'd
+  tokens (`fuzzy_token_cutoff`); ties within
   `ambiguity_epsilon` go to one budgeted Jev `Choice` over the candidate labels
   (code owns the options), with a deterministic fallback when no client is
   available. `approach_point` places the standoff on the rover->object line.
diff --git a/config.py b/config.py
--- a/config.py
+++ b/config.py
@@ -151,6 +151,7 @@ class SemanticsConfig:
     failure_cooldown_s: float = 10.0
     min_label_score: float = 0.34
     ambiguity_epsilon: float = 0.15
+    fuzzy_token_cutoff: float = 0.80   # difflib ratio for near-miss token pairs
     store_dir: str = "runs/semantic"
@@ -325,7 +326,8 @@ class RoomConfig:
                                     ("match_radius_m", s.match_radius_m, 1e-9, None),
                                     ("move_threshold_m", s.move_threshold_m, 1e-9, None),
                                     ("min_label_score", s.min_label_score, 0.0, 1.0),
-                                    ("ambiguity_epsilon", s.ambiguity_epsilon, 0.0, None)):
+                                    ("ambiguity_epsilon", s.ambiguity_epsilon, 0.0, None),
+                                    ("fuzzy_token_cutoff", s.fuzzy_token_cutoff, 0.0, 1.0)):
             if value < lo or (hi is not None and value > hi):
                 raise ConfigError(f"{where}.semantics.{name} out of range: {value}")
diff --git a/semantics.py b/semantics.py
--- a/semantics.py
+++ b/semantics.py
@@ -34,6 +34,7 @@ import re
 import threading
 import time
 from dataclasses import asdict, dataclass, field, replace
+from difflib import SequenceMatcher
 from pathlib import Path
 from typing import Protocol
@@ -582,9 +583,19 @@ _STOP_WORDS = {
 def _fold(word: str) -> str:
-    """Tiny plural fold; enough for 'mats' -> 'mat' without a stemmer."""
+    """Plural fold v2: '-ies' -> '-y', sibilant '-es' -> stem, else trailing 's'.
+
+    Enough for 'mats' -> 'mat', 'batteries' -> 'battery' and 'boxes' -> 'box'
+    without a stemmer; deterministic (pure string rules, no dictionary).
+    """
+    if len(word) > 4 and word.endswith("ies"):
+        return word[:-3] + "y"                      # batteries -> battery
+    if len(word) > 3 and word.endswith("es"):
+        stem = word[:-2]
+        if stem.endswith(("s", "x", "z", "ch", "sh")):
+            return stem                             # boxes -> box, glasses -> glass
     if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
-        return word[:-1]
+        return word[:-1]                            # mats -> mat, kites -> kite
     return word
@@ -598,13 +609,49 @@ class ScoredCandidate:
     score: float
+# Asymmetric token overlap (the plan's §4 formula): recall-heavy so a verbose
+# detector label ("plastic water bottle") is not punished for its extra words,
+# with a precision term so a short label is not punished by a long query.
+# Symmetric Jaccard was neither: 1/3 < 0.34 dropped "bottle" (measured).
+_W_RECALL = 0.7
+_W_PRECISION = 0.3
+
+
+def _match_tokens(q: set[str], w: set[str], cutoff: float) -> tuple[int, int]:
+    """Pair query tokens with label tokens; returns (matched q, matched w).
+
+    Exact matches first, then char-similarity (difflib) pairs gated at
+    `cutoff` for typo tolerance. Deterministic: query tokens in sorted order,
+    best ratio wins, ties broken by the lexicographically smallest label
+    token; a label token is claimed at most once.
+    """
+    exact = q & w
+    m = p = len(exact)
+    used: set[str] = set()
+    for tq in sorted(q - exact):
+        pick, pick_r = None, 0.0
+        for tw in sorted(w - exact):
+            if tw in used:
+                continue
+            r = SequenceMatcher(None, tq, tw).ratio()
+            if r >= cutoff and (pick is None or r > pick_r or (r == pick_r and tw < pick)):
+                pick, pick_r = tw, r
+        if pick is not None:
+            used.add(pick)
+            m += 1
+            p += 1
+    return m, p
+
+
 def rank_candidates(query: str, objects: list[SemanticObject],
                     cfg: SemanticsConfig | None = None) -> list[ScoredCandidate]:
-    """Jaccard token overlap, best object per label, deterministic order.
+    """Asymmetric token overlap + difflib typo fallback, best object per label.
-    Jaccard (not weighted recall) is what the prototype measured: 'go to the
-    blue mat' -> 0.67, bare 'mat' -> 0.5 for both mats (ambiguous), and a
-    non-existent object -> empty.
+    score = 0.7*matched/|q| + 0.3*matched/|w|: recall dominant, so
+    'go to the blue mat' -> 1.0, bare 'mat' -> 0.85 for both mats (ambiguous),
+    'bottle' vs 'plastic water bottle' -> 0.80 (was 0.33, dropped), and
+    'chargr' -> charger via the SequenceMatcher fallback. A non-existent
+    object still scores 0 for every label -> empty.
     """
@@ -615,8 +662,8 @@ def rank_candidates(query: str, objects: list[SemanticObject],
         if not w:
             continue
-        union = q | w
-        score = len(q & w) / len(union) if union else 0.0
+        m, p = _match_tokens(q, w, cfg.fuzzy_token_cutoff)
+        score = _W_RECALL * (m / len(q)) + _W_PRECISION * (p / len(w))
         if score < cfg.min_label_score:
             continue
```

(Reduced context in the two `@@` headers above; the byte-exact diff is
`i1-evidence/i1-ranking-formula.patch`, 136 lines.)

## 6. Residual risks (honest list)

1. **Verb/agent near-miss**: `"charge"` ~ `"charger"` ratio `0.923 ≥ 0.80` → `"charge the robot"`
   would now resolve to the `charger` object (baseline: None). Arguably acceptable/helpful for a
   fetch instruction, but it is a behaviour change not covered by the corpus. Mitigation if the
   owner objects: raise `fuzzy_token_cutoff` to 0.90 (only exact-ish typos pass; `chargr 0.923`
   still passes) or block prefix-pairs.
2. **Concatenated words** `"waterbottle"` (no space) still miss: token-pair difflib gives
   `0.625/0.705 < 0.80`. Whole-string ratio `0.957` would catch it; out of scope here (a
   `token_set`-style addition is the rapidfuzz/ M2 option per `REVIEW-m1-consolidated.md:54,173`).
3. **Fold v2 corner**: `-ies` on `-ie` nouns (`cookies → cooky`) and non-plural trailing `s`
   (`lens → len`, `campus → campu`) — both pre-existing v1 classes, unchanged severity.
4. **Bare `"mat"` on richer maps**: `yoga mat extra large` now scores `0.775 ≥ 0.34` and joins the
   Jev Choice options where before it was dropped (0.25). The shipped test fixture has no yoga mat,
   so tests are unaffected; this is more candidates for Jev, not a wrong top-1.
5. `min_label_score=0.34` is nearly inert for 1–2-token queries once a token matches (`0.7·½=0.35`);
   kept for compatibility. `ambiguity_epsilon=0.15` is unchanged but now operates on the
   recall-weighted scale (margins on the corpus are 0.35–0.50, so no drift observed).
6. difflib cost is `O(|q|·|w|)` ratio calls with short strings — negligible at matcher scale
   (labels are a handful of tokens; each call sub-millisecond).

## 7. Recommendation

Accept as the D5/I1 fix: net **+17/-0** on a 51-case corpus, 78/78 tests, zero new deps (stdlib
`difflib`), thresholds backward-compatible, deterministic. Item R1 above is the only behavioural
surprise worth a line in the PR description; R2/R3 are documented known limits, R4 is a feature.

## 8. Evidence index & exact commands

- `i1-evidence/corpus.json` — 51 cases / 4 scenarios.
- `i1-evidence/i1-ranking-formula.patch` — byte-exact `git diff` vs `9c33ec0` (sha256 above).
- `i1-evidence/eval-run.txt`, `eval-results.json` — baseline vs proposed, per-case table, margins.
- `i1-evidence/weights-run.txt` — weight sweep, unintended-ambiguity check, hash-seed determinism.
- `i1-evidence/repro-unpatched.txt`, `repro-after-patch.txt` — same probe before/after.
- `i1-evidence/pytest-patched.txt`, `pytest-fresh-clone-patched.txt` — 78/78 both.
- `i1-evidence/scripts/` — `i1_eval.py`, `i1_probe_repro.py`, `i1_weights.py`,
  `i1_baseline_semantics.py` (verbatim master `semantics.py`, `git show 9c33ec0:semantics.py`).

Commands (scratch clone `/home/freakymustard/.hermes/cache/scratch/i1/clone`, master + working patch
+ analysis scripts; a second `clone-verify` holds master + patch only):

```bash
mkdir -p /home/freakymustard/.hermes/cache/scratch/i1 && git clone https://github.com/freakymustard67/jev-rover.git /home/freakymustard/.hermes/cache/scratch/i1/clone
cd /home/freakymustard/.hermes/cache/scratch/i1/clone && git show 9c33ec0:semantics.py > i1_baseline_semantics.py
PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python i1_probe_repro.py        # failures on master
PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python i1_eval.py /home/freakymustard/jev-rover-research/runs/20260928-2342/i1-evidence/corpus.json --json-out .../eval-results.json
PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python i1_weights.py             # sweep + determinism
PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python -m pytest -q -p no:cacheprovider
git diff > /home/freakymustard/jev-rover-research/runs/20260928-2342/i1-evidence/i1-ranking-formula.patch
# fresh-clone validation
cd /home/freakymustard/.hermes/cache/scratch/i1 && git clone clone clone-verify && cd clone-verify && git apply --check .../i1-ranking-formula.patch && git apply .../i1-ranking-formula.patch && pytest -q -p no:cacheprovider
```

Read-only rule respected: nothing was written to `/home/freakymustard/jev-rover` or `/tmp/opencode`;
no git write commands run anywhere; scratch clone only.
