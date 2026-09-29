# M1 semantics layer — proposed fix package

**Base commit:** `9c33ec0` (`docs: planning package …`), i.e. the current tip of
`/home/freakymustard/jev-rover`. The real repo was **never modified** (read-only
clone source); no pushes, no PRs.

**Artifacts**

| Path | What |
|---|---|
| `patches/0001-…0012-*.diff` | 12 `git format-patch` unified diffs, one commit per fix, apply in numeric order |
| `patches.md` | this document — per fix: what / why / risk / evidence |
| `test-run-transcript.txt` | baseline, patched-clone and fresh-clone `git am` suite runs + CLI evidence |
| `evidence/*.txt` | per-fix targeted and full-suite pytest output, CLI transcripts |
| `clone/` | scratch clone with the 12 commits applied |
| `verify/` | second fresh clone with `git am ../patches/*.diff` applied |

**Apply / verify**

```bash
git clone /home/freakymustard/jev-rover <dir> && cd <dir>
git am <this dir>/patches/*.diff                 # 12 commits, clean
PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python \
    -m pytest -q -p no:cacheprovider             # -> 80 passed
```

**Result summary:** unpatched clone = 78 passed (67 product + 11 stray
prototype tests collected by accident). Patched clone = **80 passed**
(25 legacy + 55 semantics) with `testpaths = tests`, and the same 80 pass in a
fresh clone after `git am`. No new dependencies (stdlib + existing numpy/cv2
only; no scipy/Hungarian — the merge assignment is a plain distance-ordered
greedy). Test count grew by 13 new test functions (67 → 80 product tests).

**Fix ↔ report mapping:** fixes 1–8, 11 implement the ranked store/projection
recommendations in `review-reports/02-heuristics-height-merge.md` §Ranked
recommendations 4 + 5 and `03-destination-jev.md` §5; fix 8 also addresses
`00-plan-claims-audit.md` R1; fixes 9–12 address `06-tests-acceptance.md` §5,
`00-plan-claims-audit.md` R2/R3, `01-threading-snapshot.md` §5, and the stale
README counts in `06-tests-acceptance.md` §1.

---

## 1. Distance-ordered best-first merge assignment
`patches/0001-semantics-distance-ordered-best-first-merge-assignmen.diff`

- **What:** `SemanticStore.merge` now collects every (object, detection) pair
  inside `match_radius_m` and consumes the globally nearest pair first, instead
  of iterating `self.objs` in dict insertion order and letting each object grab
  its own nearest unmatched detection (`semantics.py` merge loop).
- **Why:** two same-label objects within ~1.0 m could swap identities: the
  detection nearer B was consumed by A if A was inserted first (report 02 §2).
  With one contested detection the far object now correctly misses instead of
  stealing it.
- **Risk:** low. Pure reordering of assignments; all previous merge tests pass
  unchanged. Tie-break for exactly equal distances remains insertion order
  (stable sort) — deterministic. No Hungarian/`scipy` dependency added.
- **Evidence:** new `test_contested_detection_goes_to_the_nearest_object`;
  targeted 10 passed (`evidence/fix01-targeted.txt`), full suite 79 passed
  (`evidence/fix01-full.txt`).

## 2. `min_confidence` gate on position/flags updates
`patches/0002-semantics-gate-position-flags-updates-on-min_confiden.diff`

- **What:** a matched detection with `score < semantics.min_confidence` now only
  refreshes `last_seen_s` and resets the miss counter; it no longer EMA's the
  position toward the detection, overwrites `confidence`/`height_suspect`, or
  reports `moved`.
- **Why:** a 0.05-score spurious detection could drag a tracked object and
  poison its flags (report 02 §2: "low-confidence detections poison state").
  `min_confidence` previously gated only `diff.appeared`.
- **Risk:** low. Sub-threshold hits still keep an object alive (freshness), so
  vanish timing is unchanged; `motion` is left as-is for rejected hits
  (documented in the code comment). Existing tests unaffected.
- **Evidence:** new `test_sub_threshold_hits_only_refresh_freshness`; targeted
  11 passed (`fix02-targeted.txt`), full 80 passed (`fix02-full.txt`).

## 3. `height_suspect` 2-of-3 hysteresis
`patches/0003-semantics-2-of-3-hysteresis-on-height_suspect.diff`

- **What:** the store keeps the last three raw `height_suspect` observations per
  object (`_hs_hist`, in-memory) and only flips the flag when two of them agree;
  otherwise the previous value is kept.
