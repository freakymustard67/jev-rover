# Jev state-size / token cost with a full semantic map (M1 semantics layer)

**Scope:** measure what `tactics.build_state` (tactics.py:206) costs when `scene.semantics` is a
realistic map of 10–20 objects, whether the 6 KB bound in `tests/test_semantics_schema.py:99`
covers the real path, and what compaction M3 should specify.
**Repo state:** `/home/freakymustard/jev-rover` @ `9c33ec0` (M1 semantics in `f352f61`). Read-only
respected: no writes, no git writes to the subject repo; all experiments ran in a scratch clone
(`~/.hermes/cache/scratch/wave3/w3c/clone`) with a scratch store dir. Interpreter:
`/home/freakymustard/jev-rover/.venv/bin/python` (3.11.16), `PYTHONDONTWRITEBYTECODE=1`.

**TL;DR — the bound is busted by the real path.** A realistic 10-object map through the real
pipeline measures **6125 B** on the exact constructor the test uses (`len(json.dumps(build_state(scene)))`)
— **+125 B over the 6000 B bound** — and the test's own fixture measures 4333 B, so the test passes
with 1667 B of headroom it does not actually have. The bound is crossed at **≈9.5 objects** (test
convention) / **≈12.2** (compact wire bytes). Worst contributor is not labels (10% of bytes) but the
per-object schema boilerplate (**67%**): `plane_assumed`, `sources`, `height_suspect`, `motion`,
`id`, `first_seen_s`, `last_seen_s`. Nothing anywhere truncates or compacts today.

---

## 1. Finding — does the 6 KB test exercise the real path?

`tests/test_semantics_schema.py:89-99` **does call the real `build_state`** (line 96) with a real
`Scene` and a real `SemanticMap` — no stub, no monkeypatch. But the *inputs* are two fixtures that
are much cheaper than reality, and it is that gap the bound fails to cover:

| input | test fixture | realistic (this measurement) |
|---|---|---|
| Scene | `Scene(t=5.0)` — **all defaults**: no sectors, no tracks, no goal, empty `free_runs_deg` → non-semantic state **1225 B** | real perception scene (warmed synthetic room, goal + path fields) → non-semantic state **2751 B** |
| objects | 10 × `{id,label:"thing i",x=1.0+0.1i,y=2.0,confidence:0.9}`; all other per-object fields at **defaults** | 10–20 × real projected objects (real labels, `last_seen_s`, `height_suspect`, EMA positions) |
| map tail | `diff` all-empty (55 B), `destination: null` (21 B) | `diff` with a `moved` id (65 B), `destination` populated (151 B) |
| measured | **4333 B — PASSES with 1667 B headroom** | **6125 B at 10 objects — FAILS the same assertion by +125 B** |

The fixture is ~1.41× smaller in total and ~2.25× smaller in its non-semantic scene than the real
path. `docs/planning/m1-plan.md:93-95` (C5) says M1 "adds a state-size bound test (10 objects →
state < 6 KB)" and line 345 places it in the **integration** tests ("State-size bound: 10 objects →
`len(json.dumps(build_state(scene)))` < 6 KB"); what actually shipped is a hand-built fixture in the
schema test. The full 78-test suite passes (`78 passed in 13.64s`) while the real path exceeds the
bound — i.e. **the bound is asserted on a construction that cannot fail it for the reason that
matters**. That is itself the finding: the test needs to run through `SyntheticRoom` → `Perception`
→ `FakeVision` → `SemanticStore.merge` → `snapshot` → `build_state`.

## 2. Measured sizes (real code path)

Scenario: `config/room.synthetic.json` (6.4 × 3.6 m), `Perception` warmed 8 frames, `FakeVision.from_world`
with N realistic floor objects, `SemanticsRunner` (store dir in scratch), pass 1 = all appear,
pass 2 = +2 cm jitter on every object (exercises EMA) and one real 0.40 m move, `snapshot(6.0)`
(age 1.0 s), `runner.set_destination(...)` for the object a `--find` would resolve, then the real
`tactics.build_state(scene)`.

