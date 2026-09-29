# W3a — M1 "semantics layer" 12-patch fix pack: fresh-clone apply, suite, and traceability verification

**Date:** 2026-09-29 (~01:00–01:12 IST) · **Run:** w3a (independent re-execution in fresh clones)
**Subject repo:** `/home/freakymustard/jev-rover` @ `9c33ec06afcc644c23deef76447b4a2370de31c1`
(public `freakymustard67/jev-rover`), worktree clean — **read-only throughout, untouched**
**Pack under test:** `runs/20260928-1930/wave2/w2b/patches/0001-…0012-*.diff`
(12 mail-format patches, one commit per fix, `git am` series)
**Scratch clones (mine, writable):**
- `~/.hermes/cache/scratch/wave3/w3a/clone` — **patched** (12 commits on 9c33ec0, tip `2adf06f`)
- `~/.hermes/cache/scratch/wave3/w3a/clone-base` — **unpatched control** @ 9c33ec0
**Interpreter / flags:** `/home/freakymustard/jev-rover/.venv/bin/python` (3.11.16, pytest 9.1.1),
`PYTHONDONTWRITEBYTECODE=1`, `pytest -p no:cacheprovider` everywhere, always from clone root.

---

## 0. Verdict summary

| Claim to verify | Result |
|---|---|
| 12 patches apply cleanly to 9c33ec0 in a fresh clone (`git am`) | **CONFIRMED** — exit 0, 12 commits, 0 conflicts, 0 fuzz, no `--3way` fallback needed, diffs unmodified |
| Unpatched baseline = 78 collected (67 product in `tests/` + 11 stray from `docs/planning/prototype/`) | **CONFIRMED** — 78 collected / 78 passed; split 67 + 11 verified per-directory |
| Patched result = 80 passed with `testpaths` scoped to `tests/` (patch 0012) | **CONFIRMED** — 80 collected / 80 passed, reproduced twice |
| Patched tree matches w2b's own verified artifact | **CONFIRMED** — `diff -r` byte-identical to `w2b/verify/` (excluding .git/runs/caches) |
| As-written acceptance command end-to-end on patched tree | **PARTIAL** — the reported `SystemExit('--mission goto needs --waypoint NAME')` is fixed on the patched tree, but the literal command still cannot run in a fresh clone for a pre-existing, unrelated reason (default `config/room.json` is not shipped in the repo). With a config supplied + `--no-jev` it resolves and exits 0 (exact stdout below) |
| Series covers the `--trace` NameError (`print(trace_line)`) | **NO — not covered.** No patch touches `trace_line`/`--trace`; the NameError reproduces at run.py:371 in the patched clone. A one-line fix exists separately at `runs/20260929-wave2/reviewer-addendum/trace-nameerror.diff` (applies cleanly on top; not part of this series) |
| Regressions attributable to the series | None observed (full suite green twice; no previously-passing behavior asserted broken) |

The pack does what it claims for the 11 known M1 findings it targets + the height-hysteresis item;
the single known bug outside its scope is `--trace`.

---

## 1. Apply verification (fresh clone, `git am`)

```bash
git clone /home/freakymustard/jev-rover …/w3a/clone          # HEAD 9c33ec0, clean
git -C clone -c user.name=cron-verify -c user.email=cron@local am \
    /home/freakymustard/jev-rover-research/runs/20260928-1930/wave2/w2b/patches/*.diff
# -> exit 0
Applying: semantics: distance-ordered best-first merge assignment
Applying: semantics: gate position/flags updates on min_confidence
Applying: semantics: 2-of-3 hysteresis on height_suspect
Applying: semantics: cap resurrection with max_misses eviction
Applying: semantics: canonicalise labels for matching and adapter filters
Applying: semantics: restore the centres < 0.15 m dedupe clause
Applying: semantics: probe a 7x3 median patch, not a single pixel
Applying: semantics: guard the frame resolution against the configured camera
Applying: semantics: count cooldown refusals in skipped stats
Applying: run: tri-state --semantics, wire --semantics-once, idle default for --find
Applying: semantics: approach_point never overshoots the destination
Applying: tests: scope pytest to tests/ and fix stale README counts
```

