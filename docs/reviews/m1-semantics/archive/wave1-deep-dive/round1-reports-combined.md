# jev-rover - M1 deep review | Round 1 raw reports

_8 parallel subagents, run 2026-09-29. Recovered verbatim from the delegation records (chat delivery was dropped when the origin session was recycled mid-flight)._


---

# [00] Plan-claims audit vs codebase + plan-to-implementation coherence
_source: 00-plan-claims-audit.md_

## Audit: M1 plan claims vs current jev-rover

**Context:** the plan's header ("no code written yet") is stale — M1 is committed (`f352f61`, 14 files, +1745/−12; identical file list to `58fb7c9..f352f61`). `/tmp/opencode/m1-plan.md` is byte-identical to `docs/planning/m1-plan.md`. `PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider`: **78 passed**, git status clean before/after; nothing created/modified.

### 1. Claim-by-claim (pre-M1 rev `58fb7c9` and current tree)

1. **Scene fields all defaulted (plan cites scene.py 266-285)** — VERIFIED. Current `scene.py:327-350` all defaults incl. `semantics=None`, `sweep=None`; pre-M1 `58fb7c9:scene.py:265-285` all defaults. Citation is stale: current 266-285 is the semantics block (264-291). **Caveat:** "old serialized scenes still parse" is UNVERIFIABLE — no Scene deserializer exists anywhere (only `config.from_dict`, `semantics.load_map:373`); no reader of scene JSON in repo or tools (grep clean).
2. **`config._section` ignores unknown keys** — VERIFIED pre-M1 (`58fb7c9:config.py:215-228`, loops `fields(typ)` only). Current: strict only for the four new sections (`config.py:241-245,362-369`); legacy tolerance test at `tests/test_semantics_schema.py:67-70`. Structural delta: `_section` now recurses nested dataclasses + `get_type_hints` for *all* sections (`config.py:370-396`) — tested but wider than "ignores unknown keys".
3. **runs/ gitignored** — VERIFIED `.gitignore` `runs/`; default `store_dir="runs/semantic"` `config.py:154`.
4. **`build_state` pops only `mission`** — VERIFIED `tactics.py:206-210`; `test_semantics_schema.py:96-98`; `tactics.py` absent from `f352f61` (untouched).
5. **`Tactician.offer` read-only precedent** — VERIFIED `tactics.py:272-292`; state built caller-side, comment 285-287.
6. **Perception mutated ~15 Hz on main thread** — VERIFIED `run.py:187` (default 15.0), `run.py:287`.
7. **Full-res homography vs 0.5-scaled frames** — VERIFIED `perception.py:657-661,744`; `run.py:243,294` pass full-res. Caveat: frame resolution vs `camera.width/height` never validated (see R1).
8. **OccupancyGrid has `occupied_thr`/`stale_s`** — PARTIAL MISMATCH: constants/params live on `OccupancyGrid` (`OCCUPIED_THR=0.42` `perception.py:301`; `observed(t, stale_s)`:368) but the *fields* are on `SemanticContext:157-161`, sourced `720-721`.
9. **"README lesson #2" (age_s recomputed)** — MISMATCHED: `README.md:77` "Rules of thumb learned the hard way"; item #2 is "White quiet zone" (`:82-83`). No age_s/freeze text in README (repo grep finds only `docs/planning/m1-plan.md:97`). Behavior exists (`semantics.py:322-333,547-548`).
10. **"25 existing tests"** — VERIFIED (git grep: 7+2+6+5+5). Actual new tests = **42** (67 fns, 78 collected), not "~22"; README:247 says 67 — internally consistent with 25+42.

### Plan-vs-implementation coherence (pinned decisions)

- **C1 probe rule** ✅ `semantics.py:176-203` (floor_lab None→True:187; outside image→True:193; polygon/lab/cell checks; `maybe_pass` refuses `no_context` 506-508). **C2 reject-never-clamp** ✅ `:222-225`, no clip/clamp in file, counted in `PassResult.rejected:406`. **C7 Detection dataclass** ✅ `:52-64`.
- **§3 schema** ✅ `scene.py:269-350` incl. extra `Destination.object_id:291`. `sources` is a plain list (`:276`); plan's "set semantics, sorted" not implemented (vacuous with one source — reopen at M4).
- **Config keys/defaults** ✅ `config.py:120-187` except: `min_label_score=0.34` not the table's 0.35 (matches the later "findings folded in"; plan internally inconsistent); added `model.fixtures:125` and `project.point_by_label:132` (not in §4 block).
- **Interface drift vs §4:** `build_vision(cfg, homography)` (`semantics.py:114`); `FakeVision.from_world(entries, homography)` (arg order swapped); `SemanticStore.merge(objects, rejected, model, t_pass)` not `(PassResult, ctx)` (`:259-260`); `SemanticsRunner.__init__` requires `vision` (`:485`); `maybe_pass(t, ctx, frame, …)` (`:500`). Heuristics: EMA on x,y only, confidence raw (`:282-284`) and Jaccard ranking (`:601-608`), matching folded notes, contradicting the plan's own pinned table.
- **`--find` "prints + sets nothing"** — divergence: `run.py:303` calls `runner.set_destination(dest)` → lands in `scene.semantics.destination` and persisted `latest.json` (no driving, correct).
- **Acceptance gaps:** no loop-rate invariant test, no zero-worker-thread assertion, no CLI-level `--find` e2e; state-size bound exists at `test_semantics_schema.py:89-99` (not integration as §5 implies).

### 2. Ranked missed risks

- **R1 (med-high): frame-resolution coupling unvalidated.** `semantic_context` uses the config-resolution polygon as-is (`perception.py:707`) while `FloorModel` scales its polygon to the actual frame (`:253-254`). If the capture resolution ≠ `camera.width/height`, projections and the probe are silently wrong. Recommend: assert `frame.shape == (camera.height, camera.width)` in `process()`/`semantic_context`, or scale the polygon like `FloorModel`.
- **R2 (med): `--semantics off` doesn't override `semantics.enabled`** (`run.py:242`): "off ⇒ byte-identical" holds only for `enabled:false` configs; an enabled config still spawns the worker. Recommend tri-state flag (unset/off/fake).
- **R3 (med): acceptance tests #5 and parts of #1/#3 are claimed but absent.** Add loop-rate invariant, zero-thread check, CLI `--find` e2e.
- **R4 (low-med): fingerprint/`decision_needed` ignore semantics** (`tactics.py:255-270,277`) — semantics changes never re-ask Jev; deliberate M3 scope but worth documenting since tokens are billed only on geometry-triggered calls.
- **R5 (low): summary/scene-log shape changes** (`run.py:377` always emits `"semantics"`; scene logs +2 keys) — note for any external log consumer.
- **R6 (low): stale citations** — scene.py line range, "README lesson #2", "~22 tests"; patch the plan doc to match committed reality.

**No files created or modified.**

---

# [01] Threading, SemanticContext snapshot seam, measured copy costs, frame-space ambiguity
_source: 01-threading-snapshot.md_

All evidence gathered. Final review summary follows.

---

## Verdict

