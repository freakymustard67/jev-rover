# I7 — approach-standoff fix (w4b F1/F2 + standoff rule): implementation & E2E validation

**Task:** implement + E2E-validate the approach-standoff fix for jev-rover at PR tip `bec1d91`
(`review/m1-semantics-audit`), per `runs/20260928-2057/w4b-approach-planner.md`.
**Verdict:** fix implemented, unit + E2E verified (patched vs unpatched, deterministic),
`git am` clean on a fresh clone; suite 80 → **86 passed**. The permanent-standstill failure mode
(parked 0.318 m short, 658 `map_brake` trips, latch never fires) is gone.

**Artifacts** (all under `runs/20260929-0118/i7-approach-standoff-evidence/`, sha256s in
`MANIFEST.sha256`):

| Artifact | Purpose |
|---|---|
| `i7-approach-standoff.patch` | **the patch** (2 commits; sha256 `3b222edd251db9428e4aca459956c9ad2916641fee96f1e14a0541f758fd6e91`) |
| `i7-approach-e2e.py` | E2E harness 1: deterministic stamped-grid sweep, obj_r ∈ {0.15,0.30,0.40}, modes jev/baseline, wiring raw/proj/rule/rec |
| `i7-approach-pipeline.py` | E2E harness 2: real `SyntheticRoom` + `Perception` loop (adapted from w4b-approach-pipeline.py), mat r≈0.30, cfg standoff 0.35 |
| `i7-geometry-probe.py` | blocked-radius/occupancy probe per obj_r |
| `i7-e2e-{patched,unpatched}.json/.out`, `i7-pipeline-{patched,unpatched}.json/.out` | raw outputs |
| `i7-e2e-patched-rerun.json` | determinism check (byte-identical to `i7-e2e-patched.json`) |
| `i7-suite-amtest.out`, `i7-am-verify.out` | suite + `git am` verification |
| `i7-errata-snippet.md` | errata draft (also applied in patch commit 2) |

**Environment:** scratch clone `~/.hermes/cache/scratch/i7/repo` (branch `review/m1-semantics-audit`,
2 commits on `bec1d91`); unpatched control clone `i7/repo-base` at `bec1d91`; fresh-clone `git am`
verification `i7/repo-amtest`; master worktree `i7/repo-master` at `9c33ec0`. Python
`/home/freakymustard/jev-rover/.venv/bin/python`, `PYTHONDONTWRITEBYTECODE=1`, no network during
tests. Real repo untouched; nothing pushed.

---

## 1. The fix (patch commits `176a535` fix+tests, `2105e4a` docs)

**F2 — `control.py` never splices a blocked goal onto the path** (`plan()`, patched lines 88–140).
The goal cell is resolved once (`goal_cell`); `_free_near` gives the substitute `e`. The final
waypoint is the raw goal **only when its own cell is free**; otherwise it stays the substitute cell
centre. `path_valid` / `path_bearing_deg` can no longer advertise a point the planner itself blocks.
(`_to_cell`/`_free_near` extracted to module helpers, control.py:39–57, shared with…)

**F1 — `Planner.project_to_free(point, max_r=6)`** (control.py:143–166): returns `point` unchanged
when its cell is free, else the centre of the nearest free cell within `max_r` — exactly the
substitute `plan()` uses. Wired at the approach_point caller (`run.py:342`):
`gx, gy = executor.planner.project_to_free((ax, ay)) or (ax, ay)`, and printed when it moves the
goal (`[find] approach inside planner inflation; goal projected to …`). This is the value M2
routing must set as `scene.goal` (comment at the call site; m2-design addendum in commit 2).

**Standoff rule — `config.py`:**
- `DestinationConfig.standoff_m` documented (0.35 kept as the floor default).
- Validation floor (`config.py:307–317`): `standoff_m >= grid.inflation_m + grid.cell_m + 0.05`
  (0.32 synthetic / 0.34 example — both configs still load; the old `>= 0` check could not catch
  the silent-substitution regime).
- `RoomConfig.required_standoff_m(obj_r, margin=0.05)` (`config.py:374–389`): w4b §6 rule —
  `obj_r + max(inflation + cell/2, 0.30 map_brake gap) + margin`, rounded up to 5 cm →
  **0.50 / 0.65 / 0.75 m for r = 0.15 / 0.30 / 0.40** (`obj_r + 0.35` with shipped constants).