- Resulting history: `9c33ec0` + exactly 12 commits, tip `2adf06f`; `git status --porcelain` → clean.
- **No patch failures, no fuzz, no whitespace warnings, no `git apply --3way` needed**, no patch text modified.
- The 12 patch files are **byte-identical** to the copies in `runs/20260929-wave2/w2b/patches/` (md5 set match, 12/12).
- The patched tree is **byte-identical to `runs/20260928-1930/wave2/w2b/verify/`** (a second fresh clone w2b
  verified with the same `git am` route): `diff -r -x .git -x runs -x __pycache__ -x '*.pyc' -x .pytest_cache` → no output.
- Raw transcripts: `w3a/git-am-transcript.txt`, `w3a/w3a-commands.md` (full command log).

## 2. Suite counts (independently observed)

| State | `--collect-only -q \| tail -1` | Full suite `pytest -q -p no:cacheprovider` |
|---|---|---|
| **Unpatched** @ 9c33ec0 | `78 tests collected in 0.62s` | `78 passed in 14.08s` |
| Unpatched per-dir | `tests/` → 67 · `docs/planning/prototype` → 11 | (the 11 stray are `test_april_sem.py`) |
| **Patched** (all 12) | `80 tests collected in 0.89s` | `80 passed in 17.85s`; re-run `80 passed in 19.18s` |
| Patched, prototype explicit | `pytest docs/planning/prototype --collect-only` → `11 tests collected in 0.20s` | prototype stays opt-in runnable |

- w2b's two headline numbers (78 baseline, 80 patched) are **independently confirmed** in my fresh clone.
- Test-count arithmetic checks out: 67 product tests + **13 new test functions** = 80; the default
  collection is 80 because patch 0012 adds `pytest.ini` with `testpaths = tests`.
- Files: `w3a/unpatched-collect.txt`, `unpatched-fullsuite.txt`, `unpatched-collect-tests-only.txt`,
  `unpatched-collect-prototype.txt`, `patched-collect.txt`, `patched-fullsuite.txt`, `patched-fullsuite-final.txt`.

## 3. Traceability table — patch → finding → test → result

Finding sources: `round1-reports/*` (wave-1) + `runs/20260928-1930/wave2/w2d/meta-review.md` (A-numbered).
All targeted tests were run by explicit node id in the **fully patched clone** (see `w3a/targeted-tests.txt`).

