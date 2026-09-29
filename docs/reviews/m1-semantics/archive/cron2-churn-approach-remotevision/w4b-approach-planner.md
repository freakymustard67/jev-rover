# w4b — Approach-point routing: does a 0.6–0.8 m destination object stall the Planner/Executor?

**Task:** CONFIRM/REFUTE proposal §7 item 4 (`docs/planning/semantics-layer-proposal.md:171`):
destination = object (x,y) with configured standoff (`destination.standoff_m`, e.g. 0.35 m;
"obstacle inflation already exists in the planner"). Test: object 0.6–0.8 m wide (radius 0.30–0.40 m),
inflation 0.22 m → planner-blocked radius ~0.52–0.62 m while the approach point sits at 0.35 m.

**Verdict: CONFIRMED.** With the object in the occupancy grid — which the real perception pipeline
does for a mat/box-sized object — the 0.35 m approach point lies inside the planner's inflated
blocked region for object radii r ≥ ~0.12 m. The planner quietly substitutes the goal cell yet
publishes a path ending at the unreachable point (`control.py:128`), and execution ends in one of
two terminal states, both permanently short of the ring:
- **Jev missions:** permanent standstill — `map_brake` freezes the rover, v=0, w=0, for the rest of
  the run (measured: still for all of the final 6 s, 656–658 reflex trips). Stop point ≈ `r + 0.28 m`
  from the object centre, i.e. `(r + 0.28) − standoff` short of the ring.
- **No-Jev baseline:** turn-in-place chatter limit cycle — position frozen (span 0.0 m over 20 s),
  yaw oscillating ±84 °/s, v=0, never advancing.
The mission latch (0.30 m, `run.py:41,93`) only masks this when the shortfall < 0.30 m — it fails for
r=0.40 @ 0.35 (0.318 m short, Jev) and (0.385–0.435 m short, baseline), and fails by 3 mm for
r=0.30 @ 0.35 (0.303 m, baseline).

Environment: **PR tip `bec1d91`** (`review/m1-semantics-audit`), scratch clone
`~/.hermes/cache/scratch/w4b/repo`. Suite on the tip: **80 passed** (14.66 s), no network.
Sims: `w4b-approach-sim.py` (engineered grid + driving), `w4b-approach-pipeline.py`
(real `SyntheticRoom` + `Perception` E2E), `w4b-approach-analysis.py`, `w4b-fill-matrix.py`.
All run from the scratch clone root with `/home/freakymustard/jev-rover/.venv/bin/python`,
`PYTHONDONTWRITEBYTECODE=1`.

---

## 1. Geometry of the blocked region (measured, not inferred)

`Executor.__init__` builds the planner with radius `cfg.grid.inflation_m` (`control.py:164`);
`Planner.blocked()` = `grid.inflated_occupied(0.22) | nogo` (`control.py:55-56`).
`inflated_occupied` = cv2 dilate with an ellipse of radius 4 cells (`perception.py:373-377`);
cell = 0.05 m, so nominal dilation 0.20 m + cell quantization.

Measured blocked radius around a stamped obstacle disc (`w4b-approach-sim.py` probe):

| obj radius r | planner-blocked radius | offset | approach cell @0.35 standoff |
|---|---|---|---|
| 0.20 | 0.426 | +0.226 | — |
| **0.30** | **0.535** | +0.235 | **blocked** |
| 0.35 | 0.576 | +0.226 | — |
| **0.40** | **0.637** | +0.237 | **blocked** |
| 0.50 | 0.742 | +0.242 | — |

**blocked_radius(r) ≈ r + 0.235 m** (≈ inflation_m 0.22 + ½ cell 0.025, measured slightly high).
The approach point at 0.35 m is inside it for r ≥ ~0.12 m; for r = 0.30/0.40 the approach point is
0.185 m / 0.287 m inside the blocked disc. (For r=0.40, 0.35 < r: the point is even inside the raw
object footprint.)

**The shipped 0.35 m default (`config.py:161`) is planner-safe only for objects of radius ≈ ≤ 0.11 m**
— and physically reachable "cleanly" (no reflex stop) only for r ≲ 0.07 m (see §4).

## 2. Planner behaviour with the goal inside the blocked region

`Planner.plan()` (`control.py:58-129`):
- `free_near` (`control.py:70-81`, `max_r=6` cells ⇒ 0.30 m Chebyshev search) substitutes the goal
  cell with a nearby free cell (`control.py:83-88`); A* then runs to that cell. Measured: it found a
  substitution in every tested case, e.g. r=0.40 → cell (27,35) 0.355 m away (Euclidean).
