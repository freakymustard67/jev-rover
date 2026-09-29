# i5 — Jev-state digest: prototype implemented, measured through the real pipeline, verified

**Scope:** implement and validate the Jev-state compaction recommended by the M1 state-cost
audit (`runs/20260928-1930/w3c-state-cost.md` §6, option #1 + #2/#3/#4/#5/#6 folded in) on the
PR tip, and replace the under-sized bound test with one that runs the real integration path
(#8, per `docs/planning/m1-plan.md:345`).

**Repo state:** read-only respected — `/home/freakymustard/jev-rover` was never written to; all
work happened in the scratch clone `/home/freakymustard/.hermes/cache/scratch/i5/repo`
(`git clone https://github.com/freakymustard67/jev-rover` → `review/m1-semantics-audit` @
`bec1d91`), interpreter `/home/freakymustard/jev-rover/.venv/bin/python` (3.11.16),
`PYTHONDONTWRITEBYTECODE=1`. Nothing was pushed anywhere; `/tmp/opencode` untouched.

**Evidence dir:** `runs/20260929-0118/i5-state-digest-evidence/` (patch, scripts, raw outputs,
sha256 for every file in `sha256sums.txt`).

---

## TL;DR

* `SemanticMap.jev_digest()` shipped at **scene.py:318-364**; `build_state` now swaps
  `observed.semantics` for the digest (**tactics.py:206-213**). `Scene.to_dict()` and all
  disk/log artifacts keep the full 11-field schema — **unchanged**.
* Real-pipeline byte counts (`len(json.dumps(build_state(scene)))`, 8/9/10/15/20/30 fixtures,
  2 passes + jitter + one 0.40 m move + destination set):
  **pre 5681 / 5913 / 6134 / 7266 / 8398 / 10729 → post 4433 / 4497 / 4563 / 4918 / 5236 / 5658.**
  At 20 objects: **8398 → 5236 B, −37.7 %**; at 30: **5658 B** with the 25-entry cap engaged
  (`objects_more: 4`).
* Bound crossing (test convention, fit over the 6 points): **9.42 → 34.94 objects**; beyond the
  cap the payload stops growing, so any map up to `MAX_DETECTIONS = 64` stays ≈5.7 KB (§3).
* Integration-path bound (20 realistic fixtures, one pass — the new test): digest **5115 B < 6000**,
  full `scene.to_dict()` **7590 B < 10 000**.
* Tests: **82 passed** in the patched clone and in a fresh clone after `git am` (80 pre-existing
  + 2 added; one schema test adjusted in place).
* Patch: `i5-state-digest-evidence/i5-state-digest.patch`, sha256
  `73717eac6725b9caf9540e1ddea20f8f933766dabb3d74c05a69c292b8095846`; applies cleanly via
  `git am` on a fresh clone at `bec1d91`; on master `9c33ec0` three files apply cleanly and
  `tests/test_semantics_schema.py` does not (expected, §4).

---

## 1. What was implemented

### 1.1 Digest shape (`SemanticMap.jev_digest`, scene.py:303-364)

```python
JEV_MAX_OBJECTS = 25           # cap on the shipped list, nearest-to-rover first
JEV_MIN_CONFIDENCE = 0.5       # mirrors SemanticsConfig.min_confidence
JEV_DIFF_IDS = 3               # first N ids kept per diff bucket
```

View = `{age_s, passes, model, objects[], destination, diff}` (+ `objects_more` only when the
cap bites):

* per object: `{label, x (2 dp), y (2 dp), confidence}`; `height_suspect: true` only when set,
  `motion` only when non-static;
* dropped from the Jev view: `id`, `sources`, `plane_assumed`, `first_seen_s`, `last_seen_s`;
* objects with `confidence < min_confidence` (0.5, `config.py:144`) are dropped — they can never
  be destinations;
* list sorted nearest-to-rover (`pose` passed by `build_state`) then capped at 25, remainder
  counted in `objects_more`;
* `diff` = counts only `{appeared, moved, vanished}` + first ≤3 ids per non-empty bucket.

Real example (`states/post_20.json`): 19 of 20 objects shipped; `"blue mat"` carries
`"motion": "moved"` (the fixture that moved 0.40 m), `height_suspect: true` on four objects;
`diff = {"appeared":0,"moved":1,"vanished":0,"moved_ids":["obj_0004"]}`.

### 1.2 Wiring (tactics.py:206-213)

```python
def build_state(scene: Scene) -> dict:
    observed = scene.to_dict()
    mission = observed.pop("mission", {"mode": "goto"})
    if scene.semantics is not None:
        observed["semantics"] = scene.semantics.jev_digest(pose=(scene.pose.x, scene.pose.y))
    return {"robot": ROBOT, "mission": mission, "observed": observed}
```

Only the Jev payload shrinks. `Scene.to_dict()` (scene.py:422 after this patch; :363 at bec1d91),
`SemanticStore._save_latest`
(semantics.py:414-426), `events.jsonl` (semantics.py:428-442), and `viz.py` are untouched;
`tests/test_semantics_schema.py` asserts the on-disk object record keeps all 11 fields.

### 1.3 Consumer audit — which semantics fields Jev actually uses

* `grep -n "semantic" tactics.py` (pre-patch) → **no matches**: the six QUESTIONS/MANEUVERS
  rubrics inspect only `observed.obstacles`, `observed.dynamics`, `observed.hardware`,
  `observed.goal`. Today's Jev consumers do not read *any* semantic field, so nothing they use
  was dropped; the digest keeps label/position/confidence, freshness (`age_s/passes/model`),
  `destination` and the diff — the fields the plan and M3 name, and the ones a label-aware
  judgment ("the mug is on the path") needs.
* `run.py:329` (`scene.semantics = runner.snapshot(t)`), `run.py:331-343` (`--find` via
  `resolve_destination`) and the summary all read the full Scene map, not the payload — unchanged.
* `viz.py` never reads semantic fields (no "objects" match).
* `tools/smoke_jev.py` builds scenes without a semantic map → unaffected.

## 2. Measured before/after (real pipeline)

Driver: `i5-state-digest-evidence/scripts/measure_digest.py` — SyntheticRoom → Perception
(8 warm frames) → `FakeVision.from_world` → `SemanticsRunner` (pass 1 = all appear, pass 2 =
+2 cm jitter on every object and one real 0.40 m move) → `snapshot(6.0)` → destination set →
`build_state`. Exactly the w3c audit's method; roster copied verbatim.

| fixtures | map objects | pre B | post B | saved | saved % | shipped | objects_more |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 8  | 8  | 5681  | **4433** | 1248 | 22.0 | 8  | – |
| 9  | 9  | 5913  | **4497** | 1416 | 23.9 | 9  | – |
| 10 | 10 | 6134  | **4563** | 1571 | 25.6 | 10 | – |
| 15 | 15 | 7266  | **4918** | 2348 | 32.3 | 15 | – |
| **20** | 20 | **8398** | **5236** | **3162** | **37.7** | 19 (dog bowl 0.42 dropped) | – |
| 30 | 30 | 10729 | **5658** | 5071 | 47.3 | 25 | 4 |

`pre` is the old shape (`scene.to_dict()` + `robot`/`mission`, mission popped) computed in the
same run; on a pristine `bec1d91` clone the same driver asserts reconstruction == the real old
`build_state` output (passed, byte-identical: 5681/5913/6134/7266/8398/10729). Those pre numbers
sit +7…13 B above the w3c report's (5674/5905/6125/7253/8397/10719) because w3c measured master
`9c33ec0`, one file-equivalent of review commits behind the tip; the pipeline map itself is
byte-identical across clones (determinism check in §5).

Components at 20 objects (post): floor 3561 (robot 716 + mission 65 + scene/tracks/goal/sectors
2751-ish + sweep null), objects **1406** (19 entries ≈ 74 B each; was 4567 in the full schema),
destination 136, diff 79, scalars 54.
Compact/wire convention (`pydantic_core.to_json`, w3c §2): scales with the same ratios.

**New test path** (`scripts/probe_bound.py`, exact replica of the new integration test; one pass,
20 realistic fixtures): map 20 objects, digest ships 19, **digest payload 5115 B** (test
convention), **`scene.to_dict()` 7590 B**. Both under their bounds (6000 / 10 000).

## 3. Bound crossing and the cap

* Fit over the six points, test convention: pre `3841.5 + 229.0·N` → 6000 B at **9.42 objects**
  (audit: 9.46 on master). Post `4011.4 + 56.9·N` → 6000 B at **34.94 objects**
  (residual 86 B because the 30-object point is inside the cap).
* The cap makes the fit moot beyond 25 shipped entries: measured object block 1809 B for 25
  realistic entries at 30 fixtures; with N ≥ 25 the shipped list is constant, `objects_more`
  grows by ≤2 chars, diff ids are bounded at ≤3 per bucket. Derived worst case for
  `MAX_DETECTIONS = 64` (semantics.py:49): ≈ **5.66–5.7 KB — under the bound at any N**.
  (Derived arithmetically from the 30-object measurement, not measured at 64 — a 64-object
  synthetic roster would need >0.5 m spacing for the dedupe radius.)

## 4. Tests (82 passed) and patch verification

* `tests/test_semantics_schema.py:89` `test_build_state_ships_the_compacted_semantics_digest` —
  digest shape in `build_state`, **and** the raw `to_dict()` object record still has the 11
  fields (disk schema unchanged). The old byte-bound assertions (<6000, <3000 delta on the
  hand-built fixture) are gone — serialization only, as recommended.
* `tests/test_semantics_schema.py:109` `test_jev_digest_omits_defaults_drops_low_confidence_and_caps`
  — unit: drop <0.5, cap 25 + `objects_more`, omission of default `motion`/`height_suspect`,
  counts-only diff with first-3 ids, destination round-trip.
* `tests/test_semantics_integration.py:141` `test_state_size_bound_on_the_real_path` — per
  m1-plan.md:345: SyntheticRoom + Perception (6 warm frames) + `FakeVision.from_world`
  (roster at :117, 20 realistic fixtures) + one pass → assert 20 map objects, digest payload
  < 6000 B, and looser `scene.to_dict()` < 10 000 B.
* Suite at tip: **82 passed in 15.6 s** (`pytest -q -p no:cacheprovider`, was 80).
  Fresh clone at `bec1d91` + `git am` of the patch → **82 passed in 16.3 s**.
* `git apply --check` on a fresh **master 9c33ec0** clone: **fails** only on
  `tests/test_semantics_schema.py:86` (`patch does not apply`); `scene.py`, `tactics.py`,
  `tests/test_semantics_integration.py` apply cleanly; `git apply --3way --check` reports
  clean for the three and "with conflicts" for the schema test. Cause: the 14 commits between
  `9c33ec0` and `bec1d91` rewrote exactly that region (the test this patch adjusts). Not forced.
  Full log: `i5-state-digest-evidence/patch_verification.txt`.

## 5. Reproducibility (re-run anything)

```bash
# patched clone at review/i5-state-digest (commit 3c30fcb), suite:
cd /home/freakymustard/.hermes/cache/scratch/i5/repo
PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python -m pytest -q -p no:cacheprovider

# before/after measurement (spot-check driver; ~40 s):
E=/home/freakymustard/jev-rover-research/runs/20260929-0118/i5-state-digest-evidence
PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python $E/scripts/measure_digest.py \
  --root /home/freakymustard/.hermes/cache/scratch/i5/repo \
  --store /home/freakymustard/.hermes/cache/scratch/i5/store_patched \
  --states $E/states --out $E/measure_results_digest.json

# pre-digest numbers with the REAL old build_state (pristine clone at bec1d91):
PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python $E/scripts/measure_digest.py \
  --root /home/freakymustard/.hermes/cache/scratch/i5/pristine2 \
  --store /home/freakymustard/.hermes/cache/scratch/i5/store_pristine \
  --states $E/states_pristine --out $E/measure_results_pristine.json   # asserts recon == real

# the new integration test alone / the exact test-path byte probe:
PYTHONDONTWRITEBYTECODE=1 .../python -m pytest -q tests/test_semantics_integration.py::test_state_size_bound_on_the_real_path
PYTHONDONTWRITEBYTECODE=1 .../python $E/scripts/probe_bound.py --root <clone>
```

Determinism: full re-runs of both driver invocations reproduced byte-identical outputs
(`measure_results_*.json` and every `states*/post_*.json` hash unchanged between runs — see
`sha256sums.txt`; the store is rebuilt from scratch each run, no wall-clock input). The parent
cron can diff a re-run's stdout (`driver_patched_stdout.txt`, `driver_pristine_stdout.txt`)
against the recorded files.

## 6. Verified vs assumed

**Verified (measured/executed):** all byte numbers through the real pipeline; pristine-clone
equality of reconstruction vs the old build_state; digest shape incl. drop/cap/omission flags
(unit test + real payloads); disk schema unchanged (unit test on `to_dict`); `git am` on fresh
`bec1d91` + full suite green; `git apply --check` behaviour on master; determinism across runs
and clones; subject repo untouched (all writes confined to the i5 scratch dir and the evidence
dir).

**Assumed / not measured:** the 64-detection worst case is derived from the 25-cap + the
measured 30-object point, not measured at 64 objects; the `min_confidence` used by the digest is
the config *default* 0.5 (a room that overrides `cfg.semantics.min_confidence` is not consulted —
see decision 1); token savings are not re-measured (no tokenizer installable; the w3c §4 anchor
applies unchanged: −3162 B/call at 20 objects ≈ −1.2k tok char/4 / −1.9k upper at refresh_s
cadence).

## 7. Caveats

* If Jev must *name* vanished/moved objects across calls, the ≤3 raw ids reference objects whose
  `id` no longer ships — see decision 2.
* A vision model returning pathologically long labels can still inflate the 25 shipped entries;
  the cap bounds the count, not label length (same exposure as before, reduced 25×).
* `objects_more` counts only cap-omitted objects; sub-`min_confidence` drops (e.g. dog bowl) are
  silent in the payload. If Jev should know "things exist but are unreliable", that is a
  decision (currently they cannot be destinations, so silence is defensible).
* The digest is a build_state-side view only: `Tactician._key` still has no semantic fields, so
  a new object alone does not trigger a call (unchanged from M1; M3 owns the fingerprint).

## 8. What to fold in / decision needed

1. **`min_confidence` plumbing:** `build_state` has no config, so `jev_digest` defaults to 0.5
   (the config default, config.py:144). If any room overrides it, either thread the value
   through (`Tactician` → `build_state`) or accept the fixed 0.5. *Owner decision.*
2. **Diff ids vs labels:** the digest drops `id` from the object list but echoes `moved_ids`
   et al. Replacing the id list with *labels* ("what moved" is more useful than "obj_0004") is
   a one-line change; costs a few bytes. *Owner decision.*
3. **`objects_more` key name / cap value (25):** anything larger needs a conscious edit; the
   6 KB bound holds at any cap ≤ ~55 entries by the fit.
4. **README.md:120** ("labeled objects ... resolved destination, per-pass diff") could gain one
   sentence that the Jev payload is the digest while disk keeps the full schema. Not touched here.
5. **M4 sweep:** if confirmations ever ride the Jev state, keep counts/verdicts (audit #7),
   not per-object records.

*Report written disk-first at `runs/20260929-0118/i5-state-digest.md`. All numbers above
reproduce from the recorded scripts/outputs in `runs/20260929-0118/i5-state-digest-evidence/`.*