| # | Patch (subject) | Known finding(s) addressed | Test added/changed | Targeted run |
|---|---|---|---|---|
| 0001 | distance-ordered best-first merge assignment | R1-**02** §2 "Greedy is order-dependent, not distance-ordered" — same-label objects within the match radius could swap identities (detection nearer B consumed by A by insertion order) | **new** `tests/test_semantics_merge.py::test_contested_detection_goes_to_the_nearest_object` | **1 passed** (0.14s) |
| 0002 | gate position/flags on `min_confidence` | R1-**02** §2 "Low-confidence detections poison state" — sub-threshold detections EMA'd position, overwrote confidence/`height_suspect`, counted as moved | **new** `tests/test_semantics_merge.py::test_sub_threshold_hits_only_refresh_freshness` | **1 passed** (0.14s) |
| 0003 | 2-of-3 hysteresis on `height_suspect` | R1-**02** §2 "`height_suspect` has no hysteresis — overwritten every pass; one noisy probe flips it" | **new** `tests/test_semantics_merge.py::test_height_suspect_flips_only_on_two_of_three` | **1 passed** (0.14s) |
| 0004 | cap resurrection with `max_misses` eviction | R1-**02** §2 "Never evicts" — objects resurrect with the same id after arbitrary absence; unbounded map growth; 6 KB state bound had no runtime enforcement | **new** `test_eviction_caps_resurrection_and_map_growth`; **modified** `tests/test_semantics_schema.py::test_build_state_carries_semantics_and_stays_bounded` (added `size - base < 3000` delta bound) | **2 passed** (0.46s) |
| 0005 | canonicalise labels for matching and adapter filters | R1-**02** §2 "labels compared case-sensitively while adapters filter case-insensitively → real adapters returning 'Blue Mat' create duplicates every pass"; also R1-**03** §2 "plural fold only `-s` (`boxes`→`boxe`)" | **new** `test_label_matching_folds_case_and_plurals` (merge); **new** `test_canonical_label_folds_case_and_plurals` + `test_dedupe_and_adapter_filter_are_label_case_insensitive` (projection) | **3 passed** (0.18s) |
| 0006 | restore the centres < 0.15 m dedupe clause | R1-**02** §2 "dedupe 'or centres < 0.15 m' clause dropped (IoU only) → containment/duplicates survive" | **new** `test_dedupe_collapses_close_world_centres_without_overlap`; **new** `test_projection_dedupes_near_coincident_same_label` | **2 passed** (0.79s) |
| 0007 | probe a 7×3 median patch, not a single pixel | R1-**02** §1 "Probe metric is below the noise floor … 7–15 px probe band (patch median over ~7×3 px, not `frame[y,x]` single pixel — MJPG ringing at object edges)"; shadow/dark-spot false positives | **new** `tests/test_semantics_projection.py::test_probe_median_survives_a_single_dead_pixel` | **1 passed** (0.82s) |
| 0008 | guard frame resolution against configured camera | R1-**00** R1 "frame-resolution coupling unvalidated"; R1-**02** §1 "no guard that the worker frame resolution equals `cfg.camera` … silently misplaces probe/polygon tests" | **new** `tests/test_semantics_worker.py::test_frame_resolution_mismatch_is_refused_once` (warn-once) + `test_frame_resolution_match_is_accepted` | **2 passed** (0.32s) |
| 0009 | count cooldown refusals in skipped stats | R1-**01** §5 "cooldown refusals aren't counted in `skipped`"; R1-**06** §5 acceptance #4 "add `skipped['cooldown'] += 1` plus a summary-shape assertion" | **modified** `test_failure_cooldown` (+`skipped["cooldown"] == 1`); `test_stats_shape` (pins the six-key skipped set) | **2 passed** (1.34s) |
| 0010 | tri-state `--semantics`, wire `--semantics-once`, idle default for `--find` | R1-**00** R2 "`--semantics off` doesn't override `semantics.enabled` (tri-state recommended)"; R1-**03** §4 "`--find` acceptance command cannot run (`SystemExit`); `--semantics-once` parsed but never read — inert" | **no unit test** (run.py-only change; no test node exists — verified at runtime, §4b) | runtime OK (banners; `off` overrides; no-mission `--find` works) |
| 0011 | approach_point never overshoots the destination | R1-**03** §5 "standoff > distance flips the point to the far side of the destination" | **new** `tests/test_semantics_destination.py::test_approach_point_never_overshoots_the_destination` | **1 passed** (0.11s) |
| 0012 | scope pytest to `tests/`, fix stale README counts | R1-**06** §1 "no pytest.ini → 11 prototype tests ride along by default"; R1-**07** §2 / R1-**06** §1 "README says 25/67 tests, actual 78" | **no test functions** — new `pytest.ini` (`testpaths = tests`) + README corrections | scope verified: default collect 80 (was 78); prototype explicit 11; README now "80 tests (25 legacy + 55 semantics)" |

Totals: **13 new test functions + 2 modified tests**; every named test passes in the patched clone;
full suite 80/80 green.

### 3a. Notes on coverage gaps in the series itself

- **Patch 0010 has no automated test coverage** (run.py CLI wiring is untested; `grep` confirms no test
  imports `run.py` or references `--semantics`). Runtime checks in §4b are the only evidence.
- **Patch 0012's README fix is partial**: `docs/planning/README.md:38` still says `# 67 tests` (should be 80);
  patch 0012 touched only the top-level `README.md` + `pytest.ini`. Cosmetic, but the "stale counts" finding
  is not fully swept.
- The series does **not** touch: loop-rate invariant test, zero-worker-thread check, CLI-level `--find` e2e,
  `--trace` (all listed as missing acceptance/tests in R1-**06** and w2d meta-review).

## 4. Acceptance command path

