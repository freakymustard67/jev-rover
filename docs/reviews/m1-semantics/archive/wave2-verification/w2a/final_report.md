# W2A — Behavioural verification of the M1 semantics layer

**Scope.** Seven specific behavioural claims about the committed M1 semantics implementation,
verified with minimal executable demos that drive the real code paths.
**Target:** `/home/freakymustard/jev-rover` @ `9c33ec0` (M1 in `f352f61`). Repo untouched (read-only);
experiments ran against a local clone `wave2/w2a/clone` (same commit) with the repo interpreter
`/home/freakymustard/jev-rover/.venv/bin/python`, `PYTHONDONTWRITEBYTECODE=1`.
Artifacts written to `wave2/w2a/artifacts/`. The shipped suite passes in the clone (67 passed) —
none of the behaviours below is covered by it.

**Run:** `cd wave2/w2a && for i in 1 2 3 4 5 6 7; do PYTHONDONTWRITEBYTECODE=1 \
/home/freakymustard/jev-rover/.venv/bin/python demo_$i.py; done`

## Verdict table

| # | Claim | Verdict | Script | Key output | Incident code |
|---|-------|---------|--------|-----------|---------------|
| 1 | Insertion-order greedy merge → two same-label objects can swap identities when a detection belonging to the newer object sits closer to the older one | **CONFIRMED** (greedy pairing was also min-cost in the headline case — see note) | `demo_1.py` | `obj_0001 consumed B's detection; obj_0002 consumed A's -> SWAPPED`; persists next pass; same detections with creation order reversed → `no swap` | `semantics.py` L270-280 |
| 2 | Object resting on a non-floor-coloured mat → `height_suspect=True` | **CONFIRMED** | `demo_2.py` | probe LAB distance 84.0 ≥ tol 26.0 → `height_suspect=True` (mat) / `False` (bare floor); also via `SemanticsRunner` (`worker_errors=0`) | `semantics.py` L176-203 (called L232) |
| 3 | No eviction: unmatched for 50 passes → never removed, can resurrect with same id | **CONFIRMED** | `demo_3.py` | after 50 empty passes: `objects_in_map=1`, `misses=50`, `last_seen_s=0.0`; `vanished` fired once (t=2.0); pass 52 → same `obj_0001`, `appeared=[]`, `next_id=2` | `semantics.py` L308-313; no deletion of `self.objs` anywhere |
| 4 | Below-`min_confidence` detection (0.05) within match radius still moves position via EMA and overwrites confidence/height_suspect | **CONFIRMED** | `demo_4.py` | `x 3.0→3.12` (EMA α 0.4), `conf 0.90→0.05`, `height_suspect False→True`, `motion=moved`, `diff.moved=['obj_0001']` | `semantics.py` L271-280 (no filter), L284-285 (overwrite) |
| 5 | Frame shape ≠ config `camera.width/height` → no error in the semantics path (silently wrong projection) | **CONFIRMED** | `demo_5.py` | real `Perception` accepts 640×360 under a 1280×720 config; projection `(1.500,2.400)` vs true `(3.0,1.2)` = **1.92 m error, no exception**; runner merges it silently (`worker.errors=0`); `for_scale(0.5)` → 0.000 m | `semantics.py` L209-216; `perception.py` L704-722 |
| 6 | Label matching is case-sensitive: `'blue mat'` + `'Blue Mat'` → duplicate | **CONFIRMED** | `demo_6.py` | pass 2 creates `obj_0002 'Blue Mat'` (`appeared`), dedupe keeps both overlapping case-variant detections, query ties 1.00/1.00 | `semantics.py` L273, L161 (vs case-folded L93/L120/L129/L592) |
| 7 | Two consecutive passes displaced 0.2 m each leave `motion='static'` although total drift is 0.4 m | **REFUTED** (as stated) | `demo_7.py` | straight 0.2 m/pass: motions `['static','moved']` — raw 0.200 then 0.320 > 0.25; `static/static` only for oscillation (net 0) or estimate-relative steps (net 0.28 m) | `semantics.py` L281-291 |