- **Why:** the flag was overwritten every pass, so one noisy probe (shadow,
  edge ringing) flapped it (report 02 §2). Hysteresis both ways: 1 clean probe
  does not clear a suspect flag either.
- **Risk:** low-med. The history is not persisted and resets on reload — a
  restart costs up to two passes before a flip. New objects still take their
  first value from the projection. Existing `test_height_suspect_and_rejected_propagate`
  unaffected (single pass).
- **Evidence:** new `test_height_suspect_flips_only_on_two_of_three`; targeted
  12 passed (`fix03-targeted.txt`), full 81 passed (`fix03-full.txt`).

## 4. Eviction / resurrection cap + state-bound guard
`patches/0004-semantics-cap-resurrection-with-max_misses-eviction.diff`

- **What:** new config `semantics.max_misses` (default 10, validated `>= vanish_passes`);
  `merge()` evicts an object after that many missed passes, cleaning
  `_misses`/`_labels`/`_hs_hist`. The state-size test now also asserts the
  semantics delta over the bare scene (< 3 KB) next to the absolute 6 KB bound.
- **Why:** objects never left the map, so identities resurrected after
  arbitrary absence and churn grew the map without bound — the C5 6 KB state
  bound had no runtime enforcement (report 02 §2, 06 §4).
- **Risk:** low. Eviction happens after the single `vanish` event
  (`max_misses >= vanish_passes` enforced), so diffs are unchanged; a returning
  object is a new id, which is the intended "cap resurrection" semantic.
  Measured bound headroom (this host): base state 2039 B, 10 populated objects
  4333 B, delta 2294 B < 3000 B, absolute 4333 B < 6000 B.
- **Evidence:** new `test_eviction_caps_resurrection_and_map_growth` + extended
  `test_build_state_carries_semantics_and_stays_bounded`; targeted 22 passed
  (`fix04-targeted.txt`), full 82 passed (`fix04-full.txt`).

## 5. Label canonicalisation (casefold + -s/-es/-ies folds)
`patches/0005-semantics-canonicalise-labels-for-matching-and-adapte.diff`

- **What:** new `canonical_label()` (casefold, tokenise, fold `-ies`→`y`,
  `-es` after s/x/z/ch/sh, `-s`) used for **comparisons only** in merge
  matching, `dedupe_detections`, and both adapter filters (`FakeVision.infer`,
  `build_vision`). Display labels keep their original spelling.
- **Why:** merge/dedupe compared labels case-sensitively while the adapter
  filter did not, so a real adapter returning "Blue Mat" created a duplicate
  object every pass; plural forms (`"blue mats"`, `"boxes"`) never matched
  (report 02 §2, 03 §2).
- **Risk:** low-med. `canonical_label` also collapses punctuation (`"blue-mat"`
  → `"blue mat"`) — intentional for matching. The fold is heuristic, not a
  stemmer: `"houses"`→`"hous"`, `"cookies"`→`"cooky"` (documented in the
  docstring; a real stemmer is M2). Scoring is untouched.
- **Evidence:** new `test_canonical_label_folds_case_and_plurals`,
  `test_dedupe_and_adapter_filter_are_label_case_insensitive`,
  `test_label_matching_folds_case_and_plurals`; targeted 24 passed
  (`fix05-targeted.txt`), full 85 passed (`fix05-full.txt`).

## 6. Restore the "centres < 0.15 m" dedupe clause
`patches/0006-semantics-restore-the-centres-0.15-m-dedupe-clause.diff`

- **What:** `dedupe_detections` gains keyword-only `homography` +
  `center_dist_m`; same-label boxes whose **world-space bbox centres** are
  within `semantics.dedupe_center_m` (new config, default 0.15 m) collapse to
  the highest score, alongside the IoU rule. `project_detections` wires the
  context homography in.
- **Why:** the plan's dedupe rule is "same label + (IoU > 0.5 **or** centres
  < 0.15 m)" (`m1-plan.md:258`); only IoU shipped, so near-coincident
  duplicate boxes with no overlap survived as two objects (report 02 §2).
- **Risk:** low. Existing callers without a homography keep IoU-only behaviour;
  centres are floor-projected, so a duplicate pair on furniture is approximate
  — dedupe-only, no state writes.
- **Evidence:** new `test_dedupe_collapses_close_world_centres_without_overlap`,
  `test_projection_dedupes_near_coincident_same_label`; targeted 21 passed
  (`fix06-targeted.txt`), full 87 passed (`fix06-full.txt`).

