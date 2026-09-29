# M1 Implementation Plan — Offline Semantics Skeleton

**Status:** plan for review — no code written yet.
**Responds to:** `/tmp/opencode/semantics-layer-proposal.md` (§14 M1).
**Scope ruling:** M1 = schema + `semantics.py` (`FakeVision`, projection, merge/diff, destination
matcher) + perception snapshot seam + config + tests + minimal `run.py` wiring. No triggers, no
Jev integration beyond schema flow, no real vision adapter, no sweep logic, no driving to
destinations.

---

## 1. Conflicts and corrections vs. the current code

### C1 — SUBSTANTIVE: `height_suspect` as specified in §3/§5 would flag every real object

Proposal: *"`height_suspect: true` whenever the bbox bottom overlaps a non-floor region of the
grid"*. Any object resting on the floor (mat, box, ball, cable) is itself non-floor, so its own
pixels are marked occupied in the grid; the rule fires for ~100% of detections. The signal is
supposed to mean "this object may be on furniture; the floor projection is not trustworthy".

**M1 rule (needs your approval):** the flag comes from probing *below the object's base*, plus
grid freshness:

```
anchor_px   = bottom edge midpoint of the bbox (or config `project.point`)
world_xy    = H(anchor_px)
if world_xy outside floor polygon            -> REJECT the detection (see C2)
probe_px    = anchor_px + (0, probe_px)      # config `probe_px`, default 6
probe_floor = inside_polygon(probe_px) and lab_distance(frame[probe_px], floor_lab)
              < floor_lab_tolerance
cell_ok     = grid snapshot has that 5 cm cell observed (free or occupied)
height_suspect = (not probe_floor) or (not cell_ok)
```

Rationale: for a thing on the floor, the pixels immediately below its base are floor; for a thing
on a table, they are table (non-floor, and usually a *persistent* grid occupant).
Fallbacks: `floor_lab is None` (no frame classified yet) → pass refused with a clear reason, not
silently wrong flags. Probe outside the image → `height_suspect=true`.

If you prefer the literal §3 rule, the alternative is a **delayed** rule: compare against the grid
occupied state from *before the object first appeared*, which requires keeping a persistent map and
only works for objects newer than the map. I recommend the probe rule; the delayed rule can be a
second signal in M3.

### C2 — Contradiction in §5: "clamp into the floor polygon; reject outside"

Both cannot hold. M1 rejects (counted in `PassResult.rejected`). Clamping invents a position,
which violates §13 ("no invented objects"). Approval requested only so the choice is on record.

### C3 — No conflicts found in schema/backward compatibility (verified)

* Every `Scene` field has a default (`scene.py` lines 266-285), so appending `semantics` and
  `sweep` is additive and old serialized scenes still parse.
* `config._section` reads only known dataclass fields and ignores unknown JSON keys, so configs
  without the new sections load unchanged.
* `runs/` is already gitignored (`runs/semantic/` artifacts are covered).
* `tactics.build_state` pops only `mission`; `semantics`/`sweep` ride under `observed` with zero
  code change. See C5 for the token consequence.

### C4 — Interface seam the proposal does not specify: what the worker receives

The worker cannot safely touch `Perception`'s live state (mutated at 15 Hz by the main thread).
M1 adds a read-only snapshot, mirroring the `Tactician.offer` rule we already learned the hard way:

```python
# perception.py
@dataclass
class SemanticContext:
    homography: Homography          # immutable after construction
    room_w_m: float; room_h_m: float; cell_m: float
    polygon_px: np.ndarray          # full-res copy
    floor_lab: np.ndarray | None    # copy of FloorModel.color_lab (None until first classify)
    floor_lab_tolerance: float
    grid_log_odds: np.ndarray       # copy
    grid_last_seen: np.ndarray      # copy
    grid_t: float
    occupied_thr: float; stale_s: float

    def occupied(self) -> np.ndarray
    def observed(self) -> np.ndarray

# perception.py
def semantic_context(self, t: float) -> SemanticContext
```

Worker payload additionally carries a **full-resolution `frame.copy()`** (not the 0.5-scaled
processing frame; the homography is defined in full-res camera pixels) and `labels`/`kind`.
`FakeVision` only reads the frame shape, so tests do not need real pixels for the fake path.

### C5 — Token/state growth when semantics is enabled