The committed M1 implementation (`f352f61`) faithfully realizes plan C4 + §4, and the concurrency model is **sound for the single-writer/multi-reader pattern it claims**: no data races, no double-submission, no lost results. Two real gaps: a **frame-space ambiguity** the seam inherits (raw vs undistorted) that will bite M2, and **main-thread disk I/O on merge**. Everything else is measured-negligible.

## Measured copy costs (real code, real configs, this host)

Both rooms are 6.4×3.6 m, cell 0.05 m → grid 128×72 = 9,216 cells; dtypes verified in `OccupancyGrid.__init__`: `float32` both arrays.

| item | room.example | room.synthetic |
|---|---|---|
| `grid_log_odds` copy | 36,864 B (1.9 µs) | same |
| `grid_last_seen` copy | 36,864 B (1.8 µs) | same |
| `polygon_px` copy (float32, 4 pts) | 32 B | 32 B |
| `floor_lab` copy (float64×3) | 24 B | 24 B |
| **`semantic_context()` total** | **73,784 B, 10.8 µs** | 73,784 B, 12.4 µs |
| **`frame.copy()` per pass** | **6,220,800 B (5.93 MiB), ~0.55 ms** | 2,764,800 B (2.64 MiB), ~0.42 ms |

Homography is shared by reference (144 B, not copied — mutation-free by convention). Per pass the main thread pays ~6.3 MB / ~0.6 ms once; at the configured 4 passes/min that's ≤25 MB/min and 0.6 ms in one 66 ms frame (≈1%), amortized ~0.04%. **Acceptable; the copy is not a problem.** `np.copyto` into a preallocated buffer measured no win (10.1 vs 10.3 GB/s), so that "optimization" is pointless. `semantic_context` is built only at offer time (guarded), not per frame; per-frame cost is `runner.snapshot(t)` — cheap dataclass copies.

## Correctness vs plan C4/§4

**Verified true:**
- Processing frame is 0.5-scaled (`proc_scale=0.5`, `homography_small = for_scale`) while tag pose and all semantics use full-res `self.homography`; §4's "FULL-RES bbox" convention is therefore correct and the 6 MB copy is required by that convention.
- Snapshot is complete, array copies are disjoint from live state (`color_lab` — the only field mutated live by `adapt` — is copied); `frame.copy()` is load-bearing (frame continues to be drawn on after offer).
- Tests confirm the discipline: worker/projection/integration 19/19 pass; repo tree stayed clean.

**Gaps found (ranked):**
1. **Frame-space ambiguity (real, M2-blocking).** `run.py:294` offers the **raw pre-undistort** frame; `Perception.process` rebinds locally after `cv2.undistort`, and `calibrate.py cmd_floor` clicks on raw frames despite its docstring saying "re-run floor on undistorted frames". So with `camera.intrinsics` set, perception's pose/floor work in undistorted space while the H clicked in raw space; the worker gets raw space. M1 is self-consistent only because `FakeVision.from_world` generates bboxes through the same H. `process()` doesn't expose the frame it used, so run.py *can't* offer the right one today. Pin this in M2.
2. **Main-thread disk I/O.** `store.merge` (called inside `runner.poll`, on the loop) does `latest.json` serialize + `os.replace` + `events.jsonl` append. Small (<6 KB) but it's the one blocking op in the perception block; move into the worker thread if store grows.
3. `close()` joins with `timeout=1.0` while M2 passes take seconds — returns with the thread still finishing (daemon, benign; parametrize).
4. **Plan C4's "FakeVision only reads the frame shape, so tests need no real pixels" is inaccurate.** `FakeVision.infer` checks only `ndim`, but `project_detections → height_suspect` reads `frame[y,x]` pixels whenever `floor_lab` is set (and `maybe_pass` requires it), so any test asserting `height_suspect=False` needs floor-coloured pixels. Worker tests dodge this (zeros frame, no such assertion); integration tests correctly use rendered frames. Doc fix.
5. Minor: cooldown refusals aren't counted in `skipped`; a pass_id is consumed even if `offer` fails (harmless gap); `_out`'s eviction path is unreachable under single-in-flight (belt-and-braces, fine).

## Race verdict (queue size 1 + pass_id)