### 4a. The as-written command still cannot run — but for a different (pre-existing) reason

The plan's acceptance #3 text is `--semantics fake --semantics-once --find "blue mat"` **on `room.synthetic.json`**
(`docs/planning/m1-plan.md:352`); the literal command in the brief omits the config. Observed:

| Command (timeout 30, venv python) | Clone | Exit | Tail of stdout+stderr |
|---|---|---|---|
| `run.py --semantics fake --semantics-once --find "blue mat"` | unpatched (`clone-base`) | 1 | `FileNotFoundError: [Errno 2] No such file or directory: 'config/room.json'` |
| same | patched (`clone`) | 1 | identical `FileNotFoundError: 'config/room.json'` |
| `run.py --config config/room.synthetic.json --semantics fake --semantics-once --find "blue mat"` | unpatched | 1 | `--mission goto needs --waypoint NAME` ← **reproduces the w2b "previously failed" claim** |
| same (with config) | patched | 1 | `RuntimeError: set TYPESAFE_API_KEY (see .env.example)` — environment lacks the API key; **no longer the mission error** (GoalManager accepted the new idle default) |

Cause of the first row: the repo ships only `config/room.example.json` and `config/room.synthetic.json`;
`config/room.json` (the `--config` default) **does not exist** in the repo or any fresh clone. This is
pre-existing at 9c33ec0 and not a regression of the series — but as literally written the command is unrunnable
until a config is supplied or the default is changed. Transcripts: `w3a/accept-*.txt`.

### 4b. Patched, synthetic, no mission, `--no-jev` (proves the idle-default + `--find` fix)

```text
$ run.py --config config/room.synthetic.json --source synthetic --no-jev \
         --semantics fake --semantics-once --find "blue mat" --seconds 12      # exit 0
[semantics] enabled: model=fake-vision-v0 fixtures=3 (one pass at mission start)
[find] 'blue mat' -> blue mat at (3.00,1.20) m conf=0.92 object=obj_0001; approach (3.11,1.53) standoff=0.35 m
```

This line is **identical** to w2b's own evidence `evidence/fix10-acceptance.txt` (same approach point
`(3.11,1.53)`, same `mission: none`, `jevs: false`) — independent reproduction of their acceptance artifact.

### 4c. Corrected variant (task-specified: `--mission patrol --no-jev` on synthetic)

```text
$ run.py --config config/room.synthetic.json --source synthetic --mission patrol --no-jev \
         --semantics fake --semantics-once --find "blue mat" --seconds 20      # exit 0
[semantics] enabled: model=fake-vision-v0 fixtures=3 (one pass at mission start)
[find] 'blue mat' -> blue mat at (3.00,1.20) m conf=0.92 object=obj_0001; approach (3.13,1.53) standoff=0.35 m
… summary printed …
summary written to runs/summary_20260929-010556.json
```