## 7. Probe a 7×3 median patch instead of one pixel
`patches/0007-semantics-probe-a-7x3-median-patch-not-a-single-pixel.diff`

- **What:** `_lab_at` samples a `PROBE_PATCH_PX = (7, 3)` patch (clipped at the
  frame edge) and returns the per-channel median LAB. `project.probe_px` keeps
  its meaning (offset below the bbox base), so configs are unchanged.
- **Why:** the single-pixel probe sat below the noise floor (2–3 cm at the
  shipped scales); MJPG ringing, a dead pixel, or a contact-shadow speck could
  flip `height_suspect` (report 02 §1).
- **Risk:** low. Median over 21 px is robust to a single outlier; a genuine
  dark band still flags suspect (asserted). Config-compatible by design.
- **Evidence:** new `test_probe_median_survives_a_single_dead_pixel`; targeted
  13 passed (`fix07-targeted.txt`), full 88 passed (`fix07-full.txt`).

## 8. Frame-resolution guard on the semantics path
`patches/0008-semantics-guard-the-frame-resolution-against-the-conf.diff`

- **What:** `SemanticContext` carries `camera_w`/`camera_h` (populated by
  `Perception.semantic_context`, default 0 = no check). `SemanticsRunner.maybe_pass`
  refuses a pass whose `frame.shape` does not match, prints one loud stderr
  warning, and counts `skipped["resolution"]`.
- **Why:** `polygon_px`, the homography and the probe are all in
  configured-camera pixels while the worker received whatever the capture
  produced; a capture-size mismatch silently misplaced every projection
  (`00-plan-claims-audit.md` R1, report 02 §1).
- **Risk:** low-med. Chosen over "scale the polygon like FloorModel" because
  scaling the polygon alone leaves the homography wrong; failing loudly is the
  only correct option for a capture-size mismatch. Hand-built contexts without
  camera dims keep working (check skipped). `skipped` gained a key — external
  consumers of `stats()` should note it.
- **Evidence:** new `test_frame_resolution_mismatch_is_refused_once`,
  `test_frame_resolution_match_is_accepted`; targeted 13 passed
  (`fix08-targeted.txt`), full 90 passed (`fix08-full.txt`).

## 9. Count cooldown refusals in stats
`patches/0009-semantics-count-cooldown-refusals-in-skipped-stats.diff`

- **What:** the failure-cooldown branch in `maybe_pass` increments
  `skipped["cooldown"]`; `stats()` therefore exposes it. The stats-shape test
  now pins the full `skipped` key set.
- **Why:** acceptance #4 ("cooldown counters visible") was unmet — the branch
  returned `False` without touching any counter (report 06 §5, 01 §5).
- **Risk:** none (counter only). `skipped` key set: `interval, budget, inflight,
  no_context, resolution, cooldown`.
- **Evidence:** extended `test_failure_cooldown` + `test_stats_shape`; targeted
  9 passed (`fix09-targeted.txt`), full 90 passed (`fix09-full.txt`).

## 10. run.py: tri-state `--semantics`, wired `--semantics-once`, idle `--find` default
`patches/0010-run-tri-state-semantics-wire-semantics-once-idle-defa.diff`

- **What:**
  - `--semantics` default is now unset (tri-state): unset follows
    `config.semantics.enabled`, `off` disables outright (over an enabled
    config; `--find` is then ignored with a printed note), `fake` forces the
    fixture adapter even when the config names another kind.
  - `--semantics-once` is read: without it the runner audits every
    `audit_period_s` (proposal §8 trigger table) after the mission-start pass;
    with it, only the mission-start pass runs.
  - `--find` with no `--mission`/`--waypoint` now defaults to the new idle
    mission `none` (GoalManager sets `goal.type="done"`, executor idles)
    instead of the legacy `goto` that died with
    `--mission goto needs --waypoint NAME`. Explicit `--mission`/`--waypoint`
    still win; plain runs keep the legacy goto default.
- **Why:** `--semantics off` did not override an enabled config (audit R2);
  `--semantics-once` was parsed but never read; the plan's acceptance command
  `--semantics fake --semantics-once --find "blue mat"` could not run
  (report 03 §4, 06 §2).
