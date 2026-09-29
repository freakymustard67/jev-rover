Review complete — read-only; repo `git status` clean, `PYTHONDONTWRITEBYTECODE=1`, probes in scratch (`/home/freakymustard/.hermes/cache/scratch/taskA4/probe_rank.py`), destination tests re-run green (5 passed).

## Verdicts (shipped code vs plan)

**1. Gating/budget/fingerprint — the semantics Choice bypasses all of it (dormant in M1).**
- Option ownership matches the house pattern (criteria keys = code-built option list): `mission.py:36-44` (routes + `no_match`), `tactics.py:115` (`criteria=MANEUVERS`), `semantics.py:632-642` (candidate labels). ✓
- Tactician machinery: when-to-ask `decision_needed`, fingerprint `_key` + skip, `min_dt`, `attempts < budget=400`, one-slot queue on a worker thread, stats — `tactics.py:189-203,254-292,300-353`. `_ask_jev_label` (`semantics.py:628-651`) uses **none** of it: no budget, no rate limit, no fingerprint/cache, **synchronous**, no stats accounting. Its docstring "One budgeted Choice" (`semantics.py:629`) and the commit message overstate: nothing budgets it; proposal §9 isn't met.
- Mitigation: run.py passes `jev=None` (`run.py:299`), so the path is test-only (`tests/test_semantics_destination.py:50-57`). M2 wiring must (a) share the Tactician's client/budget or reuse the runner's own budget/cooldown, (b) run it on the semantics worker — as written it would block the perception branch — (c) cache by `(text, sorted labels)` like `_key`.

**2. Matcher robustness — fine for the headline queries, brittle for detector-style labels.**
Measured shipped: `"go to the blue mat"` → blue mat 1.0; `"find the water bottle"` → water bottle 1.0. But:
- Jaccard punishes verbose labels: `"bottle"` vs `"plastic water bottle"` = 0.333 < 0.34 → **dropped**; `"mat"` vs `"yoga mat extra large"` = 0.25 → dropped. Wrong bias when labels come from a detector.
- Plan §4 table (`m1-plan.md:263`) is **stale**: it specifies `0.7·m/|q| + 0.3·m/|l| + 0.2 substring`; shipped is Jaccard (`semantics.py:601-625`, `min_label_score=0.34` at `config.py:152`) per the plan's own fold-in note (`m1-plan.md:407-408`). Under the plan formula "bottle/plastic water bottle" scores 0.8 — the shipped code gave up.
- No typo tolerance: `"chargr"`, `"waterbottle"` → 0 candidates → None (difflib ratio 0.923/0.957).
- Plural fold only `-s` (`semantics.py:584-588`): `"boxes"`→`"boxe"` → 0.
- No reusable mission text parsing exists — `mission.py` routes via LLM Choice over route names; `--instruction` is logged only.
- Plan §6 test description ("blue mat" vs "blue mat large" within epsilon) is wrong under **both** formulas (1.0 vs 0.667 shipped; 1.2 vs 0.9 plan); the shipped test correctly uses bare `"mat"` (tie 0.5/0.5 → Choice). Plan text needs correcting.

**Ranked alternatives**
1. **Recommended (now, zero deps, ~20 lines):** restore asymmetric overlap `0.7·|q∩w|/|q| + 0.3·|q∩w|/|w|` (the plan's formula) + `-es/-ies` fold + a `difflib` char-ratio fallback for empty/below-threshold token matches. Fixes the measured misses; keep Jev Choice for ambiguity/ties; re-tune thresholds (they shift).
2. **rapidfuzz** (`token_set_ratio`/`WRatio`): best quality (word order, subsets, typos), but adds a compiled dep to a 3-dep project (`requirements.txt:1-4`) and forces threshold rescaling to 0–100. Right at M2 when real labels arrive.
3. **Embeddings via LLM budget: reject/defer** — the SDK surface in use is Choice/Noul/Score (`tactics.py:23`); the label Choice already is the semantic arbiter and would need the same nonexistent budget plumbing.
4. Keep Jaccard as-is: survivable for M1 fixtures, but the knife-edge (`1/3` vs `0.34`) and length penalty will bite with real labels.

**3. Re-export — no cycle, but inert.** `mission.py:24`; chain mission→semantics→{config,perception,scene} (`semantics.py:43-45`, `perception.py:29-41`); nothing imports mission (grep). Caveats: importing mission now pulls cv2/numpy; `mission.main()` never calls `resolve_destination` (`mission.py:56-89`), and there are no retry/Noul stubs despite plan lines 245-247 — the re-export is for external importers only.

**4. `--find` — plan's acceptance command cannot run.** Verified: `--semantics fake --semantics-once --find "blue mat"` → `SystemExit("--mission goto needs --waypoint NAME")` from `run.py:47-49` (mission defaults to `goto`, `run.py:173`; GoalManager built unconditionally, `run.py:232`). Fix the acceptance text (add `--mission patrol` or a waypoint) or give `--find` a benign mission default. Also `--semantics-once` is parsed (`run.py:191-192`) but never read — inert. `find_done` latches after one attempt even on failure (`run.py:298-311`) — matches "retry in M2", but the message doesn't hint at label typos. Otherwise print-only, no conflict; runner auto-enables with `--find` (`run.py:242`).

**5. `approach_point` — matches the plan, one real degeneracy.** Code `dest − unit(dest−from)·standoff` (`semantics.py:676-682`) ≡ plan formula. Exact overlap → `dest` (tested, `test_semantics_destination.py:79-80`). **standoff > distance flips the point to the far side of the destination**: from (3.2,1.0), dest (3.0,1.0), standoff 0.35 → (3.35,1.0) — beyond the object, no guard. Recommend `if length <= standoff: return dest`, and M2 validating the point against polygon/planner inflation before driving. Print-only in M1, so latent only.