## Per-claim evidence

### 1. Insertion-order greedy identity swap — CONFIRMED
Two `'blue mat'` objects, older A=`obj_0001` at x=0.00, newer B=`obj_0002` at x=0.30. Next pass:
B's detection at 0.28 (|0.28−0.00| = 0.28 m, *closer* to A than A's own detection 0.45 at 0.45 m).
Detection scores are identity tags (merge copies `d.confidence`, L284), so consumption is observable:

```
pass2: obj_0001 x=0.112 conf=0.11 motion=moved      <- consumed B's detection (0.28)
pass2: obj_0002 x=0.360 conf=0.99 motion=static     <- consumed A's detection (0.45)
obj_0001 consumed B's detection; obj_0002 consumed A's detection -> SWAPPED
pass3 (B→0.30, A→0.60): obj_0001 x=0.187 conf=0.11 ; obj_0002 x=0.456 conf=0.99  -> association stays swapped
reversed creation order, same detections: obj_0001 (B, first) consumed B's detection; obj_0002 consumed A's -> no swap
```
The loop `for oid, prev in self.objs.items()` (L270, insertion order) gives the older object first
pick among unmatched detections within `match_radius_m` (L271-280), with no per-pass motion check.

**Overstatement note:** in the headline scenario the *swapped* pairing is also the minimum-cost pairing
under the code's own distance metric (own 0.47 m vs swapped 0.43 m), so greediness alone is not the
sole determinant — the association metric (detection vs the *lagging smoothed* estimate) plus per-pass
motion comparable to the match radius is the root cause. Insertion order is still decisive for this
algorithm: with the same detections and reversed creation order the swap does not happen. The shipped
test `test_ids_stable_with_two_same_label_objects` only exercises well-separated (3 m) objects.

### 2. `height_suspect` on a mat — CONFIRMED
Frame: synthetic-room floor colour + blue-mat rectangle (world 2.6–3.4 × 0.8–1.6 m), object bbox
`(570,450,630,510)` with its base on the mat. `height_suspect` probes the pixel at bbox bottom-centre
+ `probe_px` (L191-192): `(600,516)`, LAB distance from floor **84.0 ≥ tolerance 26.0** → `True`.
Control (same object on bare floor, probe `(1000,556)`, distance 0.0) → `False`. Reproduced both
through `project_detections` (L232) and end-to-end through `SemanticsRunner`/`SemanticsWorker`
(`merged height_suspect=True`, `worker_errors=0`).

**Note:** this is the documented M1 rule (`docs/planning/m1-plan.md` C1; probe below the base, not grid
overlap). For a *flat* mat the flag is a false positive for "elevated" — it is only honest as
"projection untrustworthy", which is the module's stated intent.

### 3. No eviction — CONFIRMED
After 50 consecutive empty passes: object still in the map (`objects_in_map=1`), `misses=50`,
`last_seen_s=0.0`, `next_id` still 2; `events.jsonl` shows `vanished` exactly once at miss #2. A later
detection at (3.05, 1.2) matched the original `obj_0001` with `appeared=[]` — the resurrection is
*silent* (no `appeared` event) and `misses` resets to 0. `resolve_destination('blue mat')` still resolves
the 50-pass-stale object (`object_id=obj_0001`) because ranking ignores `last_seen_s` and
`SemanticMap.age_s` is pass-level, not per-object.

### 4. Sub-threshold-confidence detection updates the object — CONFIRMED
`min_confidence` (0.5) is consulted only at L304 to gate `diff.appeared` for *new* objects. A matched
detection at distance 0.3 m (≤ 0.5) with score 0.05: `x 3.0 → 3.12` (= `0.6*3.0 + 0.4*3.3`, the EMA
step, 0.12 m), `confidence 0.90 → 0.05`, `height_suspect False → True`, `motion='moved'` and a `moved`
event emitted. Related: an *unmatched* 0.05-score detection creates an object with `appeared=[]`.

