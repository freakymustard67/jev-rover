# i14 / m15-series — stacking the three fix packs (i5+i6+i7) on PR-tip bec1d91, and the landing plan onto master

Run: 2026-09-29 ~08:23–08:35 IST · workspace `/home/freakymustard/.hermes/cache/scratch/c6/` · Python
`/home/freakymustard/jev-rover/.venv/bin/python` (3.11.16) · read-only vs `/home/freakymustard/jev-rover`;
nothing pushed; all git write ops in scratch clones.

## Headline verdicts

1. **All three patches (4 commits) stack cleanly in order i5→i6→i7 on a fresh clone at prtip `bec1d91`**
   with `git am -3` — zero conflicts, zero manual edits. Suite ladder measured:
   **80 → 82 → 87 → 93** (`-q -p no:cacheprovider`).
2. **Combined suite = 93 passed** (16.37 s), not the estimated ≈88. The deviation is fully explained and
   benign: the packs' report counts were *standalone* (each measured on `bec1d91` alone — i6 "85 = 80+5",
   i7 "86 = 80+6"), and i7's briefed "+1 test" is wrong — i7's own report says "+6 new"
   (`i7-approach-standoff.md` §3: "86 passed (80 + 6 new)"). The three packs touch **disjoint test files**
   (schema+integration / merge / new `test_approach_standoff.py`), so net additions are additive:
   80 + 2 + 5 + 6 = **93**.
3. **Behavioral spot-checks reproduce on the combined tree** (reduced but decisive):
   i5 digest 20-obj real pipeline **5236 B** (expected ≈5236), 30-obj capped **5658 B** (expected ≈5658);
   i6 10-seed S2 d=0.5 IDs **3.00** (expected ≈3.0), phantom first-vanish **pass 3 on all 10 seeds**,
   0/1170 field mismatches vs the recorded 100-seed run; i7 rule arms r∈{0.15,0.30,0.40} arrive at
   **0.040 / 0.050 / 0.030 m with 0 reflex trips** (expected 0.03–0.05 / 0).
4. **Landing plan (empirical):** apply the series *after PR #1 merges* (or onto the review branch now).
   Post-PR-merge master simulation: all 4 commits apply clean, **93 passed**. Directly onto current master
   `9c33ec0` it does **not** apply cleanly (i5: 1 file/2 hunks; i6: 3 files/6 hunks; i7's docs commit
   modify/delete) — every conflict is context-shift caused by the not-yet-merged review branch and
   disappears post-merge. The i7 docs-errata commit (`7bbd21a`) is droppable (docs-only, +0 tests; it fails
   on master-now only because `docs/reviews/m1-semantics/m2-design.md` is absent there).

## 1. Inputs, integrity, stack run

```
$ git rev-parse HEAD                     # after clone + fetch review/m1-semantics-audit:prtip
bec1d91fe17682ce38d24e334c94c67fa4bcc685          # == required tip ✓

$ sha256sum <patches>                    # all MATCH the recorded values (verified before applying)
i5 73717eac6725b9caf9540e1ddea20f8f933766dabb3d74c05a69c292b8095846  ✓
i6 0916094006af3952d39649c93e20ee53f1d1c5665a4a97c4aaef024656bf00fa  ✓
i7 3b222edd251db9428e4aca459956c9ad2916641fee96f1e14a0541f758fd6e91  ✓
```

Per-stage results (fresh clone, `git am -3`, in order):

| stage | am result | HEAD commit | suite | log |
|---|---|---|---|---|
| baseline | — | bec1d91 | **80 passed** (14.54 s) | transcript-prtip.log |
| + i5 `0001` | clean (rc=0) | `cca5987` semantics: Jev payload ships a compact state digest | **82 passed** (15.65 s) | suite-after-i5.log |
| + i6 `0002` | clean (rc=0) | `8458359` fix(semantics): mover carry hysteresis, sub-threshold decay, resurrection event | **87 passed** (15.46 s) | suite-after-i6.log |
| + i7 `0003`,`0004` | clean (rc=0) | `a1d0ed2` semantics: project approach goals… ; `7bbd21a` docs: errata… | **93 passed** (16.37 s) | suite-after-i7-combined.log |