With `semantics` populated, `observed` in the Jev state grows by the whole object list (and the
map is re-serialized every pass). M1 keeps `tactics.py` untouched but adds a **state-size bound
test** (10 objects → state < 6 KB). M3 owns compaction (labels-positions digest, one-line diff),
as the proposal says.

Also an instance of README lesson #2: `age_s` must be recomputed on every scene, not frozen at
merge time. `SemanticsRunner.snapshot(t)` returns a `dataclasses.replace` copy with fresh `age_s`
each frame.

### C6 — Config strictness gets riskier with four new sections

`_section` silently drops unknown keys, so `"max_age_ss"` in the new sections would be ignored
without warning while the feature quietly does nothing. M1 proposes `_section(..., strict=True)`
for `semantics`, `destination`, `sweep`, `confirmation` only (existing sections unchanged).
Approval requested; can be dropped to keep M1 smaller.

### C7 — Naming/type style: `Detection` as `TypedDict` (§4)

The codebase is dataclasses end-to-end and tests benefit from attribute access/defaults. M1 uses a
dataclass `Detection`; the `VisionModel` Protocol signature stays exactly as proposed
(`infer(frame, *, labels)`). One-line diff if you insist on `TypedDict`.

### C8 — Explicitly unaffected (no action)

Control loop, planner, reflex, link/UDP contract, firmware watchdog semantics, viz signature,
`SyntheticRoom` geometry, existing Jev questions/fingerprint/budget. `sweep` is schema-only in M1
(field present, always `None`).

---

## 2. M1 scope

**In:**
1. Schema dataclasses in `scene.py` + two optional `Scene` fields.
2. Config sections + example config (disabled by default).
3. `perception.SemanticContext` + `OccupancyGrid.snapshot()`.
4. `semantics.py`: `Detection`, `VisionModel` Protocol, `FakeVision` (+ `from_world`),
   `build_vision`, `project_detections`, `SemanticStore` (merge/diff/persist),
   `SemanticsWorker` (thread), `SemanticsRunner` (budget/cooldown/stale/poll),
   `rank_candidates`, `resolve_destination`, `approach_point`.
5. `run.py`: `--semantics {off,fake}`, `--semantics-once`, `--find LABEL`,
   `summary["semantics"]`.
6. Tests (~22 new) + README/config docs.

**Out (later milestones):** real vision adapters (`LocalVision`, `RemoteVision` — M2), driving to
a resolved destination (`--find` prints + sets nothing, driving is M2), trigger scheduler (§8 —
M3), `destination_trustworthy` Noul and state compaction (M3), sweep firmware/matcher (`sweep.py`,
M4), semantic artifacts in `viz.py` (stretch, not acceptance).

---

## 3. Exact schema (scene.py)

```jsonc
{
  "semantics": {                          // SemanticMap | null
    "age_s": 4.2,                         // recomputed every frame by the runner
    "passes": 7,
    "model": "fake-vision-v0",
    "objects": [{
      "id": "obj_0003",
      "label": "blue mat",
      "x": 3.21, "y": 1.12,
      "confidence": 0.82,
      "sources": ["vision"],              // set semantics, sorted list in JSON
      "plane_assumed": "floor",
      "height_suspect": false,
      "first_seen_s": 123.4,              // session-relative seconds (see C-notes below)
      "last_seen_s": 234.5,
      "motion": "static"                  // static | moved
    }],
    "destination": {                      // Destination | null
      "label": "blue mat", "x": 3.21, "y": 1.12,
      "confidence": 0.82, "source": "vision", "object_id": "obj_0003"
    },
    "diff": {"appeared": ["obj_0009"], "moved": [], "vanished": ["obj_0002"]}
  },
  "sweep": {                              // SweepState | null; schema only in M1
    "age_s": 0.0, "last_seq": 0,
    "pose_sigma": [0.0, 0.0, 0.0],
    "confirmations": [], "backend": "none"
  }
}
```

Dataclasses added to `scene.py`: `SemanticObject`, `Destination`, `SemanticDiff`, `SemanticMap`,
`SweepConfirmation`, `SweepState`. `Scene` gains
`semantics: SemanticMap | None = None` and `sweep: SweepState | None = None`
(internal-only fields like `last_pass_t` live on the store, not in the schema; `age_s` is
materialized per snapshot).

Notes: times are session-relative (`scene.t`), consistent with `target.unseen_for_s` and
`goal.progress_s`. Cross-session identity is open question §15.4; M1 persists but does not
auto-load. `sources` is an insertion-ordered dedupe list.

---

## 4. Public interfaces (`semantics.py`)