- **No poll/offer race exists as built**: both are main-thread-only; `_inflight` is main-thread state; the worker only pushes to `_out`. Single in-flight is doubly enforced (`_inflight` guard + `_q` maxsize=1). Contract should assert "offer = main thread only" for future callers.
- **Result finishing between maybe_pass checks**: refused for one frame via `skipped["inflight"]` — correct, never double-submits; run.py polls before offering, so no delay in M1.
- **Staleness**: loop clock on both sides (`t` at poll vs `t_submit`) — consistent, immune to NTP steps; discards by age-at-poll (a better rule than the plan's literal `t_done − t_submit`); `t_done_wall` unused. Correct.
- **pass_id N−1-after-N discard is unreachable defense-in-depth** as built, not a live race path.
- numpy copies release the GIL; sides read disjoint memory → no locks needed. Correct.

## Ranked improvements

1. Pin the frame-space contract (make `process()` expose its used frame, or document raw-space + gate M2 on it) — correctness.
2. Move merge/persist off the control loop.
3. Parametrize `close()` join timeout.
4. For M2 CPU-bound detectors only: offer half-res frame + `for_scale` H (1.48 MiB copy, 4× less detector input).
5. Doc fixes: C4 pixels claim, "main thread offers only", cooldown counter.

**Artifacts:** `/home/freakymustard/.hermes/cache/scratch/taskA2/{measure.py,measure_frames.py}`. No repo/`/tmp/opencode` writes; repo tree clean; tests run read-only (scratch TMPDIR, no cache/bytecode).

---

# [02] Heuristics: probe-rule edge cases; merge/EMA/diff/thresholds
_source: 02-heuristics-height-merge.md_

Analysis complete. Findings below (all read-only; no repo writes).

## Basis
Code under review: `semantics.py` (commit f352f61), `perception.py`, `run.py`, `synthetic.py`, configs. Scale numbers computed from `config/room.example.json` (1920×1080 → 3.33 mm/px) and `room.synthetic.json` (1280×720 → 5 mm/px), plus an illustrative oblique overhead (2.0 m cam, 35° tilt, 60° hfov: 1.2–2.8 mm/px).

## 1. C1 probe rule — breaks on the plan's own headline object
Implemented rule: `semantics.py:176-203` — probe single pixel at `(bbox_cx, y1+probe_px)`; suspect if not floor-coloured, outside polygon, off-frame, or anchor cell unobserved; `floor_lab is None → True`.

- **Blue mat (false positive confirmed).** `FloorModel.classify` marks anything whose LAB distance to `floor_lab` ≥ `lab_tolerance` (22 example / 26 synthetic) as non-floor (`perception.py:275-276`). A saturated blue mat is far outside that ball → mat pixels are non-floor → occupied grid. So *any object resting on the mat* probes mat pixels → `height_suspect=True`, even though the mat is floor-level and the projection is good. The rule is untested for this case: `synthetic.py` renders no mat (`FURNITURE_LAYOUT` only), and `tests/test_semantics_projection.py:62-72` only covers bare floor + table.
- **Mat-agnostic by construction.** The signal is "surface colour below the base differs from floor", not "surface is elevated". Coloured tablecloth / floor-coloured table → false negative. It cannot answer the question it is named after.
- **Floor sample is contaminated by floor-cover.** `floor_lab` = median LAB over *all* polygon pixels at first classify (`perception.py:269-273`), mat/furniture included, and `adapt` (0.98/0.02, lines 286-290) drifts toward the perceived median. A rug covering >50% of the polygon makes the *rug* the floor colour and inverts classification — and with it the probe and the whole grid.
- **Probe metric is below the noise floor.** 6 px = **2.0 cm** (example cfg), **3.0 cm** (synthetic), **~0.7–1.7 cm** at a realistic oblique overhead. For a 0.3 m object at those scales (105–244 px wide) a detector bbox edge error of ±5 px is 0.6–3 cm — same order as the whole probe. Under 5 cm grid cells the probe often stays inside the anchor cell. Recommend: 7–15 px probe **band** (patch median over ~7×3 px, not `frame[y,x]` single pixel — MJPG ringing at object edges, `perception.py:58`), and probe distance in **metres via `H`** (`probe_below_base_m ≈ 0.05 m`), not pixels.
- **Shadow/dark-spot false positives.** OpenCV LAB `L` drops ~25+ under contact shadow/dark floorboard vs tolerance 22–26, and shadow chroma is near-floor. A contact shadow sits exactly where the probe lands. Cheap discriminator: split the distance into |ΔL| and chroma Δ(ab); if it is L-dominated and chroma-small, treat as floor-level (shadow), not suspect.
- **Rover/target.** `_rover_mask`/`target_m` are applied to grid candidates (`perception.py:750-751`) but never exposed in `SemanticContext`; probe below an object next to the rover can land on the rover body or the follow target (both non-floor) → false suspect, unstable as the rover moves.
- **Geometry/edges.** Polygon inset (120 px ≈ 40 cm in the example cfg) means objects within ~0.4 m of a wall are *rejected*, not flagged (`semantics.py:222-225`); frame-border `y1 == h-1` probes always suspect (`py ≥ h` early return, and test at `:75`); no guard that the worker frame resolution equals `cfg.camera` resolution — `polygon_px` and `H` are full-configured-res while `FloorModel` rescales (`perception.py:253-260`, `707`), so a capture-size mismatch silently misplaces probe/polygon tests.
- **Latent coordinate-system bug.** `process()` undistorts into a *local* `frame` (`perception.py:733-734`) while `run.py:294` hands the **raw** frame to the worker. With `camera.intrinsics` set (as `calibrate.py` instructs), floor_lab/polygon/homography are undistorted-space but bboxes/probe are distorted-space: systematic offset (10–30 px at corners). Same class as viz drawing on the raw frame (`run.py:342`).

## 2. Merge/EMA/diff/thresholds
- **Greedy is order-dependent, not distance-ordered.** `semantics.py:270-280` iterates `self.objs` in insertion order and each object grabs its nearest unmatched detection. Two same-label objects within ~1.0 m can swap identities (detection nearer B is consumed by A, which was inserted first). Fix is ~10 lines: sort all (object, detection) pairs by distance and assign best-first; Hungarian (`linear_sum_assignment`) is trivially affordable at ≤64 detections and strictly better.
- **Radius vs cadence.** 0.5 m at a ≥2 s interval = a 0.25 m/s mover is matchable; audit passes are 60 s (a pet moves metres) → movers fragment into appear/vanish churn. And with eviction absent, churn accumulates. Static objects, by contrast, want a *smaller* radius (0.15–0.25 m) so two distinct statics never merge. Suggest two-state radius from `motion`, or `r_eff = min(0.5, 0.05 + v_max·dt)`.
- **Never evicts.** `merge` increments `_misses` but never removes (`semantics.py:308-313`); "vanish fires once" is asserted (`test_semantics_merge.py:38`). Objects resurrect with the same id after arbitrary absence → silent identity absorption, unbounded map growth, and the C5 6 KB state bound has no runtime enforcement. Cap resurrection (misses ≤ ~10) and prune after N.
- **Low-confidence detections poison state.** No `min_confidence` gate on matching: `prev.confidence = d.confidence` (raw), `prev.height_suspect = d.height_suspect`, position EMA'd 40% toward any detection, including a 0.05-score spurious one (`semantics.py:282-285`). `min_confidence` only gates `diff.appeared` (`:304`). Require ≥`roi_min_confidence` to move position/flags; below that, count only as a freshness hit.
- **`height_suspect` has no hysteresis** — overwritten every pass; one noisy probe flips it. Use streak/decay (e.g. 2-of-last-3).
- **Slow drift invisible to `moved`.** Raw vs smoothed threshold 0.25 m/pass (`:290-291`) misses 0.15 m/pass creep (BlobTracker uses 0.12 m/s velocity, `perception.py:507-516` — inconsistent conventions). Prefer rate: `> 0.05 m/s` or displacement vs the raw position ≥2 passes back.
- **Doc↔code divergences:** confidence EMA (plan pinned α on confidence; code raw — prototype-backed), dedupe “or centres < 0.15 m” clause dropped (`semantics.py:155-164`, IoU only → containment/duplicates survive), vanish-then-remove dropped, labels compared case-sensitively (`:161`, `:273`) while adapters filter case-insensitively (`:93`) → real adapters returning "Blue Mat" create duplicates every pass.
- **Approach point.** `approach_point` is centre-relative (`semantics.py:676-682`); standoff 0.35 m lands *inside* any destination wider than ~0.7 m (mat 0.6 m + 0.22 m inflation) → planner sees its goal in an inflated obstacle. Use `standoff + object half-extent` or the object's radius.

No existing height/“on furniture” logic anywhere: `TargetTracker` colour mode projects the HSV centroid through `H` assuming the floor plane (`perception.py:560-577`) with no height flag — the semantics layer could later supply exactly that for the target. Tracks/sectors are not linked to semantic objects (`scene.tracks` vs `scene.semantics.objects` are parallel truths; no `track_id`), and semantics rides whole under `observed` into Jev state (`tactics.py:206-210`), so token cost scales with the unevicted map.

## Ranked recommendations
1. Make the probe mat/cover-aware: config `ground_cover_labels` (or a cover-region set assembled from same-pass mat detections) — a probe inside a cover region counts as floor-level. Fix the untested case first.
2. Undistort once per frame in `run.py`; pass one frame to `process()`, worker, and renderer.
3. Robust `floor_lab` bootstrap (largest LAB cluster, not whole-polygon median; warn when the second cluster >20%) + shadow-aware probe (|ΔL| vs chroma) + probe band in metres, not single pixel.
4. Store fixes: distance-ordered assignment (or Hungarian), `min_confidence` gate on position/flags, hysteresis on `height_suspect`, eviction/resurrection cap, label canonicalisation, restore the <0.15 m dedupe clause, resolution guard on frame vs config.
5. Add rover/target/hazard masks to `SemanticContext`; link `track_id`; measure approach standoff from object extent; add a mat + shadow to `synthetic.py` and a test asserting an object on a mat is not height-suspect.

---

# [03] Destination matcher, Jev Choice gating, --find, approach_point
_source: 03-destination-jev.md_

Review complete — read-only; repo `git status` clean, `PYTHONDONTWRITEBYTECODE=1`, probes in scratch (`/home/freakymustard/.hermes/cache/scratch/taskA4/probe_rank.py`), destination tests re-run green (5 passed).

## Verdicts (shipped code vs plan)

**1. Gating/budget/fingerprint — the semantics Choice bypasses all of it (dormant in M1).**
- Option ownership matches the house pattern (criteria keys = code-built option list): `mission.py:36-44` (routes + `no_match`), `tactics.py:115` (`criteria=MANEUVERS`), `semantics.py:632-642` (candidate labels). ✓
- Tactician machinery: when-to-ask `decision_needed`, fingerprint `_key` + skip, `min_dt`, `attempts < budget=400`, one-slot queue on a worker thread, stats — `tactics.py:189-203,254-292,300-353`. `_ask_jev_label` (`semantics.py:628-651`) uses **none** of it: no budget, no rate limit, no fingerprint/cache, **synchronous**, no stats accounting. Its docstring "One budgeted Choice" (`semantics.py:629`) and the commit message overstate: nothing budgets it; proposal §9 isn't met.
- Mitigation: run.py passes `jev=None` (`run.py:299`), so the path is test-only (`tests/test_semantics_destination.py:50-57`). M2 wiring must (a) share the Tactician's client/budget or reuse the runner's own budget/cooldown, (b) run it on the semantics worker — as written it would block the perception branch — (c) cache by `(text, sorted labels)` like `_key`.

**2. Matcher robustness — fine for the headline queries, brittle for detector-style labels.**
Measured shipped: `"go to the blue mat"` → blue mat 1.0; `"find the water bottle"` → water bottle 1.0. But:
- Jaccard punishes verbose labels: `"bottle"` vs `"plastic water bottle"` = 0.333 < 0.34 → **dropped**; `"mat"` vs `"yoga mat extra large"` = 0.25 → dropped. Wrong bias when labels come from a detector.
- Plan §4 table (`m1-plan.md:263`) is **stale**: it specifies `0.7·m/|q| + 0.3·m/|l| + 0.2 substring`; shipped is Jaccard (`semantics.py:601-625`, `min_label_score=0.34` at `config.py:152`) per the plan's own fold-in note (`m1-plan.md:407-408`). Under the plan formula "bottle/plastic water bottle" scores 0.8 — the shipped code gave up.
- No typo tolerance: `"chargr"`, `"waterbottle"` → 0 candidates → None (difflib ratio 0.923/0.957).
- Plural fold only `-s` (`semantics.py:584-588`): `"boxes"`→`"boxe"` → 0.
- No reusable mission text parsing exists — `mission.py` routes via LLM Choice over route names; `--instruction` is logged only.
- Plan §6 test description ("blue mat" vs "blue mat large" within epsilon) is wrong under **both** formulas (1.0 vs 0.667 shipped; 1.2 vs 0.9 plan); the shipped test correctly uses bare `"mat"` (tie 0.5/0.5 → Choice). Plan text needs correcting.

**Ranked alternatives**
1. **Recommended (now, zero deps, ~20 lines):** restore asymmetric overlap `0.7·|q∩w|/|q| + 0.3·|q∩w|/|w|` (the plan's formula) + `-es/-ies` fold + a `difflib` char-ratio fallback for empty/below-threshold token matches. Fixes the measured misses; keep Jev Choice for ambiguity/ties; re-tune thresholds (they shift).
2. **rapidfuzz** (`token_set_ratio`/`WRatio`): best quality (word order, subsets, typos), but adds a compiled dep to a 3-dep project (`requirements.txt:1-4`) and forces threshold rescaling to 0–100. Right at M2 when real labels arrive.
3. **Embeddings via LLM budget: reject/defer** — the SDK surface in use is Choice/Noul/Score (`tactics.py:23`); the label Choice already is the semantic arbiter and would need the same nonexistent budget plumbing.
4. Keep Jaccard as-is: survivable for M1 fixtures, but the knife-edge (`1/3` vs `0.34`) and length penalty will bite with real labels.

**3. Re-export — no cycle, but inert.** `mission.py:24`; chain mission→semantics→{config,perception,scene} (`semantics.py:43-45`, `perception.py:29-41`); nothing imports mission (grep). Caveats: importing mission now pulls cv2/numpy; `mission.main()` never calls `resolve_destination` (`mission.py:56-89`), and there are no retry/Noul stubs despite plan lines 245-247 — the re-export is for external importers only.

**4. `--find` — plan's acceptance command cannot run.** Verified: `--semantics fake --semantics-once --find "blue mat"` → `SystemExit("--mission goto needs --waypoint NAME")` from `run.py:47-49` (mission defaults to `goto`, `run.py:173`; GoalManager built unconditionally, `run.py:232`). Fix the acceptance text (add `--mission patrol` or a waypoint) or give `--find` a benign mission default. Also `--semantics-once` is parsed (`run.py:191-192`) but never read — inert. `find_done` latches after one attempt even on failure (`run.py:298-311`) — matches "retry in M2", but the message doesn't hint at label typos. Otherwise print-only, no conflict; runner auto-enables with `--find` (`run.py:242`).

**5. `approach_point` — matches the plan, one real degeneracy.** Code `dest − unit(dest−from)·standoff` (`semantics.py:676-682`) ≡ plan formula. Exact overlap → `dest` (tested, `test_semantics_destination.py:79-80`). **standoff > distance flips the point to the far side of the destination**: from (3.2,1.0), dest (3.0,1.0), standoff 0.35 → (3.35,1.0) — beyond the object, no guard. Recommend `if length <= standoff: return dest`, and M2 validating the point against polygon/planner inflation before driving. Print-only in M1, so latent only.

---

# [04] Open-vocabulary vision landscape for M2 (web research, verified sources)
_source: 04-vision-landscape-web.md_

## Open-vocabulary detection landscape for jev-rover M2 (verified 2026-09-29)

All weights/numbers verified today via HF model API, repo raw READMEs, arXiv HTML/API. `[derived]` = extrapolation, not measurement.

### Ranked shortlist
1. **Grounding DINO family, MM-GDINO-T weights** — best open-weights accuracy + attribute handling, Apache-2.0, one-call transformers API, sub-second-to-1s/pass class. Default LocalVision detector.
2. **YOLOE / YOLOE-26 (Ultralytics)** — 4–25 M params, near-CNN speed, CPU/4 GB fallback, `set_classes()`; AGPL-3.0 and weakest attribute binding.
3. **SAM 3 / SAM 3.1 (Meta)** — prompts handle colours ("yellow school bus"), boxes+scores+masks; ~850 M params, gated weights, custom license → best as RemoteVision.

### Comparison

**Grounding DINO / MM-Grounding-DINO-T — #1**
- Weights ungated, checked today: `IDEA-Research/grounding-dino-tiny` 689 MB fp32 (≈172 M params), `-base` 933 MB (≈233 M), `openmmlab-community/mm_grounding_dino_tiny_o365v1_goldg_v3det` (arch `MMGroundingDinoForObjectDetection`, 2025-07-23). All Apache-2.0; mmdetection checkpoints also public.
- Quality: GDINO-T 48.4 COCO zero-shot / 28.8 LVIS-minival AP; **MM-GDINO-T 50.4 vs 48.4** (O365+GoldG, same backbone). GDINO paper evaluates referring expressions "specified with attributes".
- Latency (A100, 800×1333): 9.4 FPS PyTorch / 42.6 FPS TensorRT (GD1.5 Table 5). [derived] ≈3 FPS PyTorch on a 6–8 GB laptop GPU ⇒ 0.3–0.7 s/pass; ~2–3× at 1280 px — fine for "seconds per pass".
- VRAM [derived]: ≤1 GB fp32 weights; 800–1333 px Swin-T activations fit 6–8 GB; 640–800 px at 4 GB.
- Integration: `AutoModelForZeroShotObjectDetection`; prompts lowercase, `" . "`-separated, box/text thresholds; output = phrase+box+score → maps 1:1 to `infer(frame, labels)`.
- Issues: misses small/thin objects at 800 px (tile/upscale); colour words silently ignored (attribute marginalization, DSAA).

**YOLOE / YOLOE-26 — #2 (fast path)**
- YOLOE-26n/s/m/l: 3.9/10.7/21.3/25.5 M params, 6.1/21.9/70.6/89.0 GFLOPs, text-prompt mAP 24.7/30.8/35.4/37.8; YOLOE-v8-S 305.8 FPS T4 TensorRT.
- License: AGPL-3.0 (repo + weights). Internal use fine; shipping needs Ultralytics Enterprise license.
- Integration: `model.set_classes([...])`; first call downloads CLIP text encoder (`mobileclip2_b.ts`, ≈254 MB) — pre-cache for the rover. Prompt-free `*-seg-pf.pt` rejects `set_classes()`.
- Quality: MobileCLIP text encoder → weakest attribute binding; strong on small objects, runs on CPU.

**SAM 3 / SAM 3.1 — #3 (accuracy / remote)**
- ~850 M params (450 M vision + 300 M text + 100 M detector); 30 ms/image H200 for 100+ objects [derived: ~1–3 s/pass on laptop GPU]; fp32 ckpt 3.44 GB.
- Concept prompts are colour-bearing noun phrases by design → best attribute behaviour; returns boxes + scores + masks (transformers `Sam3Model`/`Sam3Processor`).
- Blockers: HF checkpoints **gated=manual**, custom SAM License (non-transferable; ITAR/military restrictions; publication acknowledgement), py3.12 + CUDA 12.6+, flash-attn-3. SAM 3.1 released 2026-03-27.

**Runners-up**
- YOLO-Worldv2 (AGPL/GPL): 47.4/42.7/37.4 FPS PyTorch A100 @640 (S/M/L); 35.4 AP @52 FPS V100 — superseded by YOLOE, same `set_classes()` API.
- OWLv2 base-ensemble (Apache-2.0, 620 MB): trivial path but 2023-era — OWL-ViT-L 42.2 COCO vs GDINO-T 48.4 in the same table.
- OmDet-Turbo-Tiny (Apache-2.0, in transformers since 4.45): 42.5 COCO zero-shot, 21.5 FPS PyTorch / 140 FPS TRT A100 @640.
- YOLO-UniOW S/M/L (GPL-3.0, 291–383 MB): 26.2/31.8/34.6 LVIS-minival AP, 98.3/86.2/64.8 FPS V100; heavier mmdetection path.
- Florence-2 base/large-ft (MIT; 0.23/0.77 B): card documents OD/phrase-grounding/OCR but **no** OVD prompt → crop-level verifier, not detector.
- **DINO-X and Grounding DINO 1.5/1.6 Pro/Edge: weights NOT downloadable** — no HF repos exist (searches empty today); official repos are API SDKs against DeepDataSpace with paid tokens.

### Colour-attribute mitigations (ranked)
1. **Detect class → verify colour from crop**: query "mat .", score crops with a small CLIP classifier (Detic pattern, Apache-2.0) or Florence-2 region-to-category; final = det_score × P(colour). Detector-agnostic.
2. **Fix the CLIP matching head, not localization**: a linear projection on frozen features is enough for fine-grained matching; localization contributes marginally (2404.03539).
3. **Attribute activation, training-free**: HA-FGOVD (LLM-highlighted attribute tokens + token-mask composition, frozen models, FG-OVD SOTA); DSAA (attribute prefix adapter, non-invasive).
4. **Measure on FG-OVD** before/after; 2025 fine-grained-prompt task/dataset adds stronger prompts.
5. **Prompt hygiene**: keep attribute and class tokens separate/repeated ("blue mat . blue . mat ."), lowercase, " . " separators, add colour hard negatives ("grey mat .").
6. **Deterministic colour naming** (HSV/Lab nearest colour) as auditable tie-breaker.
7. **Fine-tune last**: YOLOE-26 has linear-probe/full-tuning recipes; keep colour as a separate head.

### Verification notes
- This box has **no GPU** (4 cores, 5 GB RAM) → no on-hardware latency measurement; laptop figures are labelled extrapolations from A100/T4/V100.
- `web_search`/`web_extract` 403'd; everything fetched with `curl` (HF API, GitHub raw, arXiv API/HTML; DDG HTML for discovery).

### Citations
- https://huggingface.co/IDEA-Research/grounding-dino-tiny · https://huggingface.co/IDEA-Research/grounding-dino-base · https://huggingface.co/openmmlab-community/mm_grounding_dino_tiny_o365v1_goldg_v3det · https://raw.githubusercontent.com/open-mmlab/mmdetection/main/configs/mm_grounding_dino/README.md · https://raw.githubusercontent.com/IDEA-Research/GroundingDINO/main/README.md · https://arxiv.org/abs/2303.05499 · https://arxiv.org/abs/2405.10300 · https://github.com/IDEA-Research/Grounding-DINO-1.5-API · https://www.deepdataspace.com/blog/Grounding-DINO-1.6-Pro
- https://github.com/IDEA-Research/DINO-X-API · https://arxiv.org/abs/2411.14347
- https://docs.ultralytics.com/models/yoloe/ · https://arxiv.org/abs/2503.07465 · https://raw.githubusercontent.com/THU-MIG/yoloe/main/README.md · https://huggingface.co/jameslahm/yoloe · https://raw.githubusercontent.com/ultralytics/ultralytics/main/LICENSE · https://docs.ultralytics.com/models/yolo-world/ · https://arxiv.org/abs/2401.17270 · https://raw.githubusercontent.com/AILab-CVC/YOLO-World/master/LICENSE
- https://arxiv.org/abs/2511.16719 · https://huggingface.co/facebook/sam3 · https://huggingface.co/facebook/sam3.1 · https://raw.githubusercontent.com/facebookresearch/sam3/main/README.md · https://raw.githubusercontent.com/facebookresearch/sam3/main/LICENSE · https://huggingface.co/docs/transformers/main/en/model_doc/sam3
- https://huggingface.co/google/owlv2-base-patch16-ensemble · https://arxiv.org/abs/2306.09683 · https://huggingface.co/omlab/omdet-turbo-swin-tiny-hf · https://raw.githubusercontent.com/om-ai-lab/OmDet/main/README.md · https://arxiv.org/abs/2403.06892 · https://raw.githubusercontent.com/THU-MIG/YOLO-UniOW/main/README.md · https://huggingface.co/leonnil/yolo-uniow · https://arxiv.org/abs/2412.20645 · https://huggingface.co/microsoft/Florence-2-base-ft · https://arxiv.org/abs/2311.06242
- Mitigations/newer: https://arxiv.org/abs/2201.02605 (Detic) · https://github.com/facebookresearch/Detic · https://arxiv.org/abs/2404.03539 · https://arxiv.org/abs/2409.16136 (HA-FGOVD) · https://arxiv.org/abs/2605.18023 (DSAA) · https://arxiv.org/abs/2311.17518 (FG-OVD) · https://arxiv.org/abs/2503.14862 · https://arxiv.org/abs/2507.17436 (Dynamic-DINO) · https://arxiv.org/abs/2606.11546 · https://arxiv.org/abs/2607.01759 · https://arxiv.org/abs/2605.10190

**Artifacts:** `/home/freakymustard/.hermes/cache/scratch/taskA5/final_report.md` (990 words incl. citations), plus raw evidence in `landscape.md`, fetched READMEs/HTML (yoloe/uniow/gdino/sam3/gd15 HTML, mmdetection configs, arXiv XML). **Issue:** no local GPU, so latency figures are primary-source A100/T4/V100 numbers plus clearly-labelled extrapolation; all "downloadable today" claims were checked against the live HF API on 2026-09-29.

---

# [05] ToF physical confirmation (VL53L1X) + single-view height estimation math
_source: 05-sweep-height-math.md_

All sources verified and both numeric checks done. Writing up.

---

# A) ToF physical confirmation — checked against primary sources

**Sources fetched today** (st.com is firewalled on this host; official ST PDFs were retrieved via mirrors and verified by DocID):
DS = VL53L1X datasheet **DocID031281 Rev 3** (pololu/sparkfun mirrors of the ST PDF); UM2356 API manual **DocID031478 Rev 1** (pololu mirror); **AN5191 Rev 1** (ROI) and **AN5231 Rev 2** (cover window) via `r.jina.ai` text-proxy of st.com; ST community thread 254529 (answer by ST employee J. Kvam); ClearGrasp arXiv:1910.02550; multipath arXiv:1404.1116; Hale & Querry water data (omlc.org).

**Transparent PET / glass — the water-bottle case.** Confidence: HIGH for the mechanism, MEDIUM-HIGH for end-to-end detectability. ST's own docs contradict the datasheet's marketing line ("absolute distance whatever the target color and reflectance"):
- AN5231: required window transmission >87% at 930–950 nm; PMMA 94%, polycarbonate/tempered 85–88%, **"PET is not recommended (80% transmission)"**. A bottle is two walls: ~0.8² plus ~4–5% Fresnel loss per surface.
- ST forum (vendor-authoritative): "most plastic is transparent to 940 nm no matter what color"; "even the clearest glass is only 95% transparent"; **"ALL the photons are averaged… with a really bright target like sheet metal that will dominate"**; and curved glossy surfaces "bounce off and are not detected at all".
- Physics: a *full* bottle returns a shallow volume echo (water absorption a=0.267 cm⁻¹ at 940 nm → 1/e depth ≈ 3.7 cm, Hale & Querry), usually **detectable with a few-cm positive bias**. An *empty* PET bottle gives only off-axis Fresnel glints; from most bearings the sensor returns the **background wall** (attenuated ~×0.3 through two walls). Cross-domain corroboration: standard depth sensors fail on transparent objects (ClearGrasp); mixed/transparent returns corrupt ToF depth (arXiv:1404.1116).
- Consequence for design: for bottle-class labels, "range = expected background" and "dropout" must map to **inconclusive / not-confirmed**, never *contradicted*. Confidence: HIGH on that rule.

**Dark/black surfaces.** HIGH (DS tables 6/7/9). What matters is signal rate, not colour: max range 88% white 360 cm vs 17% grey 170 cm (dark, 100 ms); 4×4 ROI drops grey-17% to **45 cm**. Black matte (<5%) will only be seen close-in; the actionable levers are longer timing budget (TB 100→140 ms: 360→400 cm white) and the driver's signal limit (default 1 Mcps → status 2 SIGNAL_FAIL; lowering it trades false ranges for reach — UM2356 warns unset limits "could return an incorrect measurement").

**Ambient light.** HIGH: long mode, white 88%: 360 cm dark → **166 cm at 50 kcps/SPAD (sun behind a window) → 73 cm at 200 kcps/SPAD (direct sun)**; short mode is ambient-flat at ~130 cm. DS footnote: **office lighting ≈ 5 kcps/SPAD** — benign. Verdict: fine indoors away from direct sun/IR flood; direct sun reduces the sweep to a <1 m proximity sensor.

**Status/intensity as the disambiguator.** HIGH. UM2356 exposes per-measurement `RangeStatus` (0 valid, 1 SIGMA_FAIL, 2 SIGNAL_FAIL, 4 out-of-bounds, 5 HW, 7 wrap, 8 processing, 14 invalid), `SignalRateRtnMegaCps`, `AmbientRateRtnMegaCps`, `EffectiveSpadRtnCount`, `SigmaMilliMeter`; defaults sigma 15 mm / signal 1 Mcps. The scan payload should carry all five per beam — that's what separates "no object", "object too dark/transparent", and "noisy".

**Outcome design.** Confidence: MEDIUM-HIGH. Three values + confidence is right, but map them as: *confirmed* = status 0 AND |range−expected| ≤ tol; *contradicted* = status 0 AND range matches neither the object nor its plausible background (e.g. a closer obstacle); *absent/inconclusive* = status≠0, dropout, or range = background → keep vision confidence, don't refute. Mitigations that fit a servo sweep: multi-bearing rescan (the bottle's specular lobe is narrow — a glint is angle-dependent), ROI shrink to 8×8/4×4 (15–20°) to exclude wall/background returns (AN5191 §4.4 recommends exactly this when walls contaminate the reading), longer TB on rescan, and per-ROI offset calibration if <5 cm accuracy is needed (AN5191 §3.2).

**Scan matching (coarse-to-fine grid vs ICP).** Confidence: HIGH. Keep the grid: I timed the sim's matchers on this box — local search **88 ms** (1,521 candidates × 91 beams), bootstrap **608 ms** (19,499 × 46). It needs no correspondences, can't diverge, handles 5% dropouts/2% outliers via the capped loss; ICP/Gauss-Newton needs correspondences and its Jacobians are discontinuous exactly at AABB corners, where all the information lives. Bigger risk than the optimizer is the **beam model**: real beams are 15–27° cones (emitter cone stays 27° regardless of ROI, AN5191), and "photons are averaged" — the sim's 2° rays, uniform dropouts and reflectance-free model validate the *matcher*, not *detectability*.

# B) Object height from the floor homography

**Derivation.** H maps pixels→floor (z=0). For a bbox: P_b = H(bottom px), P_t = H(top px) is *not* the object top's floor position — it's where the top ray crosses z=0. With camera centre (C_xy, Z_c) and a vertical segment standing at P_b of height h, the core-projection identity gives

  **|P_t − P_b| = h/(Z_c − h) · d,  d = |P_b − C_xy|  ⟺  h = Z_c·Δ/(d + Δ)**

(degenerates as h→Z_c: the top pixel reaches the horizon). Verified numerically: exact (0.000 cm) at 6 positions × 4 heights and on a 22°-tilted camera; 0.7–7.5 cm error when feeding it an honest cylinder-silhouette bbox.

**Is h in H alone? No — HIGH confidence, proven.** (Z_c, f) and (k·Z_c, k·f) give a numerically *identical* H (max difference 0.00e+00); the uncalibrated plane-pose is a 2-parameter family. Needed: Z_c and C_xy. The repo stores only floor correspondences + polygon (`calibrate.py`), so an extension is required: **(1, recommended)** run the already-supported `calibrate.py intrinsics`, then `cv2.solvePnP` on the stored floor reference points → pose → Z_c, C_xy; **(2)** no-intrinsics fallback: tape Z_c, C_xy ≈ H(principal point ≈ image centre) — ±5 cm here costs only ±1.4 cm on h; **(3)** self-calibration from ≥2 detections (Δ-lines intersect at C_xy) fails at 21–45 cm error — discard.

**Sensitivity (measured).** Top edge ±2 px → ±4.4 cm on a 0.25 m bottle (~18%); bottom edge ±1 px → ±2.5 cm; Z_c ±5 cm → ±0.45 cm; C_xy ±5 cm → ±1.4 cm. Conditioning: near-nadir at 1280×720 (f≈340–420 px), a 0.25 m object spans only 12–25 px vertically (1 px ≈ 1.1–2.1 cm of h); more camera obliquity helps.

**Failure modes (must-gate).** A flat 0.6×0.4 m mat yields h_est = 0.93 m (that's its footprint, not height) — needs the label/plane prior (M1 already has `project.point_by_label`). A 0.25 m bottle on a 0.75 m table yields h_est ≈ 0.58 m ≈ 2.3× its true height, with the anchor landing on the table footprint — i.e. the anomaly *is* the signal, as long as the class prior says "bottle ⇒ 0.1–0.4 m".

**Verdict / fit.** Feasible and cheap **as a coarse, class-gated plausibility signal** (±5 cm best case) — into M1 only if intrinsics are available (else it is a classic M3 second signal alongside the C1 probe rule, which needs no calibration and already catches the on-table case). Do not trust it under ~10 px bbox extent, and never treat its output as a measurement.

**What I ran/created:** sim re-run (read-only, reproduces §10.1 exactly; its sanity line prints 1.224 m vs "expect ~3.25" because a furniture box lies on that ray — cosmetic only); `height_math_check{,2,3}.py` in `.hermes/cache/scratch/taskA6/`. Nothing written to the repo or /tmp/opencode. Two caveats: st.com needed mirrors/proxy; DS pages 2–3 and 25–27 have no text layer (OCR-only, not needed).

---

# [06] Test suite run + test-plan/acceptance review
_source: 06-tests-acceptance.md_

**1. Suite result — verified, but it is 78 tests, not 25**
- Exact command run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider --durations=10` → **78 passed, 0 failed, 0 skipped, 0 errors, 14.44s** (pytest 9.1.1, pytest 9.1.1 `.venv` present).
- Composition (collect counts, exact): 25 legacy — test_control 7, test_link 2, test_perception_synthetic 6, test_scene_geometry 5, test_tactics 5 — **+42 M1 semantics** (schema 9, projection 8, merge 9, destination 5, worker 7, integration 4) **+11 collected by default from `docs/planning/prototype/test_april_sem.py`**. There is no pytest.ini/pyproject/testpaths, so `docs/` is collected; the suite is not 78 product tests. README.md:48 still claims "25 tests" — stale. The plan's "~22 new" shipped as 42; all 6 planned files exist.
- Slowest: `test_stale_result_is_dropped` 1.10s, `test_pose_converges_after_a_sudden_turn` 1.03s, `test_failure_cooldown` 1.01s, `test_slow_vision_never_blocks_the_caller` 0.97s.
- Cache hygiene: my runs wrote nothing — `.pytest_cache` and `tests/__pycache__` mtimes are unchanged (19:23–23:11) and a controlled fresh+stale-pyc experiment under both flags produced zero pycs. Observation: `docs/planning/prototype/__pycache__/*.pyc` carry mtime 00:05:08 (inside the session window) but cannot be from my flagged runs; an unflagged invocation wrote them. Recommend `testpaths = tests` (or `norecursedirs = docs`) so the prototype stops riding along.

**2. Coverage map vs plan (per §5/§6)**
- Delivered well: projection (world round-trip, per-label anchor override, reject-not-clamp, height_suspect floor/furniture/frame-exit, dedupe, anchor helpers); merge (appear/move/vanish, raw-displacement regression, large-jump→new object, id stability, EMA convergence, min_confidence, rejected propagation, latest.json round-trip incl. events.jsonl fields, snapshot-copy aliasing); destination (ranking, ambiguity→exactly one fake-Jev Choice with code-owned options, deterministic no-Jev fallback, below-threshold, standoff + degenerate approach); worker (non-blocking submit, single in-flight, age-stale, budget, cooldown, no-context refusal, stats shape); integration (e2e pass→destination, two-pass move, slow-vision non-block, disabled default).
- Weak/placeholder/missing:
  - **Acceptance #5 loop-rate invariant is not implemented as written** — replaced by the submit-<50ms + poll-cheap test; no fps/cadence assertion anywhere.
  - **height_suspect untested branches**: probe outside the floor polygon (semantics.py:195) and the promised "cell never observed" branch (semantics.py:203). The floor_lab=None test exits earlier at the line-187 fallback, so it does not cover the observed() path.
  - **No run.py-level tests**: acceptance #2's "summary null / zero worker threads" and acceptance #3's `--semantics fake --semantics-once --find "blue mat"` are code-only/manual. `close()`-joins-thread is asserted nowhere.
  - Plan's "stale pass_id N−1 after N" cannot be tested as written: queue(1) + single-inflight makes an older pid structurally impossible; the age-based reformulation is the correct test. Not a gap, worth documenting.
  - Tolerances slightly loosened vs plan (0.03 m vs promised 0.02 m round-trip) — harmless given integer bbox rounding.

**3. Feasibility — both stands**
- Inverse homography exists: `perception.Homography.world_to_img` (perception.py:132); `FakeVision.from_world` already uses it to build fixtures (semantics.py:96–111). Round-trip fixtures are sound and in use.
- ≥6 warm frames works: tests use `WARM = 6`, SyntheticRoom.render() + `perception.process(frame, i/15)` at model time — offline, no camera; the 42 green semantics tests prove grid/floor warm in 6 frames.

**4. Robustness**
- Loop-rate test as planned (1s window, ±20% baseline ratio) is the flakiest item on a loaded box. Better formulations, in order: (a) absolute cadence — while `SlowVision.sleep(2.0)` is in flight, time 200 `maybe_pass/poll` iterations and assert median per-iteration < 5 ms (no baseline ratio, median kills scheduler outliers); (b) if a ratio is wanted, median of ≥3 windows with a tolerant bound (≤1.5× baseline) and a slow vision sleeping longer than the window; (c) keep today's submit-<50ms check only after warm-up.
- State size (measured read-only, scratch script below): base `build_state` = 2039 B; 10 minimal objects = 4333 B; fully populated (sources/times/motion/destination/diff) = 4634 B. The 6 KB bound holds with ~1.4–1.7 KB headroom; safer as "delta < 3 KB" plus the absolute bound, since size depends on the rest of the scene.
- Worker timing: replace single-sample `<50ms` with warm-up + median-of-5 (or 200 ms bound on CI); poll loop as median per-poll (<2 ms). Budget/cooldown/stale already use the injected model clock `t` → deterministic; keep that pattern. `test_stale_result_is_dropped` wastes ~1s because `_drain` can't observe the drop; polling until `stale_dropped` increments cuts runtime without weakening it.
- If semantics-off byte-identity is to be an acceptance, add a smoke test that runs `run.py --source synthetic --seconds 1 --semantics off` in a tmp cwd and asserts `summary["semantics"] is None` + no `SemanticWorker` thread.

**5. Summary schema claim — half true**
- Written at run.py:368–381: `"semantics": runner.stats() if runner else None` → `runs/summary_<stamp>.json`; existing `runs/summary_20260928-231002.json` shows `"semantics": null` for a disabled run.
- `stats()` (semantics.py:556–569) exposes passes, passes_started, errors, last_error, rejected_total, stale_dropped, median_ms, and `skipped {interval, budget, inflight, no_context}`. **Budget ✓ and errors ✓ appear under "semantics"; there is no cooldown counter** — the cooldown block (semantics.py:511–512) returns False without incrementing anything. Acceptance #4's "cooldown counters visible" is not met as written; add `skipped["cooldown"] += 1` plus a summary-shape assertion.

**Files/artifacts**: created only `/home/freakymustard/.hermes/cache/scratch/taskA7/{measure_state.py, probe/test_probe.py, probe2/t.py}`. No repo file modified (git status clean before and after; no caches written by my runs).

---

# [07] Repo/GitHub/CI coherence + lessons cross-check
_source: 07-repo-coherence.md_

All checks done. Summary follows.

---

## 1. Local vs GitHub — coherent
- Local HEAD = `9c33ec06afcc644c23deef76447b4a2370de31c1`, "docs: planning package (semantics proposal, M1 plan, sweep validation, sim + prototype)", 2026-09-28 23:46:24 +0530 (18:16Z).
- `git ls-remote origin` HEAD and `refs/heads/master` = same SHA; GitHub API latest commit = same SHA. **No divergence.**
- Branch `master` tracks `origin/master`, up to date; `git status` clean, no untracked/stashed files. Working tree = commit.
- `gh auth status`: logged in as `freakymustard67` (repo scope). `gh repo view`: `jev-rover`, `master`, **public**. No writes performed.

## 2. CI — none exists
- No `.github/`, no `.gitlab-ci.yml`, `.circleci`, `.travis.yml`. **A PR would trigger zero automated checks.**
- Local suite: `./setup.sh` (venv + requirements + pytest), then `.venv/bin/python -m pytest` (`pytest tests/ -q` per docs). I ran `--collect-only`: **78 tests** collected.
- Doc drift: README.md:48 says "25 tests"; README.md:247 and docs/planning/README.md:38 say "67 tests". Actual = 78. Stale counts — worth fixing before a review PR.

## 3. Cited "lessons" — verified, with one attribution nuance
- **README lesson #2 exists but doesn't say what the plan paraphrase implies.** README.md:216–218 (under "## What the build actually taught (worth keeping)", 209): *"Publish derived state every frame, not only when it changes. The planner replans at 0.75 s intervals; scenes are rebuilt at 15 Hz. Only writing `path_*` on replan frames made 80% of states lie."* README never mentions `age_s`, merge, or semantics.
- The C5 text is m1-plan.md:97–99: *"Also an instance of README lesson #2: `age_s` must be recomputed on every scene, not frozen at merge time. `SemanticsRunner.snapshot(t)` returns a `dataclasses.replace` copy with fresh `age_s` each frame."* Framed as *"an instance of"* — acceptable as application, **but the phrase "README lesson #2: age_s must be recomputed…" is plan text, not README text**. Don't quote it as if README says it. Implementation is real: semantics.py:322/326 (`snapshot`/fresh `age_s`), 547–548; asserted by tests/test_semantics_integration.py:50.
- **Tactician.offer pattern verified.** tactics.py:272–273: *"def offer(self, scene: Scene, now: float) / Non-blocking. Hand the latest scene over if it is worth a call."*; tactics.py:285–286: *"Build the state NOW, in the caller thread. The worker gets an immutable snapshot instead of racing the control loop's mutations."* M1 plan C4 (m1-plan.md:63): *"mirroring the `Tactician.offer` rule we already learned the hard way"*. The "hard way" provenance is README lesson #1 (211–215, offer-before-planned → `path_valid` always false) — consistent, though the literal phrase "learned the hard way" appears in README only at line 77 (calibration rules). semantics.py:13 states it "mirrors `tactics.py`"; SemanticsWorker.offer at semantics.py:430. No misattribution beyond the paraphrase caveat.

## 4. Existing semantics/sweep build state
- **Semantics: built** (M1 shipped — README.md:276–277, docs/planning/README.md:21). `semantics.py` (691 lines), config sections, 4 test files, `prototype/` + `tools/sim/tof_sim.py` tracked.
- **Sweep: schema/sim only.** `SweepState` in scene.py, `config.sweep`, test in test_semantics_schema.py:29; simulator exists; no `sweep.py` — M4 planned (docs/planning/README.md:24).

## 5. Other reviewer notes
- **Doc conflicts already documented, not hidden:** m1-plan.md §1 C1–C8 records proposal-vs-code conflicts (C1 height_suspect rule, C2 clamp-vs-reject contradiction) with owner approvals at lines 387–392. No conflicting *documents*; the plan and proposal are reconciled.
- **Dangling `/tmp` paths in committed docs:** m1-plan.md:4 ("Responds to `/tmp/opencode/semantics-layer-proposal.md`") and semantics.py:18 ("prototype: /tmp/opencode/semantics_proto/") — should point at the repo copies (`docs/planning/`).
- README.md is 278 lines, not 225 as task context stated.
- Docs claim "no network" for tests — consistent with test layout. `.gitignore` covers `.venv/`, `.env`, `runs/`, `calibration/camera.json`, `firmware/.../secrets.h` — no secrets risk in visible tree.

**Bottom line:** repo is clean and fully synced with GitHub; review PR would be green-by-default (no CI at all — the main gap), and the two plan citations are real with one paraphrase caution.