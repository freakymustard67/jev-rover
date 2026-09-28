# Proposal — Triggered Semantic Layer + Physical Confirmation (sweep) for jev-rover

**Status:** proposal for review — do NOT start building before proposing an M1 plan.
**Origin:** design session with the project owner, 2026-09-28. Supersedes none of the existing architecture; extends it.
**Audience:** the coding agent working in `~/jev-rover`. Read fully, then respond with an M1 implementation plan + any conflicts with current code.

---

## 0. Summary

Add a *semantic layer* on top of the existing geometric perception:

1. **Geometry (exists):** fixed-camera floor homography + occupancy grid → dense `Scene` in meters, 15–30 Hz. This already IS the coordinate system.
2. **Semantics (new):** a vision model that runs **only on triggers** (not per frame) and attaches meaning to geometry: object labels + floor coordinates, including *destinations described in natural language* ("go to the blue mat"). Output: a `SemanticMap` merged into the scene.
3. **Physical confirmation (new, optional):** a servo-swept ToF sweep that verifies/refutes semantic objects by measuring real distances, and doubles as a slow but accurate pose anchor when the camera is degraded.

Rationale for trigger-based ML: the rover CPU must stay free for the control loop (15–20 Hz). A vision pass costs seconds on CPU — fine per-event, fatal per-frame. The existing Jev uncertainty signals (`observation_unreliable`, `path_obstructed`, `truly_stuck`) are natural triggers: the model's own doubt summons the expensive check.

The AprilTag/no-marker question is orthogonal to this proposal. Nothing here puts ML in the control loop.

---

## 1. Goals / non-goals

**Goals**
- G1: Localize user-named objects ("blue box") in room coordinates and route to them.
- G2: Maintain a persistent semantic map per room; detect changes between passes ("what's new/moved").
- G3: Keep CPU/latency budget safe: event-driven, budgeted, never blocking the control loop.
- G4: Multi-modal truth: camera gives position fast; sweep confirms physically; disagreement is a first-class signal.
- G5: Fully testable offline (fake vision adapter, synthetic room, no network in tests).

**Non-goals**
- No per-frame detection, no VLM in the control loop.
- No full SLAM; declared map stays the room rectangle + live grid.
- No 3D reconstruction; floor-plane projection with explicit `height_suspect` honesty.
- Sweep is a confirmation/anchor source, not the primary pose source (primary stays camera).

---

## 2. Architecture and data flow

```
 camera frame ──► perception.py ──► Scene (pose, grid, sectors, tracks)   [existing, 15-30 Hz]
                                        │
                   ┌────────────────────┴─────────────────────┐
                   ▼                                          ▼
        semantics worker (triggered)                 sweep worker (optional)
   VisionModel(frame) → detections              servo ToF sweep → ranges
   project to floor (homography)                 scan match → pose + ranges
   merge/persist SemanticMap                     confirm objects near bearings
                   │                                          │
                   └──────────────► Scene.semantics(+objects, destination) ◄──┘
                                        │
                                        ▼
                              tactics.py (Jev, budgeted)  [existing]
                                        │
                                        ▼
                              control.py → planner → link  [existing]
```

Worker discipline: mirror the existing Jev layer — run in a background thread, the caller builds an immutable state snapshot before handing it over, stale results are rejected by fingerprint/age. A semantic pass must never block `run.py`'s loop (overhead when idle ≈ 0).

---

## 3. Scene schema extensions (exact)

Additive, optional, backwards compatible. Suggest:

```json
{
  "semantics": {
    "age_s": 4.2,
    "passes": 7,
    "model": "fake-vision-v0",
    "objects": [
      {
        "id": "obj_0003",
        "label": "blue mat",
        "x": 3.21, "y": 1.12,
        "confidence": 0.82,
        "sources": ["vision", "sweep"],
        "plane_assumed": "floor",
        "height_suspect": false,
        "first_seen_s": 123.4,
        "last_seen_s": 234.5,
        "motion": "static"
      }
    ],
    "destination": {
      "label": "blue mat", "x": 3.21, "y": 1.12,
      "confidence": 0.82, "source": "vision"
    },
    "diff": {"appeared": [], "moved": [], "vanished": []}
  },
  "sweep": {
    "age_s": 1.1,
    "last_seq": 41,
    "pose_sigma": [0.03, 0.03, 2.0],
    "confirmations": [
      {"object_id": "obj_0003", "result": "confirmed", "range_err_m": 0.04}
    ]
  }
}
```