```python
@dataclass
class Detection:
    label: str
    bbox_px: tuple[int, int, int, int]   # x0, y0, x1, y1, FULL-RES camera pixels
    score: float = 1.0

class VisionModel(Protocol):
    name: str
    def infer(self, frame: np.ndarray, *, labels: list[str] | None = None) -> list[Detection]: ...

class FakeVision:
    def __init__(self, fixtures: list[Detection], name: str = "fake-vision-v0")
    def infer(self, frame, *, labels=None) -> list[Detection]      # label filter; frame shape checked
    @classmethod
    def from_world(cls, homography, entries, name=...) -> "FakeVision"
        # entries: (label, x_m, y_m, w_m, h_m, score)  -> bbox from 4 projected corners

def build_vision(cfg: SemanticsConfig) -> VisionModel      # "fake" in M1; local/remote -> clear error

def project_detections(dets, frame, ctx: SemanticContext, cfg: SemanticsConfig, t: float
                       ) -> tuple[list[SemanticObject], int]        # objects, rejected

class SemanticStore:
    def __init__(self, cfg: SemanticsConfig, room_name: str)
    def merge(self, result: PassResult, ctx: SemanticContext) -> tuple[SemanticMap, SemanticDiff]
    # writes runs/semantic/<room>_latest.json (atomic) and appends _events.jsonl

@dataclass
class PassResult:
    pass_id: int; t_submit: float; t_done: float
    kind: str; labels: list[str] | None
    objects: list[SemanticObject]; rejected: int; detections: int; model: str

class SemanticsWorker:                       # one thread, single in-flight, queue size 1
    def offer(self, *, pass_id, t_submit, frame, ctx, labels, kind) -> bool
    def poll(self) -> PassResult | None
    def close(self) -> None

class SemanticsRunner:                       # main-thread facade for run.py
    def __init__(self, cfg: RoomConfig, room_name: str, vision: VisionModel | None = None)
    def maybe_pass(self, t: float, *, labels=None, kind="full", force=False) -> bool
        # guards: enabled, min_interval_s, max_passes_per_min (rolling 60 s), cooldown,
        # single in-flight, ctx availability (floor_lab, grid observations)
    def poll(self, t: float) -> SemanticMap | None      # merges fresh results; None if unchanged
    def snapshot(self, t: float) -> SemanticMap | None  # fresh age_s copy for the current scene
    def stats(self) -> dict     # passes, errors, skipped_budget, rejected, median_ms, last_error

def rank_candidates(query: str, objects: list[SemanticObject], cfg) -> list[ScoredCandidate]
def resolve_destination(text: str, semantic: SemanticMap, jev=None, cfg=None) -> Destination | None
def approach_point(dest: Destination, from_xy: tuple[float, float], standoff_m: float
                   ) -> tuple[float, float]
```

`mission.py` re-exports `resolve_destination` (implementation in `semantics.py`) so the proposal
§7 signature is honored without duplicating logic; the retry-with-fresh-pass and the feasibility
Noul are M2/M3 stubs there.

### Pinned heuristics (M1)

| concern | rule |
|---|---|
| anchor | `project.point` = `bbox_bottom_center` (default) / `bbox_center` / `centroid`→bbox center in M1 |
| projection | full-res bbox corners clipped to frame → anchor px → `H.img_to_world` |
| reject | projected point outside floor polygon, or non-finite; counted per pass |
| reject threshold | point-in-polygon via `cv2.pointPolygonTest` on the scaled polygon |
| `height_suspect` | C1 probe rule above |
| duplicate detections in one pass | same label + (IoU > 0.5 or centers < 0.15 m) → keep highest score |
| merge | same label + nearest ≤ `match_radius_m` (0.5); greedy; unmatched = new id `obj_%04d` |
| smoothing | EMA α=0.4 on `x`,`y`,`confidence` |
| `motion` | `moved` if raw displacement this pass > `move_threshold_m` (0.25) else `static` |
| diff | `appeared` = new id with confidence ≥ `min_confidence` (0.5, new config key); `moved` = above; `vanished` = unmatched for ≥ `vanish_passes` (2), then removed from `objects` |
| destination ranking | token overlap: 0.7·(matched/query_tokens) + 0.3·(matched/label_tokens) + 0.2 substring bonus; stopword strip; trailing-s singular; per-label best object |
| ambiguity | top − second < `ambiguity_epsilon` (0.15) and top ≥ `min_label_score` (0.35) → one Jev `Choice` over top-3 labels; with `jev=None` deterministic: highest (score, confidence) |
| approach | `dest_xy + unit(from_xy − dest_xy) · standoff_m` |
| workers | daemon thread, queue size 1, `pass_id` monotonic, results with `t_done − t_submit` older than `max_age_s` discarded at poll |