- **Risk:** med (CLI behaviour, untested by the unit suite).
  - Audits are now on by default when the runner is enabled without
    `--semantics-once`; bounded by `min_interval_s`, `max_passes_per_min` and
    the failure cooldown. This is an interpretation of the proposal's §8 table
    — flagged for review; reverting is a one-line `and False`.
  - `none` is a new `--mission` choice; `summary["mission"]` now records the
    effective mission.
  - The acceptance command still needs `--no-jev` (or a TypeSafe key), as any
    run does — that requirement is pre-existing and out of scope.
- **Evidence:** CLI transcripts in `test-run-transcript.txt` §[3] and
  `evidence/fix10-{acceptance,off-override,enabled-default}.txt`: acceptance
  run resolves `blue mat` at (3.00, 1.20) with mission `none`; enabled config
  + `--semantics off` → `"semantics": null` and no store artifacts; enabled
  config unset → runner on with the audit cadence message.

## 11. `approach_point` never overshoots the destination
`patches/0011-semantics-approach_point-never-overshoots-the-destina.diff`

- **What:** `approach_point` returns the destination itself when the current
  distance is `<= standoff_m`, instead of stepping to the far side of the
  object.
- **Why:** from (3.2, 1.0) to a destination at (3.0, 1.0) with the default
  0.35 m standoff the old formula produced (3.35, 1.0) — beyond the object
  (report 03 §5).
- **Risk:** low. Subsumes the old `length < 1e-9` degenerate case; the
  "outside the ring" result is byte-identical to the old formula.
- **Evidence:** new `test_approach_point_never_overshoots_the_destination`;
  targeted 6 passed (`fix11-targeted.txt`), full 91 passed (`fix11-full.txt`).

## 12. `pytest` scoped to `tests/` + README counts corrected
`patches/0012-tests-scope-pytest-to-tests-and-fix-stale-README-coun.diff`

- **What:** new `pytest.ini` with `testpaths = tests`; README now says 80 tests
  (25 legacy + 55 semantics) in both places (quick start and module table), and
  documents that the prototype tests are opt-in.
- **Why:** without `testpaths`, `pytest` also collected
  `docs/planning/prototype/test_april_sem.py` (11 tests), so the default run
  was not the product suite; README claimed "25 tests" (quick start) and
  "67 tests" (module table), both stale (report 06 §1).
- **Risk:** none. Prototype tests remain runnable explicitly
  (`pytest docs/planning/prototype` → 11 passed, verified).
- **Evidence:** counts verified by collection (`evidence/fix12-full.txt`,
  `fix12-prototype-explicit.txt`; per-file table in `test-run-transcript.txt`).
  Real numbers were taken from the suite, not guessed.

---

## Downgraded / deliberately not done (recommendations)

These came out of the review reports but were **not** in the fix list; they are
recorded here as review input, not implemented:

1. **Probe in metres via `H` + shadow-aware split** (`|ΔL|` vs chroma): the
   probe is still a pixel offset; the review's recommendation to express it in
   metres (~0.05 m) is a config-semantics change, M2.
2. **`floor_lab` bootstrap / ground-cover awareness** (mat-aware probe, largest
   LAB cluster): a blue mat under an object still reads as non-floor → false
   `height_suspect`. Needs a `ground_cover_labels` concept; deliberately not
   invented here.
3. **Frame-space contract (raw vs undistorted)**: `run.py` hands the raw frame
   to the worker while `Perception.process` undistorts locally; with
   `camera.intrinsics` set, bboxes and floor/polygon space diverge (report 01
   gap 1 — the M2 blocker). Out of scope for this package; flagged.
4. **Move merge/persist off the control loop** (report 01 gap 2): `store.merge`
   still runs on the main thread at poll time; measured small (<6 KB writes).
5. **Label scoring**: Jaccard still drops `"bottle"` vs `"plastic water bottle"`
   (0.333 < 0.34) and has no typo tolerance; the review's asymmetric-overlap +
   `difflib` fallback (`03-destination-jev.md` §Ranked 1) is a scoring change
   requiring threshold re-tuning — M2.
6. **Budget/fingerprint for `_ask_jev_label`** (report 03 §1): still a direct,
   unbudgeted Choice (test-only path while `run.py` passes `jev=None`).
7. **CLI-level tests** for `--semantics off` byte-identity / zero worker
   threads and the loop-rate invariant (report 06 §2/§4): the `--semantics off`
   override was verified by CLI transcript here; a subprocess smoke test is
   recommended for CI.
8. **State `sources` set-semantics + compaction** (plan C-notes): unchanged.