Notes:
- `height_suspect: true` whenever the bbox bottom overlaps a non-floor region of the grid, or the bbox bottom sits above the horizon-ish band → object may be on furniture; the floor projection is not trustworthy until confirmed.
- `sources` is a set; sweep confirmation adds `"sweep"`, contradiction removes confidence.
- Everything optional: configs without `semantics`/`sweep` behave exactly as today.

---

## 4. Module: `semantics.py`

```python
class Detection(TypedDict):
    label: str
    bbox_px: tuple[int, int, int, int]   # x0, y0, x1, y1
    score: float

class VisionModel(Protocol):
    name: str
    def infer(self, frame: np.ndarray, *, labels: list[str] | None) -> list[Detection]: ...

class FakeVision(VisionModel):      # deterministic fixture/replay, used by tests
    ...
class LocalVision(VisionModel):     # small open-vocab model on the box (optional extra)
    ...
class RemoteVision(VisionModel):    # HTTP endpoint (better laptop / cloud); timeouts, no creds in repo
    ...
```

- **Adapter selection by config**; `FakeVision` is the default in tests and CI-like runs.
- **State snapshots**: the worker receives `(frame, grid_snapshot, accessed_at_s)`; results older than `semantics.max_age_s` are discarded before merging.
- **Budget**: `max_passes_per_min`, single in-flight pass, cooldown after failure. Counters in `runs/summary_*.json` under `"semantics": {"passes": n, "errors": n, "median_ms": ...}`.

---

## 5. Projection math (`semantics` → floor coordinates)

- Primary reference point: **bbox bottom-center** (configurable `bbox_bottom_center | bbox_center | centroid`).
- Project via the existing floor homography to `(x, y)` in room meters; clamp into the floor polygon; reject outside.
- Compute `height_suspect` per §3 using the occupancy grid / floor mask.
- Temporal merge: match new detections to existing objects by label + proximity (greedy ≤ 0.5 m) with light smoothing (EMA α≈0.4); `motion` = static/moved from displacement over pass interval.
- Persist per room: `runs/semantic/<room_name>_latest.json` + append-only event log `runs/semantic/<room_name>_events.jsonl`. Never commit artifacts (already gitignored under `runs/`).

---

## 6. Change detection ("diff")

Between consecutive passes:
- `appeared`: new object id with confidence ≥ threshold
- `vanished`: object missing for N consecutive passes
- `moved`: same object, displacement > 0.25 m
Emit into `scene.semantics.diff` and the event log. Optionally include one-line diff summary in the Jev state so judgments can react ("a new obstacle appeared on my route") — keep it short, it costs tokens.

---

## 7. Destination resolution — NL text → coordinates

Extend `mission.py`:

```python
def resolve_destination(text: str, semantic: SemanticMap, jev) -> Destination | None
```

Pipeline:
1. Extract the object reference from the mission string (rule-based; "go to the …", "patrol until …").
2. Rank candidate objects from the semantic map by string similarity (embeddings if available; else normalized token overlap). If the top candidates are ambiguous (scores within epsilon), ask **Jev Choice over the candidate labels** (one budgeted call; code owns the option list, Jev picks the intended one).
3. If no candidate or confidence too low → trigger a fresh semantic pass (optionally with a targeted prompt/labels) and retry once.
4. Destination = object `(x, y)` with a configured standoff (`destination.standoff_m`, e.g. 0.35 m before the point; obstacle inflation already exists in the planner).
5. Feasibility check reuses the existing mission feasibility Noul. If unresolved after retry: abort with a clear message, never wander.

Acceptance for the first real feature: **`run.py --find "blue mat"` routes the rover to the mat and stops within standoff**, on synthetic fixtures first, then on the live camera.

---

## 8. Triggers and budgets (scheduler)

| Trigger | Condition | Pass kind |
|---|---|---|
| mission start | new run / explicit `--semantics-once` | full |
| destination request | `resolve_destination` miss | full, targeted labels |
| Jev uncertainty | `observation_unreliable > 0.5` for k consecutive judgments (configurable) | region-of-interest pass (labels = currently tracked tracks only) |
| audit | every `audit_period_s` (default 60 s) in patrol | full |
| disagreement | sweep range ≠ expected for an object (> tolerance) | targeted re-pass of that region |
| manual | debug key / `--find` | full |

Budget guards: one in-flight pass, min interval, `max_passes_per_min`, hard timeout per pass; on failure degrade confidence and continue (never block control).

---

## 9. Jev integration

- Keep existing six questions and their semantics unchanged.
- Add state fields only (§3). Optionally add **one** new Noul: `destination_trustworthy` — "Given the state, is the resolved destination location trustworthy enough to plan to?" — consumed by code, not vice versa.
- `observation_unreliable` remains the primary uncertainty trigger, as designed.
- All semantic calls go through the same gating/fingerprint/budget machinery in `tactics.py`.

