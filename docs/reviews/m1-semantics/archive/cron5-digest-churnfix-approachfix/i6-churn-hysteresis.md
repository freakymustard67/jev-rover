# i6 — churn/hysteresis fix pack for `SemanticStore.merge` (validated at PR tip bec1d91)

**Verdicts up front**

1. **Implemented and validated with the REAL patched `merge()`** (no sim copy):
   mover carry hysteresis + sub-threshold confidence decay + resurrection
   event, exactly per `runs/20260928-2057/w4a-churn.md` §7. Mover churn at
   d = 0.5 m/pass: **8.58 → 3.01 new IDs, 4.93 → 0.01 vanish events**; phantom
   pinning: **never-vanishes → vanishes for 100/100 seeds**; stationary scenes
   unchanged; **85/85 tests pass** (80 baseline + 5 new).
2. **One projection delta found in the w4a report (not in the fix):** §7's table
   row "phantom vanish @pass 2" is wrong for the recommended F2 decay — the
   report's own note (`§7: "one extra pass of vanish latency (pass 3 vs pass 2)"`)
   and the measured result both say **pass 3** from an initial conf 0.9. The
   w4a sim's "HF" branch never executed F2: `patch in ("F1", "HF")` catches HF
   first (`w4a-churn-sim.py:117-123`), so its HF rows reproduce F1. Measured on
   the real merge: S4a vanish @pass 3 (ls freezes at 60); S6 (conf 0.40 from
   pass 0) vanish @pass 2, matching F2 math. All mover numbers reproduced as
   projected.