- **But `control.py:128` then splices the raw goal back in as the last waypoint**: `pts[-1] = goal`.
  Measured `plan_end == approach point` for every blocked case. The published path — and the
  `path_bearing_deg` Jev sees (`control.py:192-198`) — advertises a final point the planner itself
  considers blocked. Nothing downstream re-checks it: `_follow` steers at the last waypoint
  (`control.py:233-239`, `261-273`), and when `plan()` returns `[]` the executor still steers at the
  raw `scene.goal` (`control.py:233-239`).
- Replanning every 0.75 s (`control.py:208`) re-does this silently; `path_valid` stays True.

So the planner cannot be the layer that prevents the stall — it hides the substitution.

## 3. Driving results (measured, dt=1/15 s, 45 s budget, goal = approach point)

Reached = min distance to the goal < 0.30 (the `GoalManager` latch, `run.py:41,93`).
"parked" = v=0,w=0 for the final ≥6 s (map_brake); "chatter" = v=0 but w=±84 °/s, position frozen.

### Jev `hold_course` (judgment source jev), object in grid
| r | standoff | approach blocked | min dist to goal | outcome |
|---|---|---|---|---|
| 0.30 | 0.35 | yes | 0.225 | **parked** (map_brake ×656), permanently short |
| 0.30 | 0.40 | yes | 0.176 | parked ×656 |
| 0.30 | 0.45 | yes | 0.112 | parked ×656 |
| 0.30 | 0.50 | yes | 0.080 | parked ×657 |
| 0.30 | 0.55 | no | 0.030 | clean arrival, no reflex |
| 0.30 | 0.60 | no | 0.020 | clean arrival |
| 0.40 | 0.35 | yes | **0.318 → latch FAILS** | **parked (map_brake ×658), permanent standstill** |
| 0.40 | 0.40 | yes | 0.269 | parked ×658 |
| 0.40 | 0.45 | yes | 0.210 | parked ×658 |
| 0.40 | 0.50 | yes | 0.182 | parked ×659 |
| 0.40 | 0.55 | yes | 0.115 | parked ×659 |
| 0.40 | 0.60 | no | 0.060 | parked ×659 (braked before the free goal) |
| 0.40 | 0.70 | no | 0.040 | clean arrival |

### Baseline (no Jev), object in grid
| r | standoff | min dist to goal | outcome |
|---|---|---|---|
| 0.30 | 0.35 | **0.303 → latch FAILS (3 mm)** | chatter, position frozen |
| 0.30 | 0.40 | 0.253 | chatter/parked |
| 0.30 | 0.45 | 0.230 | chatter |
| 0.30 | 0.50 | 0.160 | chatter |
| 0.30 | 0.60 | 0.060 | parked |
| 0.40 | 0.35 | **0.435 → FAILS** | chatter |
| 0.40 | 0.40 | **0.385 → FAILS** | chatter |
| 0.40 | 0.45 | **0.326 → FAILS** | chatter |
| 0.40 | 0.50 | 0.260 | chatter/parked |
| 0.40 | 0.60 | 0.180 | parked |

### Stall signatures (traces, 1 Hz excerpts)
- Baseline chatter (r=0.40, standoff=0.35): x,y constant at (1.22, 1.96) for 12+ s; yaw alternating
  0.7°↔6.3°; `v=0, w=±84`; src `baseline`; nearest 0.39 m. (Blocked-turn term, `control.py:283-286`,
  toggling left/right at 84 °/s with no translation.)
- Jev park (r=0.40, standoff=0.35): (1.33, 1.97), yaw frozen 6.1°, `v=0,w=0`, src
  `reflex:map_brake`, nearest 0.29 m — identical rows to t=45 s (658 trips).
- Controls (object NOT stamped in grid): both radii reach min dist 0.03 m at standoff 0.35
  (src `path`, no reflex). **The stall comes purely from the object's own cells in the grid.**
- Diagonal approach (r=0.40, from 45°): same stall (min 0.424); `free_near` still substitutes.

Numeric model fitted to the data (collinear):
- Jev: stops when surface gap < 0.30 m (`control.py:407-410`, hardcoded `nearest_m < 0.30`,
  ±35°) ⇒ stop at `r + ~0.28`; shortfall = `(r + 0.28) − standoff`.
- Baseline: blocked-turn when `clear_ahead < footprint×2.2 = 0.396` (`control.py:283`) ⇒ stop at
  `r + ~0.35`; shortfall = `(r + 0.35) − standoff`.

## 4. Does the destination object actually enter the grid? (real pipeline)

`perception.process()` (`perception.py:748-762`): every pixel classified not-floor inside the room
polygon becomes an obstacle candidate → `occ_pts` → `grid.update` with `occ_delta=0.65` vs
`OCCUPIED_THR=0.42` (`perception.py:343-360,303`) — **one frame stamps the cell occupied. Nothing
excludes semantic objects, and nothing correlates the semantics store with grid cells.**