### Config additions (all defaulted; example config ships them with `enabled:false`)

```jsonc
"semantics": {
  "enabled": false,
  "model": {"kind": "fake", "labels": [], "endpoint": "", "timeout_s": 10},
  "project": {"point": "bbox_bottom_center", "probe_px": 6},
  "audit_period_s": 60, "max_passes_per_min": 4, "max_age_s": 30,
  "roi_min_confidence": 0.5, "min_confidence": 0.5,
  "match_radius_m": 0.5, "ema_alpha": 0.4, "move_threshold_m": 0.25,
  "vanish_passes": 2, "dedupe_iou": 0.5,
  "min_interval_s": 2.0, "failure_cooldown_s": 10.0,
  "min_label_score": 0.35, "ambiguity_epsilon": 0.15,
  "store_dir": "runs/semantic"
},
"destination": {"standoff_m": 0.35},
"sweep": {"enabled": false,
          "sensor": {"kind": "tof", "min_range_m": 0.04, "max_range_m": 4.0},
          "match": {"coarse_step_m": 0.2, "fine_step_m": 0.05, "yaw_step_deg": 5},
          "desmear": false},
"confirmation": {"backend": "none", "tolerance_m": 0.15}
```

---

## 5. File-by-file plan

| file | change | est. |
|---|---|---|
| `scene.py` | 6 schema dataclasses; 2 optional `Scene` fields | +120 |
| `config.py` | 4 sections + validation (`>`0 budgets, standoff ≥0, enum `project.point`, model kind) + optional strict mode (C6) | +110 |
| `config/room.example.json` | disabled example sections | +25 |
| `perception.py` | `SemanticContext` + `OccupancyGrid.snapshot()` + `Perception.semantic_context(t)` | +70 |
| `semantics.py` (new) | everything in §4 | ~450 |
| `mission.py` | import + re-export `resolve_destination`; CLI prints destination when a map exists | +20 |
| `run.py` | flags, runner wiring on perception frames, per-frame `snapshot()` assignment, summary | +60 |
| `tests/test_semantics_schema.py` | schema + config defaults + backward compat | +90 |
| `tests/test_semantics_projection.py` | anchor modes, world round-trip, reject, height_suspect | +130 |
| `tests/test_semantics_merge.py` | merge/EMA/ids/dedupe/diff/persist | +140 |
| `tests/test_semantics_destination.py` | ranking, ambiguity→fake Jev Choice, approach, None paths | +120 |
| `tests/test_semantics_worker.py` | non-blocking, stale, budget, cooldown, error path | +110 |
| `tests/test_semantics_integration.py` | synthetic e2e, two-pass diff, state-size bound, loop-rate invariant | +150 |
| `README.md` | short semantics section + config table | +40 |

Total ≈ 1,600 changed lines, ~22 new tests.

---

## 6. Test plan (detail) and acceptance

**Projection tests.** `FakeVision.from_world` fixtures at known points → projected object error
< 0.02 m in the synthetic room (exact homography, so this is a regression bound, not a tolerance).
Anchor modes differ as expected for an off-center fixture. A fixture whose anchor lands outside
the polygon is rejected (count increments) and produces no object. `height_suspect=False` for a
fixture over open floor; `True` when the probe row crosses furniture; `True` when the probe is
outside the frame; `True` when the cell was never observed in the snapshot.

**Merge/diff tests.** Two passes, same fixture → one object, stable id, EMA moved < raw. Fixture
displaced 0.4 m → `motion="moved"`, `diff.moved` contains the id. Second object appears →
`diff.appeared` (respecting `min_confidence`); removed fixture for 2 passes → `diff.vanished`,
gone from `objects` on the third. Duplicate overlapping detections collapse to one. `latest.json`
round-trips; `events.jsonl` has one line per event with `t`/`kind`.

