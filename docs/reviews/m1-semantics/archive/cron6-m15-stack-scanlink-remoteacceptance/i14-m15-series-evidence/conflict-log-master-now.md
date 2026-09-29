# Conflict log — series vs master 9c33ec0 (master-now) and post-PR-merge

All attempts via `git am -3` in a scratch clone at 9c33ec0 (master tip == 9c33ec0;
master has 0 commits since the merge-base with review/m1-semantics-audit; the
review branch is 14 commits ahead). Python: /home/freakymustard/jev-rover/.venv/bin/python (3.11.16).

## A. master-now (9c33ec0), series applied directly

### i5 (0001) — CONFLICT (1 file, 2 hunks), RESOLVED
- `git am -3 i5-state-digest.patch` → rc=128, `CONFLICT (content)` in
  `tests/test_semantics_schema.py` (UU). Other files (scene.py, tactics.py,
  tests/test_semantics_integration.py) auto-merge clean.
- Conflict hunks:
  - lines 89–93: `<<<<<<< HEAD` old test def `test_build_state_carries_semantics_and_stays_bounded`
    vs incoming new test def `test_build_state_ships_the_compacted_semantics_digest`.
  - lines 101–141: HEAD old test body (asserts `size < 6000`) vs incoming new
    assertions (raw 11-field disk schema + new digest tests).
- Nature: **context-shift only** — master still carries the pre-review region
  (incl. obsolete bounded test) that the 14 review commits rewrote; the patch is
  expressed against the post-review text.
- Resolution used (recorded): `git checkout --theirs -- tests/test_semantics_schema.py`,
  `git add`, `GIT_EDITOR=true git am --continue` → rc=0, commit df2935e.

### i6 (0002) — CONFLICT (3 files, 6 hunks), NOT resolved (moot post-merge)
- rc=128, `UU config.py`, `UU semantics.py`, `UU tests/test_semantics_merge.py`.
- Conflict hunks (marker lines):
  - `config.py`: 149–155 (new carry/sub-threshold config fields added into a defaults block).
  - `semantics.py`: 255–259 (`best_i, best_d` line vs carry-hysteresis block),
    276–287 (label matching vs carry loop), 292–331, 369–381 (sub-threshold decay /
    resurrection logic regions).
  - `tests/test_semantics_merge.py`: 111–206 (one large block).
- Nature: **context-shift + semantic overlap** with the not-yet-merged review
  commits (both sides edit merge internals: master = pre-review M1 merge loop,
  patch = post-review + i6). Not mechanically resolvable by ours/theirs; needs a
  manual intent merge. Obsolete once PR #1 merges.
- Not resolved; `git am --abort` (back to i5 commit df2935e).

### i7 (0003+0004) — commit 1 CLEAN, commit 2 FAILS (droppable)
- Commit 1 (`a1d0ed2` semantics: project approach goals…): applied cleanly via
  `git am -3` — run.py and config.py auto-merged. (The i7 report's
  `git apply --check` failure on run.py was **2-way only**; 3-way am resolves it.)
- Commit 2 (`7bbd21a` docs errata): rc=128, `CONFLICT (modify/delete)` —
  `docs/reviews/m1-semantics/m2-design.md` deleted in HEAD (absent on master) and
  modified by the patch. **Docs-only commit; recommend dropping on any master-based landing**
  (dropping it does not change test counts; it adds no tests and 0 code lines).
- Not resolved; `git am --abort`.

## B. Post-PR-merge master (simulated)

- `git checkout -B postmerge origin/master` (9c33ec0) + `git merge --no-ff origin/review/m1-semantics-audit`
  → merge commit ec6b914; verified merged tree == bec1d91 tree (`git diff --stat bec1d91` empty).
- `git am -3` all three patches, in order: **all 4 commits applied cleanly (rc=0)**, commits a2fd4bf,
  13e58ae, b0fd27c, d6983ed.
- Suite on that tree: **93 passed in 15.55 s** (suite-postmerge-series.log).

## C. Stacked on review branch now (the delivery path used for this task)

- Fresh clone at prtip bec1d91; sha256-verified patches; `git am -3` i5→i6→i7 all clean,
  0 conflicts; suite 80 → 82 → 87 → 93 (suite-after-*.log). No resolution needed.

## Classification summary

| Patch | master-now | class | post-PR-merge |
|-------|-----------|-------|---------------|
| i5 | 1 file / 2 hunks conflict | context-shift (pre-review region) | clean |
| i6 | 3 files / 6 hunks conflict | context-shift + semantic overlap (needs intent merge) | clean |
| i7 | commit1 clean; commit2 modify/delete | commit2 inherent (file absent on master), droppable (docs-only) | clean (4 commits) |