3. Master applicability: the patch is **branch-scoped to `review/m1-semantics-audit`**.
   `git apply --check` against master 9c33ec0 **fails** (master lacks the PR
   branch's sub-threshold gate/test context) and `-3` conflicts on all three
   files. Nothing was forced.

---

## 1. Patch — what changed (working clone `~/.hermes/cache/scratch/i6/repo`)

Commit `7c5de1f` on top of `bec1d91`; diffstat 3 files, +130/−9;
`git format-patch -1` → `i6-churn-hysteresis.patch` (270 lines,
sha256 `0916094006af3952d39649c93e20ee53f1d1c5665a4a97c4aaef024656bf00fa`).

**config.py — `SemanticsConfig` (after `max_misses`, tip lines 147-152):**

```python
carry_factor: float = 2.0          # mover hysteresis: eff radius = max(R, factor*last raw step)
carry_cap_m: float = 1.0           # ... capped at match_radius_m + carry_cap_m
sub_threshold_decay: float = 0.7   # confidence decay per sub-threshold-only pass
```

**semantics.py — `SemanticStore.merge` (5 touches):**

* `__init__`: `self._step: dict[str, float] = {}` (last raw per-object displacement).
* pairs build: per-object `radius = min(max(R, carry_factor*carry), R + carry_cap_m)`
  with `carry = self._step.get(oid, R)` (bootstrap: fresh objects carry R ⇒ first
  hand-off up to 2R) — replaced the fixed `dd <= match_radius_m` test.
* consume loop: `was_gone = _misses.get(oid, 0) >= vanish_passes` recorded before
  any refresh; sub-threshold hit **decays `prev.confidence *= sub_threshold_decay`
  BEFORE** the `last_seen_s`/`_misses`/`seen` refresh and **only continues to the
  refresh if the decayed value is still ≥ min_confidence**; the `taken.add(i)` stays
  before the fade-out `continue`, so a faded ghost's detection is consumed and
  cannot spawn a duplicate id.
* on the matched (seen) path: if `was_gone`, `diff.appeared.append(oid)` — the
  S3 resurrection announcement (see §2).
* after a confirmed match: `self._step[oid] = raw` (raw = detection-to-EMA
  displacement, computed as before); new objects: `_step[oid] = match_radius_m`;
  eviction: `_step.pop(oid, None)`.

Behavioral wedge preserved: sub-threshold hits that still hold (decayed conf ≥
min_confidence), keep the object fresh **without** moving it or overwriting flags.

## 2. S3 resurrection event — choice and semantics

w4a §3/§7: after a vanish, the return re-matched the same ID with **no event**
at all (`silent_resurrections` = 1.00 of S3b runs). The report leaves the fix
open ("optional follow-up"). Smallest correct approach implemented:

* a matched object whose `_misses >= vanish_passes` (i.e. it had actually
  vanished — a one-pass miss that never vanished must NOT re-announce) and
  whose sighting counts as seen (confirmed, or sub-threshold but still holding)
  is appended to `diff.appeared` again; `_labels` is unchanged, so
  `events.jsonl` label lookup already works.
* verified end-to-end: S3b patched `appeared` = 2.00 (birth + return),
  `resurrections_announced` = 1.00 (was 0.00); S3a unchanged (no flap).

**Consumer-visible change:** `diff.appeared` can now contain an **existing**
(not brand-new) id after a vanish; id sequence numbers are still never reused.
If a `resurrected`-style separate field is preferred, that is an owner decision
(deliberately not done here to keep the diff minimal).

## 3. Test changes (with justification)

Baseline suite at bec1d91: **80 passed**. Patched: **85 passed**
(`tests/test_semantics_merge.py` 14 → 19 tests).

Updated (2):

* `tests/test_semantics_merge.py:112` (`test_sub_threshold_hits_only_refresh_freshness`):
  `o.confidence == 0.9` → `round(0.9 * 0.7, 3)` (= 0.63) — the decay is the
  point of the fix. The test is renamed
  `test_sub_threshold_hits_refresh_only_until_confidence_fades`, and now also
  pins the fade: at pass 2 (0.63 → 0.441 < 0.5) `last_seen_s` **stays 1.0** and
  the hit counts as a miss (1 miss < vanish_passes ⇒ no vanish yet).
* `test_large_jump_is_a_new_object`: the 0.9 m jump that used to spawn a new id
  is now inside the bootstrap carry (2R = 1.0 m), so the jump is increased to
  1.2 m to keep testing the new-object path. The intended 2R first hand-off is
  pinned by the new `test_first_handoff_within_carry_keeps_identity` (0.9 m ⇒
  same id, no appeared/vanished).

Added (5):

* `test_first_handoff_within_carry_keeps_identity` — bootstrap carry = R.
* `test_mover_hysteresis_keeps_identity_at_half_metre_per_pass` — deterministic
  S2 d=0.5: one id for 10 passes, `moved` fires every pass after pass 0
  (baseline churned a new id at pass 2 and lost `moved`).
* `test_phantom_only_sub_threshold_hits_vanishes` — S4: 0.9 → 0.63 (seen) →
  0.441, 0.309 (two misses) → `vanished` on the 2nd faded pass, `last_seen_s`
  frozen at 1.0, vanish fires once.
* `test_stationary_scene_is_unchanged_under_noise` — S1: 5 objects, sub-0.05 m
  deterministic jitter, 10 passes, ids stable, no events after pass 0.
* `test_resurrection_after_vanish_emits_appeared` — S3b event fix, plus the
  no-flap control (one-pass miss ⇒ no re-announce).

## 4. Validation method (REAL merge, not the sim copy)

Driver: `i6-churn-hysteresis-evidence/i6-churn-driver.py` (adapted from
`w4a-churn-sim.py`: identical scenario generators, seed derivation
`seed_for(name, s)` and metric definitions, so per-seed RNG streams match the
w4a runs; scenario names unchanged for comparability). It **imports
`config`/`scene`/`semantics` from `--repo`** and drives the real
`SemanticStore.merge()`; no merge copy anywhere. Extra metric
`resurrections_announced` for the S3 check; added S6 (weak-but-real).

Runs (100 seeds × 10 passes, t = 0…540 s, R = 0.5, vp = 2; store dirs in
scratch, repo untouched):

* **baseline-fresh** — fresh clone (`i6/verify`) at `bec1d91` (unpatched).
* **patched-fresh** — same clone after `git am` of the patch artifact (b5a0ce5).
* patched-workclone — the working clone `7c5de1f`; compared per-seed against
  patched-fresh: **10 800 fields / 1 200 runs, 0 mismatches** ⇒ the formatted
  patch is the tested code.
* determinism: a `--seeds 3` re-run matches the full run on all 324 compared
  fields (0 mismatches) ⇒ re-running one scenario for spot-checks is exact.

## 5. Measured before/after (100 seeds) vs w4a §7 projections

| scenario | baseline (real, bec1d91) | patched (real, b5a0ce5) | w4a §7 projection | delta |
|---|---|---|---|---|
| S1 σ=0.03 | 5.00 newIDs / 0.00 vanish | 5.00 / 0.00 | 5.00 / 0.00 | none |
| S1 σ=0.10 | 5.03 / 0.03 (moved 5.17) | 5.00 / 0.00 (moved 5.21) | 5.02 / 0.04 | −0.02 / −0.04, seed-noise level |
| S2 d=0.2 | 3.94 (max 5) / 0.77 | 3.00 (max 3) / 0.00 | 3.0 / 0.0 | none |
| S2 d=0.5 σ=0.03 | **8.58 (max 10) / 4.93** (moved 3.42) | **3.01 (max 4) / 0.01** (moved 8.99) | 3.0 / 0.0 (max 4) | +0.01 / +0.01 (1/100 seed vanished @pass 3) |
| S2 d=0.5 σ=0.10 | 8.67 / 4.99 | 3.41 (max 7) / 0.41 | 3.4 (σ=0.10 row) | +0.01, noise edges |
| S2 d=0.8 σ=0.03 | 12.00 (max 12) / 8.00 (moved 0.00) | **5.96 (max 6) / 2.00** (moved 6.04) | 5.9 / 2.0 | +0.06 / 0 |
| S2 d=0.8 σ=0.10 | 11.91 / 7.92 | 5.83 (max 8) / 2.45 | 5.8 | match |
| S2 d=1.2 σ=0.03 | 12.00 / 8.00 | 12.00 / 8.00 | unchanged | none (honest limit) |
| S3a one-pass miss | 1.00 / 0.00 | 1.00 / 0.00 | unchanged | none |
| S3b two-pass miss | appeared 1.00; 1 resurrection, **0 announced** | appeared 2.00; 1 resurrection, **1 announced** | (S3 fix) | fixed, 100/100 |
| S4a phantom σ=0.03 | **0.00 vanish, ls pinned @540** | **1.00 @pass 3 (100/100), ls=60** | "@pass 2", ls stops refreshing | **1-pass delta — report-row issue, see §6** |
| S4b phantom σ=0.10 | 0.13 vanish, ls 540 | 1.00 @pass 3, ls=60 | — | pinned now too |
| S6 weak-but-real (conf 0.40) | 0.00 vanish, ls pinned @540 | 1.00 @pass 2 (100/100), ls=0 | "@pass 2" | matches |

Side signals: mover `moved` events restored (S2 d=0.5: 3.42 → 8.99; d=0.8:
0.00 → 6.04) and map bloat removed (alive@end = 3.01 for 3 physical objects at
d=0.5 vs 8.58 baseline). S1 `moved` count is statistically unchanged
(5.17 → 5.21 over 5 objects × 100 seeds).

## 6. The one delta, explained (phantom vanish pass)

With the mandated semantics (decay **before** the refresh, decay = 0.7), the
S4a sequence is: pass 1 conf 0.9 → decay 0.63 ≥ 0.5 ⇒ **seen** (ls = 60);
pass 2 → 0.441 < 0.5 ⇒ miss #1; pass 3 → 0.309 ⇒ miss #2 ⇒ **vanished at
pass 3**. The w4a report's §7 *note* states exactly this ("F2 … one extra pass
of vanish latency … pass 3 vs pass 2") — its §7 *table* row quotes the pass-2
timing, which comes from the sim's F1-equivalent HF branch. The sim never
actually ran the recommended F2 policy under the HF label; the separate F2 row
in §6 (pass 3, ls 60) is the correct one. So the implemented behavior matches
the §7 patch sketch and the F2 measurement; the projection row in §7 is the
artifact that needs correcting. For an object whose every detection is weak
from the start (S6), vanish fires at pass 2 as projected.

## 7. Patch artifact verification

| check | result |
|---|---|
| `git format-patch -1` → `i6-churn-hysteresis.patch` | 270 lines, sha256 `0916…00fa` |
| `git am` on a **fresh clone** at `review/m1-semantics-audit` (bec1d91) | **clean** (b5a0ce5) |
| full suite in the fresh am'd clone | **85 passed** (18.4 s) |
| `git apply --check` against master 9c33ec0 | **fails** (config.py:147, semantics.py:287, tests:103 — master lacks the PR context); `-3` conflicts on all three; not forced |
| artifact parity: patched-fresh vs working clone | 10 800 fields, 0 mismatches |
| determinism re-run (3 seeds) | 324 fields, 0 mismatches |

## 8. Verified vs assumed

* **Verified (real merge, 100 seeds, two independent clones):** every row in
  §5; the projection match for movers; the phantom fix; resurrection
  announcement; deterministic reruns; the formatted patch equals the tested
  code; 85/85 suite.
* **Reproduced, not assumed:** the w4a baseline numbers were re-run with the
  real unpatched merge and match §3 (e.g. S2 d=0.5: 8.58 newIDs / 4.93 vanish
  here vs 8.58 / 4.93 in the report; S4a pinned, S3b silent resurrection).
* **Assumed (not independently re-derived):** the report's analytic mover bound
  `d ≤ min(2R, α(R+carry_cap)) ≈ 0.6 m/pass` — consistent with the measured
  3.0–3.4 IDs at d = 0.5 and partial recovery at 0.8, but it is the report's
  derivation, not re-proved here.
* **Not tested:** interactions with the audit worker (budget/cooldown), vision /
  homography noise in pixels, `resolve_destination` consumption of
  `confidence` after decay, same-label proximity mis-association wider carry
  windows may admit (first-hand-off ≤ 2R is a deliberate mild widening),
  and downstream consumers' assumptions about `appeared`/`vanished` pairing.

## 9. Caveats

* Sim-driven (as reserved in w4a §8): `merge()` alone, world-metre objects,
  fixed 60 s cadence, homogeneous per-axis Gaussian noise. Store writes go to
  scratch only.
* S2 d=0.5 σ=0.03 residual: 1/100 seeds still vanished (pass 3) and max 4 IDs
  (vs mean 3.01) — carry gives no guarantee under noise at the cap edge.
* S6 now emits a `vanished` for an id that never emitted `appeared` (its
  creation conf 0.40 < min_confidence). This unpaired-event shape pre-exists
  (w4a noted invisible low-confidence entries vanish in S4b σ=0.10) and is
  made more visible by the fix — see owner decisions.
* σ=0.10 rows remain noise-edged (3.41–5.83 IDs); they are not systematic churn.
* `moved` counts for movers increase (that is the restored signal), so
  consumers with `moved`-volume expectations should be told.

## 10. Decisions the owner must make

1. **F2 vs F1**: keep decay-before-refresh (implemented; confidence stays a
   meaningful freshness field, one extra vanish pass for strong objects) or
   switch to the pure freshness gate (vanish @pass 2, confidence frozen at
   0.9). Report recommends F2; implemented F2.
2. **`carry_cap_m` (default 1.0)** bounds stability at d ≤ 0.6 m/pass; raising
   it extends mover continuity at the cost of wider same-label association
   windows. The first hand-off also widened to 2R by design (bootstrap).
3. **S3 event semantics**: `appeared` now also announces resurrections of
   **existing** ids (no separate `resurrected` field). If consumers treat
   `appeared` as "brand-new id", they need updating; alternatively add a
   dedicated field.
4. **Unpaired `vanished`** for never-announced weak ids (S6): suppress vanish
   for ids that never appeared, or announce weak creations, or accept.
5. **Rebase/merge order**: patch applies only on the review branch; master
   needs the branch context first (expected, not forced).

## 11. Artifacts (all under `runs/20260929-0118/`)

| file | sha256 | content |
|---|---|---|
| `i6-churn-hysteresis.md` | — | this report |
| `i6-churn-hysteresis-evidence/i6-churn-hysteresis.patch` | `0916094006af3952d39649c93e20ee53f1d1c5665a4a97c4aaef024656bf00fa` | `git format-patch` of 7c5de1f |
| `…/i6-churn-driver.py` | `128e950f3d402b22fc67c40e90dd72ed23b7a182f87f106f5a47bf63def19460` | real-merge driver (100 seeds) |
| `…/results-baseline.json` | `f0d1eee0071774a37948eb0e8f29f6e2274eca969d8eebb07f894b6f192c60b1` | baseline per-seed + aggregates (fresh clone) |
| `…/results-patched.json` | `24d24b50636968af2b4d7ae70a951a42d0a8f378d47e58aa934d12642d63d28f` | patched per-seed + aggregates (fresh am'd clone) |
| `…/results-patched-workclone.json` | `5314169314a5a47abad7a7212638f6f2a3ea51f4a0c3cd6a4d6d3c863e60a542` | working-clone patched run (parity check) |
| `…/i6-churn-baseline-raw.txt`, `…/i6-churn-patched-raw.txt` | `6040f8d2…`, `9324fc85…` | driver stdout |
| `…/i6-churn-git-verification.txt` | — | am/apply/determinism transcript |
| `…/i6-churn-compare-runs.py`, `…/i6-commit-msg.txt` | `7fefdbb7…`, `b828eb94…` | parity checker; commit message |

Scratch clones: `~/.hermes/cache/scratch/i6/repo` (working, commit 7c5de1f),
`~/.hermes/cache/scratch/i6/verify` (fresh clone + `git am`, currently detached
at bec1d91; the am'd commit b5a0ce5 is on the branch). Nothing was pushed; the
real repo `~/jev-rover` and `/tmp/opencode` were never touched.

**Re-run one scenario (spot-check):**

```
cd ~/.hermes/cache/scratch/i6/verify && git checkout -q review/m1-semantics-audit && \
PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python \
  /home/freakymustard/jev-rover-research/runs/20260929-0118/i6-churn-hysteresis-evidence/i6-churn-driver.py \
  --repo "$PWD" --tag spotcheck --seeds 10 --out /home/freakymustard/.hermes/cache/scratch/i6/spotcheck.json
```