Pipeline E2E (`w4b-approach-pipeline.py`, SyntheticRoom + real Perception; "blue mat" 0.7×0.6 m at
x∈[2.65,3.35], y∈[0.90,1.50]; dest = centroid (3.0,1.2); approach (2.65,1.2) = exactly the mat edge):
- **180/288 mat-region cells occupied** after 10 warm frames (1155 total incl. wall ring) → the mat
  is in the grid.
- Planner-blocked region around the dest point: structural radius ≈ mat reach (0.35 m) + 0.235 m
  ≈ 0.59 m (measured max 0.94 m including synthetic speckle cells); approach cell **blocked=True**.
- `Planner.plan` found a path ending at (2.65, 1.2) (the splice).
- Drive A (baseline): min dist 0.345 m → **latch FAILS**; chatter at (2.32,1.11), d_mat 0.33 m.
- Drive B (Jev): min dist **0.270 m** → latch fires by 3 cm; but the rover is **parked** (final 6 s
  still, 573 `map_brake` trips) at 0.615 m from the destination point — **0.265 m off the requested
  0.35 m ring**; "stops within standoff" (`semantics-layer-proposal.md:174`) is violated in spirit.
- Fix probe: nearest free cell (2.43, 0.98), 0.318 m from the approach point (effective standoff
  0.62 m) → clean arrival, min 0.045 m, zero reflex trips.

## 5. Patch 11 presence (checked, not re-audited)

Commit `a82ca97` "semantics: approach_point never overshoots the destination" is on the PR tip
(`git log bec1d91`), implemented at `semantics.py:780-783` (returns dest when `length <= standoff`),
tested by `tests/test_semantics_destination.py:83-91`, documented `docs/reviews/m1-semantics/patches.md:234-245`
(patch 11). Relevant here: with the patch, a rover already inside the standoff ring is sent to the
destination ITSELF — i.e. for these objects, into the blocked region.

## 6. Safe-standoff rule (derived + verified)

Two independent envelopes act against the goal:

1. **Planner floor (must not be violated):** `standoff > r + inflation_m + ½·cell_m`
   ≈ `r + 0.235` measured (else the goal cell is blocked and the goal gets substituted/spliced).
2. **Reflex envelope (else the rover stops short):** the approach ring must sit outside the
   stop envelopes — Jev `map_brake` 0.30 m (`control.py:407-410`) / baseline blocked-turn 0.396 m
   (`control.py:283`) measured as stop surface-gaps ≈ 0.28 / 0.35 m.

**Rule:** `standoff_m >= obj_radius_m + max(inflation_m + cell_m/2, reflex_stop_m) + margin`,
with `margin ≥ 0.05`; round up to 0.05 m.
With the shipped constants (inflation 0.22, reflex 0.30–0.35): **`standoff >= obj_r + 0.35`**;
for the task's 0.6/0.8 m objects (r = 0.30/0.40) that is **0.65–0.75 m** (recommend **0.70 / 0.80**)
— matching measured clean arrivals (Jev: 0.55/0.70; baseline: 0.60/0.80; Jev 0.60 still parked
0.06 short). Minimum that merely keeps the planner honest (`r + 0.25`) is NOT enough: the rover
still parks ~0.3 m short and only the 0.30 m latch hides it.

Alternative / complementary fixes, in order of preference:
- **(F1) Project the goal to free space at the approach_point caller** (or a new
  `Planner.project_to_free()`): take the free-space substitute (as `free_near` already computes,
  `control.py:70-81`) and use IT as `scene.goal`, for the latch and for metrics. Verified in E2E:
  clean arrival, 0 reflex trips. Caveat: the projected point is the boundary of the inflated ring
  (~r + 0.24–0.28 m standoff), and the reflex may still stop just short — keep the 0.30 m latch, or
  push the projection out by the reflex margin.
- **(F2) Do not splice a blocked goal onto the path:** `control.py:128` `pts[-1] = goal` should be
  the substituted cell (or the plan should be rejected), so `path_valid`/`path_bearing_deg` never
  lie to Jev.
- **(F3) Exclude the destination object's footprint from the grid** while routing to it — correct
  but needs identity plumbing (grid cells carry no object id; would have to subtract the projected
  bbox footprint in the control loop). Heavier; only if tight (< inflation) standoffs are needed.
- **(F4) Per-object standoff from the detection bbox** — needs the footprint stored; today
  `SemanticObject` (`scene.py:285-292`) has no size field, `resolve_destination` returns a point
  (`semantics.py:754-772`). Add world-space w/h at projection time, then apply the rule in (F1).