### Test-count reconciliation (def-level audit, exact)

```
i5 cca5987: +3 defs, −1 def (test_build_state_carries_semantics_and_stays_bounded removed; replaced by
            test_build_state_ships_the_compacted_semantics_digest + test_jev_digest_… + integration bound test) → net +2
i6 8458359: +6 defs, −1 def (test_sub_threshold_hits_only_refresh_freshness renamed+extended to …_until_confidence_fades) → net +5
i7 a1d0ed2: +6 defs, −0 (all in new tests/test_approach_standoff.py) → net +6;  7bbd21a docs-only → +0
collected defs: bec1d91=80 → cca5987=82 → 8458359=87 → HEAD=93
```

Caller estimate 80→82→85→86, ≈88 deviates because: (a) i6's "85" and i7's "86" are standalone counts on
bec1d91 (each already includes its own additions but none of the others'); (b) i7 was briefed as "+1 test"
but ships +6; (c) 80+2+5+6 = 93 exactly, and every def is accounted for — **no lost, duplicated, or
skipped tests; no test double-counts across the disjoint files**. The measured number (93) is the correct
one; the estimate ≈88 was arithmetically inconsistent with the packs' own reports.

## 2. Behavioral spot-checks on the combined tree

Reduced variants, run against the stacked clone (`repoA`), scripts copied from the packs' evidence dirs.

**(a) i5 digest** — `measure_digest.py --root repoA --store <scratch> --states <scratch> --out <scratch>`:

- 20-obj realistic roster through the real pipeline: `bound_check_20.post_bytes = **5236**`, under_6000 true.
  (Matches the expected ≈5236 B.)
- 30-obj: `map_objects=30, digest_objects=25 (capped), pre_bytes=10730, build_state_bytes=**5658**`
  (matches expected ≈5658 B; cap at 25 objects holds).
- Fit: pre 6000 B crossing at 9.42 objects → post at 34.94 objects (same shape as the i5 report).

**(b) i6 churn** — `i6-churn-driver.py --repo repoA --seeds 10 --out <scratch>`:

- `S2-mover-d0.5-s0.03`: newIDs mean/max **3.00/3**, vanished 0.00 (expected ≈3.0 ✓; 100-seed recorded
  value 3.01).
- `S4a-phantom-alive` and `S4b`: `firstvanish {3: 10}` — phantom vanishes at **pass 3 for all 10 seeds** ✓.
- `S3b-twopass-miss`: resurrections/announced 1.00/1.00 (resurrection `appeared` event fires) ✓.
- Determinism: `i6-churn-compare-runs.py results-patched.json i6-10seed-combined.json`
  → **"checked 1170 fields across 130 per-seed runs; mismatches=0"** — the combined tree reproduces the
  recorded patched run bit-for-bit on all shared seeds.

**(c) i7 approach E2E** — `REPO=repoA i7-approach-e2e.py`, rule arms (`cfg.required_standoff_m` = obj_r+0.35):

| obj_r | min distance to goal | reflexes | arrival | both modes |
|---|---|---|---|---|
| 0.15 | 0.040 m | `{}` | yes | jev + no-Jev |
| 0.30 | 0.050 m | `{}` | yes | jev + no-Jev |
| 0.40 | 0.030 m | `{}` | yes | jev + no-Jev |

Arrival 0.03–0.05 m / 0 trips — exactly the i7 report's acceptance band. The unpatched r=0.40 permanent
stall repro was skipped (optional per brief); notably the stall is also gone in the patched tree's raw arm.

## 3. Series artifacts (regenerated from the stacked clone)

```
$ git format-patch bec1d91..HEAD -o <evidence>/      # 0001..0004 (original commit messages preserved)
$ git diff bec1d91..HEAD > combined.diff             # 43353 B, whole-series squash
```

Artifact sha256s (full list in `sha256sums.txt`):

| artifact | sha256 |
|---|---|
| 0001-semantics-Jev-payload-ships-a-compact-state-digest.patch | 4ded2a127eb92e5d988e405759744f2ef9a888abbba43d3b513f8426ced6c138 |
| 0002-fix-semantics-mover-carry-hysteresis-sub-threshold-d.patch | d9a638a325c7a0266414a8ae466fe4cbb11f07c755fe97775e1174b2995f5419 |
| 0003-semantics-project-approach-goals-to-free-space-never.patch | 6819f5096013c0b8d6d003aadcefedfe12c4721dbfec90e70fdede47311ef9c4 |
| 0004-docs-errata-for-approach-standoff-vs-planner-inflati.patch | eb53dc67fdc6578a4972fd27580440e1a7de94972687826424cd5764acd21e08 |
| combined.diff | 817bd221e0303071f943d809b4ec1b24f1215bf936420642e62bd8c15fae7ac3 |
| workflow-transcript.md | 4c37a6309f9b01f001dc860958a89b179a7d6691743a8db09b18ec3de0a207a8 |
| conflict-log-master-now.md | 1b26027ec04c0303e442aa244ada90e25df1f3b9582d5bbb4a74c3658fa5faf5 |
| spot-i5-digest-combined.json | 7de6e724b4aad0a30e5fba2d595289f2528e66c46b92aea48b62f889d66f2fde |
| spot-i6-10seed-combined.json | dee0485762917c8f8589406275dbbfc784b257cf5f990cdecfe2de13e645aab0 |
| spot-i7-e2e-combined.json | 7381e23b0df6b42501ae4281c2117489c4afb8b580bb3eb4ebbccf6695f5bfbe |
| suite-after-i5.log / i6 / i7-combined | a639eac1…977 / 942f9439…af5 / 2ed20d0d…2d |
| suite-postmerge-series.log | 238a0fb3703620e7a7d466548c603b72b4fde486887b5ffb1da1405dd58d764a |

## 4. Rebase/merge plan onto master — empirical

Topology (from fresh fetch): **origin/master == `9c33ec0` == merge-base(master, review)**; master has
**0 commits** since; the review branch is 14 ahead. Any GitHub merge of PR #1 (ff or merge commit)
yields a master tree ≡ `bec1d91` tree (verified: simulated merge commit `ec6b914`, `git diff --stat bec1d91`
empty).

### (a) master-now (`9c33ec0`), `git am -3` per patch — conflicts recorded

- **i5 `0001` — CONFLICT**, 1 file, 2 hunks: `tests/test_semantics_schema.py` lines 89–93 (old def
  `test_build_state_carries_semantics_and_stays_bounded` vs incoming new def) and 101–141 (old body
  `size < 6000` vs incoming digest assertions). Class: **context-shift** — master still holds the pre-review
  region that the 14 review commits rewrote. Resolved in-session with
  `git checkout --theirs -- tests/test_semantics_schema.py; git add; GIT_EDITOR=true git am --continue`
  → clean (commit `df2935e` on scratch branch `series-master`).
- **i6 `0002` — CONFLICT**, 3 files, 6 hunks: `config.py` 149–155; `semantics.py` 255–259, 276–287,
  292–331, 369–381; `tests/test_semantics_merge.py` 111–206. Class: **context-shift + semantic overlap**
  (both sides edit the merge loop: master = pre-review `best_i, best_d`/label matching; patch = post-review
  + carry-hysteresis). Not mechanically resolvable ours/theirs; needs an intent merge. Left unresolved
  (moot post-merge); `git am --abort`.
- **i7 `0003`+`0004`** — commit 1 (`0003`): **CLEAN** via `am -3` (run.py + config.py auto-merge; the i7
  report's `git apply --check` failure on run.py was 2-way-only). Commit 2 (`0004`): **FAILS** —
  `CONFLICT (modify/delete)`: `docs/reviews/m1-semantics/m2-design.md` deleted in HEAD (absent on master).
  Class: inherent-for-master-now, **droppable** (docs-only, +0 tests; keep it if landing post-merge, where
  the file exists).

### (b) post-PR-merge master — verified clean

```
$ git checkout -B postmerge origin/master && git merge --no-ff origin/review/m1-semantics-audit
$ git am -3 0001.patch 0002.patch 0003.patch 0004.patch     # all rc=0, 4 commits
$ pytest -q -p no:cacheprovider
93 passed in 15.55s                                          # suite-postmerge-series.log
```

### (c) stacked-branch-now — the path executed for this task

Fresh clone at prtip `bec1d91` + `git am -3` in order → clean, 80→82→87→93. If this branch (or its
commits) is opened as PR #2 on top of `review/m1-semantics-audit`, it merges with PR #1 with no rebase.

### Recommended landing strategy (owner)

**Primary — post-PR-merge (all commands verified clean):**
```
git fetch origin
git checkout -b feature/m1-fixpacks-series origin/master          # after PR #1 is merged
git am -3 0001-semantics-Jev-payload-ships-a-compact-state-digest.patch
git am -3 0002-fix-semantics-mover-carry-hysteresis-sub-threshold-d.patch
git am -3 0003-semantics-project-approach-goals-to-free-space-never.patch
git am -3 0004-docs-errata-for-approach-standoff-vs-planner-inflati.patch   # skip if desired (docs-only)
python -m pytest -q -p no:cacheprovider                            # expect 93 passed
git push -u origin feature/m1-fixpacks-series
```
**Secondary — stack now on the review branch** (if PR #2 must exist before PR #1 merges):
```
git fetch origin
git checkout -b review/m1-fixpacks origin/review/m1-semantics-audit
git am -3 0001*.patch 0002*.patch 0003*.patch 0004*.patch
python -m pytest -q -p no:cacheprovider                            # expect 93 passed
```
**Not recommended:** applying the series directly onto master-now `9c33ec0` — it requires resolving the
i5 (2 hunks) and i6 (6 hunks) context conflicts and skipping `0004`; all of that evaporates once PR #1
merges, so it only creates a divergent tree.

If it *must* land on master-now: `git am -3` i5 → resolve schema test via `--theirs` (recorded above);
i6 needs a manual re-derivation of the merge-loop hunks against pre-review master (not attempted here —
deemed moot); i7 `0003` applies clean, `0004` skip (`git am --skip`).

## 5. Decisions & uncertainties

- **Decision:** reconciled the caller's expected 85/86/≈88 against measured 87/93 by auditing test defs
  per commit (added/removed sets above) rather than trusting any report; concluded 93 is correct and the
  ≈88 estimate was inconsistent. `git am -3` conflicts were recorded and classified, not force-resolved.
- **Decisions on master-now:** i6 conflicts deliberately left unresolved (moot post-merge; a wrong
  mechanical resolution would be worse than none). i5's conflict was resolved only to unblock the i7
  attempt in the same sequence (resolution recorded).
- **Uncertainty:** the post-merge simulation is local (`git merge --no-ff`); if master advances before
  PR #1 merges, the clean result still holds for the i5/i6/i7 code regions (they are relative to post-review
  files) but a fresh check is warranted at landing time.
- **Uncertainty:** i6's master-now conflicts are classified "context-shift + semantic overlap" by
  inspection of the conflict blocks; an intent-merge on master-now was not attempted, so the exact manual
  effort there is unmeasured.
- **Reduced scope:** spot-checks are reduced variants (10 seeds; rule arms only for i7; 30-obj capped path
  via the i5 driver). i6 determinism went beyond the brief (full-field comparison to the 100-seed run).

## 6. Limitations

- No hardware/live-camera validation (same sim-only caveat as all three packs).
- The i7 unpatched-stall comparison arm was skipped (optional per brief).
- Conflict line numbers refer to scratch working trees at conflict time (reproducible from the commands
  in `workflow-transcript.md`).
- `git am -3` does 3-way **textual** merges: textual cleanliness does not prove semantic coherence; the
  suite (93) and the three spot-checks are the coherence evidence for the combined tree.

## Artifacts

- Evidence dir: `/home/freakymustard/jev-rover-research/runs/20260929-0248/i14-m15-series-evidence/`
  (4 format-patch files, combined.diff, commit-log.txt, suite logs ×4, spot-check JSONs ×3,
  workflow-transcript.md, conflict-log-master-now.md, transcript-prtip.log, sha256sums.txt).
- Scratch clones: `/home/freakymustard/.hermes/cache/scratch/c6/work/repoA` (stacked, 93),
  `repoM` at `postmerge` (post-merge + series, 93) and `series-master` (master-now experiment).