### 5. Wrong-sized frame — CONFIRMED
The real `Perception` accepts a 640×360 frame although config says 1280×720 (`FloorModel` scales its
polygon; `semantic_context` still hands out the full-res homography and polygon, `perception.py`
L704-722). A 640-space bbox projected with that ctx:

```
half-res bbox=(275,210,325,270) + full-res ctx -> world=(1.500,2.400) true=(3.0,1.2) error=1.92 m, no exception
Homography.for_scale(H,0.5) -> world=(3.000,1.200) error=0.000 m
full-res bbox on the small frame -> silently clipped to (550,359,639,359), error=0.61 m, no exception
SemanticsRunner end-to-end -> merged world=(1.5,2.4) error=1.92 m, worker.errors=0, last_error=None
```
Nothing compares `frame.shape[:2]` with `cfg.camera.width/height`; `semantics.py` L209/213-216 uses the
shape only for clipping. Practically reachable: `Camera.read` (perception.py L71-82) never verifies the
negotiated size after `cap.set(...)` (L53-55), so a driver that ignores or clamps the request yields
differently-sized frames silently. (`Perception`'s own `homography_small` is also fixed at
`proc_scale` of the config size, L660-661.)

### 6. Case-sensitive labels — CONFIRMED
`d.label != prev.label` (L273) is raw string equality; `dedupe_detections` (L161) likewise. Object
`'blue mat'` + detection `'Blue Mat'` → second object `obj_0002`, announced as `appeared`. Two
overlapping detections differing only in case are both kept. `rank_candidates` tokenises to lowercase,
so both labels score 1.00 for "go to the blue mat": a tie resolved by the deterministic fallback, and
`_ask_jev_label` would offer Jev two identical-looking labels. Inconsistent with the case-folded
lookups at L93 (FakeVision filter), L120-121 (build_vision filter), L129-130 (anchor override), L592
(query tokens).

### 7. Two 0.2 m passes stay `static` — REFUTED as stated
`raw` is `|detection − previous SMOOTHED position|` (L281, L290), compared strictly to
`move_threshold_m` (0.25). Straight-line 0.2 m/pass over two passes:

```
[A] straight   pass1 raw 0.200 -> static (pos 0.080); pass2 raw 0.320 -> MOVED (pos 0.208)   motions=['static','moved']
[B] oscillating +0.2 then −0.2 -> static/static (0.4 m travelled, net drift 0.0)
[C] estimate-relative +0.2 each pass -> static/static (detection stream net 0.28 m)
```
So the code *does* flag the second consecutive step of a 0.4 m same-direction drift (the raw-vs-lagging-
estimate measure compounds (for same-direction steps, `raw_n = step + (1−α)·raw_{n−1}`:
0.200, 0.320, 0.392 …). What is hidden: the first sub-threshold
step (by design), oscillation, and estimate-relative stepping. No reading with net drift 0.4 m over two
0.2 m steps yields `static/static` (collinear same-direction steps are the only way to reach 0.4 m net,
and that is exactly the case that reads 0.320 m and fires).

## Method / limitations
- Every demo drives committed code (`SemanticStore.merge`, `project_detections`, `height_suspect`,
  `SemanticsRunner`/`SemanticsWorker`, `Perception`, `Homography.for_scale`, `rank_candidates`,
  `resolve_destination`) — no reimplementation; detection scores are used as identity tags in demo 1.
- No hardware, no camera, no network; stores write under `wave2/w2a/artifacts/`.
- Not verified: real-adapter behaviour (M2/M3 do not exist in this commit), sweep confirmation (M4),
  and whether a real camera driver actually ignores the requested resolution (claim 5's trigger).