---

## 10. Sweep module (physical confirmation + pose anchor)

### 10.1 Evidence from simulation (already run)

VL53L1X-class model (4 m range, ~1.5 cm + 1% noise, 5% dropouts, 2% outliers), room 6.4×3.6 m with 4 furniture blocks, scan-matching recovery:

| Scenario | pos p50/p90 | yaw p50/p90 | success |
|---|---|---|---|
| stationary, 91 beams (2°) | 2.4 / 4.0 cm | 1.2 / 2.3° | 100% |
| stationary, 31 beams (6°, ~1 s) | 2.5 / 5.5 cm | 1.1 / 2.3° | 100% |
| bootstrap, no prior | 8 / 116 cm | 3 / 15° | 77% |
| driving 0.45 m/s, 1.0 s sweep | 24 / 29 cm | 4.7 / 10.7° | 7% |
| driving 0.45 m/s, 0.5 s sweep | 13 / 16 cm | 3.2 / 7.4° | 70% |

Sim source: `/tmp/opencode/tof_sim.py` (numpy-only, uses this room's geometry). **All scenarios are front-facing 180° sweeps (−90°…+90°) — the servo's actual range.**

Desmear validation (matcher models commanded (v, w) per beam, with deliberate motion-estimate errors):

| Scenario (1 s sweep, 2° beams) | pos p50/p90 | yaw p50/p90 | success |
|---|---|---|---|
| desmear, drive 0.45 m/s | 2.7 / 5.6 cm | 1.2 / 2.2° | 100% |
| desmear, drive, +25% speed error | 6.4 / 10.1 cm | 1.5 / 3.1° | 100% |
| desmear, pivot in place 30°/s | 2.2 / 4.6 cm | 1.3 / 2.2° | 100% |
| desmear, drive 0.3 + turn 20°/s | 3.8 / 7.3 cm | 1.4 / 2.3° | 100% |
| desmear, hard pivot 60°/s | 3.2 / 5.7 cm | 1.2 / 2.0° | 100% |
| naive (no desmear), turn 30°/s | 9.1 / 13.2 cm | 15.5 / 18.9° | 37% |

Conclusions:
- **Paused sweeps are as accurate as the tag** (cm, ~1–2°) → premium confirmation source.
- **Sweeping while moving/turning works with desmear** — modeling the commanded (v, w) per beam restores near-stationary accuracy, robust to ±25% motion error and even 60°/s pivots. Without desmear, turning wrecks heading (15.5°) → **desmear is a hard requirement, not an option**.
- **Front 180° is sufficient**: a "scan turn" (spin in place while sweeping) yields a fresh full fix at 2–5 cm / ~1–2° — no 360° hardware needed. Rear objects are confirmed by turning toward them at waypoints.
- **Bootstrap ambiguity** (77% with the front-half view) → fix with two sweeps at two nearby poses, a scan-turn, or a coarse camera/vision fix first.

### 10.2 Firmware protocol extension (ESP32)

- New command: `scan = {start_deg, end_deg, step_deg, rate_hz}` (servo on existing PWM; reuse the existing ToF).
- Constraints: with `sweep.desmear=true` (validated in §10.1) scanning while driving/turning is allowed; otherwise scan only when `|v| ≤ v_scan_max` (config). Watchdog reset semantics unchanged — a scan must not starve the 400 ms watchdog (interleave reads).
- New telemetry: `scan_seq`, and a compact scan payload `{seq, angles[deg int8 relative], ranges[cm uint16]}` chunked if needed. No acks/retries, UDP fire-and-forget stays.

### 10.3 Host `sweep.py`

- `SweepMatcher.match(scan, prior, grid) -> (pose_delta, sigma, confidence)` — coarse-to-fine grid search as in the sim; desmear with commanded motion when enabled.
- `confirm_objects(scan, scene) -> [confirmation]` — for each candidate object with `height_suspect` or low confidence: bearing window = predicted bearing ± margin; compare measured range vs expected (from grid); tolerance by object size; outcomes confirmed/contradicted/absent.
- Contradiction policy: drop confidence below planning threshold → destination re-resolve → possibly a targeted vision re-pass.
- If no servo hardware: `confirmation.backend = "none"`; everything above still works, objects just carry lower confidence.

---

## 11. Config additions (all optional with defaults)

```json
{
  "semantics": {
    "enabled": false,
    "model": {"kind": "fake", "labels": [], "endpoint": "", "timeout_s": 10},
    "project": {"point": "bbox_bottom_center"},
    "audit_period_s": 60,
    "max_passes_per_min": 4,
    "max_age_s": 30,
    "roi_min_confidence": 0.5
  },
  "destination": {"standoff_m": 0.35},
  "sweep": {
    "enabled": false,
    "sensor": {"kind": "tof", "min_range_m": 0.04, "max_range_m": 4.0},
    "match": {"coarse_step_m": 0.2, "fine_step_m": 0.05, "yaw_step_deg": 5},
    "desmear": false
  },
  "confirmation": {"backend": "none", "tolerance_m": 0.15}
}
```

Backward compatibility: existing configs load unchanged; missing sections take these defaults.

---

## 12. Testing plan (offline-first, mirrors the current suite)

Unit:
- projection: bbox→floor coordinates incl. `height_suspect` when overlapping furniture in the grid
- merge/diff: appeared/moved/vanished, EMA smoothing, id stability
- destination ranking: candidate scoring; ambiguity → Jev Choice path with a fake client
- scheduler: triggers fire correctly, budget/cooldown respected, stale results discarded
- confirmation logic: confirmed/contradicted/absent from synthetic ranges

Integration (no network):
- synthetic room + `FakeVision` fixtures at known points → assert map error < tolerance
- full run `--semantics fake`: loop timing unaffected (assert control-rate unchanged vs. baseline)
- diff across two fixture passes produces the expected events

Live (opt-in, like `tools/smoke_jev.py`):
- `tools/smoke_semantics.py`: one real pass against the chosen model, prints detections + projected coordinates; skipped by default

---

## 13. Safety and failure modes

- ML never in the control loop; destinations pass through the existing planner, collision reflex, and watchdog.
- Physical (sweep) beats semantic on disagreement; semantic never upgrades past the planner's veto.
- All remote calls timeboxed; failures degrade confidence, never block.
- No secrets in config/artifacts; endpoint + token from env only.
- Keep "unknown" honest in the scene — no invented objects; low-confidence objects are still reported as uncertain to Jev.

---

## 14. Milestones and acceptance criteria

- **M1 — offline semantics skeleton.** Schema (§3), `semantics.py` with `FakeVision`, projection + merge + diff, destination matcher; unit + integration tests green; zero behavior change when disabled. *Accept: all new tests pass, existing 25 pass unchanged, loop-rate invariant holds.*
- **M2 — real vision adapter + manual trigger.** One real model (local or remote per owner's choice); `run.py --find "<object>"` works on fixtures then live; `tools/smoke_semantics.py`. *Accept: "blue mat" found and routed to on the real camera with standoff.*
- **M3 — triggers + Jev integration.** Scheduler (§8) incl. Jev-uncertainty trigger; `destination_trustworthy` (optional); budgets in summary. *Accept: uncertainty-triggered passes fire under injected uncertainty; cost/latency budget enforced.*
- **M4 — sweep track.** Firmware scan + `sweep.py` matcher (sim-validated first); confirmation protocol wired to semantics. *Accept: paused-sweep pose error ≤ 6 cm / 3° (90th pct as in sim); confirmations decide object trust in tests.*

Sequencing note: M1–M3 are independent of the sweep hardware and can proceed immediately. M4 requires the servo; start with the firmware + matcher work and gate hardware on sim results.

---

## 15. Open questions for the owner

1. Model placement: small local model on the rover host, or remote endpoint on the better laptop? (Trade: latency vs. model quality.)
2. Fixed demo label set first (car, mat, box, ball…) vs. open-vocabulary immediately?
3. Destination standoff default and whether "arrived" should be vision-confirmed (second pass) or sweep-confirmed when available.
4. Persist semantic maps across days (per-room memory) or session-only?

---

## Appendix A — references

- Sweep simulation: `/tmp/opencode/tof_sim.py` (results in §10.1)
- Existing contracts: `scene.py` (Scene JSON), `tactics.py` (Jev state/gating), `mission.py` (NL missions), `run.py` (episode loop, stats), `link.py` (UDP + watchdog), `synthetic.py` (test room), `tests/` (offline suite)
- Working assumptions: camera fixed overhead per session (portable between rooms), floor plane calibrated (homography), rover footprint 0.18 m, planner inflation 0.22 m.

## Appendix B — what this does NOT change

- Pose pipeline (tag / dots / sweep / camera-blob) — orthogonal; sweep here is a consumer and an optional anchor.
- Existing Jev questions, gating, fingerprints, budgets.
- Control loop structure, planner, reflex, firmware watchdog semantics.
