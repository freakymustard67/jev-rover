# I2 — probe hardening pack: `height_suspect` (jev-rover semantics layer)

**Scope:** prototype + measurement of a hardened height-probe rule for the M1 semantics layer.
Master `9c33ec0`; PR #1 head `bec1d91` (`review/m1-semantics-audit`). All work in scratch
(`/home/freakymustard/.hermes/cache/scratch/i2/`); the real repo and `/tmp/opencode` were never written to
(`git status --short` clean at `9c33ec0`). Nothing pushed. Owner decision D4 is *pending* — this is a
decision pack, not a merge.

Raw evidence: `runs/20260928-2342/i2-evidence/` (scripts, logs, `out-*.json` per rule, diffs, scenario dump).

---

## 0. TL;DR

1. **The mat FP is exactly "the probe reads a floor cover, and the rule calls any non-floor colour elevated".**
   One pixel at `bbox_bottom_center + probe_px` (semantics.py:191-192) is compared to a pure floor colour
   (semantics.py:197). On the blue mat the reading is LAB `[101,144,76]` vs floor `[168,125,123]` → distance
   **84.02** ≥ tol 26 → `True`, for an object that is provably flat on the floor.
2. **Nothing consumes the flag** (only merge-overwrite + JSON) — the rule change is zero control-loop risk.
3. **6 px is not a metric distance**: on a modelled oblique overhead rig it is **1.69 cm** near the image
   bottom vs **13.89 cm** at the top (8.2×; 2× again if applied to a `proc_scale=0.5` frame/H pair). The
   shipped configs are top-down (200 px/m) where it is exactly 3.00 cm.
4. **Measured, on 15 labelled scenarios** (13 floor-truth, 1 elevated, 1 unverifiable):

   | rule | FP | FN |
   |---|---|---|
   | master `9c33ec0` (single pixel) | **10** | 0 |
   | PR tip `bec1d91` (7×3 median, patch 7) | **10** | 0 |
   | prototype core (median + metric + shadow split + blue-mat cover) | **5** | 0 |
   | prototype + second cover registered (grey mat) | **3** | 0 |

   Flake on the floor control (200 reps, sensor noise): master **24/200** (all 24 = injected dead/hot
   pixel), PR/prototype **0/200**.
5. **Recommendation (D4):** adopt *median + metric offset* now (M1.5: no clean-frame behaviour change, kills
   the pixel-defect flakes, fixes the oblique-band ambiguity); keep *shadow split + cover registration* for M2
   behind config defaults + the empty-room background, because they change the documented M1 rule and add one
   measured FN mode (neutral grey elevated surfaces). Details in §8.

---

## 1. What "false-positive on a mat" means, exactly

`height_suspect` (semantics.py:176-203):

* probe pixel: `px = round((x0+x1)/2)`, `py = round(y1) + project.probe_px` (semantics.py:191-192);
  `probe_px = 6` (config.py:131) — i.e. a **single pixel 6 px below the bbox base**.
* reading compared against `ctx.floor_lab` — the *median LAB of the whole floor polygon*
  (perception.py:257-262) — with `floor_lab_tolerance` (26 in `config/room.synthetic.json`, 22 in
  `config/room.example.json`; used at semantics.py:197).
* so the rule is: *"the 1 px below the base is floor-coloured"*. Any other colour → `True`
  ("projection untrustworthy"), plus guards for off-frame / outside-polygon / unobserved grid cell
  (semantics.py:193-203).

The mat case (reproduced from `w2a/demo_2.py`, frame drawn with the real homography):
object `(3.0, 1.2, 0.3×0.3 m)`; mat = world rect x 2.6–3.4, y 0.8–1.6, BGR `(180,90,40)`.
Probe pixel `(600, 516)`; reading `[101,144,76]`; floor `[168,125,123]`; distance **84.02**
(dL 67.0, dchroma 50.7) → `True`. The mat detection itself (acceptance label `blue mat`, anchor overridden
to `centroid` via `point_by_label`, but the *probe* still uses the bbox base) reads the same: probe
`(600, 546)`, distance **84.02** → `True`.

