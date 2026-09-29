# M2 design — real vision adapter + manual trigger

Design only; **no repo changes applied**. Everything below was verified against the working tree
at `9c33ec0` (M1 shipped). Scratch, probes and the clone used for the acceptance run live in
`/home/freakymustard/.hermes/cache/scratch/wave2/w2c/`.

Inputs folded in: wave-1 reports `01` (frame-space gap), `02` (heuristics), `03` (destination),
`04` (verified model landscape), `06` (tests/acceptance), `00`/`07` (coherence), plus a re-read of
`perception.py`, `run.py`, `calibrate.py`, `semantics.py`, `config.py`, `viz.py`, `tactics.py`,
`tests/`, `config/room.synthetic.json`, `docs/planning/{m1-plan,semantics-layer-proposal,README}.md`.

M2 scope (as commissioned): **one real vision adapter (local and/or remote) behind the existing
`VisionModel` seam + a manual trigger**, wired through the M1 runner (one in-flight, budgets,
stale rejection, cooldown). Out: trigger scheduler (M3), sweep (M4), driving to a destination
(see §3.5 — the plan's M2 acceptance line says "routed to"; that is a separate workstream).

Two M1 defects are M2-blocking and are fixed first, because a real detector's bboxes are the first
pixel-space data to cross the seam:

1. **frame-space ambiguity** (raw capture vs undistorted/homography space) — §1;
2. **the plan's acceptance command cannot run as written** (verified) and the cooldown counter is
   missing — §3.

---

## 1. Frame-space contract fix

### 1.1 The defect (what the code does today)

| where | today | space |
|---|---|---|
| `run.py:287` | `perception.process(frame, …)` | **raw** capture |
| `perception.py:733-734` | `frame = cv2.undistort(frame, K, dist)` into a local | undistorted, full-res |
| `perception.py:736-805` | pose, floor, target, grid, blobs all use that local | undistorted |
| `self.homography` | built from `cfg.homography` correspondences | undistorted (per `calibrate.py`'s own rule, lines 9-10: "re-run `floor` on undistorted frames") |
| `run.py:294` | `runner.maybe_pass(t, ctx, frame, …)` | **raw** — worker projects bboxes through `H` |
| `run.py:342` | `renderer.draw(frame, …)` | **raw** — polygon/belief overlays computed with full `H` |
| `calibrate.py:147-211` | `cmd_floor` clicks on the grabbed frame | **raw**, despite its docstring |

M1 is self-consistent only because `FakeVision.from_world` builds bboxes through the same full-res
`H`, and no shipped config sets `camera.intrinsics`. With intrinsics set (which `calibrate.py`
instructs the operator to do), bboxes/probe/renderer live in a different space than the homography.

**Magnitude (measured today, this box).** K = (fx=fy=800, cx=640, cy=360), dist =
(k1=−0.15, k2=0.05, p1=p2=0.001), 1280×720: `cv2.undistortPoints` moves the frame corners by
**46–88 px** (centre 0 px). At 3.3–5 mm/px that is 15–45 cm of floor error — larger than the 0.05 m
grid cell and larger than the 6 px probe itself. (The wave-1 report quoted 10–30 px with milder
coefficients; either way it is tens of pixels.)

### 1.2 The contract (one sentence)

> The frame handed to the semantics worker and to the renderer must be the **same frame object
> `Perception.process` used**: full configured resolution, post-undistort — i.e. the space the
> homography is defined in. That frame is `Perception.frame_h`.

Corollary: `Detection.bbox_px` is "pixels of the frame passed to `infer()`" — for M2 adapters that
is full-res homography space (LocalVision downscales internally and maps boxes back; RemoteVision
rescales server boxes back — §2.3/§2.4).

### 1.3 Minimal API change (chosen) — expose the frame that was used

`perception.py`:

```python
class Perception:
    def __init__(self, cfg: RoomConfig, proc_scale: float = 0.5):
        ...
        self.frame_h: np.ndarray | None = None   # NEW: last frame in homography space

    def process(self, frame, t, cmd_v=0.0, cmd_w_deg_s=0.0, frame_age_s=0.0) -> Scene:
        """... Contract: `frame` is a raw capture at cfg.camera resolution. On return,
        `self.frame_h` is the frame in homography space (post-undistort, full-res).
        Every consumer after this call — semantics worker, renderer — must use
        `self.frame_h`, never the raw capture. All downstream pixel coordinates
        (detector bboxes, the height probe, the floor polygon) live in that space."""
        ...
        if self.intrinsics is not None:
            frame = cv2.undistort(frame, self.intrinsics[0], self.intrinsics[1])
        self.frame_h = frame                       # NEW
        ...
```

Exact edit points:

| # | file:line | edit |
|---|---|---|
| 1 | `perception.py:657-673` (`__init__`) | add `self.frame_h: np.ndarray \| None = None` |
| 2 | `perception.py:725-735` (`process`) | set `self.frame_h = frame` after the undistort block (covers both branches); add the contract paragraph to the docstring |
| 3 | `perception.py:704-722` (`semantic_context`) | docstring: polygon/floor_lab are full-res homography space; the offered frame must be `frame_h` |
| 4 | `run.py:294` | `runner.maybe_pass(t, perception.semantic_context(t), perception.frame_h, kind="full", force=True)` |
| 5 | `run.py:342` | `canvas = renderer.draw(perception.frame_h, scene, judg, cmd, executor.path)` |
| 6 | `calibrate.py:154` (`cmd_floor`, after `frame = _grab(cap)`) | `frame = _homography_space(frame, cfg)` before the click loops |
| 7 | `calibrate.py:289-290` (`cmd_check`) | `r.draw(perc.frame_h, …)` after `perc.process(frame, 0.0)` |
| 8 | `calibrate.py` (new helper, near `_grab`) | `_homography_space(frame, cfg)` (below) |
| 9 | `viz.py:55` (`Renderer.draw` docstring) | precondition: "frame must be `Perception.frame_h`; overlays are computed in homography space" |

```python
# calibrate.py — new helper so the rule is unit-testable and cannot drift from perception.py
def _homography_space(frame: np.ndarray, cfg: RoomConfig) -> np.ndarray:
    """Undistort iff camera.intrinsics is configured (the same rule Perception applies)."""
    if not cfg.camera.intrinsics:
        return frame
    intr = load_intrinsics(cfg.camera.intrinsics)          # imported from perception
    if intr is None:
        raise SystemExit(f"camera.intrinsics is set but {cfg.camera.intrinsics} is not readable")
    return cv2.undistort(frame, intr[0], intr[1])
```

**Why this and not "hoist undistort into `run.py`"** (the wave-1 suggestion):

* Hoisting means `process()` must stop undistorting; a caller that forgets `prepare()` is then
  silently wrong *exactly when intrinsics are set* (the failure mode we are fixing), or
  `process()` keeps undistorting and double-corrects. Both are worse contracts than "read the
  frame it used".
* No new precondition, no signature change, no double-undistort, and no behaviour change for the
  7 existing `process()` call sites in tests plus `calibrate.py` (intrinsics None ⇒
  `frame_h is frame`).
* Rejected alternatives: returning `(scene, frame)` (breaks every caller), or storing the frame in
  `Scene` (scene is serialised into the Jev state — a 6 MB payload and token cost).

**What this does not change:** the 0.5-scaled processing path (`small`, `homography_small`) stays
internal; `maybe_pass` still copies the frame once per pass (measured wave-1: 6.2 MB / 0.55 ms at
1920×1080, 2.8 MB at 1280×720 — not a problem); nothing in the Jev path changes.

### 1.4 Optional hardening (same PR, cheap)

* **Resolution guard** (wave-1 R1): in `process()`, warn once when
  `frame.shape[:2] != (cfg.camera.height, cfg.camera.width)` — the polygon, `H` and probe are
  full-config-resolution and silently wrong otherwise.
* **Intrinsics-resolution guard**: `calibrate.py cmd_intrinsics` already stores `"resolution"` in
  `calibration/camera.json`; return it from `load_intrinsics` and warn once in `Perception` when it
  differs from the capture size (distortion coefficients are resolution-specific).
* `Renderer.draw` already warps the grid with `Hinv @ G`; with `frame_h` in the same space the
  overlay is finally honest on a distorted lens. No code change.

### 1.5 Tests for the contract (hermetic, no intrinsics needed on disk)

* `test_frame_h_identity_without_intrinsics` — `perc.process(raw, 0.0)`; `perc.frame_h is raw`.
* `test_frame_h_is_the_undistorted_frame` — write a temp intrinsics JSON, monkeypatch
  `cv2.undistort` to return a sentinel, assert `perc.frame_h is sentinel` (and that the pose/floor
  code saw the sentinel).
* `test_worker_receives_frame_h` — recording `VisionModel` asserts `id(frame)` in `infer` equals
  `id(perc.frame_h)`; this is exactly the `run.py:294` call pattern.
* `test_calibrate_homography_space` — helper: identity when `intrinsics` empty; undistorted
  (monkeypatched) otherwise; `SystemExit` when the file is missing.

---

## 2. LocalVision / RemoteVision

### 2.1 Detector choice (verified 2026-09-29; report 04 + re-checked today)

| | **MM-GDINO-T** (default local) | GDINO-T (fallback) | YOLOE-26 | SAM 3.1 |
|---|---|---|---|---|
| repo | `openmmlab-community/mm_grounding_dino_tiny_o365v1_goldg_v3det` | `IDEA-Research/grounding-dino-tiny` | `jameslahm/yoloe` | `facebook/sam3.1` |
| license / gated | Apache-2.0 / no | Apache-2.0 / no | AGPL-3.0 | custom / **gated** |
| weights | `model.safetensors` = 692,015,412 B (~689 MB, HEAD-checked today) | 689 MB | 4–25 M params | 3.44 GB |
| quality | COCO zero-shot 50.4 (O365+GoldG) | 48.4 | 24.7–37.8 text-prompt mAP | best attribute handling |
| integration | `AutoModelForZeroShotObjectDetection`; `model_type=mm-grounding-dino`; `AutoProcessor` → `GroundingDinoProcessor` (verified in `processing_auto.py`) | same API | `set_classes()` | `Sam3Processor` |
| role in M2 | **default `local`** | documented fallback (`model_id` swap) | CPU fallback later | reference `remote` endpoint |

Rationale: Apache-2.0, ungated, one-call transformers API, best open-weights accuracy of the
shortlist; MM-GDINO-T beats GDINO-T (50.4 vs 48.4) on the same backbone. Colour words are
*not* reliably honoured by any open-vocab detector (attribute marginalization, report 04) — hence
the staged colour design in §2.5.

**CPU reality check (this host, measured):** 4 cores, 5.7 GB RAM (3.1 GB available), **no GPU**,
17.4 GB free disk; the venv has numpy/cv2/typesafe only — no torch. 689 MB fp32 weights + Swin-T
activations are feasible but slow; the local profile on this box is
`image_shortest_edge=400, image_longest_edge=666` (processor `size`, default 800/1333 verified in
`image_processing_grounding_dino.py`) with `torch.set_num_threads(4)`. Expect seconds to tens of
seconds per pass — measure with the smoke tool (§3.2) before accepting a live run. If that is too
slow, the same config switches to `kind=remote`; that answers proposal §15.1 by measurement
(this host cannot be the fast local host).

### 2.2 Module layout and factory growth

New module `vision.py`: adapters, HTTP contract, prompt helpers. Top-level imports are stdlib +
`cv2`/`numpy` only; `torch`/`transformers` are imported inside `_ensure()`, so the suite and
`semantics.py` never require them.

`semantics.py` `build_vision` (114-124) grows:

```python
def build_vision(cfg: SemanticsConfig, homography: Homography) -> VisionModel:
    """Adapter factory. fake = fixtures; local/remote = real models (M2)."""
    kind = cfg.model.kind
    if kind == "fake":
        ...unchanged...
    if kind in ("local", "remote"):
        from vision import make_vision      # lazy: optional deps stay out of the import graph
        return make_vision(cfg.model)
    raise NotImplementedError(f"unknown vision model kind {kind!r}")
```

```python
# vision.py
DEFAULT_LOCAL_MODEL = "openmmlab-community/mm_grounding_dino_tiny_o365v1_goldg_v3det"

def make_vision(cfg: VisionModelConfig) -> VisionModel:
    if cfg.kind == "local":
        return LocalVision(cfg)
    if cfg.kind == "remote":
        return RemoteVision(cfg)
    raise ValueError(f"not a real adapter: {cfg.kind!r}")
```

`VisionModel` (semantics.py:62-67) gains `def warmup(self) -> None: ...`; `FakeVision` gets a
no-op so the runner can call it unconditionally.

### 2.3 LocalVision

```python
class LocalVision:
    """Open-vocabulary detector on this box (transformers). Lazy load; one worker thread."""

    def __init__(self, cfg: VisionModelConfig):
        self.cfg = cfg
        self.model_id = cfg.model_id or DEFAULT_LOCAL_MODEL
        self.name = f"local:{self.model_id.rsplit('/', 1)[-1]}"
        self.load_s: float | None = None        # surfaced in stats()
        self.unknown_phrases = 0
        self._model = self._processor = None
        self._device = ""
        self._load_error: str | None = None     # cached: one download attempt per process
        self._lock = threading.Lock()

    # -- lifecycle: runs on the semantics worker thread, never on the control loop
    def warmup(self) -> None:
        self._ensure()

    def infer(self, frame: np.ndarray, *, labels=None) -> list[Detection]:
        self._ensure()
        labels = list(labels or self.cfg.labels)
        img, scale = self._prepare_image(frame)                     # optional input_scale
        phrases = build_prompt(labels, attributes=self.cfg.prompt_attributes)
        inputs = self._processor(
            images=img, text=[phrases],                             # list-of-lists: model-card form
            size={"shortest_edge": self.cfg.image_shortest_edge,
                  "longest_edge": self.cfg.image_longest_edge},
            return_tensors="pt").to(self._device)
        with self._torch.no_grad():
            outputs = self._model(**inputs)
        res = self._processor.post_process_grounded_object_detection(
            outputs, input_ids=inputs.get("input_ids"),
            threshold=self.cfg.box_threshold, text_threshold=self.cfg.text_threshold,
            target_sizes=[(img.shape[0], img.shape[1])])[0]
        return self._to_detections(res, labels, scale)

    def _ensure(self) -> None:
        """Load weights + optional warm-up exactly once; cache failures verbatim."""
```

Pinned details:

* **Lazy load + warm-up.** `build_vision()` runs on the main thread (`run.py:243`) — it must not
  import torch or load weights. `_ensure()` runs on the worker thread. `warmup()` runs one forward
  on a 64×64 zeros frame (the processor resizes it to the configured size — the warm-up
  deliberately costs one pass, it is what removes the cold-start penalty from the first real
  pass); `load_s` goes to stats.
* **Failure caching + cooldown.** `ImportError` → `"local vision needs torch+transformers
  (…); pip install -r requirements-vision.txt or set semantics.model.kind=remote"`; any load
  failure is cached in `_load_error` and re-raised without re-downloading. The existing worker
  error path + `failure_cooldown_s` (default 10 s; real configs: 30–60 s) then throttles retries
  and the run continues degraded — no new machinery.
* **Prompt/label strategy.** Verified in transformers `main`: `GroundingDinoProcessor` merges a
  candidate-label list itself — `[t.strip().lower() for t in text]`, joined as `". ".join(...) +
  "."` (`_merge_candidate_labels_text`) — and passes strings containing "." through unchanged. So
  both the model-card list-of-lists form and the pre-merged string are valid; canonical form is
  **lowercase, `" . "`-separated, trailing "."**. `build_prompt` returns the phrase list and
  `merge_prompt` the serialization used in logs:

  ```python
  def build_prompt(labels: list[str], *, attributes: bool = False) -> list[str]:
      out: list[str] = []
      for l in labels:
          p = " ".join(l.strip().lower().split())
          if not p:
              continue
          out.append(p)
          if attributes:                      # FG-OVD hygiene: repeat the class token alone
              cls = p.split()[-1]
              if cls != p:
                  out.append(cls)
      return out

  def merge_prompt(phrases: list[str]) -> str:
      return ". ".join(phrases) + "."
  ```

  Returned `text_labels` are decoded phrases; `canonical_label(phrase, labels)` maps back (exact
  after normalisation, token-subset fallback) and drops anything unmatched, counted in
  `unknown_phrases` — the store vocabulary stays exactly the configured one.
* **Thresholds:** `box_threshold=0.30`, `text_threshold=0.25` (processor defaults are 0.25/0.25);
  both config keys so a run can be tuned without code.
* **Boxes:** `post_process_grounded_object_detection` returns absolute pixel boxes in the image
  given to the processor; multiply by `1/input_scale` to keep `Detection`'s full-res contract.
* **Half-res / CPU profile.** The cost lever is the model input tensor ⇒ the processor `size`
  (`image_shortest_edge`/`image_longest_edge`). A frame-level downscale before the processor is
  pointless unless `size` is also reduced (the processor would upscale back). So:
  * M2 ships `input_scale` (default 1.0) for the *remote* bandwidth case and as the hook for the
    seam-level variant below;
  * the CPU profile is `image_shortest_edge=400, image_longest_edge=666`.
  * **Seam-level half-res** (wave-1 #4: offer a 0.5 frame + `Homography.for_scale` H) is specified
    but **deferred**: it saves 4.4 MB/pass of copy (≈0.4 ms — negligible per wave-1 measurement)
    and costs `semantic_context(t, scale)` + a `frame_scale` field on `SemanticContext` + probe
    scaling in `height_suspect` + `FakeVision.from_world(..., scale)` + a resize at `run.py:294`.
    Revisit only if profiling shows the copy or the full-res probe matters.
* **Device:** `auto` → cuda if available else cpu; `half` only on cuda; `eval()` always.

### 2.4 RemoteVision

One POST per pass, JSON body (base64 JPEG):

```jsonc
// POST {endpoint}                                Content-Type: application/json
// Authorization: Bearer <token>                  (only if the env var is set)
{
  "protocol": 1,
  "request_id": "p0007",
  "image": {"format": "jpeg", "b64": "<base64>", "width": 1280, "height": 720, "scale": 0.5},
  "labels": ["mat", "box", "bottle"],
  "thresholds": {"box": 0.30, "text": 0.25},
  "timeout_s": 8.0
}
// 200
{
  "model": "sam3.1",
  "ms": 412,
  "detections": [
    {"label": "mat", "bbox_px": [412, 288, 690, 470], "score": 0.87, "color": "blue"}
  ]
}
```

Rules:

* `bbox_px` is in the pixel space of the **image that was sent** (after `input_scale` /
  `jpeg_max_px`); RemoteVision multiplies by `1/scale` to return full-res `Detection`s. The server
  must not rescale — stated in the endpoint contract.
* **Secrets via env only** (proposal invariant, `docs/planning/README.md:33`): the token is read
  at call time from the env var named by `model.auth_env` (default `JEV_ROVER_VISION_TOKEN`),
  never from config, never logged, never in artifacts. Endpoint from `model.endpoint`, overridden
  by `JEV_ROVER_VISION_ENDPOINT`; both empty → clear error naming both.
* Errors: non-2xx → `RemoteVisionError("HTTP 502: …")`; timeout → `TimeoutError`; malformed JSON →
  `RemoteVisionError`; malformed entries skipped and counted; detections capped at
  `MAX_DETECTIONS` (64). All of these land in `SemanticsWorker._run`'s `except` → `PassResult(error)`
  → runner cooldown. No retries in the adapter (the runner owns the cooldown).
* Transport injection for hermetic tests: `RemoteVision(cfg, transport=None)`; the default
  transport is `urllib.request` (stdlib — the repo has 3 dependencies and no `requests`); tests
  pass `(url, body, headers, timeout) -> (status, bytes)`.
* JPEG: `cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, cfg.jpeg_quality])[1].tobytes()`
  → `base64.b64encode`; `jpeg_max_px` (0=off) downscales first (bandwidth lever). Encode happens
  on the worker thread, never on the loop.

### 2.5 Colour handling — staged, honest

* **Stage 1 (M2 default): prompt class nouns only** (`["mat","box","bottle","charger"]`). The
  query keeps attributes and `rank_candidates` already matches `"blue mat"` → `"mat"` (Jaccard
  0.5 ≥ `min_label_score` 0.34). Colour words in the prompt are a promise open-vocab detectors
  cannot keep (attribute marginalization; report 04 §"Colour-attribute mitigations").
* **Stage 2 (opt-in `color_verify: true`):** crop the bbox and score colour hypotheses with a small
  CLIP classifier (`color_model_id`, e.g. `openai/clip-vit-base-patch32`) or the deterministic
  Lab nearest-colour fallback (report 04 mitigation #6, auditable); final confidence =
  `det_score × P(colour)`; label becomes `"blue mat"` only above threshold. `prompt_attributes:
  true` additionally emits the FG-OVD-style repeated phrase (`"blue mat . mat ."`).
* The interface is a one-method protocol so tests can stub it:
  `ColorVerifier.verify(frame, bbox, colours) -> (colour, p)`. M2 ships the deterministic
  fallback; CLIP stays optional.

### 2.6 Config growth (M2 keys)

`VisionModelConfig` additions — all defaulted, so every existing config parses (the semantics
section is strict, so typos still raise; `config.py:242-245`, `_section` 362-394):

```python
@dataclass
class VisionModelConfig:
    kind: str = "fake"                    # fake | local | remote
    labels: list[str] = field(default_factory=list)
    endpoint: str = ""
    timeout_s: float = 10.0
    fixtures: list[FixtureEntry] = field(default_factory=list)
    # --- M2 ---
    model_id: str = ""                    # local: HF id; "" -> DEFAULT_LOCAL_MODEL
    device: str = "auto"                  # auto | cpu | cuda | cuda:N
    half: bool = False                    # fp16, cuda only
    image_shortest_edge: int = 800        # processor size (CPU profile: 400)
    image_longest_edge: int = 1333        # (CPU profile: 666)
    box_threshold: float = 0.30
    text_threshold: float = 0.25
    warmup: bool = True
    input_scale: float = 1.0              # optional frame downscale; boxes rescaled back
    prompt_attributes: bool = False
    color_verify: bool = False
    color_model_id: str = "openai/clip-vit-base-patch32"
    jpeg_quality: int = 85                # remote
    jpeg_max_px: int = 0                  # remote: longest edge of the JPEG; 0 = off
    auth_env: str = "JEV_ROVER_VISION_TOKEN"
```

Validation, appended to `_validate_semantics` (`config.py:296-344`):

* `kind in ("local","remote")` ⇒ `labels` non-empty (a real model with no prompt can only return
  nothing);
* thresholds in (0,1); `image_shortest_edge ≥ 64`; `image_longest_edge ≥ image_shortest_edge`;
  `input_scale` in (0,1]; `jpeg_quality` in [1,100]; `device` in `auto|cpu|cuda[:N]`;
* `kind == "remote"` ⇒ endpoint non-empty **or** `JEV_ROVER_VISION_ENDPOINT` present — checked at
  `build_vision()` time (warn), not at config load, so hermetic configs load in CI.

Example blocks (documented in `config/room.example.json` + README):

```json
"semantics": {
  "enabled": true,
  "model": {
    "kind": "local",
    "model_id": "openmmlab-community/mm_grounding_dino_tiny_o365v1_goldg_v3det",
    "labels": ["mat", "box", "ball", "bottle", "charger", "chair"],
    "device": "auto",
    "image_shortest_edge": 800, "image_longest_edge": 1333,
    "box_threshold": 0.30, "text_threshold": 0.25
  },
  "project": {"point": "bbox_bottom_center", "probe_px": 6,
              "point_by_label": {"mat": "centroid"}}
}
```

```json
"model": {
  "kind": "remote",
  "endpoint": "http://192.168.1.20:8080/detect",
  "labels": ["mat", "box", "bottle"],
  "timeout_s": 8.0, "auth_env": "JEV_ROVER_VISION_TOKEN",
  "jpeg_quality": 85, "jpeg_max_px": 960
}
```

`config/room.synthetic.json` keeps `kind: "fake"` — the default suite must stay offline.

### 2.7 Runner interplay: warm-up, cooldown, budgets, manual trigger

* **Prewarm (new, ~15 lines).** With a real model the load is 5–60 s; without prewarm the first
  real pass's result is dropped as stale (`max_age_s` is measured against `t_submit`). Add
  `SemanticsRunner.prewarm(t)`: offers `PassRequest(kind="warmup", frame=zeros(32,32,3), ctx=None)`;
  the worker calls `vision.warmup()` and pushes `PassResult(kind="warmup")`; `poll()` consumes it
  without merging and records `warmup_ms`. `run.py` calls it once when the runner is first ready.
  Edit points: `semantics.py` `PassRequest` (`ctx: SemanticContext | None`), `SemanticsWorker._run`
  (kind branch before `infer`), `SemanticsRunner` (new method), `run.py` trigger block.
* **Stale/budget guidance.** `max_age_s ≥ 2× expected pass latency` (local CPU: 60–120 s);
  `max_passes_per_min=4` unchanged; single in-flight already prevents pile-up. `close()` joins
  with `timeout=1.0` (`semantics.py:477-479`) while a real pass runs seconds — parametrize
  (`close(timeout_s=…)`, config `shutdown_join_s`, default 5.0).
* **Cooldown counter.** `semantics.py:511-512` returns `False` silently; add
  `self.skipped["cooldown"] += 1` (acceptance #4's "cooldown counters visible" is otherwise unmet).
* **Manual trigger.** Keep the mission-start forced pass as M2's manual trigger and make it
  explicit: new `--semantics-pass` flag (fires one forced pass once floor context exists; implies
  runner creation); `--find LABEL` implies it (today's behaviour, verified). `--semantics-once` is
  parsed but never read (`run.py:191-192`): either wire it to mean "no re-triggers after the manual
  pass" (real in M3) or remove it from the docs' commands — do not leave it looking functional.
  `--semantics` choices grow to `off|fake|local|remote`, overriding `cfg.semantics.model.kind`.
* **Stats.** `stats()` grows `warmup_ms`, `model_load_s` (`getattr(vision, "load_s", None)`),
  and `skipped["cooldown"]`.

### 2.8 Dependencies

`requirements-vision.txt` (new, optional; `setup.sh` untouched, README documents it):

```
torch>=2.3
transformers>=4.55
pillow
```

* `transformers>=4.55`: first release containing `mm-grounding-dino` (model dir merged
  2025-08-01; 4.55.0 uploaded 2025-08-05 — verified via PyPI JSON). The 5.x line also ships it
  (verified on `main`); pin `<5` only if 5.x breaks the two calls.
* CPU install: `pip install torch --index-url https://download.pytorch.org/whl/cpu` (~200 MB
  wheel; ~2 GB disk total). RAM ~1.5 GB resident + activations — tight on this box: use the
  400/666 profile and measure.

---

## 3. Acceptance & tests

### 3.1 Corrected acceptance commands (verified today)

The plan's command cannot run:

```
$ run.py --config config/room.synthetic.json --source synthetic \
      --semantics fake --semantics-once --find "blue mat"
--mission goto needs --waypoint NAME          # SystemExit, run.py:47-49 (mission defaults to goto)
```

It also needs `--no-jev` to be hermetic: `Tactician.__init__` raises without
`TYPESAFE_API_KEY`/`JEV_API_KEY` (`tactics.py:229-231`). README.md:131-133 already has the working
form; the plan (`m1-plan.md:352`), `docs/planning/README.md:39-40` and `README.md:277` must be
aligned to:

```bash
.venv/bin/python run.py --config config/room.synthetic.json --source synthetic \
    --mission patrol --seconds 20 --no-jev --semantics fake --find "blue mat"
```

Verified output (this box, today):

```
[semantics] enabled: model=fake-vision-v0 fixtures=3 (M1: one pass at mission start, no triggers)
[find] 'blue mat' -> blue mat at (3.00,1.20) m conf=0.92 object=obj_0001; approach (3.13,1.53) standoff=0.35 m
"semantics": {"passes": 1, "errors": 0, "skipped": {"interval": 0, "budget": 0, "inflight": 0, "no_context": 0}, "median_ms": 0.6, ...}
```

`--semantics-once` dropped: it is inert. (The approach point varies with the rover pose at
resolution time; README's older capture shows `(3.12,1.53)`.)

**M2 acceptance (proposed):**

1. Suite green: 67 tests in `tests/` + 11 prototype = 78 collected today; new hermetic tests added;
   no network, no live calls, no torch import in the default suite.
2. `--semantics off` unchanged (byte-identical apart from the two `null` keys).
3. Corrected fake command above prints the `[find]` line; `summary.semantics.passes ≥ 1`.
4. Frame-space: with `camera.intrinsics` set, worker + renderer receive `Perception.frame_h`
   (§1.5 tests; CLI smoke optional).
5. **Local live:** `tools/smoke_semantics.py --config config/room.json --source camera --kind local`
   prints ≥1 detection with floor coordinates and exits 0; then
   `run.py --config config/room.json --source camera --mission patrol --no-jev --semantics local --find "mat" --seconds 60`
   shows `semantics.model="local:mm_grounding_dino_tiny_o365v1_goldg_v3det"`, `passes ≥ 1`,
   `model_load_s`/`warmup_ms`, and the `[find]` line.
6. **Remote live:** same with `--kind remote` + `JEV_ROVER_VISION_TOKEN`; a dead endpoint must show
   `errors ≥ 1` + `skipped.cooldown ≥ 1` and must not change control-loop cadence beyond the budget
   (existing loop test).
7. Counters (budget/cooldown/errors/median_ms/model) visible in `runs/summary_*.json` under
   `"semantics"`.
8. **Decision — routing.** The plan's M2 acceptance says "routed to"; this note scopes M2 to
   adapter + manual trigger (per the commission). If routing is in scope, the minimal version is:
   on resolve, set `scene.goal` to the approach point through the existing `GoalManager`/`Executor`
   path, gated by a new `semantics.drive_to_destination: false` default — a separate workstream,
   not covered here. Also still open: `--find` currently latches on the first attempt even on a
   miss (`run.py:298-311`); proposal §7's "retry once with a fresh targeted pass" is the natural
   M2 follow-up.

### 3.2 Smoke tool `tools/smoke_semantics.py`

One real pass, main thread (blocking is fine), prints detections + floor coordinates. Mirrors
`tools/smoke_jev.py` in style; no rover moves.

```python
"""One real semantic pass: frame -> model -> floor coordinates. No rover moves.

    set -a && . ./.env && set +a
    .venv/bin/python tools/smoke_semantics.py --config config/room.json \
        --source camera --kind local --labels "mat,box,bottle"
"""
# argparse: --config, --source {camera,synthetic,IMAGE}, --kind {fake,local,remote},
#           --labels "a,b,c", --seconds-warm 2.0, --save-frame PATH
# flow:
#   cfg = RoomConfig.load(path); cfg.semantics.model.kind = args.kind
#   if args.labels: cfg.semantics.model.labels = [s.strip() for s in args.labels.split(",")]
#   perc = Perception(cfg); source = Camera(cfg.camera) or SyntheticRoom(cfg)
#   warm >= 6 frames through perc.process (floor sample + grid), t = i / 15
#   vision = build_vision(cfg.semantics, perc.homography)
#   t0 = time.time(); dets = vision.infer(perc.frame_h, labels=cfg.semantics.model.labels); dt
#   print: model name, prompt string, load_s, latency ms, detection count
#   for d in dets: print(label, bbox_px, score)
#   ctx = perc.semantic_context(t)
#   objs, rejected = project_detections(dets, perc.frame_h, ctx, cfg.semantics, t)
#   for o in objs: print(label, (x, y) m, height_suspect, confidence)
#   print(rejected, "rejected;", merged JSON line for piping)
# exit 0 if >=1 detection projected, 1 if the model failed, 2 if zero detections
```

Why direct `infer` (not the runner): the tool's job is to debug the adapter in isolation — prompt,
thresholds, latency, box space. The runner path is covered by acceptance 5/6 and the opt-in test.

### 3.3 e2e test strategy

* **Default suite stays synthetic + `FakeVision` (hermetic):**
  * `tests/test_vision_prompt.py` — `build_prompt`/`merge_prompt`/`canonical_label`
    (normalisation, attribute repeats, unmatched phrases), box rescale round-trip.
  * `tests/test_vision_remote.py` — stub transport: request JSON shape (protocol, decodable JPEG,
    labels, thresholds), scale mapping, error/timeout mapping, malformed entries, 64-cap.
  * `tests/test_frame_space.py` — §1.5.
  * `tests/test_semantics_worker.py` additions — `skipped["cooldown"]` increments; a warmup result
    is not merged and `warmup_ms` is recorded.
* **Opt-in real model (never CI):** `tests/test_vision_local_real.py`, marked
  `@pytest.mark.realvision`, skipped unless `JEV_ROVER_REALVISION=1` and torch/transformers are
  importable (and the weights are cached, or `JEV_ROVER_REALVISION_DOWNLOAD=1`): one pass over a
  `synthetic.py`-rendered frame (its furniture boxes are the objects) with labels
  `["box", "table"]`; assert ≥1 detection, boxes inside the frame, and `project_detections`
  yields ≥1 object inside the polygon; plus one `SemanticsRunner` pass with the real adapter.
  Runtime: tens of seconds to minutes on CPU — documented in the test docstring.
* **`pytest.ini` (new):** `testpaths = tests` (stops `docs/planning/prototype/test_april_sem.py`
  from riding along — wave-1 report 06), `markers = realvision: needs local model weights`,
  `addopts = -m "not realvision"`.
* **CLI-level:** the corrected command (§3.1) is the e2e; optionally a subprocess test in a tmp
  cwd asserting `summary["semantics"]["passes"] == 1` (pattern from report 06).

### 3.4 Evidence log (measured/verified today)

* Plan acceptance command → exact `SystemExit("--mission goto needs --waypoint NAME")`; corrected
  command runs and prints the `[find]` line + `semantics.passes=1`.
* Undistort offset probe: 46–88 px at corners (K=800/640/360, k1=−0.15), 0 px at centre.
* HF API: openmmlab repo ungated, Apache-2.0, `model.safetensors` 692,015,412 B; `mm-grounding-dino`
  → `GroundingDinoProcessor` in `processing_auto.py`; processor lowercases + `". "`-joins phrases;
  image processor `size` default `{shortest_edge: 800, longest_edge: 1333}`; `outputs.input_ids`
  exists for post-processing.
* Host: 4 cores, 5.7 GB RAM (3.1 GB free), no GPU, 17.4 GB disk, venv without torch.
* Suite: `pytest tests/` = 67 passed; default collection 78 (11 from the prototype dir).

### 3.5 Decisions requested

1. Frame-space fix shape: expose `Perception.frame_h` (chosen) — confirm.
2. Default local model + CPU profile (400/666) — confirm; or default `remote` on this host.
3. Colour: stage 1 class-only prompts by default (stage 2 opt-in) — confirm.
4. Manual trigger: `--semantics-pass` flag; `--semantics-once` wire or drop — decide.
5. Routing/driving in M2 or follow-on (acceptance item 8) — decide.

---

## Appendix — edit-point checklist

```
perception.py   __init__ + frame_h; process() sets frame_h + contract docstring;
                semantic_context docstring; optional resolution/intrinsics warnings
run.py          offer perception.frame_h; draw perception.frame_h; --semantics choices += local/remote;
                --semantics-pass; runner creation condition; prewarm call; startup print
calibrate.py    _homography_space helper + cmd_floor use; cmd_check draws perc.frame_h
viz.py          Renderer.draw docstring precondition
semantics.py    build_vision real kinds; VisionModel.warmup; PassRequest.ctx optional + kind="warmup";
                worker warmup branch; SemanticsRunner.prewarm; skipped["cooldown"]; close(timeout);
                stats() additions
config.py       VisionModelConfig M2 keys; _validate_semantics checks
vision.py       NEW: LocalVision, RemoteVision, build_prompt/merge_prompt/canonical_label, make_vision
config/*.json   room.example.json M2 block; room.synthetic.json stays fake
requirements-vision.txt  NEW (torch, transformers>=4.55, pillow)
pytest.ini      NEW (testpaths, realvision marker)
tools/smoke_semantics.py NEW
tests/          test_frame_space.py, test_vision_prompt.py, test_vision_remote.py,
                test_vision_local_real.py (opt-in), worker additions
docs            m1-plan.md:352 / docs/planning/README.md:39-40 / README.md:277 acceptance commands;
                README M2 section; docs/planning/README.md status row + test count (78)
```
