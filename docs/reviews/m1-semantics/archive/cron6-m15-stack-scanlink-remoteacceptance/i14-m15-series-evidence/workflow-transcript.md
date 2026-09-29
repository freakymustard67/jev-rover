# i14 workflow transcript — commands + outputs (consolidated)

Host: Linux; timezone IST. Python: `/home/freakymustard/jev-rover/.venv/bin/python` (3.11.16).
All git work in scratch only: `/home/freakymustard/.hermes/cache/scratch/c6/`. Never pushed.

## 1. Clone at prtip + sha256 of patches

```
$ git clone /home/freakymustard/jev-rover repoA   # local mirror of freakymustard67/jev-rover
$ git remote set-url origin https://github.com/freakymustard67/jev-rover
$ git fetch origin review/m1-semantics-audit:prtip
$ git checkout prtip && git rev-parse HEAD
bec1d91fe17682ce38d24e334c94c67fa4bcc685          # == required tip

$ sha256sum i5-state-digest.patch i6-churn-hysteresis.patch i7-approach-standoff.patch
73717eac6725b9caf9540e1ddea20f8f933766dabb3d74c05a69c292b8095846  i5-state-digest-evidence/i5-state-digest.patch   # MATCH
0916094006af3952d39649c93e20ee53f1d1c5665a4a97c4aaef024656bf00fa  i6-churn-hysteresis-evidence/i6-churn-hysteresis.patch  # MATCH
3b222edd251db9428e4aca459956c9ad2916641fee96f1e14a0541f758fd6e91  i7-approach-standoff-evidence/i7-approach-standoff.patch # MATCH
```

## 2. Baseline suite (prtip)

```
$ PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python -m pytest -q -p no:cacheprovider
80 passed in 14.54s
```

## 3. Apply i5 → suite

```
$ git am -3 .../i5-state-digest.patch
Applying: semantics: Jev payload ships a compact state digest      # rc=0, clean
$ git log --oneline bec1d91..HEAD
cca5987 semantics: Jev payload ships a compact state digest
$ pytest -q -p no:cacheprovider
82 passed in 15.65s          # 80 + 3 added − 1 removed (obsolete bounded test) = +2 net
```

## 4. Apply i6 → suite

```
$ git am -3 .../i6-churn-hysteresis.patch
Applying: fix(semantics): mover carry hysteresis, sub-threshold decay, resurrection event   # rc=0, clean
$ git log --oneline bec1d91..HEAD
8458359 fix(semantics): mover carry hysteresis, sub-threshold decay, resurrection event
cca5987 semantics: Jev payload ships a compact state digest
$ pytest -q -p no:cacheprovider
87 passed in 15.46s          # 82 + 6 added − 1 replaced (rename+extend) = +5 net
```

## 5. Apply i7 → combined suite

```
$ git am -3 .../i7-approach-standoff.patch
Applying: semantics: project approach goals to free space; never splice a blocked goal (w4b F1/F2)
Applying: docs: errata for approach standoff vs planner inflation (w4b/I7 audit)            # rc=0, clean
$ git log --oneline bec1d91..HEAD
7bbd21a docs: errata for approach standoff vs planner inflation (w4b/I7 audit)
a1d0ed2 semantics: project approach goals to free space; never splice a blocked goal (w4b F1/F2)
8458359 fix(semantics): mover carry hysteresis, sub-threshold decay, resurrection event
cca5987 semantics: Jev payload ships a compact state digest
$ pytest -q -p no:cacheprovider
93 passed in 16.37s          # 87 + 6 added = +6 net
```

### Test accounting (per-commit def-level audit)

```
i5 cca5987: +3 test defs, −1 test def  → net +2   (80→82)
i6 8458359: +6 test defs, −1 test def  → net +5   (82→87)
i7 a1d0ed2: +6 test defs, −0           → net +6   (87→93);  7bbd21a docs-only, +0
collected defs: bec1d91=80, cca5987=82, 8458359=87, HEAD=93
```
Reconciliation of caller estimate (80→82→85→86, combined ≈88): the packs'
standalone counts (i6: "85 = 80+5"; i7: "86 = 80+6") each ignored the fixes
already stacked beneath them; the actual stack is additive because the three
packs touch disjoint test files (schema+integration / merge / new approach file):
80+2+5+6 = 93. i7's brief "+1 test" is wrong — its own report says "+6 new".

