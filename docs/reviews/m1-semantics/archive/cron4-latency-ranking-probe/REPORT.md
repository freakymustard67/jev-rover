# Cron pass #4 — jev-rover hourly verification & improvement

- **Stamp:** 20260928-2342 (UTC) ≈ 2026-09-29 05:15–05:50 IST
- **HEAD seen:** `9c33ec0` (unchanged; local + origin). Worktree clean before/after (verified `git status --short` empty).
- **PR #1:** open, `clean`, merged=false, head `bec1d91`, 0 comments / 0 reviews, `updated_at` still 2026-09-28 19:57 UTC (no new owner activity). Artifacts: `pr1.json`, `pr1-comments.json`, `pr1-reviews.json`, `refs.txt`.
- **Scope:** V9 (latency-budget realism) + I1 (ranking formula) + I2 (probe hardening) — 3 parallel subagents + cron spot-checks.

## Sub-task summaries

1. **V9 — `v9-latency-budget.md` (+ `v9-evidence/`)**: worker-thread insulation CONFIRMED (5 s detector ⇒ 640×360 loop 14.87–15.0 Hz vs 15.0 baseline; semantics-only ≤1.5 ms/tick; 720p runs host/render-bound — `SyntheticRoom.render` ≈40 ms is a harness artifact). Costs measured (`frame.copy()` 0.39 ms, submit 0.49 ms, merge+latest.json 0.74 ms @720p). **Stale-drop coupling CONFIRMED**: `L=31 s > max_age_s=30` ⇒ every result dropped — `store.passes=0`, store dir empty, no warning (only `stats()['stale_dropped']` at exit). Budgets as documented; fail-cooldown uncounted on master (fixed only in unmerged PR #1 `ed9ddde`). Loop-rate invariant test delivered: `tests/test_semantics_loop_rate.py` (6 tests, green; tests/ dir suite 73).
2. **I1 — `i1-ranking-formula.md` (+ `i1-evidence/`)**: asymmetric `0.7·R+0.3·P` + difflib fallback (cutoff 0.80) + fold v2 (`-ies`, sibilant `-es`). Corpus 51 cases: **34/51 → 51/51**, 17 fixes, 0 regressions. Knife-edge reproduced (`bottle` 1/3=0.333<0.34; bonus `bring me the coffee`). Thresholds `0.34`/`0.15` unchanged; new `fuzzy_token_cutoff=0.80`. 78/78 tests (patched + fresh-clone apply). Patch: `i1-evidence/i1-ranking-formula.patch` sha256 `521f996f…`.
3. **I2 — `i2-probe-hardening.md` (+ `i2-evidence/`)**: mat FP mechanism exact (probe reads mat LAB `[101,144,76]` vs floor; |d|=84.02 ≥ tol 26; mat = floor cover, not elevation). **No consumer of `height_suspect` today** (merge-overwrite + JSON only) ⇒ zero control risk. `probe_px=6` = 3.00 cm top-down but 1.69–13.89 cm oblique (8.2×). FP/FN over 15 scenarios: master 10/0, PR-tip 10/0 (median = flake fix), proto 5/0, proto+2 covers 3/0. Suite 83/83. Staged D4 recommendation.

## Cron spot-checks (own re-runs)

- V9: `v9_harness.py stale --L 31` → `store_passes=0, stale_dropped=1, snapshot=false, store_files=[]` ✔; `loop --L 5.0` → 15.0 Hz, 180 ticks/12 s ✔.
- I1: re-ran `i1_eval.py` on the patched clone; counted programmatically (`count_eval.py`): baseline 34/51, proposed 51/51, 17 improvements, 0 regressions ✔; patch sha256 matches claim ✔.
- I2: re-ran `eval_rules.py` on master + proto clones: FP 10 → 3, FN 0; residual `fp_cases` match the report ✔; `height_suspect` consumer grep confirms merge/serialization/tests only ✔.
- Repo clean @ `9c33ec0` after all work.

## Decisions for owner

- **D4** (probe rule): stage 1 median+metric now (M1.5); stage 2 shadow split + cover registration for M2 opt-in; bool vs `probe_surface` field before M3.
- **D5** (ranking adoption): accept I1 patch shape? (patch ready in evidence).
- PR #1 still awaiting review/merge; R2 parked. Adoption list → BACKLOG I13.

## Notes

- Scratch clones (`~/.hermes/cache/scratch/{v9,i1,i2}`) prune ~72 h; key scripts + logs + diffs + scenario dumps copied into the three evidence dirs under this run.
- Tools used: check_pr.py (PR/refs), count_eval.py (I1 counts); both kept here for reproducibility.
