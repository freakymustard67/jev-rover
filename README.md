# jev-rover

A small rover that navigates a room from a **fixed overhead camera**, with Jev
(TypeSafe System One) as an advisory tactical layer. Code owns perception,
planning, execution and safety; Jev owns the judgments that are hard to write
as `if` statements: *is this path still good, is the rover genuinely stuck, is
that gap wide enough to be worth taking, is this observation good enough to
trust at speed.*

The architecture mirrors the drone project (<https://github.com/RomanSlack/jev-drone>),
with one structural difference: the camera is fixed and external, so
localization is solved by a floor homography instead of visual odometry.

```
 fixed camera (room)
      │
      ▼
┌──────────────────────────────────────────────────────────────┐
│ LAPTOP (Python)           15 Hz  perception   camera -> Scene │
│                            5 Hz  Jev          6 atomic Qs     │
│                           20 Hz  planner      A* + pure pursuit│
│                          100 Hz  reflex       code-owned, vetoes│
└──────────────────────────┬───────────────────────────────────┘
                           │ UDP, 20 Hz JSON, 400 ms watchdog
┌──────────────────────────▼───────────────────────────────────┐
│ ESP32 (firmware/)                                             │
│   motor mixing + slew limit + ToF reflex + watchdog stop      │
└───────────────────────────────────────────────────────────────┘
```

Two safety layers, and neither asks Jev for permission:

* **On the ESP32**: a failed sensor read stops motion; `dist_front_m < 0.18 m`
  forces `v = 0`; no command for 400 ms stops the motors. See `firmware/README.md`.
* **On the laptop**: the same reflex is mirrored in `control.py`, plus the map
  brake and the no-telemetry stop. Jev proposes; `control.py` decides; the
  ESP32 can always refuse.

The LLM question ("feed it to an LLM too") is answered in `mission.py`: mission
language maps to a **Choice over known routes** plus a feasibility **Noul**,
~100 ms and type-safe. An LLM only belongs here for open-ended dialogue; it
should emit a route name, never motor commands.

## Quick start (no hardware, no API key)

```bash
./setup.sh
.venv/bin/python -m pytest                                     # 25 tests
.venv/bin/python run.py --config config/room.synthetic.json \
    --source synthetic --mission patrol --seconds 30 --no-jev  # baseline drive
```

The synthetic source renders the room, the AprilTag and the furniture, then the
*real* perception pipeline reads it - same code path as the camera. Add
`--video runs/synth.mp4` for the HUD, and drop `--no-jev` (with a key) to put
Jev in the loop:

```bash
cp .env.example .env        # paste your TypeSafe key
set -a && . ./.env && set +a
.venv/bin/python tools/smoke_jev.py                            # 3 direct judgments
```

## Real room, step by step

```bash
.venv/bin/python calibrate.py cameras          # find your device (e.g. /dev/video10)
.venv/bin/python calibrate.py tag --id 0 --size 0.15
                                               # print at 100%, measure the square
cp config/room.example.json config/room.json   # point camera.source at your device
.venv/bin/python calibrate.py floor --config config/room.json
                                               # click floor corners, then reference points
.venv/bin/python calibrate.py check --config config/room.json
                                               # belief map must line up with the floor
```

Rules of thumb learned the hard way:

* **Tag size**: aim for >= ~50 px per side in the feed. A 1080p camera covering
  6.4 m sees a 0.10 m tag as ~30 px, which drops detections at rotated angles;
  0.15-0.20 m is the practical range. `calibrate.py tag` renders at true size.
* **White quiet zone**: the tag needs a white margin (>= 1 module) or detection
  fails against a dark rover body. The printed PNG includes it.
* **Mount the tag flat** on the roof, "up" pointing along the rover's forward
  axis; if it is rotated, set `rover.tag_yaw_offset_deg`.
* **Lens distortion**: optional (`calibrate.py intrinsics`), then set
  `camera.intrinsics` and re-run `floor` on undistorted frames.
* Wire the rover's own footprint as `rover.footprint_radius_m`; the room's walls
  are the floor polygon boundary, and everything outside the polygon is treated
  as wall.

Then drive:

```bash
# mock link: commands are simulated, nothing transmits
.venv/bin/python run.py --config config/room.json --source camera \
    --mission goto --waypoint kitchen

# real rover: requires --arm, and the ESP32 from firmware/
.venv/bin/python run.py --config config/room.json --source camera \
    --link udp --arm --esp32 192.168.4.1 --mission patrol --route patrol
```

`--mission track` follows the target configured in `target.mode` (a second
AprilTag or a colored object).

## The scene contract

Everything downstream of `perception.py` reads `scene.Scene` - one dataclass,
JSON-serializable, roughly 3.5 KB:

| group | what it contains | why it exists |
| --- | --- | --- |
| `pose`, `twist`, `quality` | rover pose, velocity, `pose_source` (tag/dead_reckon/lost), tag age, `occlusion_risk` (fraction of the 1.5 m ring never seen) | the model must know what the system cannot see |
| `sectors`, `free_runs_deg`, `clear_ahead_m`, `widest_run_*` | 7 named rover-frame sectors with free distance **and a `limit`**: `obstacle` / `unseen` / `range` | "wall at 0.6 m" and "never looked there" are different facts |
| `tracks` | obstacles with position, velocity, `kind` (static/slow/moving), age | people and pets are not furniture |
| `goal` | bearing, range, `path_valid`, `path_len_m`, **`path_bearing_deg`** (where the planner wants to go) | the plan is part of the observation, not hidden in code |
| `dynamics`, `hardware` | commanded vs observed speed, `no_progress_s`, ToF distance, watchdog state | stall and slip are observable facts |
| `target`, `mission`, `nogo_hit` | follow target and mission context | - |
| `semantics`, `sweep` | labeled objects (M1: triggered vision layer), resolved destination, per-pass diff, sweep state (schema only until M4) | meaning, not just geometry |

## Semantic layer (M1)

Geometry answers *where*; the semantic layer answers *what*, on demand. A vision
model runs only when triggered (mission start in M1; uncertainty/audit triggers
in M3), its detections are projected onto the floor plane, merged into a
persistent per-room map, and diffed between passes. `FakeVision` ships in M1 so
the whole pipeline is testable offline; real adapters are M2.

```bash
.venv/bin/python run.py --config config/room.synthetic.json --source synthetic \
    --mission patrol --seconds 8 --no-jev --semantics fake --find "blue mat"
# [find] 'blue mat' -> blue mat at (3.00,1.20) m conf=0.92; approach (3.12,1.53) standoff=0.35 m
```

* **Projection**: bbox anchor -> floor homography -> metres, rejecting anything
  outside the floor polygon (never clamping: no invented positions).
  `project.point_by_label` overrides the anchor per label because the measured
  error differs by object shape: `centroid` is ~1.5 cm for flat mats vs ~30 cm
  for `bbox_bottom_center`, and the reverse for standing objects.
* **`height_suspect`**: probes the pixels *below* the bbox base. Floor-coloured
  means the object rests on the floor; anything else (furniture) means the
  projection may be biased, which is the honest flag rather than a guess.
* **Motion**: computed from the RAW per-pass displacement, never the EMA step
  (smoothing turns a 0.35 m move into 0.14 m and would miss it).
* **Destination**: Jaccard token overlap over labels; ties within
  `ambiguity_epsilon` go to one budgeted Jev `Choice` over the candidate labels
  (code owns the options), with a deterministic fallback when no client is
  available. `approach_point` places the standoff on the rover->object line.
* **Worker discipline**: immutable `SemanticContext` snapshot (grid arrays +
  floor colour + polygon) plus a frame copy, one pass in flight, hard
  `max_passes_per_min` budget, failure cooldown, stale-result rejection.
  Counters land in `runs/summary_*.json` under `"semantics"`.
* Artifacts: `runs/semantic/<room>_latest.json` and `_events.jsonl` (written,
  not auto-loaded yet).

Perception itself: AprilTag pose straight from the floor homography (no
intrinsics needed for the planar case), floor-color model (optionally with an
empty-room background + slow adaptation), log-odds occupancy grid with explicit
unknown cells, blob tracking with velocity from a >= 0.3 s baseline, and a
vectorized ray fan. Walls are the polygon boundary; the rover's own footprint is
cleared from the map every frame (it is a blind spot, not an obstacle).

## The Jev layer (`tactics.py`)

Six atomic questions, one request, ~300-350 ms end to end, ~3.5 k tokens per
call. Code decides *when* to ask (`decision_needed`: obstacle within 3 m, a
blocked sector, high occlusion, no progress, a moving obstacle close, or a
stale judgment), fingerprints the scene so unchanged situations reuse the
answer, and caps the spend (`--budget`).

| question | type | meaning |
| --- | --- | --- |
| `maneuver` | Choice | hold_course / veer_left / veer_right / creep / back_and_turn / stop_and_wait / reacquire_goal |
| `risk` | Score | clear -> tight -> about to hit something, judged along the path bearing |
| `path_still_good` | Noul | may the planner keep driving its route? |
| `truly_stuck` | Noul | stall worth a recovery, vs. briefly slow |
| `path_obstructed` | Noul | seen obstacle on the goal line (vs. merely unseen) |
| `observation_unreliable` | Noul | creep until the picture improves |

Code consumes the Nouls as **gates**, not vibes: if `path_still_good >= 0.5`
the executor follows the planner's route and only scales speed by `risk`;
side-bias maneuvers override the planner only when the path is *not* good.
Every threshold a human should review lives at the top of `tactics.py`.

## The ablation

The point of the no-Jev baseline is a measurable comparison. Run the same
mission both ways and diff `runs/summary_*.json`:

```bash
.venv/bin/python run.py --config config/room.json --mission patrol \
    --route patrol --seconds 120 --no-jev --log runs/base.jsonl
.venv/bin/python run.py --config config/room.json --mission patrol \
    --route patrol --seconds 120 --log runs/jev.jsonl
```

| metric | where it comes from |
| --- | --- |
| collisions | `collision_reflex_events` (map/ToF reflex trips), plus bump events if you wire one |
| stuck fraction | `stuck_s / duration_s` |
| time under reflex | `reflex_reasons` histogram |
| goal completion | `goals_reached` vs `goals_expected` |
| cost/latency | `jev.calls`, `jev.tokens`, `jev.median_latency_s` |

`synthetic.py` gives a deterministic arena for repeatable runs before touching
hardware.

## What the build actually taught (worth keeping)

1. **The state must contain the answer.** First live smoke test: every judgment
   was `reacquire_goal`, because the smoke script forgot to set a goal. Second
   finding: the closed loop offered the scene to Jev *before* the executor had
   planned, so `path_valid` was always false. Jev was right both times - the
   state was wrong. Fix the state, not the prompt.
2. **Publish derived state every frame**, not only when it changes. The planner
   replans at 0.75 s intervals; scenes are rebuilt at 15 Hz. Only writing
   `path_*` on replan frames made 80% of states lie.
3. **Tags need a quiet zone.** Without the white margin, a rotated marker on a
   dark body fails detection at intermediate angles (8/24 angles missed at
   40 px). The renderer enforces it now, and `calibrate.py tag` prints it.
4. **Walls are not "unknown".** Cells outside the floor polygon were never
   observed, so rays stopped at 1.6 m in an empty room. The polygon boundary is
   now occupied terrain.
5. **A blind spot belongs to the robot.** The rover's own pixels were stamped as
   obstacles on the first frame (before a pose existed) and could never be
   re-observed. The footprint is cleared every frame and covered by the ToF.
6. OpenCV 5.0.0 ships an ArUco regression for rotated markers; this project
   pins `opencv-contrib-python<5` (4.12 verified, all angles).

## Layout

| file | what it is |
| --- | --- |
| `perception.py` | camera, homography, AprilTag pose, floor model, occupancy grid, trackers |
| `scene.py` | the Scene contract and all geometry conventions (bearings positive left) |
| `tactics.py` | **everything Jev-facing**: questions, rubrics, thresholds, gating, budget |
| `control.py` | A* planner, pure-pursuit executor, code-owned reflex |
| `run.py` | episode loop, missions, metrics, summaries in `runs/` |
| `link.py` | UDP / mock / dry-run transports, telemetry parsing |
| `viz.py` | HUD: camera + belief overlay, minimap, Jev panel |
| `synthetic.py` | deterministic room renderer for tests and dry runs |
| `calibrate.py` | cameras, tag printing, floor homography, intrinsics, sanity check |
| `mission.py` | natural-language missions as Choice + Noul over known routes; re-exports destination resolution |
| `semantics.py` | triggered semantic layer: FakeVision, projection, merge/diff, destinations (M1) |
| `firmware/` | reference ESP32 sketch and wire protocol |
| `tests/` | 67 tests: geometry signs, synthetic perception e2e, planner, reflexes, Jev gating, semantics (no network) |

## Honest limitations

* A single RGB camera cannot see shadow vs. dark object perfectly; the log-odds
  grid decays transient errors, and `floor.background` helps with an empty-room
  reference. Reflective/mirror floors will confuse it.
* Regions behind tall furniture are genuinely unobserved; the system reports
  `unseen`, and Jev is expected to creep rather than guess.
* There is no rear sensor: reverse is slow, short, and blind by construction.
* UDP is fire-and-forget by design; the watchdog is the reliability mechanism.
* `synthetic.py` renders the tag at 2.5x life size so detection is stable for
  tests; pose geometry does not use the tag size, but do not read detection
  ranges as real-world claims.
* Measured on this machine: perception ~50 ms/frame at 640x360 processing
  resolution (roughly 12-15 Hz with overhead), Jev median ~0.32 s, p90 ~1.0 s.

## Planning & roadmap

`docs/planning/` holds the design documents behind the current build state:

* `semantics-layer-proposal.md` — triggered semantic layer (vision model on
  demand) + physical sweep confirmation; milestones M1–M4
* `m1-plan.md` — the M1 implementation plan, conflicts found, decisions approved
* `sweep-validation.md` — simulation evidence for marker-less localization
  (180° ToF sweep + desmear; desmear is mandatory for moving sweeps)
* `prototype/` — pre-implementation validation scripts and their tests
* `../tools/sim/tof_sim.py` — the sweep simulator (numpy-only)

**Status:** M1 (offline semantic skeleton) shipped —
`.venv/bin/python run.py --config config/room.synthetic.json --source synthetic --semantics fake --semantics-once --find "blue mat" --seconds 5`.
Next: M2 (real vision adapter + driving to a resolved destination).