**Destination tests.** "go to the blue mat" ranks the mat first; two labels ("blue mat", "blue
mat large") within epsilon → fake Jev client called exactly once with options equal to the
candidate labels (code owns options); the answer maps back to a destination; no candidate →
`None`; `approach_point` distance equals standoff ± 1e-6 and lies on the rover→object line.

**Worker tests.** A `FakeVision` subclass that sleeps 0.5 s: `offer()` returns in < 50 ms and the
caller loop keeps counting iterations; second offer while in flight is refused; result from
`pass_id` N−1 arriving after N is discarded; `max_passes_per_min=2` blocks the third pass and
`stats()["skipped_budget"]` increments; a raising model → `errors=1`, cooldown blocks the next
`maybe_pass`; `close()` joins.

**Integration tests (no network).** Synthetic room + `Perception` (≥6 frames so the grid/floor are
warm) + one pass → objects at fixture coordinates. Two passes with a moved fixture → events.
Loop-rate invariant: with a 0.3 s sleeping vision and semantics enabled, frames/second over 1 s
within 20% of the disabled baseline. State-size bound: 10 objects → `len(json.dumps(build_state(scene)))`
< 6 KB. Disabled path: `--semantics off` → `scene.semantics is None`, summary
`"semantics": null`, zero worker threads created.

**Acceptance (M1 done)**
1. `pytest` green: existing 25 unchanged + ~22 new, no network, no live calls.
2. `--semantics off` (default): behavior byte-identical apart from two `null` keys.
3. `--semantics fake --find "blue mat"` on `room.synthetic.json` resolves the
   fixture and prints the standoff approach point (routing/driving intentionally not wired yet).
   Runnable form — the originally written command was missing `--mission`/`--no-jev`; corrected
   in the M1 deep review (`docs/reviews/m1-semantics/REVIEW.md` §3.1):

   ```bash
   .venv/bin/python run.py --config config/room.synthetic.json --source synthetic \
       --mission patrol --seconds 20 --no-jev --semantics fake --find "blue mat"
   ```
4. Budget/cooldown/error counters visible in `runs/summary_*.json` under `"semantics"`.
5. Loop-rate invariant test passes with a deliberately slow fake model.

---

## 7. Sequencing (each step keeps the suite green)

1. Schema + config + example JSON + schema tests.
2. `SemanticContext`/snapshot + tests.
3. Projection + `FakeVision` + heuristics + tests.
4. Store: merge/diff/persist + tests.
5. Worker + runner + tests.
6. Destination matcher + approach + tests (fake Jev).
7. `run.py` wiring + summary + integration/loop-rate/state-size tests.
8. README + config docs.

---

## 8. Decisions requested before building

1. **C1** — approve the probe-based `height_suspect` rule (or choose the delayed-grid variant).
2. **C2** — reject outside the polygon, never clamp.
3. **C6** — strict config parsing for the four new sections (typos raise) — yes/no.
4. **C7** — `Detection` as dataclass instead of `TypedDict` — yes/no.
5. `min_confidence` as a new config key (proposal §11 did not list it).
6. M1 `--find` scope: resolve + print only, driving wired in M2 — confirm.
7. Semantics enters the Jev state uncompacted in M1 (size-bounded by test), compaction in M3 —
   confirm.
8. Persist artifacts (`latest.json`, `events.jsonl`) but do not auto-load at start in M1 — confirm
   (ties into proposal §15.4).

---

## Approvals (2026-09-28, owner)

All eight decisions approved: C1 probe rule (ray-walk proven as fallback, probe is
the M1 rule), C2 reject-not-clamp, C6 strict config, C7 Detection as dataclass,
`min_confidence` added, `--find` print-only, uncompacted state with size test,
persist-no-autoload.

## Findings folded in from /tmp/opencode/semantics_proto/

1. Motion must use RAW displacement, not the EMA step (0.35 m reads as 0.14 m
   after smoothing). Implemented in `SemanticStore.merge`; regression test
   `test_motion_uses_raw_displacement_not_the_ema_step` and the two-pass
   integration test.
2. Per-label anchor override (`project.point_by_label`): measured ~30 cm error
   for `bbox_bottom_center` on flat mats vs ~1.5 cm for `centroid`; the reverse
   for standing objects. The synthetic config sets `{"blue mat": "centroid"}`.
3. An object on a table projects ~61.6 cm off; the probe rule flags it while
   proximity-to-furniture alone does not. Covered by
   `test_height_suspect_on_furniture_and_on_floor`.

Also adopted from the prototype: Jaccard ranking (`min_label_score=0.34`,
`ambiguity_epsilon=0.15`), keep-object-on-vanish (the vanish event fires once at
the threshold; the object can be re-matched later), and raw confidence (no EMA
on confidence).
