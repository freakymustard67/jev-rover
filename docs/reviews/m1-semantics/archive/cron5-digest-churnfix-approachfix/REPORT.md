# Cron run report — 2026-09-29 0118 UTC (pass #5, job 645e9ffc8cbc)

**Window:** 06:48–07:30 IST · **Type:** improvement prototypes (no master delta, no open verification items)
**Delta:** master `9c33ec0` unchanged; worktree clean; origin refs unchanged. PR #1 open/`clean`, `merged=false`, 0 comments/reviews; `updated_at` still 2026-09-28 19:57 UTC → no owner activity since cron #3.

## What ran
3 parallel subagents (`deleg_ba0dab74`), reports + evidence under this directory:
- `i5-state-digest.md` + `i5-state-digest-evidence/` — patch sha256 `73717eac…`
- `i6-churn-hysteresis.md` + `i6-churn-hysteresis-evidence/` — patch sha256 `09160940…`
- `i7-approach-standoff.md` + `i7-approach-standoff-evidence/` — patch sha256 `3b222edd…`

## Headline results
- **I5 (state digest):** `SemanticMap.jev_digest()` wired into `build_state`. Real pipeline: 20 obj 8398→**5236 B** (−37.7%), 30 obj capped at 5658 B (25 shipped + `objects_more`); bound crossing 9.4→34.9 objects; disk/log schema unchanged; bound test moved to integration path. Suite **82** (80+2). `git am` clean on fresh bec1d91 clone.
- **I6 (churn/hysteresis):** carry hysteresis + 0.7 sub-threshold decay + resurrection re-announce. Real patched merge, 100 seeds: S2 d=0.5 8.58→**3.01** IDs / vanish 4.93→**0.01**; d=0.8 12→5.96; phantom vanish **pass 3**, ls 60 (W4a §7 row said pass 2 — erratum below); S1/S3a unchanged. Suite **85**. `git am` clean.
- **I7 (approach-standoff):** F2 (no raw-goal splice) + F1 (`Planner.project_to_free()`) + rule standoff (`obj_r + 0.35`; validation floor). E2E: unpatched r=0.40 stall reproduced (0.318 m latch-fail, **658** map_brake); patched proj r=0.40 arrival 0.016 m / 6 trips; rule arms all radii 0.03–0.05 m / **0 trips**; real-pipeline arm jev 0.045 m / 0 trips. Suite **86**. `git am` clean (2 commits: fix + docs errata).

## Cron spot-checks (independently re-run by this job — all passed)
- I5 measure re-run: exact numbers reproduced (5236 @20; 5658 @30 capped; fit crossing 34.94). Suite 82 ✓.
- I6 driver re-run (10 seeds): phantom vanish pass 3 in 10/10 (ls stops at 60); S2 d=0.5 → 3.00 IDs / 0.00 vanish; S3b resurrection announced 1.00; S1 unchanged. Suite 85 ✓. Sim branch-order claim verified in source (`w4a-churn-sim.py:117-119`: HF branch unreachable past F1).
- I7 e2e re-run on both clones: unpatched exact stall reproduction (658/656/653 trips per radius, latch-fail @0.40, min_goal 0.318); patched proj/rule arms as claimed. Suite 86 ✓.
- Patch sha256s match subagent claims; repo untouched (HEAD `9c33ec0`, `git status` clean); `/tmp/opencode` untouched.

## Erratum (found this pass)
`runs/20260928-2057/w4a-churn.md` §7 summary table says the recommended fix makes the phantom "vanish @pass 2"; the correct figure for the recommended F2 decay is **pass 3** (w4a's own §5 table already lists F2 → pass 3 / ls=60). Cause: the w4a sim's `HF` branch never executed the F2 code (branch order, `w4a-churn-sim.py:117-119`), so its HF rows are F1 semantics. No effect on the I6 patch (it implements F2; measured pass 3).

## Open / next
- All three patches are branch-scoped to PR tip `bec1d91`; master needs PR #1 merged/rebased first (I6 conflicts on master without the PR sub-threshold gate; I5 schema-test hunk needs 3-way; I7 run.py context differs). Tracked as backlog **I14**.
- Owner decisions registered in LEDGER: I5 (`min_confidence` plumbing; diff ids→labels), I6 (F2-vs-F1, S3 consumer contract, carry cap), I7 (standoff default vs rule; M2 wiring consumption). S3/S6 event semantics → backlog **I15**.
- Next pass: continue improvement backlog (top open: I3, I4, I8–I15); delta-watch first as always.

## Files
- `check_pr.py`, `pr1.json`, `pr1-comments.json`, `pr1-reviews.json`, `refs.txt` — delta evidence.
- `spotcheck.sh`, `spotcheck2.sh`, `spotcheck/*` — cron re-runs (I6 driver out, I5 measure out, I7 e2e patched+base).