The summary carries the semantics counters (acceptance #4 material): `passes: 1`, `errors: 0`,
`rejected_total: 0`, `stale_dropped: 0`, `skipped: {interval, budget, inflight, no_context, resolution, cooldown}`
— the two new keys (`resolution`, `cooldown`) from patches 0008/0009 are present. Transcripts:
`w3a/accept-patched-nomission-nojev.txt`, `w3a/accept-patched-corrected.txt`.

### 4d. Patch-0010 tri-state runtime checks (no unit tests exist)

| Run (synthetic, `--no-jev`, `--seconds 5-6`) | Observed |
|---|---|
| `config/room.enabled.json` (enabled:true), flag **unset** | banner `(mission-start pass + audit every 60s)`; summary `"semantics": {…}` present → **unset follows config** ✓ |
| same config + `--semantics off` | **no** `[semantics] enabled` banner (count 0); summary `"semantics": null` → **off overrides an enabled config** ✓ |
| `room.synthetic.json` (enabled:false) + `--semantics fake`, flag absent | banner `(mission-start pass + audit every 60s)` → `--semantics-once` is now actually read (cadence changes with it) ✓ |

Transcripts: `w3a/semantics-enabled-default.txt`, `semantics-off-override.txt`, `semantics-audit-cadence.txt`.
(`config/room.enabled.json` is an untracked helper file inside my clone only; suite still 80 passed with it present.)

## 5. `--trace` NameError — NOT covered by the series (reproduced)

- Unpatched repo: `grep -n trace_line run.py` → `339: print(trace_line)` (matches the known finding).
- Patched clone: same dead print at **run.py:371** (line shifted +32 by patch 0010 — no fix).
- `grep -l trace` over all 12 patch diffs → **no matches**; nothing in the series touches `--trace`.
- Empirical repro in the **patched** clone:

```text
$ run.py --config config/room.synthetic.json --source synthetic --mission patrol --no-jev --trace --seconds 5
exit=1
Traceback (most recent call last):
  File "…/w3a/clone/run.py", line 420, in <module>
    raise SystemExit(main())
  File "…/w3a/clone/run.py", line 371, in main
    print(trace_line)
NameError: name 'trace_line' is not defined
```

- The fix exists as a separate artifact (`runs/20260929-wave2/reviewer-addendum/trace-nameerror.diff`,
  a 1-line deletion of `print(trace_line)`), and I verified with `git apply --check` that it **applies cleanly
  on top of the 12-patch series** (its base blob `9c38ed6` is exactly the run.py blob produced by patch 0010).
  It is **not part of the 12-patch pack**, and the repo's `master` is still at 9c33ec0 (unfixed there too).
- Transcript: `w3a/trace-repro-patched.txt`.

## 6. Conflicts / fuzz / regressions

- Apply: zero conflicts, zero fuzz, no 3-way; `git am` exit 0; worktree clean immediately after.
- Suite: green at 78 (unpatched) and 80 (patched), twice; no test was skipped/xfailed; runtimes 14–19 s.
- No file outside the intended patch scope changed: the diff of my patched tree vs w2b's `verify/`
  tree is empty.
- Environment caveat: the acceptance runs without `--no-jev` die on a missing `TYPESAFE_API_KEY`
  (RuntimeError) — an environment/key limitation here, not a patch regression (the same happens
  before this series; it is why the corrected variant uses `--no-jev`, matching w2b's own evidence run).

## 7. Evidence index (all under `runs/20260928-1930/`)

| File | Content |
|---|---|
| `w3a-fixpack-apply.md` | this report |
| `w3a/w3a-commands.md` | ordered command log for everything below |
| `w3a/git-am-transcript.txt` | `git am` output (12 clean Applying lines) |
| `w3a/unpatched-collect.txt`, `unpatched-fullsuite.txt` | 78 collected / 78 passed |
| `w3a/unpatched-collect-tests-only.txt`, `unpatched-collect-prototype.txt` | 67 / 11 split |
| `w3a/patched-collect.txt`, `patched-fullsuite.txt`, `patched-fullsuite-final.txt` | 80 collected / 80 passed ×2 |
| `w3a/targeted-tests.txt` | per-patch node-id test transcript (16 runs, all pass) |
| `w3a/accept-unpatched-aswritten.txt`, `accept-patched-aswritten.txt` | FileNotFoundError `config/room.json` |
| `w3a/accept-unpatched-synthcfg.txt` | SystemExit `--mission goto needs --waypoint NAME` |
| `w3a/accept-patched-synthcfg-nomission.txt` | RuntimeError `TYPESAFE_API_KEY` (no `--no-jev`) |
| `w3a/accept-patched-nomission-nojev.txt`, `accept-patched-corrected.txt` | working acceptance runs + summary |
| `w3a/trace-repro-patched.txt` | `--trace` NameError in patched clone |
| `w3a/semantics-enabled-default.txt`, `semantics-off-override.txt`, `semantics-audit-cadence.txt` | patch-0010 tri-state checks |
| `w3a/patch-md5-a.txt`, `patch-md5-b.txt`, `a.sums`, `b.sums` | patch-set md5 comparison (12/12 identical) |

Limit of this verification: counts/tests/CLI runs only — no review of whether each fix is *complete*
(e.g. probe still single-band, mat/cover false-positive open per R1-02 §1) was attempted; that is design-review
territory, out of scope for this apply-verification.