**Tests — `tests/test_approach_standoff.py` (new, 6 tests):** plan never ends on a blocked goal cell
+ final waypoint == `project_to_free`; executor replan ends at the substitute (not the raw goal);
exact goal preserved when free; standoff rule clears planner + reflex envelopes and is 5 cm-rounded
(0.65/0.75 pinned); rule ring is planner-free (r=0.30); config rejects standoff below the floor.
Existing ring-geometry tests unchanged and passing.

---

## 2. E2E before/after

### H1 — stamped-grid sweep (deterministic; obj (2.0,2.0), start (0.7,2.0); latch = goal tol 0.30, arrival = <0.10 m)

**BEFORE (unpatched `bec1d91`)** — reproduces w4b measurements exactly:

| obj_r | mode | min d_goal | latch<0.30 | reflex trips | tail | plan_end |
|---|---|---|---|---|---|---|
| 0.15 | jev | 0.070 | yes | map_brake 653 | parked (frozen 6 s) | raw goal |
| 0.15 | base | 0.150 | yes | 0 | frozen (chatter/stop) | raw goal |
| 0.30 | jev | 0.225 | yes | **map_brake 656** | parked (frozen) | raw goal (splice) |
| 0.30 | base | 0.303 | **NO (3 mm)** | 0 | frozen | raw goal (splice) |
| 0.40 | jev | **0.318** | **NO** | **map_brake 658** | parked (frozen) | raw goal (splice) |
| 0.40 | base | 0.435 | **NO** | 0 | frozen | raw goal (splice) |

**AFTER (patched), shipped wiring `proj@0.35`** (goal = projected free point; F1+F2):

| obj_r | mode | goal (projected) | min d_goal | latch | arrival | trips | tail |
|---|---|---|---|---|---|---|---|
| 0.15 | jev | (1.65,2.0) (=ring; cell free) | 0.070 | yes | yes | 653 | brake-held at goal |
| 0.15 | base | (1.65,2.0) | 0.150 | yes | no | 0 | stopped inside tol |
| 0.30 | jev | (1.475,1.875) | **0.054** | yes | yes | 656 | brake-held at goal |
| 0.30 | base | (1.475,1.875) | **0.131** | yes | no | 0 | stopped inside tol |
| 0.40 | jev | (1.375,1.775) | **0.016** | yes | **yes** | **6** | moving; goal reached |
| 0.40 | base | (1.375,1.775) | **0.166** | yes | no | 0 | stopped inside tol |

**AFTER (patched), rule standoff `cfg.required_standoff_m(obj_r)` = 0.50/0.65/0.75** — fully clean,
both modes, all radii: min d_goal **0.030–0.050**, latch ✓, arrival ✓, **0 reflex trips**, not
frozen, min surface gap **0.35–0.39 m**.

### H2 — real pipeline (SyntheticRoom + Perception; 0.7×0.6 m mat; dest centroid (3.0,1.2); approach (2.65,1.2); cfg standoff 0.35)

| arm | jev min d_goal / trips | base min d_goal / trips |
|---|---|---|
| BEFORE (unpatched) | 0.270 / **map_brake 573**, parked (w4b: 0.270/573 ✓) | 0.345 / **latch FAILS**, frozen |
| AFTER raw (F2 only) | 0.248 / 132, keeps trying | 0.392 / latch FAILS |
| AFTER proj (F1+F2, shipped) | **0.045 / 0 trips**, not frozen, mat gap 0.268 | 0.124 / latch ✓, 0 trips |

Projected goal (2.425, 0.975) = 0.318 m from the ring (effective standoff 0.617 m; w4b fix probe:
0.318/0.045/0 — reproduced). The pipeline's larger speckle-inflated footprint pushes the projection
far enough out that the real stack gets a **zero-reflex clean arrival at the config's 0.35 m
standoff**.

### Acceptance assessment (definition adopted)
arrival = min d_goal < 0.10 m; 0 trips = `reflexes == {}`; distance-to-object = min surface gap ≥ 0.20 m.
- **Met**: H2 pipeline after-fix (0.045 / 0 / 0.268) — the integration the task's item (3) asks for;
  H1 rule standoff for all obj_r, both modes (0.03–0.05 / 0 / 0.35–0.39).
- **Partially met, documented**: H1 `proj@0.35` for r=0.15/0.30 — the latch fires and the rover
  reaches (r=0.30: 0.054) or nearly reaches (r=0.15: 0.070) the projected goal, but the projected
  ring is inside the map_brake corridor, so the reflex *holds* it there (653/656 trips while holding
  at the goal). **The permanent standstill (latch never fires, rover frozen 0.3 m short) is gone in
  every patched arm**; zero-trip clean arrival at a fixed 0.35 ring is physically impossible for
  r ≥ ~0.12 m — that is exactly the w4b §6 finding and why the per-object standoff rule exists.