**Where it must be encoded:**
1. `run.py:333-334` (the `approach_point(...)` call site) and the future M2 routing wiring it
   anticipates (`docs/reviews/m1-semantics/m2-design.md:537-540`: "set `scene.goal` to the approach
   point through the existing GoalManager/Executor path") — compute standoff per object per the rule.
2. `config.py:161` `DestinationConfig.standoff_m = 0.35` — raise the default (≥ 0.60), or keep it as
   a floor and derive per object; `config.py:300-301` validation currently only enforces `>= 0` —
   add a geometric floor (e.g. ≥ inflation + cell + ε) and document the object-size dependence.
3. `docs/planning/semantics-layer-proposal.md:171` (claim) and `:174` (acceptance) — errata: the
   sentence "obstacle inflation already exists in the planner" is exactly what makes a 0.35 m ring
   unreachable for objects of radius ≳ 0.12 m; the acceptance "stops within standoff" needs the rule
   (or F1). Also `docs/planning/m1-plan.md:353` (routing not wired) and the M2 design note.
4. Optional hardening: `control.py:407-410` (map_brake 0.30 hardcoded — consider deriving from
   config/standoff) and a regression test in `tests/test_semantics_destination.py` pinning
   `standoff >= obj_radius + inflation + margin` (the suite currently tests only ring geometry,
   `tests/test_semantics_destination.py:73-91`).

## 7. Scope / caveats

- Routing is **not wired on the PR tip**: `--find` "only resolves and prints" (`run.py:247`;
  end-to-end the approach point is printed at `run.py:333-339`, destination stored but never set as
  `scene.goal`). This defect is **latent** and lands with the M2 routing workstream — the sims drive
  the exact wiring that note proposes.
- Velocity tracking is perfect (no slip) and dt = 1/15 s; the stall is a control-law/reflex limit,
  not a timing artifact. Budget 45 s; Jev mode tested as `hold_course` (the reflex stop is
  maneuver-independent).
- Synthetic speckle can add stray occupied cells (inflating "blocked radius" measurements in the
  pipeline run); the structural numbers are from the noise-free stamped grid.
- The stop envelopes (0.28/0.35 m) derive from the hardcoded 0.30 m map_brake and the
  `footprint_radius_m`-scaled 0.396 m baseline threshold with `footprint_radius_m = 0.18`; a
  different footprint shifts them.
- All line numbers are PR tip `bec1d91`. Reproduction commands:
  ```
  cd ~/.hermes/cache/scratch/w4b/repo
  PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python ../w4b-approach-sim.py
  PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python ../w4b-approach-pipeline.py
  PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python ../w4b-approach-analysis.py
  ```
  Raw outputs: `w4b-approach-results.json`, `w4b-pipeline-results.json`, `w4b-analysis-out.txt`
  (next to this report).

## Appendix — key PR-tip citations used

| Claim | Evidence (PR tip bec1d91) |
|---|---|
| inflation 0.22, cell 0.05 | `config.py:70-75` |
| standoff 0.35 default | `config.py:158-161` |
| standoff validation only ≥0 | `config.py:298-301` |
| exec uses inflation | `control.py:162-164` |
| `blocked()` = inflated ∪ nogo | `control.py:55-56` |
| free_near / goal substitution | `control.py:70-81`, `83-88` |
| goal splice onto path | `control.py:120-128` (128) |
| replan cadence 0.75 s | `control.py:202-221` |
| follow = last waypoint | `control.py:233-239`, `261-273` |
| baseline blocked-turn / stuck | `control.py:276-287` |
| no_progress bookkeeping | `control.py:298-309` |
| map_brake 0.30 m ±35° | `control.py:407-410` |
| goal tolerance 0.30 / latch | `run.py:41`, `run.py:93`, `run.py:179` |
| --find resolves+prints only | `run.py:247`, `run.py:330-343` |
| not-floor → grid occupied | `perception.py:748-762`, `343-360`, `303` |
| dilation implementation | `perception.py:373-377` |
| rays + nearest_m | `perception.py:773-784`; `scene.py:106-146` |
| approach_point (patch 11) | `semantics.py:776-784`; `tests/test_semantics_destination.py:73-91`; `docs/reviews/m1-semantics/patches.md:234-245` |
| proposal claim / acceptance | `docs/planning/semantics-layer-proposal.md:171`, `:174` |
| routing not wired / M2 plan | `docs/planning/m1-plan.md:353`; `docs/reviews/m1-semantics/m2-design.md:516-517`, `537-543` |
| suite green on tip | 80 passed, 14.66 s (scratch clone) |