## 6. Spot checks on the combined tree (repoA @ 93-test tip)

(a) i5 digest (measure_digest.py from i5 evidence, --root repoA, scratch store):
```
{"n_fixtures": 30, "map_objects": 30, "digest_objects": 25, "objects_more": 4,
 "pre_bytes": 10730, "build_state_bytes": 5658, "post_bytes": 5658, ...}
bound_check_20: {"post_bytes": 5236, "under_6000": true}
fit_pre : 6000 B at 9.42 objects ;   fit_post: 6000 B at 34.94 objects
```
→ 20-obj real pipeline **5236 B** (expected ≈5236 ✓), 30-obj capped **5658 B** (expected ≈5658 ✓).

(b) i6 churn (i6-churn-driver.py --repo repoA --seeds 10):
```
S2-mover-d0.5-s0.03:  newIDs 3.00/3 mean/max   (expected ≈3.0 ✓)
S4a-phantom-alive:    firstvanish {3: 10}      (pass 3, all 10 seeds ✓)
S4b-phantom-alive-s0.10: firstvanish {3: 10}   (✓)
S3b-twopass-miss: resurrections/announced 1.00/1.00 (✓)
```
Determinism vs recorded 100-seed run:
```
$ python i6-churn-compare-runs.py results-patched.json i6-10seed-combined.json
checked 1170 fields across 130 per-seed runs; mismatches=0
```

(c) i7 approach E2E (i7-approach-e2e.py, REPO=repoA) — rule arms, both modes:
```
r=0.15 rule: min_goal=0.040 arr=1 reflexes={}   (0 trips ✓)
r=0.30 rule: min_goal=0.050 arr=1 reflexes={}   (0 trips ✓)
r=0.40 rule: min_goal=0.030 arr=1 reflexes={}   (0 trips ✓)
```
Arrival 0.03–0.05 m, 0 trips — matches the i7 report's acceptance (0.03–0.05 / 0 / 0.35–0.39).
(The r=0.40 raw-arm permanent stall is also gone on the patched tree.)

## 7. Series artifacts

```
$ git format-patch bec1d91..HEAD -o <evidence>     # 0001..0004
$ git diff bec1d91..HEAD > combined.diff           # 43353 B
```

## 8. master_9c33ec0 landscape

```
origin/master == 9c33ec0 == merge-base(master, review) ; master has 0 commits since;
review branch = 14 commits ahead.  PR #1 merge (ff or merge-commit) ⇒ master tree ≡ bec1d91.
```

### master-now `git am -3` attempts (branch series-master @ 9c33ec0)

```
i5: CONFLICT tests/test_semantics_schema.py, 2 hunks (lines 89–93, 101–141)
    resolved: git checkout --theirs -- tests/test_semantics_schema.py; git add; git am --continue → ok (df2935e)
i6: CONFLICT config.py (149–155), semantics.py (255–259, 276–287, 292–331, 369–381),
    tests/test_semantics_merge.py (111–206) → aborted (needs intent merge; moot post-merge)
i7: commit1 CLEAN (run.py+config.py auto-merged by am -3, despite report's 2-way apply failure);
    commit2 FAILS modify/delete docs/reviews/m1-semantics/m2-design.md (absent on master; docs-only, droppable)
```

### post-PR-merge simulation (branch postmerge)

```
$ git checkout -B postmerge origin/master && git merge --no-ff origin/review/m1-semantics-audit
ec6b914 Merge PR #1 (review/m1-semantics-audit)
$ git diff --stat bec1d91        # EMPTY → merged tree == bec1d91 tree
$ git am -3 i5.patch i6.patch i7.patch
Applying: semantics: Jev payload ships a compact state digest
Applying: fix(semantics): mover carry hysteresis, sub-threshold decay, resurrection event
Applying: semantics: project approach goals to free space; never splice a blocked goal (w4b F1/F2)
Applying: docs: errata for approach standoff vs planner inflation (w4b/I7 audit)   # all rc=0
$ pytest -q -p no:cacheprovider
93 passed in 15.55s
```

Raw logs in this dir: suite-after-i5.log, suite-after-i6.log, suite-after-i7-combined.log,
suite-postmerge-series.log, transcript-prtip.log, conflict-log-master-now.md.