- Baseline arms with latch fired show a frozen tail (no-Jev stop inside tolerance); no-Jev latch
  failures at 0.35 (0.303/0.435 before; 0.314/0.440 raw after) are gone in the shipped wiring
  (0.131/0.166 → latch fires).

---

## 3. Tests & patch verification

- Suite on patched tip: **86 passed** (80 + 6 new), 18.5 s. Fresh `git am` clone: **86 passed**
  (`i7-suite-amtest.out`).
- `git format-patch -2 --stdout` → `i7-approach-standoff.patch` (424 lines, 2 commits);
  sha256 `3b222edd251db9428e4aca459956c9ad2916641fee96f1e14a0541f758fd6e91`.
- `git am` on a **fresh clone at `bec1d91`**: clean, both commits applied
  (`bba7cf3`, `af6caa7`; `i7-am-verify.out`).
- `git apply --check` of the two commit diffs against **master `9c33ec0`**: **fails** — commit 1 on
  `run.py` (context changed by the 14 review commits), commit 2 because
  `docs/reviews/m1-semantics/m2-design.md` does not exist on master. Noted, not forced; the patch
  targets the PR tip.
- Determinism: H1 re-run produced a **byte-identical** JSON (`8520b067…` both).

**Re-run (parent spot-check):**
```
cd /home/freakymustard/jev-rover-research/runs/20260929-0118/i7-approach-standoff-evidence
REPO=/home/freakymustard/.hermes/cache/scratch/i7/repo PYTHONDONTWRITEBYTECODE=1 \
  /home/freakymustard/jev-rover/.venv/bin/python i7-approach-e2e.py /tmp/i7-check.json   # ~6 s
cmp i7-e2e-patched.json /tmp/i7-check.json   # expect: identical
```
(`i7-approach-pipeline.py` takes ~2m45 for 4 drives; the unpatched clones are at
`i7/repo-base`, `i7/repo-amtest`, `i7/repo-master`.)

---

## 4. Caveats / assumptions

- **Sim-only validation** (no hardware, no live camera; ideal velocity tracking, dt = 1/15 s).
  H1 is the noise-free stamped-grid minimal repro; H2 uses the real perception code path with
  synthetic frames (speckle included).
- Reflex behaviour at the projected ring is **approach-geometry dependent**: H2's projection lands
  0.318 m out with the mat off-axis (>35° at arrival) → 0 trips; H1's projection lands on the
  inflated boundary with the object on-axis → trips *while holding at the goal*. The rule standoff
  removes the ambiguity.
- Routing is still **not wired** on the tip (the defect remains latent, landing with M2); this
  patch makes the caller compute the projected goal and the tests/sims exercise the exact wiring
  M2 will use.
- `ctrl` constants: map_brake 0.30 m and the blocked-turn threshold remain hardcoded in
  `control.py`; `required_standoff_m` mirrors 0.30 as a documented local constant — update both if
  the reflex envelope changes. The no-Jev blocked-turn measured stop (~0.35 m gap) is covered by
  the +0.05 margin/rounding (verified: baseline clean at the rule standoff).
- H1 obj_r=0.15 note: the 0.35 ring's cell is *not* planner-blocked (blocked radius ≈ 0.389;
  boundary case) — its stall is purely reflex-envelope; F1 correctly leaves it unchanged.

## 5. Decisions for the owner

1. **Standoff package shape**: current fix keeps `destination.standoff_m = 0.35` as the validated
   floor and adds the per-object rule (`required_standoff_m`). Alternative (w4b): raise the default
   (≥ 0.60). A third option, if "0 trips at 0.35 without knowing obj_r" is required: push the
   projection outward by the reflex margin (not implemented; ~20 lines in `project_to_free` — can
   be added on request).
2. **M2 routing**: consume `planner.project_to_free(...)` + `required_standoff_m(obj_r)`; decide
   whether to add F4-lite (carry a world-space radius from the detection bbox into
   `SemanticObject`/`Destination`) so the caller can apply the rule without the fixture path.
3. **Docs commit**: the errata (proposal :171/:174, m1-plan :353, m2-design routing note) is
   commit 2 of the patch — drop it if the merge should stay code-only (`i7-errata-snippet.md`
   keeps the text either way).
