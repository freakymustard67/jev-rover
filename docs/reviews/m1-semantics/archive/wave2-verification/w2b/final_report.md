# Final report — M1 semantics fix package (wave2/w2b)

**Task:** produce a review-ready fix package for the M1 semantics layer as
proposed unified diffs, verified to apply and keep the suite green in a scratch
clone, with the real repo untouched.

## Outcome

- **12 fixes implemented**, one git commit each in `clone/`, exported as
  `patches/0001…0012-*.diff` (`git format-patch --suffix=.diff`).
- **Fresh-clone verification:** `verify/` is a second clone of
  `/home/freakymustard/jev-rover`; `git am ../patches/*.diff` applied all 12
  cleanly and the suite is green there too.
- **Suite:** unpatched 78 collected/78 passed (67 product + 11 stray prototype
  tests). Patched: **80 product tests, 80 passed** with `testpaths = tests`;
  +13 new test functions. The 11 stray `docs/planning/prototype` tests are no
  longer collected by default but still pass when run explicitly.
- **No new dependencies** (stdlib + existing numpy/cv2; the merge assignment is
  distance-ordered greedy, not Hungarian/scipy).
- **Real repo `/home/freakymustard/jev-rover` never modified** (read-only clone
  source + its venv interpreter); no pushes, no PRs; `/tmp/opencode` untouched.

## Fixes (detail in `patches.md`: what/why/risk/evidence)

| # | Fix | Diff |
|---|---|---|
| 1 | distance-ordered best-first merge assignment | 0001 |
| 2 | `min_confidence` gate on position/flags; freshness kept | 0002 |
| 3 | `height_suspect` 2-of-3 hysteresis | 0003 |
| 4 | `max_misses` eviction cap + state-bound guard | 0004 |
| 5 | label canonicalisation (matching + adapter filters) | 0005 |
| 6 | restored "centres < 0.15 m" dedupe clause | 0006 |
| 7 | probe median patch 7×3 instead of one pixel | 0007 |
| 8 | frame-resolution guard (refuse + warn once) | 0008 |
| 9 | cooldown refusals counted in `skipped` stats | 0009 |
| 10 | run.py tri-state `--semantics`, wired `--semantics-once`, idle `--find` default mission | 0010 |
| 11 | `approach_point` never overshoots the destination | 0011 |
| 12 | `pytest.ini` `testpaths = tests` + README counts | 0012 |

## Verification (evidence)

- `test-run-transcript.txt` — baseline run, patched-clone run, fresh-clone
  `git am` run, CLI evidence, per-fix evidence table.
- `evidence/fixNN-{targeted,full}.txt` — pytest output after each fix
  (targeted file + full suite), e.g. fix01 full = 79 passed … fix11 full =
  91 passed → fix12 = 80 product tests.
- CLI evidence (`evidence/fix10-*.txt`): the plan's acceptance command
  `--semantics fake --semantics-once --find "blue mat"` now runs and resolves
  the mat at (3.00, 1.20) m with mission `none`; with a config where
  `semantics.enabled=true`, `--semantics off` yields `"semantics": null` and no
  store artifacts, while unset follows the config.
- State bound measured: bare state 2039 B, 10 populated objects 4333 B
  (delta 2294 B), absolute bound 6000 B.

## Interpretation / risk notes (see `patches.md` for the full list)

- **Fix 8** implements "fail loudly once" rather than polygon scaling —
  scaling the polygon alone would leave the homography wrong for a
  capture-size mismatch.
- **Fix 10** interprets `--semantics-once` as "mission-start pass only"; without
  it the runner audits every `audit_period_s` (proposal §8). Flagged for
  review; one-line to disable if unwanted. `--mission none` is a new choice and
  the default only when `--find` is used.
- **Fix 3** hysteresis history is in-memory (not persisted); **fix 5**'s fold is
  heuristic, not a stemmer; **fix 6** dedupes on floor-projected centres.
- Not implemented (recommended only): metre-based probe + shadow split,
  mat/cover-aware probe, raw-vs-undistorted frame-space contract (M2 blocker),
  merge off the control loop, asymmetric label scoring, Jev Choice budgeting,
  CLI-level smoke tests for `--semantics off`, loop-rate invariant.

## Artifacts

```
wave2/w2b/
├── patches/0001..0012-*.diff     # proposed unified diffs (apply in order)
├── patches.md                    # per fix: what / why / risk / evidence
├── test-run-transcript.txt       # green-suite proof (3 environments + CLI)
├── evidence/                     # raw pytest/CLI outputs (fixNN-*.txt, verify-*.txt)
├── clone/                        # scratch clone @ 12 commits (suite: 80 passed)
├── verify/                       # fresh clone + git am (suite: 80 passed)
└── final_report.md               # this file
```

Apply:

```bash
git clone /home/freakymustard/jev-rover <dir> && cd <dir>
git am <pkg>/patches/*.diff
PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python \
    -m pytest -q -p no:cacheprovider     # 80 passed
```