The mat is a **floor cover**: its top surface is coplanar with the floor (rug 2–5 mm), so the projection
`H·(x,y)` is fine — the flag is honest only as *"not verified as floor-coloured"*, not as *"elevated"*
(the review's own wording). This is the documented C1 rule (plan `m1-plan.md:14-38`), deliberately kept
intact by PR #1 (w3b check 5: "mat rule intact") — D4 is whether to change it.

Also measured in the same probe family (all with the shipped rule → `True`, all on the floor plane):
deep shadow (`|d|` 40.0/45.0, chroma-preserved darkening), a dark neutral card (`|d|` 103.16), a 3 px
tight bbox with a speckled object edge (`|d|` 126.29). Master FP list (13 floor-truth cases):
`mat_thing, mat_itself, shadow_deep, shadow_vdeep, shadow_contact, shadow_contact_narrow,
dark_card_wide, occluder_narrow, dark_mat_thing, tight_bbox_speckle` — FP = 10, FN = 0.

## 2. Consumers: what `True` gates downstream

`grep -rn height_suspect` over the product tree (excluding `__pycache__`, docs, tests):

| site | role |
|---|---|
| `semantics.py:232` | set per detection inside `project_detections` |
| `semantics.py:285` | **overwritten** on every merge pass (`prev.height_suspect = d.height_suspect`) |
| `semantics.py:342-352` | serialised into `<room>_latest.json` (`asdict` of the map) |
| `scene.py:278` | `SemanticObject.height_suspect: bool = False` (schema field) |
| `README.md:141`, `semantics.py:24` | documentation of the rule |

**No reader.** The control loop (`control.py`), mission/tactics/reflex (`run.py`, `tactics.py`,
`mission.py`), destination resolution (`rank_candidates`/`resolve_destination`, semantics.py) and the
per-pass diff (`scene.py:287-291` `SemanticDiff` carries only appeared/moved/vanished ids) never consult
the flag; nothing rejects an object on it; it is not fed to Jev. Tests that assert it:
`tests/test_semantics_projection.py:71-72,80,88`, `tests/test_semantics_integration.py:46`,
`tests/test_semantics_merge.py:87-90`. So today the flag is informational until M3 — a rule change here
cannot move the rover, and it is fully reversible.

## 3. Resolution dependency: what does `probe_px=6` mean in metres?

Frame-space check first (it corrects the task brief): the probe runs on the **raw camera frame** —
`run.py:294` passes `frame` (full-res) with `perception.semantic_context(t)` whose `polygon_px` is
full-res (perception.py:704-721). `proc_scale=0.5` (perception.py:657-660) is used only for the floor
model / grid (perception.py:744). So today `probe_px=6` is 6 **full-res** px; a `for_scale(0.5)`
frame/H pair would double every number below.

Measured with the shipped top-down config (200 px/m, `room.synthetic.json`) and a modelled oblique
overhead rig (pinhole f=900 px, 1280×720, camera 2.0 m up, pitch 35.5°, exact 4-corner floor homography;
`scripts/oblique_scale.py`, log `logs/oblique-scale.log`):

| rig | row | world y | 6 px full-res | 6 px on a half-res (proc_scale=0.5) frame/H |
|---|---|---|---|---|
| top-down 200 px/m | any | — | **3.00 cm** | **6.00 cm** |
| oblique | 700 | 0.54 m | **1.69 cm** | 3.38 cm |
| oblique | 360 | 2.00 m | **3.95 cm** | 7.90 cm |
| oblique | 240 | 2.98 m | **5.97 cm** | 11.94 cm |
| oblique | 120 | 4.53 m | **10.06 cm** | 20.12 cm |
| oblique | 60 | 5.72 m | **13.89 cm** | 27.79 cm |

Near/far metres-per-pixel ratio **8.2×**. The same 6 px is ~2 cm of floor at the near edge and ~14 cm
far up the frame (≈ 40 % of the rover's 0.36 m footprint) — the probed band does not mean a fixed thing.
Fix: `project.probe_m = 0.03` walks the offset **on the floor plane via H** (matches the shipped 3.00 cm
exactly on top-down configs, constant everywhere else). Test
`test_metric_probe_offset_is_constant_in_metres_on_an_oblique_rig` asserts it to < 1 px quantisation.

## 4. Method

Scratch clones (all under `/home/freakymustard/.hermes/cache/scratch/i2/`):

```
git clone -q https://github.com/freakymustard67/jev-rover.git clone-master                 # 9c33ec0
git clone -q --branch review/m1-semantics-audit https://github.com/freakymustard67/jev-rover.git clone-pr   # bec1d91
cp -r clone-master clone-proto
```

One rule-agnostic scenario dump (`scripts/make_scenarios.py`, run in `clone-master`; frames rendered by
the real `SyntheticRoom`, rover parked at (0.6, 0.6), noise=0 so the table measures the RULE) and one
evaluator (`scripts/eval_rules.py`) run once per clone against that dump, calling each clone's real
`semantics.height_suspect` + `project_detections` (knobs injected only where the clone's `ProjectConfig`
has them). Frames clean ⇒ identical input for all rules; sensor-noise sensitivity is measured separately
by re-adding the room's noise model (half-res ±3 BGR, upsampled) + dead/hot-pixel injection at the probe
pixel, 200 seeded reps per scenario, identical across clones.

15 scenarios, ground truth (probe below the base; "trustworthy" = floor-plane contact, any elevation or
occlusion untrustworthy):

| scenario | class | truth | what it is |
|---|---|---|---|
| floor_only | floor | F | control: object on bare floor |
| mat_thing | floor | F | object resting on a flat blue mat |
| mat_itself | floor | F | detection of the mat (centroid anchor) |
| mat_edge_floor | floor | F | mat behind the base, probe on bare floor (control) |
| shadow_mild / _deep / _vdeep | floor | F | floor shadow L−20 / L−40 / L−70, chroma preserved |
| shadow_contact / _narrow | floor | F | deep contact shadow, wide / 20 px-wide |
| dark_card_wide | floor | F | flat dark neutral card in front of the base |
| occluder_narrow | floor | F | 16 px-wide dark object in front of the base |
| dark_mat_thing | floor | F | object on a flat neutral-grey mat (L 105) |
| tight_bbox_speckle | floor | F | detector bbox 3 px tight; speckled object edge on the probe row |
| on_table | elevated | T | object on the synthetic room's table region (genuine suspect) |
| edge_polygon | unverifiable | T (conservative) | probe falls outside the floor polygon |

## 5. Results — per-rule confusion tables and raw numbers

Full table + per-case raw values: `logs/table.md`, `logs/eval-{master,pr,proto,proto-core,proto-self}.log`,
`out-*.json` (probe pixel, single-pixel LAB, 7×3 patch LAB, floor LAB, `|d|`, dL, dchroma, m/6px, e2e world
coords + flag).

| scenario | truth | master | PR | proto-core | proto(both covers) | patch LAB | `|d|` | dL | dC |
|---|---|---|---|---|---|---|---|---|---|
| floor_only | F | F | F | F | F | 168,125,123 | 0.0 | 0.0 | 0.0 |
| mat_thing | F | **T** | **T** | F | F | 101,144,76 | 84.02 | 67.0 | 50.7 |
| mat_itself | F | **T** | **T** | F | F | 101,144,76 | 84.02 | 67.0 | 50.7 |
| mat_edge_floor | F | F | F | F | F | 168,125,123 | 0.0 | 0.0 | 0.0 |
| shadow_mild | F | F | F | F | F | 148,125,123 | 20.0 | 20.0 | 0.0 |
| shadow_deep | F | **T** | **T** | F | F | 128,125,123 | 40.0 | 40.0 | 0.0 |
| shadow_vdeep | F | **T** | **T** | **T** | F | 98,125,123 | 70.0 | 70.0 | 0.0 |
| shadow_contact | F | **T** | **T** | F | F | 123,126,123 | 45.01 | 45.0 | 1.0 |
| shadow_contact_narrow | F | **T** | **T** | **T** | F | 123,126,123 | 45.01 | 45.0 | 1.0 |
| dark_card_wide | F | **T** | **T** | **T** | **T** | 65,128,128 | 103.16 | 103.0 | 5.83 |
| occluder_narrow | F | **T** | **T** | **T** | **T** | 65,128,128 | 103.16 | 103.0 | 5.83 |
| dark_mat_thing | F | **T** | **T** | F | F | 113,128,128 | 55.31 | 55.0 | 5.83 |
| tight_bbox_speckle | F | **T** | **T** | **T** | **T** | 42,131,129 | 126.29 | 126.0 | 8.49 |
| on_table | T | T | T | T | T | 95,106,146 | 78.86 | 73.0 | 29.83 |
| edge_polygon | T | T | T | T | T | (probe outside polygon) | 0.0 | 0.0 | 0.0 |
| **FP / FN** | | **10 / 0** | **10 / 0** | **5 / 0** | **3 / 0** | | | | |

Flake measurement (200 seeded reps each; "noise-only" = sensor noise alone, "injected" = every 10th/25th
frame has a dead/hot pixel exactly on the probe pixel):

| scenario | master | PR | proto |
|---|---|---|---|
| floor_only | 24/200 (noise-only 0/176, injected **24/24**) | 0/200 | 0/200 |
| mat_thing | 200/200 | 200/200 | **0/200** |
| shadow_contact | 200/200 | 200/200 | **0/200** |
| tight_bbox_speckle | 200/200 | 200/200 | 200/200 |

Read-outs:
* **Single-pixel probe = 1 pixel defect away from a flip** (24/24 injected). Pure sensor noise never
  flipped it (0/176) — the "noise floor" risk is *pixel defects*, not gaussian noise.
* **Patch 7 works as advertised** (0/24 injected) but changes nothing on clean frames (FP identical 10/10).
* **3 px tight bbox + speckled object edge defeats the median** (7×3 patch majority is object, all rules
  `True`) — FP 200/200 everywhere; that one needs a bigger patch or a base-band check, not a median.

## 6. Prototype (hardened rule) — implementation, tests, suite

`clone-proto` (= master + the diff below). Rule: `probe_surface()` classifies the surface under the base,
`height_suspect()` = `surface not in (floor, shadow, cover, self)`. Five gates, all in `semantics.py`:

1. **7×3 median patch** (`PROBE_PATCH_PX`, `_lab_patch`) — adopted from PR patch 7, verbatim semantics.
2. **Metric offset** (`probe_point`): `project.probe_m` (default 0.03 m) walked on the floor plane via
   `ctx.homography`; `probe_m=0` falls back to the legacy `probe_px` pixel step.
3. **Shadow split**: accept when `0 < dL ≤ shadow_max_dl` (60) **and** `dchroma ≤ shadow_chroma_max` (10)
   **and** the same-row neighbours at ±`ring_dx` (11 px) are at least `ring_min_drop_ratio` (0.5) as dark —
   i.e. the darkening is *lightness-only* and *row-coherent*, which is what a shadow is.
   Measured separation: floor shadows dC 0.0–1.0; the table (genuinely non-floor) dC 29.83 → stays suspect.
4. **Registered covers** (`cover_lab`, `cover_tol` 26): floor covers declared per room (mat LABs); a probe
   matching one is `cover` → not suspect. Measured: mat `[101,144,76]` exact match; mat FP 84.02 → fixed.
5. **optional `self_match`** (default **off**): probe vs the detection's own region colour. Kept off because
   of a measured trap — with `I2_SELF_MATCH=1` the prototype produces its only FN: `on_table` is accepted
   (`own` region = table when the frame does not render the object, i.e. fixture-only synthetic rooms).
   Off by default, zero cost.

Thresholds set from the measured samples above (dL/dt chroma/speckle separations); config knobs documented
in the diff. `probe_surface` returns the reason (`floor|shadow|cover|self|notfloor|no-floor-sample|
off-frame|outside-polygon|unobserved`) as a *function*, so no schema change is needed today — see §7(d).

Tests (5 new, appended to `tests/test_semantics_projection.py`; text in Appendix B):
metric offset == legacy pixel on the shipped room + dead-pixel survival; shadow accepted / narrow shadow
and dark neutral patch flagged; registered cover accepted, unregistered flagged; metric offset constant on
an oblique rig (and legacy not); probe_surface reason strings.

Suite results (`PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python -m pytest -q -p no:cacheprovider`):

| clone | result |
|---|---|
| clone-master `9c33ec0` | **78 passed** in 14.17 s |
| clone-pr `bec1d91` | **80 passed** in 15.36 s |
| clone-proto (master + I2) | **83 passed** in 15.97 s |

## 7. Residual FPs/FNs and open design questions

(a) **Residual FPs (prototype, 3 of 13 floor-truth)**: `dark_card_wide`, `occluder_narrow`
(wide/narrow dark *objects* right under the base) and `tight_bbox_speckle` (majority of the probe patch is
object edge). All three are *single-frame colour* limits: an object under the base and a shadow over the
base are indistinguishable without history or depth. Fixes: M2 empty-room background image
(`FloorConfig.background`, perception.py:249,264-271, already plumbed into `classify`), the ray-walk
variant (semantics.py:184), or a temporal check (a cover persists; a detection's shadow moves with it).

(b) **New FN risk introduced by the shadow split (measured, `scripts/grey_surface_probe.py`)**:
a *wide neutral-grey surface* under the base (painted at the floor's own chroma, L−30 / L−50) is accepted as
`shadow` (height_suspect `False`), while master flags it `True`. If that surface is a genuinely *elevated*
grey sideboard/step, the prototype is wrong where master was accidentally right. Condition:
`dL ≤ 60 and dC ≤ 10 and row-coherent`. Mitigation: the same background-image/ToF check as (a); a grey
tabletop is exactly the case the M2 empty-room reference is for.

(c) **Cover registration is manual and its tolerance is wide.** A room's mats must be added to
`project.cover_lab` (LAB medians); measured side effect: with a mid-grey mat registered
(`[105,128,128]`, tol 26), the deep narrow shadow `[123,126,123]` (dist **18.8**) is accepted via the cover
list rather than the shadow branch — harmless here, but it means `cover_tol` should be kept tight
(≤ ~15) when the room contains grey covers, and covers should be re-registered if lighting drifts.

(d) **bool vs extra field (design question).** Options:
   * **bool only, widened reference set** (recommended for the merge): `height_suspect: bool` keeps its
     meaning ("projection not verified against the floor plane"), the change is confined to
     `semantics.py` + `config.py`, no schema/consumer churn; the *reason* stays in `probe_surface()`
     (callable, test-visible).
   * **additive `probe_surface: str` field** on `SemanticObject` (scene.py:278) + store JSON: better
     debuggability and it lets M3 distinguish "not-suspect because floor" from "not-suspect because cover",
     at the cost of touching the schema tests (`tests/test_semantics_schema.py`) and any consumer that
     does strict key validation. Add it when M3 actually consumes the flag, not now.

## 8. Recommendation for D4 (owner decision)

**Stage 1 — M1.5 (adopt now, low risk):**
* 7×3 median patch (PR patch 7, already reviewed) — floor-control flake 24/200 → 0/200; zero clean-frame
  behaviour change (FP 10/10 identical); kills the single-dead-pixel failure mode.
* metric `probe_m = 0.03 m` via H — identical output on both shipped (top-down) configs, removes the
  8.2× near/far ambiguity on an oblique rig (1.69–13.89 cm → 3.00 cm everywhere).
* the 5 new tests (Appendix B) as the regression net; no consumer changes (nothing reads the flag yet).

**Stage 2 — M2 (rule change, needs an explicit owner call):**
* shadow split + registered covers, defaults as prototyped, **only** if the owner accepts the semantics
  change: *flat covers and shadows are floor-plane contact* (the documented M1 rule says the opposite —
  w3b check 5 recorded "object on a non-floor-coloured mat → `True`" as intact; this pack would flip it).
* measured effect: floor-truth FP 10 → 5 (blue mat registered) → 3 (all covers registered); FN 0 → 0 with
  `self_match` off; residual FPs are object-vs-shadow ambiguities (§7a) and the new grey-surface FN (§7b).
* prerequisites: register the room's covers in config; land the M2 empty-room background image (or another
  depth check) before the shadow branch is load-bearing, because §7(b) is only separable with history/depth.

**Stage 3 — before M3 consumes the flag:** decide §7(d) (bool vs `probe_surface` field) and re-run this
scenario pack against real camera frames (the numbers here are synthetic-room measurements).

---

## Appendix A — unified diff vs master (implementation)

```diff
diff --git a/config.py b/config.py
index 3952911..948dcf3 100644
--- a/config.py
+++ b/config.py
@@ -130,6 +130,27 @@ class ProjectConfig:
     point: str = "bbox_bottom_center"  # bbox_bottom_center | bbox_center | centroid
     probe_px: int = 6
     point_by_label: dict[str, str] = field(default_factory=dict)
+    # --- I2 probe hardening (all default to the hardened behaviour) ---------
+    # Metric probe offset in metres (walked along the floor via H).  0.0 keeps
+    # the legacy probe_px offset (which is 3 cm at the shipped 200 px/m but
+    # 1.7-13.9 cm on an oblique rig -- see runs/.../i2-probe-hardening.md).
+    probe_m: float = 0.03
+    # Probe patch (w, h) in px; the per-channel median over it is the reading.
+    probe_patch_px: tuple[int, int] = (7, 3)
+    # Shadow split: accept as "floor, in shadow" when the lightness drop is at
+    # most shadow_max_dl and the chroma shift at most shadow_chroma_max, and the
+    # same row beside the patch is at least ring_min_drop_ratio as dark.
+    shadow_max_dl: float = 60.0
+    shadow_chroma_max: float = 10.0
+    ring_dx: int = 11
+    ring_min_drop_ratio: float = 0.5
+    # Registered floor covers (mats/rugs flat on the floor), LAB triples.
+    cover_lab: list[list[float]] = field(default_factory=list)
+    cover_tol: float = 26.0
+    # Match the probe against the detection's own region colour (a flat cover of
+    # the object's material).  Off by default: unsafe when the frame does not
+    # actually render the object (fixture-only synthetic rooms) -- see report.
+    self_match: bool = False
 
 
 @dataclass
diff --git a/semantics.py b/semantics.py
index 456843c..ae12d13 100644
--- a/semantics.py
+++ b/semantics.py
@@ -47,6 +47,7 @@ from scene import Destination, SemanticDiff, SemanticMap, SemanticObject
 VISION_KINDS = ("fake", "local", "remote")
 ANCHOR_POINTS = ("bbox_bottom_center", "bbox_center", "centroid")
 MAX_DETECTIONS = 64
+PROBE_PATCH_PX = (7, 3)                # probe median patch (w, h) below the bbox base
 EVENT_LOG = "events.jsonl"
 
 
@@ -173,6 +174,99 @@ def _lab_at(frame: np.ndarray, x: int, y: int) -> np.ndarray:
     return cv2.cvtColor(frame[y, x].reshape(1, 1, 3), cv2.COLOR_BGR2LAB)[0, 0].astype(float)
 
 
+def _lab_patch(frame: np.ndarray, x: int, y: int,
+               pw: int = PROBE_PATCH_PX[0], ph: int = PROBE_PATCH_PX[1]) -> np.ndarray:
+    """Per-channel median LAB over a small patch centred on (x, y)."""
+    h, w = frame.shape[:2]
+    x0, x1 = max(0, x - pw // 2), min(w, x + pw // 2 + 1)
+    y0, y1 = max(0, y - ph // 2), min(h, y + ph // 2 + 1)
+    patch = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2LAB).reshape(-1, 3)
+    return np.median(patch.astype(float), axis=0)
+
+
+def probe_point(frame: np.ndarray, bbox: tuple[float, float, float, float],
+                ctx: SemanticContext, project: ProjectConfig) -> tuple[int, int]:
+    """Pixel the height probe reads: ``probe_m`` walked across the floor via H.
+
+    The legacy rule steps ``probe_px`` pixels down the IMAGE, whose metric
+    length near vs far differs by up to ~8x on an oblique camera.  With
+    ``probe_m > 0`` the step is a fixed distance on the floor plane, so the
+    probed band means the same thing at the top and the bottom of the frame.
+    """
+    x0, y0, x1, y1 = bbox
+    px = int(round((x0 + x1) / 2.0))
+    probe_m = float(getattr(project, "probe_m", 0.0) or 0.0)
+    if probe_m > 0.0:
+        w0 = ctx.homography.img_to_world(np.array([[px, y1]], float))[0]
+        w1 = ctx.homography.img_to_world(np.array([[px, y1 + 1.0]], float))[0]
+        d = w1 - w0
+        n = float(np.linalg.norm(d))
+        if n > 0:
+            target = w0 + d / n * probe_m
+            qx, qy = ctx.homography.world_to_img(target.reshape(1, 2))[0]
+            return int(round(qx)), int(round(qy))
+    return px, int(round(y1)) + int(project.probe_px)
+
+
+def _matches_cover(lab: np.ndarray, project: ProjectConfig) -> bool:
+    tol = float(getattr(project, "cover_tol", 0.0))
+    for c in getattr(project, "cover_lab", ()) or ():
+        if float(np.linalg.norm(lab - np.asarray(c, float))) <= tol:
+            return True
+    return False
+
+
+def probe_surface(frame: np.ndarray, bbox: tuple[float, float, float, float],
+                  wx: float, wy: float, ctx: SemanticContext,
+                  project: ProjectConfig) -> str:
+    """Classify the surface under the bbox base.
+
+    Returns one of ``floor | shadow | cover | self | notfloor`` plus the guard
+    reasons ``no-floor-sample | off-frame | outside-polygon | unobserved``.
+    ``height_suspect`` is True for everything except the first four.
+    """
+    if ctx.floor_lab is None:
+        return "no-floor-sample"
+    px, py = probe_point(frame, bbox, ctx, project)
+    h, w = frame.shape[:2]
+    if px < 0 or px >= w or py >= h or py < 0:
+        return "off-frame"
+    if not _inside_polygon(ctx.polygon_px, px, py):
+        return "outside-polygon"
+    lab = _lab_patch(frame, px, py)
+    delta = lab - ctx.floor_lab
+    if float(np.linalg.norm(delta)) < ctx.floor_lab_tolerance:
+        surface = "floor"
+    else:
+        dl = float(ctx.floor_lab[0] - lab[0])               # >0: darker than floor
+        dchroma = float(np.linalg.norm(delta[1:3]))
+        surface = "notfloor"
+        shadow = (0.0 < dl <= float(project.shadow_max_dl)
+                  and dchroma <= float(project.shadow_chroma_max))
+        if shadow:
+            # A shadow darkens the whole row, not just the probe patch: require
+            # the same-row neighbours to be (most of) as dark.
+            drops = []
+            for dx in (-int(project.ring_dx), int(project.ring_dx)):
+                drops.append(float(ctx.floor_lab[0] - _lab_patch(frame, px + dx, py, 3, 3)[0]))
+            if min(drops) >= float(project.ring_min_drop_ratio) * dl:
+                surface = "shadow"
+        if surface == "notfloor" and _matches_cover(lab, project):
+            surface = "cover"
+        if surface == "notfloor" and bool(getattr(project, "self_match", False)):
+            # the probe reads the detection's own material: a flat cover of the
+            # same colour continues below the bbox base.
+            own = _lab_patch(frame, int(round((bbox[0] + bbox[2]) / 2.0)),
+                             int(round((bbox[1] + bbox[3]) / 2.0)), 5, 5)
+            if float(np.linalg.norm(lab - own)) <= ctx.floor_lab_tolerance:
+                surface = "self"
+    if surface in ("floor", "shadow", "cover", "self"):
+        cell = ctx.cell_of(wx, wy)
+        if cell is None or not bool(ctx.observed()[cell[1], cell[0]]):
+            return "unobserved"
+    return surface
+
+
 def height_suspect(frame: np.ndarray, bbox: tuple[float, float, float, float],
                    wx: float, wy: float, ctx: SemanticContext,
                    project: ProjectConfig) -> bool:
@@ -180,27 +274,16 @@ def height_suspect(frame: np.ndarray, bbox: tuple[float, float, float, float],
 
     Probe the pixels just BELOW the base of the bbox: floor-coloured means the
     object rests on the floor; anything else (furniture, another object) means
-    it may be standing on something, which biases the floor projection. A
+    it may be standing on something, which biases the floor projection.  A
     ray-walk variant is proven for the oblique-camera case if this ever misses;
     the probe is the M1 rule.
+
+    Hardened (I2): 7x3 median patch (noise/pixel defects), metric ``probe_m``
+    offset via H, shadow split (dark but chroma-preserving and row-coherent),
+    registered floor covers (mats).  See ``probe_surface`` for the reason.
     """
-    if ctx.floor_lab is None:
-        return True
-    h, w = frame.shape[:2]
-    x0, y0, x1, y1 = bbox
-    px = int(round((x0 + x1) / 2.0))
-    py = int(round(y1)) + int(project.probe_px)
-    if px < 0 or px >= w or py >= h or py < 0:
-        return True
-    if not _inside_polygon(ctx.polygon_px, px, py):
-        return True
-    if float(np.linalg.norm(_lab_at(frame, px, py) - ctx.floor_lab)) >= ctx.floor_lab_tolerance:
-        return True
-    cell = ctx.cell_of(wx, wy)
-    if cell is None:
-        return True
-    ix, iy = cell
-    return not bool(ctx.observed()[iy, ix])
+    return probe_surface(frame, bbox, wx, wy, ctx, project) not in (
+        "floor", "shadow", "cover", "self")
 
 
 def project_detections(dets: list[Detection], frame: np.ndarray, ctx: SemanticContext,
```

## Appendix B — new tests (diff vs master, `tests/test_semantics_projection.py`)

```diff
diff --git a/tests/test_semantics_projection.py b/tests/test_semantics_projection.py
index d60188c..118c4b5 100644
--- a/tests/test_semantics_projection.py
+++ b/tests/test_semantics_projection.py
@@ -2,13 +2,15 @@
 import math
 from dataclasses import replace
 
+import cv2
 import numpy as np
 import pytest
 
 from config import SemanticsConfig
-from perception import Perception
+from perception import Homography, Perception
 from semantics import (Detection, FakeVision, WorldFixture, anchor_point,
-                       height_suspect, project_detections, resolve_anchor_kind)
+                       height_suspect, probe_point, probe_surface,
+                       project_detections, resolve_anchor_kind)
 from synthetic import SyntheticRoom
 
 WARM = 6
@@ -107,3 +109,122 @@ def test_anchor_helpers():
     assert resolve_anchor_kind(cfg.project, "mat") == "bbox_bottom_center"
     cfg.project.point_by_label["mat"] = "centroid"
     assert resolve_anchor_kind(cfg.project, "MAT") == "centroid"
+
+
+# ------------------------------------------------- I2 probe hardening tests
+
+def _bbox_and_probe(perc, synth_cfg, label, x, y, w, h):
+    bb = FakeVision.from_world([WorldFixture(label, x, y, w, h)], perc.homography).fixtures[0].bbox_px
+    bbox = (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
+    return bbox
+
+
+def test_probe_median_and_metric_offset_match_legacy_on_the_flat_room(synth_cfg):
+    """On the shipped top-down room, probe_m=0.03 m must land on the same pixel
+    as probe_px=6 (200 px/m), and a single dead pixel must not flip the flag."""
+    perc, syn, frame = _perception(synth_cfg)
+    ctx = perc.semantic_context(1.0)
+    proj = synth_cfg.semantics.project
+    bbox = _bbox_and_probe(perc, synth_cfg, "thing", 3.0, 1.2, 0.4, 0.4)
+    px, py = probe_point(frame, bbox, ctx, proj)
+    legacy = (int(round((bbox[0] + bbox[2]) / 2.0)), int(round(bbox[3])) + int(proj.probe_px))
+    assert (px, py) == legacy
+    assert height_suspect(frame, bbox, 3.0, 1.2, ctx, proj) is False
+    noisy = frame.copy()
+    noisy[py, px] = (0, 0, 0)                        # one dead pixel under the base
+    assert height_suspect(noisy, bbox, 3.0, 1.2, ctx, proj) is False
+    dark = frame.copy()
+    dark[py - 1:py + 2, px - 3:px + 4] = 0           # a real dark band still flags
+    assert height_suspect(dark, bbox, 3.0, 1.2, ctx, proj) is True
+
+
+def _darken_floor(frame, x0, x1, y0, y1, dl):
+    """Darken a rectangle by dl in LAB L, chroma preserved (a shadow)."""
+    lab = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2LAB).astype(np.float32)
+    lab[..., 0] = np.clip(lab[..., 0] - dl, 0, 255)
+    frame[y0:y1, x0:x1] = cv2.cvtColor(lab.astype(np.uint8), cv2.COLOR_LAB2BGR)
+
+
+def test_shadow_is_floor_but_a_flat_dark_object_is_not(synth_cfg):
+    perc, syn, frame = _perception(synth_cfg)
+    ctx = perc.semantic_context(1.0)
+    proj = synth_cfg.semantics.project
+    bbox = _bbox_and_probe(perc, synth_cfg, "thing", 5.0, 1.0, 0.3, 0.3)
+    px, py = probe_point(frame, bbox, ctx, proj)
+
+    shadow = frame.copy()
+    _darken_floor(shadow, px - 40, px + 40, py - 25, py + 25, 40.0)   # wide shadow band
+    assert height_suspect(shadow, bbox, 5.0, 1.0, ctx, proj) is False
+
+    narrow = frame.copy()
+    _darken_floor(narrow, px - 8, px + 8, py - 25, py + 25, 40.0)     # 16 px wide
+    assert height_suspect(narrow, bbox, 5.0, 1.0, ctx, proj) is True
+
+    card = frame.copy()
+    card[py - 1:py + 2, px - 3:px + 4] = (60, 60, 60)                 # dark neutral patch
+    assert height_suspect(card, bbox, 5.0, 1.0, ctx, proj) is True
+
+
+def test_registered_cover_is_floor_unregistered_is_not(synth_cfg):
+    perc, syn, frame = _perception(synth_cfg)
+    ctx = perc.semantic_context(1.0)
+    proj = synth_cfg.semantics.project
+    bbox = _bbox_and_probe(perc, synth_cfg, "thing", 3.0, 1.2, 0.3, 0.3)
+    mat = frame.copy()                                # a blue mat under the base
+    p0 = perc.homography.world_to_img([[2.6, 1.6]])[0]
+    p1 = perc.homography.world_to_img([[3.4, 0.8]])[0]
+    cv2.rectangle(mat, (int(p0[0]), int(p0[1])), (int(p1[0]), int(p1[1])), (180, 90, 40), -1)
+    assert height_suspect(mat, bbox, 3.0, 1.2, ctx, proj) is True
+    registered = replace(proj, cover_lab=[[101.0, 144.0, 76.0]])
+    assert height_suspect(mat, bbox, 3.0, 1.2, ctx, registered) is False
+    assert probe_surface(mat, bbox, 3.0, 1.2, ctx, registered) == "cover"
+
+
+def _oblique_homography():
+    """Pinhole fixed overhead camera (pitch 35.5 deg, 2.0 m) -> floor homography."""
+    th = math.radians(35.5)
+    c, s = math.cos(th), math.sin(th)
+    rot = np.array([[1, 0, 0], [0, -s, -c], [0, c, -s]], float)   # rows: right, down, forward
+    cam = np.array([3.2, -0.8, 2.0])
+    k = np.array([[900.0, 0, 640], [0, 900, 360], [0, 0, 1.0]])
+
+    def project(x, y):
+        pc = rot @ (np.array([float(x), float(y), 0.0]) - cam)
+        pc = pc / pc[2]
+        return [float(v) for v in (k @ pc)[:2]]
+
+    img = [project(0, 0), project(6.4, 0), project(6.4, 3.6), project(0, 3.6)]
+    return Homography(img, [[0, 0], [6.4, 0], [6.4, 3.6], [0, 3.6]])
+
+
+def test_metric_probe_offset_is_constant_in_metres_on_an_oblique_rig():
+    from types import SimpleNamespace
+    from config import ProjectConfig
+    h = _oblique_homography()
+    ctx = SimpleNamespace(homography=h)
+    proj = ProjectConfig(probe_m=0.03, probe_px=6)
+    legacy = []
+    for row in (120, 360, 600):
+        base = h.img_to_world([[640, row]])[0]
+        mpp = float(np.linalg.norm(h.img_to_world([[640, row + 1]])[0] - base))
+        bbox = (590.0, float(row - 100), 690.0, float(row))
+        px, py = probe_point(None, bbox, ctx, proj)
+        got = float(np.linalg.norm(h.img_to_world([[px, py]])[0] - base))
+        assert abs(got - 0.03) < mpp * 1.2, (row, got, mpp)
+        legacy.append(float(np.linalg.norm(h.img_to_world([[640, row + 6]])[0] - base)))
+    assert legacy[0] > 3 * legacy[-1], legacy        # the legacy 6 px is not metric
+
+
+def test_probe_surface_reasons(synth_cfg):
+    perc, syn, frame = _perception(synth_cfg)
+    ctx = perc.semantic_context(1.0)
+    proj = synth_cfg.semantics.project
+    bbox = _bbox_and_probe(perc, synth_cfg, "thing", 3.0, 1.2, 0.4, 0.4)
+    assert probe_surface(frame, bbox, 3.0, 1.2, ctx, proj) == "floor"
+    assert probe_surface(frame, bbox, 3.0, 1.2, replace(ctx, floor_lab=None),
+                         proj) == "no-floor-sample"
+    stale = replace(ctx, grid_t=10.0, grid_last_seen=np.zeros_like(ctx.grid_last_seen))
+    assert probe_surface(frame, bbox, 3.0, 1.2, stale, proj) == "unobserved"
+    h, w = frame.shape[:2]
+    off = (600.0, 700.0, 640.0, float(h - 1))
+    assert probe_surface(frame, off, 3.0, 0.2, ctx, proj) == "off-frame"
```

## Appendix C — evidence index and exact commands

All commands run with `PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python`
(no writes to the real repo; scratch root `/home/freakymustard/.hermes/cache/scratch/i2/`).
`runs/20260928-2342/i2-evidence/` holds: `scripts/` (make_scenarios, eval_rules, oblique_scale, surfaces,
grey_surface_probe, aggregate), `dump/` (scenarios.json + frames.npz, 124 KB), `logs/` (all transcripts
below, `out-*.json` per rule, `table.md`, both diffs).

```bash
# clones
git clone -q https://github.com/freakymustard67/jev-rover.git clone-master                    # 9c33ec0
git clone -q --branch review/m1-semantics-audit https://github.com/freakymustard67/jev-rover.git clone-pr   # bec1d91
cp -r clone-master clone-proto

# scenario dump (once, in clone-master; frames are rule-independent)
cd clone-master && .../python ../make_scenarios.py . ../dump                       # log: make_scenarios.log

# per-rule evaluation (each clone's real semantics.height_suspect + project_detections)
cd clone-master && .../python ../eval_rules.py . ../dump ../out-master.json 200    # log: eval-master.log
cd clone-pr     && .../python ../eval_rules.py . ../dump ../out-pr.json 200        # log: eval-pr.log
cd clone-proto  && .../python ../eval_rules.py . ../dump ../out-proto.json 200     # log: eval-proto.log        (covers: blue+grey mat)
cd clone-proto  && I2_COVERS=mat .../python ../eval_rules.py . ../dump ../out-proto-core.json 200   # eval-proto-core.log
cd clone-proto  && I2_SELF_MATCH=1 .../python ../eval_rules.py . ../dump ../out-proto-self.json 200 # eval-proto-self.log

# metric-scale numbers
cd clone-master && .../python ../oblique_scale.py .                                # log: oblique-scale.log

# prototype surface reasons + grey-surface FN check
cd clone-proto  && .../python ../surfaces.py . ../dump                             # log: surfaces.log
cd clone-master && .../python ../grey_surface_probe.py . ../dump
cd clone-proto  && .../python ../grey_surface_probe.py . ../dump                   # log: grey-surface-proto.log

# suites
cd clone-master && .../python -m pytest -q -p no:cacheprovider                     # 78 passed   (pytest-master.log)
cd clone-pr     && .../python -m pytest -q -p no:cacheprovider                     # 80 passed   (pytest-pr.log)
cd clone-proto  && .../python -m pytest -q -p no:cacheprovider                     # 83 passed   (pytest-proto.log)

# diffs
cd clone-proto && git diff -- semantics.py config.py > proto-code.diff
cd clone-proto && git diff -- tests/test_semantics_projection.py > proto-tests.diff
```

Raw per-case values: `logs/table.md` (truth, all four rules, probe px, patch LAB, `|d|`, dL, dchroma) and
`out-*.json` (adds per-case `e2e` = world (x, y) + flag through `project_detections`, plus the flake
counters). Cross-references: REVIEW-m1-consolidated.md §2.2 (probe rule), §4 patch 7, §6 D4;
runs/20260929-wave2/w2a/demo_2.py (mat FP demo); runs/20260929-wave2/w2b/patches.md (patch 7 rationale);
runs/20260928-1930/w3b-adversarial.md §6 (median window 11/21 flip threshold, out-of-frame/polygon guards,
"mat rule intact").

*Prepared 2026-09-29 by the Hermes Agent research campaign (I2 subagent), read-only on the repo,
scratch clones only.*