Sizes: `test` = `len(json.dumps(state))` (the bound's convention, default separators);
`wire` = `len(pydantic_core.to_json(state))` — what `typesafe_sdk` actually serializes
(`typesafe_sdk/_core/json.py:25-26`), ~10% smaller.

| scenario | objects | test bytes | wire bytes |
|---|---|---:|---:|
| **baseline: `semantics: null`** (same scene, real perception) | 0 | **3566** | 3227 |
| realistic, 8 objects | 8 | 5674 | 5133 |
| realistic, 9 objects | 9 | 5905 | 5342 |
| **realistic, 10 objects** | 10 | **6125** ✗ | 5540 |
| realistic, 15 objects | 15 | **7253** ✗ | 6558 |
| **realistic, 20 objects** | 20 | **8397** ✗ | 7592 |
| realistic, 30 objects | 30 | 10719 ✗ | 9694 |
| 20 objects, no destination | 20 | 8267 | 7473 |
| 20 objects, `sweep` populated (4 confirmations, M4 preview) | 20 | 8782 | 7943 |
| **cheapest realistic 10** (single pass, no destination) | 10 | **6406** ✗ | 5789 |
| 20 objects, single pass (fresh map) | 20 | 8696 | 7850 |
| *for reference:* the shipped test fixture | 10 | *4333* ✓ | *3942* |

Note the "cheapest realistic 10" row: even one pass with no destination is **over** the bound,
because a fresh map's `diff.appeared` lists all 10 ids.

## 3. Where the bound sits, and what crosses it

Least-squares fit over the six realistic points (8/9/10/15/20/30), residual ≤ 17.4 B:

* `test bytes(N) ≈ 3812.7 + 229.9·N` → **6000 B at N ≈ 9.46 objects**
* `wire bytes(N) ≈ 3468.4 + 207.1·N` → 6000 B at N ≈ 12.23 objects

So with today's uncompacted schema a **10-object map is the ceiling** on the test convention —
the exact number M1 pinned as the pass case — and it is already over. Extrapolated worst case:
`MAX_DETECTIONS = 64` (semantics.py:49) → **≈18.5 KB ≈ 7.3k tok** state-only (extrapolated from the
fit; the 30-object point is measured at 10719 B).

**Component breakdown, 20 objects (test convention):**

| block | bytes |
|---|---:|
| fixed floor: `robot` 716 + `mission` 65 + scene/tracks/goal/sectors 2751 + null `semantics`/`sweep` keys 34 | 3566 |
| `semantics.objects[]` | 4566 |
| `semantics` scalars (`age_s`, `passes`, `model`) | 54 |
| `semantics.diff` | 65 |
| `semantics.destination` | 151 |
| **total** | **8397** |

(Component sum 8402 vs measured 8397: the null `semantics`/`sweep` keys are counted in the floor
and replaced by the populated block's boundary keys.)

**Per-object field costs, 20 objects (test / wire):**

| field | test B | wire B | share |
|---|---:|---:|---:|
| `plane_assumed` (`"floor"`, always) | 520 | 500 | 11% |
| `height_suspect` (false 19/20) | 480 | 460 | 11% |
| `label` (real names, e.g. "reusable shopping bag") | 467 | 447 | 10% |
| `sources` (`["vision"]`, always) | 460 | 440 | 10% |
| `first_seen_s` | 420 | 400 | 9% |
| `last_seen_s` | 400 | 380 | 9% |
| `motion` ("static" 19/20) | 399 | 379 | 9% |
| `confidence` | 396 | 376 | 9% |
| `id` | 360 | 340 | 8% |
| `y` | 332 | 312 | 7% |
| `x` | 279 | 259 | 6% |

**Answer to "worst realistic contributor":** the seven *constant-ish* fields
(`plane_assumed` + `sources` + `height_suspect` + `motion` + `first_seen_s` + `last_seen_s` + `id`)
= **3040 B, 67% of the objects block**. Labels are 467 B (10%); positions 611 B (13%) — and of those
only 171 B are recoverable by rounding `x,y` to 2 dp (EMA-produced floats reach 19-character reprs,
measured max 19). Labels and precision are *not* the problem; schema boilerplate is. `diff` is
cheap (65 B steady state; 293 B when it lists all 20 ids on the first pass — a +228 B one-off, from
the single-pass comparison); `destination` (151 B) is the cheapest useful field in the whole block.

## 4. Token cost

**Method.** No tokenizer exists in the venv (`tiktoken`/`tokenizers`/`transformers`/`openai` all
absent; installing tiktoken into a scratch venv was blocked by the package-security gate), so:
(1) stated char/4 heuristic — a *lower bound* for JSON-heavy text; (2) an anchor derived from four
real live runs in `runs/summary_*.json`: 3531/3534/3538/3537 **tokens per call** (in+out) for a
payload of baseline state 3566 B + question rubrics 5394 B ≈ 8960 B → **≥2.53 bytes/token**, and
since the 3535 includes output tokens, 2.53 is a *floor* on bytes/token, making the derived token
counts an *upper bound*. Real values sit between char/4 and the 2.53 anchor.

| payload | bytes | tokens char/4 | tokens @2.53 B/tok (upper) |
|---|---:|---:|---:|
| baseline state, no semantics | 3566 | 892 | 1409 |
| state, 10 objects | 6125 | 1531 | 2421 |
| state, 20 objects | 8397 | 2099 | 3319 |
| state, 30 objects | 10719 | 2680 | 4237 |
| fixed question rubrics (paid every call) | 5394 | 1349 | 2132 |
| full prompt, 20 objects (state + rubrics) | 13791 | 3448 | 5451 |

**Budget impact.** The semantics block adds **2559 B at 10 objects** and 4831 B at 20 (vs. the 3566 B
baseline) = **+0.6–1.0k tokens (char/4) to +0.9–1.9k tokens (upper) per call**. With
`refresh_s = 6 s` (tactics.py:38) a 30 s run makes at least 5 calls and at most 60 (2 Hz cap) →
**+3k to +115k tokens per 30 s run**, against the ~102k tokens those historical 30 s runs consumed
*in total*. An uncompacted 20-object map roughly doubles a run's token bill; a digest (below) cuts
the map's contribution by 60–75%. Also worth pinning: the fixed rubrics (5394 B ≈ 2.1k tokens) are
**1.5× the entire baseline state** and 64% of a 20-object state — compaction of the state is a
modest lever next to it, but it is the lever M3 owns.

`build_state` itself is cheap: **1.45 ms** (10 objects) / **1.61 ms** (20 objects) per call, on the
control-loop thread inside `Tactician.offer` (tactics.py:287) — ~3% of a 20 Hz loop budget; adding a
compaction step there is effectively free.

## 5. Truncation / compaction today: none

* `grep -E 'truncat|compact|MAX_BYTES|max_'` over `tactics.py`, `semantics.py`, `run.py`,
  `scene.py`: **no size guard, no truncation, no compaction** anywhere in the state path.
* `build_state` (tactics.py:206-210) pops only `mission` and ships `scene.to_dict()` whole:
  `{"robot": ROBOT, "mission": mission, "observed": observed}`. `observed` includes
  `semantics`, `sweep`, all `sectors`, `tracks`, `goal`, `hardware`, `quality`. The prior review's
  field list needs one correction: **there is no `attrs` field and no event/history list in
  `SemanticObject`/`SemanticMap`** (scene.py:269-308) — events go only to `events.jsonl` on disk
  (semantics.py:356-370). What actually rides: `id, label, x, y, confidence, sources,
  plane_assumed, height_suspect, first_seen_s, last_seen_s, motion` per object, plus
  `age_s/passes/model/destination/diff` per map. `sweep` is always `None` in M1 (`run.py:297`
  assigns only `scene.semantics`; nothing ever sets `scene.sweep`).
* SDK side: `serialize()` → `pydantic_core.to_json` (compact) with **no size check**; the only
  truncation in the SDK is `MAX_ERROR_BODY_LENGTH` on *error* strings (typesafe_sdk/_core/errors.py:94).
* What happens if the state is too big: it is sent verbatim; if the API rejects it, `Tactician._worker`
  (tactics.py:328-335) catches it, sets the judgment to `DEFAULT` (`hold_course`, all confidences
  0.0 — the rover keeps driving on the planner + reflex layer) with `source="error:..."`, and
  **clears `_last_key` so the same scene is re-asked immediately** — at up to `min_dt` = 0.5 s,
  burning `call_budget` (400) attempts. Oversize therefore degrades to: no advisory input at 2 Hz
  plus a burned budget, not a smaller-but-valid state. (Whether the API rejects an ~8 KB state is
  **not verified** — no live calls were made; nothing in the repo or SDK guards it client-side.)
* Related design note for M3: `Tactician._key` (tactics.py:254-270) contains **no semantic fields**.
  A change that only alters the map (a new object appearing) never triggers a call; the new
  information reaches Jev only on the 6 s `refresh_s` re-ask or when geometry also changes. So the
  M3 "one-line diff" is only *read* at refresh cadence unless `_key` learns a coarse semantic
  fingerprint (e.g. counts + appeared/moved ids). Pairing a diff in the payload with a key change
  increases calls — budget the 2 Hz cap deliberately.

## 6. Recommendation for M3 (ordered, measured savings at 20 objects)

All savings below are measured on the 20-object realistic state (8397 B, test convention); the
"bound crossing" column is where `len(json.dumps(build_state))` hits 6000 B (today: 9.5 objects).

| # | action | ref | saves | state @20 obj | crossing |
|---|---|---|---:|---:|---|
| **1** | **Send a labels-positions digest instead of the full object list**: `[{"label","x"(2dp),"y"(2dp),"confidence"}]` + keep `age_s/passes/model`, `destination`, one-line diff. Compact at `build_state` — the single hot spot — or add `SemanticMap.jev_digest()` next to `snapshot()` so the shape lives with the map | tactics.py:206-210; semantics.py:322-333 | **3210 B (70%)** | 5192 B | **32 objects** |
| **1b** | Same, without `confidence` (`{label,x,y}`) | " | 3606 B (79%) | 4796 B | 45 objects |
| **1c** | List form `["label",x,y]` (positional, no keys) — 29 B/object | " | 3986 B (87%) | 4416 B | 75 objects (cap at 64) |
| **2** | **Omit constant per-object fields / null-omission** (the SDK already uses this idiom, `_omit_none` in `_core/question_types.py:70-74`): drop `plane_assumed`, `sources`, `id`, timestamps; keep `height_suspect` and `motion` **only when true/"moved"** | scene.py:269-282 | 2711 B for the full-schema variant (R1: 5691 B @20, crossing 25); included in #1 above | | |
| **3** | **Counts-only diff**: `{"appeared":3,"moved":1,"vanished":0}` (plus first ≤3 ids if Jev must name them) | semantics.py:295-299 | 7 B steady state; +228 B avoided on the first pass (65 → 293 B when all 20 ids are listed) | | |
| **4** | **Round `x`,`y` to 2 dp in the Jev view** (±1 cm ≪ 0.5 m match radius, ~1.5 cm projection error) — EMA floats hit 19-char reprs | semantics.py:282-286 | 171 B (28% of position bytes) | | |
| **5** | **Drop objects with `confidence < min_confidence` (0.5)** from the Jev view — they can never be destinations (`diff.appeared` already excludes them) | semantics.py:304-305 | 237 B/object in full form, ~68 B in digest form | | |
| **6** | **Cap the shipped list** (~25 objects, nearest/on-path first, `+N others` count): converts the 64-detection worst case from ≈18.5 KB (extrapolated) to ≈25·68 + 3836 floor ≈ **5.5 KB — under the bound** | semantics.py:49; tactics.py:206 | safety valve, not a saver | | |
| **7** | **Keep `sweep` out of the Jev state** (M1 already does); if M4 ships confirmations, send counts/verdicts, not per-object records | — | 385 B per 4 confirmations avoided | | |
| **8** | **Fix the bound test to cover the real path** — move it to `test_semantics_integration.py` per m1-plan.md:345: `SyntheticRoom` + `Perception` + `FakeVision.from_world(20 realistic fixtures)` + one pass, assert `len(json.dumps(build_state(scene))) < 6000` on the **digest** payload (and add a second, looser assertion for `scene.to_dict()` at 20 objects, e.g. < 10 KB, so the disk/log schema is also bounded). Keep the schema test for serialization only | tests/test_semantics_schema.py:89-99; m1-plan.md:93-95, 345 | prevents recurrence | | |

Recommended minimum for M3: **#1 (+#3, #5, #6, #8)**. That keeps Jev's view at ~5.2 KB / ~2.1k tokens
(upper) at 20 objects with ~0.8 KB of bound headroom (and ~1.9 KB of headroom via 1b/1c), moves the
crossing point from 9.5 to ~32 objects, and cuts the map's per-call token cost by ~60% (from
+4831 B to +1626 B per call, i.e. roughly −0.8k (char/4) to −1.3k (upper) tokens per call ⇒ −48k to
−76k tokens per 30 s run at 60 calls).
Keep the full 11-field schema in `Scene.to_dict()` (logs, `viz.py`, `runs/semantic/*.json`) — only
the Jev payload shrinks; `tests/test_semantics_schema.py:21-35` and the disk artifacts stay valid.

## 7. Evidence & reproducibility

Durable copies live beside this report (`runs/20260928-1930/w3c/`):

* `scripts/measure_state.py` — builds the realistic maps via the real pipeline
  (`FakeVision.from_world` → `SemanticsRunner` → `merge` → `snapshot` → `build_state`) and writes
  `measure_results.json` + `states/real_{8,9,10,15,20,30}.json`. Run it with
  `PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python scripts/measure_state.py`
  from a scratch dir (it hardcodes the w3c paths at the top).
* `scripts/digest_estimate.py` — digest variants and itemised per-field savings.
* `scripts/decompose.py` — fixture-vs-real decomposition (§1) and float-repr check.
* `scripts/projections.py` — the projected numbers in §3/§6.
* `scripts/spotcheck.py` — boundary-key and diff-bytes spot checks used to correct two figures.
* `evidence/` — the measured states (`real_*.json`) and `measure_results.json`.
* Scratch clone of `9c33ec0` at `~/.hermes/cache/scratch/wave3/w3c/clone`: `78 passed`
  (`pytest -q -p no:cacheprovider`), including the bound test this report shows to be
  unrepresentative. Subject repo verified clean (`git status` empty, HEAD `9c33ec0`); `/tmp/opencode`
  opened read-only.

Caveats: the object roster (indoor items like "plastic water bottle", "reusable shopping bag",
"wooden box") and positions are hand-picked but run through the real projection/merge path; the
token figure is a *range* (char/4 lower bound – 2.53 B/token upper bound), not a tokenizer
measurement, because no tokenizer was installable; the 64-object figure is a line-fit extrapolation
(30-object point measured).
