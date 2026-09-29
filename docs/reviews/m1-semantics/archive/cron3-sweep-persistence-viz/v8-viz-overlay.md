# v8 — Semantics overlay for viz.py (prototype)

Date: 2026-09-29 · Status: **DONE — all checks passed**
Clone (patched): `/home/freakymustard/.hermes/cache/scratch/w5/v8/work` @ `9c33ec0`
Baseline (pristine): `/home/freakymustard/.hermes/cache/scratch/w5/v8/pristine` @ `9c33ec0`
Evidence PNGs + scripts: `runs/20260928-2220/v8-evidence/`

## Headline

A minimal, off-by-default semantics overlay was added to `viz.py` (only file changed,
72+/2−). It draws each `SemanticObject` (dot + id/label/confidence), the resolved
`Destination` (yellow diamond), and the approach point + rover→approach line on
**both** the camera view and the minimap, reusing the renderer's existing
`_world_to_px` / `_mm_pt` mappings and `semantics.approach_point`. Flag off renders
byte-identical to the pristine clone (max|diff| = 0); flag on changes exactly the
expected regions; empty map / `semantics=None` / `grid=None` never crash; all 67
repo tests still pass.

## What was drawn, and with which math (no second transform)

| element | camera view | minimap |
|---|---|---|
| each `SemanticObject` | `_world_to_px(Hinv, o.x, o.y)` dot (4 px, outline ring when `height_suspect`) + `"{o.id} {o.label} {conf:.2f}"` | `Renderer._mm_pt(o.x, o.y, …)` dot + `"{o.label} {conf:.2f}"` |
| `Destination` | `_world_to_px`, `MARKER_DIAMOND` yellow + `"dest {label} {conf:.2f}"` | `_mm_pt`, diamond yellow |
| approach point + line | `semantics.approach_point(dest, (scene.pose.x, scene.pose.y), cfg.destination.standoff_m)` → `_world_to_px`; line from rover `_world_to_px(scene.pose.x, scene.pose.y)` | same via `_mm_pt` |

* Data source: `scene.semantics` (`SemanticMap | None`), which `run.py` already
  assigns each perception tick (`scene.semantics = runner.snapshot(t)`, run.py:297).
  No new plumbing was needed in run.py.
* Approach point is the same function the `--find` path uses (run.py:301) — the
  rendered line ends exactly where a destination drive would aim.
* Colours are new module constants (unused elsewhere): `SEM_OBJ_COLOR (255,0,255)`,
  `SEM_DEST_COLOR (0,255,255)`, `SEM_APPROACH_COLOR (0,200,255)`.
* Safety: `sem is None → return`; empty `objects`/`destination` draw nothing;
  ring for `height_suspect`; no text truncation (labels are short).
* Flag: `Renderer(..., show_semantics=False)` — matches the existing pattern
  (`show=args.show`, `video_path=args.video` are the only other viz switches; they
  are CLI→constructor kwargs, not config fields, so a constructor kwarg is the
  in-style choice).

## Evidence

### (1) Deterministic demo — real semantic map, two renders

`demo_semantics_overlay.py` (copied into `v8-evidence/`): warms a real
`Perception` on `config/room.synthetic.json` (8 frames, seed 0, rover pose
(3.20, 1.80, +0), `pose_src=tag`), then one **real** `SemanticsRunner` pass with the
config's `FakeVision` fixtures, resolves `"go to the green ball"`, sets the
destination, and renders ON and OFF canvases (no display windows).

```console
$ cd pristine && PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python \
    /home/freakymustard/.hermes/cache/scratch/w5/v8/demo_semantics_overlay.py --out .../out/pristine
clone HEAD=9c33ec0 show_semantics flag=no
scene t=0.61 pose=(3.20,1.80,+0) pose_src=tag
semantics map: passes=1 model=fake-vision-v0 age=0.0s objects=3
  obj_0001 'blue mat' (3.00,1.20) m conf=0.92 height_suspect=False -> view px=(600,480)
  obj_0002 'red box' (4.40,0.85) m conf=0.85 height_suspect=False -> view px=(880,550)
  obj_0003 'green ball' (1.50,0.72) m conf=0.80 height_suspect=False -> view px=(300,575)
destination: 'green ball' (1.50,0.72) conf=0.80 object=obj_0003; approach=(1.80,0.91) m standoff=0.35
canvas_off sha=efa8f49376ba7cdb shape=(720, 1710, 3)
pristine clone (no flag): wrote canvas_off.png only
```

```console
$ cd work && PYTHONDONTWRITEBYTECODE=1 .../demo_semantics_overlay.py --out .../out/work \
    --baseline .../out/pristine/canvas_off.png
clone HEAD=9c33ec0 show_semantics flag=yes
... (identical scene/semantics lines) ...
canvas_off sha=efa8f49376ba7cdb shape=(720, 1710, 3)
canvas_on  sha=2be9eff895d3ef62 difference vs off below
[A] changed px on-vs-off = 6836 (band 300..60000)
[B] 'blue mat' window (560,440)-(840,520) changed px = 1040
[B] 'red box' window (840,510)-(1120,590) changed px = 1006
[B] 'green ball' window (260,535)-(540,615) changed px = 2244
[C] camera-view region (1280x720) changed px = 5147
[D] minimap region (256x144 at x=1280) changed px = 1689
[E] destination window changed px = 2244; approach window changed px = 530
[F] exact-colour px object magenta: on=220 off=0
[F] exact-colour px dest yellow: on=252 off=0
[F] exact-colour px approach orange: on=200 off=0
[G] empty map: off==baseline True, on==baseline True
[H] semantics=None: on/off renders ok, both == baseline: True
[I] grid=None + flag on: ok, canvas (720, 1710, 3)
[J] baseline=.../pristine/canvas_off.png identical=True max|diff|=0
    (baseline sha=efa8f49376ba7cdb, off sha=efa8f49376ba7cdb)
[K] crops: zoom_obj1_on/off.png, zoom_minimap_on.png
RESULT: all checks PASS
```

Note the canvas sha printed by the demo (16 hex chars) is a shortened digest; the
file sha256 values below are the full PNGs.

### (2) Assertions with numbers

* **Flag off == pristine clone, pixel-identical**: `identical=True, max|diff|=0`
  ([J]); full-PNG sha256 `c53098b2…` for `out/pristine/canvas_off.png` ==
  `out/work/canvas_off.png` (byte-identical files).
* **Flag on differs, in the expected regions**: 6836 changed px total; per-object
  windows 1040/1006/2244; destination 2244; approach 530; camera view 5147;
  minimap 1689 ([A]–[E]).
* **Camera view and minimap both exercised** ([C]/[D] + colour attribution of
  `canvas_on.png`):
  ```console
  $ python probe_colors.py out/work/canvas_on.png 1280 144
  object magenta: total=220 | camera-view=148 x[296,996] y[476,579] | minimap-area=72 x[1338,1496] y[93,117]
  dest yellow:    total=252 | camera-view=156 x[291,309] y[566,584] | minimap-area=96 x[1334,1346] y[109,121]
  approach orange: total=200 | camera-view=141 x[351,367] y[530,546] | minimap-area=59 x[1347,1355] y[103,111]
  # same probe on canvas_off.png: all three ABSENT
  ```
* **Empty map**: flag on draws nothing → output identical to flag off and to the
  baseline ([G]). **`semantics=None`**: both flags render, both identical to
  baseline ([H]). **`grid=None` + flag on**: renders fine, minimap path skipped
  before the semantics call ([I]).
* **Determinism / owner re-run**: running the demo twice produced byte-identical
  PNGs (`a857eb02…` twice for canvas_on; `c53098b2…` three times for canvas_off,
  including the pristine clone).
* **Repo tests**: `pytest tests -q -p no:cacheprovider` → `67 passed in 13.30s`
  (patched clone; no test changes).

### (3) `git diff -U1` of the clone (102 lines; default-context diff is 130)

```diff
diff --git a/viz.py b/viz.py
index 5050ff8..0f29f69 100644
--- a/viz.py
+++ b/viz.py
@@ -10,2 +10,6 @@ Watch the overlay on the real camera during calibration: if the belief map
 does not line up with the floor, nothing downstream can be trusted.
+
+Pass ``show_semantics=True`` to add the M1 semantic layer on top: objects
+(id, label, confidence), the resolved destination and its approach point.
+Off by default; nothing on the canvas changes while it is off.
 """
@@ -21,2 +25,3 @@ from link import Cmd
 from scene import Scene, wrap_deg
+from semantics import approach_point
 
@@ -43,3 +48,3 @@ class Renderer:
     def __init__(self, cfg: RoomConfig, grid, show: bool = False, video_path: str | None = None,
-                 fps: float = 15.0, panel_w: int = 430):
+                 fps: float = 15.0, panel_w: int = 430, show_semantics: bool = False):
         self.cfg = cfg
@@ -47,2 +52,3 @@ class Renderer:
         self.show = show
+        self.show_semantics = show_semantics       # M1 semantic layer, off by default
         self.panel_w = panel_w
@@ -104,3 +110,7 @@ class Renderer:
 
-        # 5. minimap + panel
+        # 5. semantic layer (opt-in: canvas is untouched while the flag is off)
+        if self.show_semantics:
+            self._semantics_view(view, Hin, scene)
+
+        # 6. minimap + panel
         mm = self._minimap(scene, path, grid_rgb)
@@ -141,2 +151,4 @@ class Renderer:
         cv2.arrowedLine(img, pr, tip, (255, 255, 255), 2, cv2.LINE_AA, tipLength=0.5)
+        if self.show_semantics:
+            self._semantics_minimap(img, scene, cell, scale)
         cv2.rectangle(img, (0, 0), (img.shape[1] - 1, img.shape[0] - 1), (90, 90, 90), 1)
@@ -148,2 +160,55 @@ class Renderer:
 
+    # ------------------------------------------------ semantics overlay (M1)
+
+    def _semantics_view(self, view: np.ndarray, Hinv: np.ndarray, scene: Scene) -> None:
+        """Semantic objects, destination and approach point on the camera view.
+
+        World->pixel goes through the same ``_world_to_px`` every other marker
+        uses, so an object lands exactly on the floor point the map believes in.
+        """
+        sem = scene.semantics
+        if sem is None:
+            return
+        for o in sem.objects:
+            p = _world_to_px(Hinv, o.x, o.y)
+            cv2.circle(view, p, 4, SEM_OBJ_COLOR, -1)
+            if o.height_suspect:                   # the projection may be offset
+                cv2.circle(view, p, 8, SEM_OBJ_COLOR, 1)
+            cv2.putText(view, f"{o.id} {o.label} {o.confidence:.2f}", (p[0] + 6, p[1] - 8),
+                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, SEM_OBJ_COLOR, 1, cv2.LINE_AA)
+        dest = sem.destination
+        if dest is None:
+            return
+        pd = _world_to_px(Hinv, dest.x, dest.y)
+        cv2.drawMarker(view, pd, SEM_DEST_COLOR, cv2.MARKER_DIAMOND, 16, 2)
+        cv2.putText(view, f"dest {dest.label} {dest.confidence:.2f}", (pd[0] + 8, pd[1] + 16),
+                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, SEM_DEST_COLOR, 1, cv2.LINE_AA)
+        pa = _world_to_px(Hinv, *approach_point(
+            dest, (scene.pose.x, scene.pose.y), self.cfg.destination.standoff_m))
+        pr = _world_to_px(Hinv, scene.pose.x, scene.pose.y)
+        cv2.line(view, pr, pa, SEM_APPROACH_COLOR, 1, cv2.LINE_AA)
+        cv2.drawMarker(view, pa, SEM_APPROACH_COLOR, cv2.MARKER_TILTED_CROSS, 14, 2)
+
+    def _semantics_minimap(self, img: np.ndarray, scene: Scene, cell: float, scale: int) -> None:
+        """The same semantics on the minimap, via the same ``_mm_pt`` mapping."""
+        sem = scene.semantics
+        if sem is None:
+            return
+        for o in sem.objects:
+            p = self._mm_pt(o.x, o.y, img.shape, cell, scale)
+            cv2.circle(img, p, 3, SEM_OBJ_COLOR, -1)
+            cv2.putText(img, f"{o.label} {o.confidence:.2f}", (p[0] + 4, p[1] - 4),
+                        cv2.FONT_HERSHEY_SIMPLEX, 0.32, SEM_OBJ_COLOR, 1, cv2.LINE_AA)
+        dest = sem.destination
+        if dest is None:
+            return
+        pd = self._mm_pt(dest.x, dest.y, img.shape, cell, scale)
+        cv2.drawMarker(img, pd, SEM_DEST_COLOR, cv2.MARKER_DIAMOND, 10, 2)
+        pa = self._mm_pt(*approach_point(
+            dest, (scene.pose.x, scene.pose.y), self.cfg.destination.standoff_m),
+            img.shape, cell, scale)
+        pr = self._mm_pt(scene.pose.x, scene.pose.y, img.shape, cell, scale)
+        cv2.line(img, pr, pa, SEM_APPROACH_COLOR, 1, cv2.LINE_AA)
+        cv2.circle(img, pa, 3, SEM_APPROACH_COLOR, 2)
+
     def _panel_lines(self, scene: Scene, judg: dict, cmd: Cmd):
@@ -208,2 +273,7 @@ class Renderer:
 
+#: Semantics overlay colours (not used anywhere else on the canvas).
+SEM_OBJ_COLOR = (255, 0, 255)        # semantic object dot + id/label/confidence
+SEM_DEST_COLOR = (0, 255, 255)       # resolved destination diamond
+SEM_APPROACH_COLOR = (0, 200, 255)   # approach point + line from the rover
+
 _H_CACHE: dict[int, np.ndarray] = {}
```

### How the owner switches it on (off by default)

Two-line integration in `run.py` (not applied to the read-only repo; shown as the
intended change):

```python
p.add_argument("--viz-semantics", action="store_true",
               help="draw the semantic map (objects, destination, approach) on the debug canvas")
...
renderer = Renderer(cfg, perception.grid, show=args.show, video_path=args.video,
                    show_semantics=args.viz_semantics)          # run.py:238
```

Live run (synthetic, no hardware/API key):

```console
.venv/bin/python run.py --config config/room.synthetic.json --source synthetic \
    --mission goto --waypoint b --semantics fake --find "go to the blue mat" \
    --seconds 20 --viz-semantics --video runs/semantics_demo.mp4
```

Behaviour notes for the owner:
* Off = zero change: `show_semantics` defaults False and the canvas is untouched
  (proven byte-identical) — existing invocations need no edits.
* The overlay appears only when `scene.semantics` exists (true after the first
  semantics pass; `run.py` sets it every tick when a runner is active) and the
  destination marker only after `--find` resolves (then `runner.set_destination`).
  With semantics off/None the overlay is a silent no-op.
* It composes with the normal debug output: `--video`/`--show` only.

## Deviations / caveats

* `from semantics import approach_point` is a module-level import in `viz.py`.
  `semantics → perception → (config, scene)` never imports `viz`, so no cycle;
  the existing lazy `from perception import Homography` in `_H_CACHE` stays as is.
* `viz.py` re-reads nothing new per frame: the overlay draws from the immutable
  `SemanticMap` snapshot already in `Scene` (worker-thread safe by construction).
* Out of scope, noticed while reading `run.py`: `--trace` prints an undefined
  `trace_line` (run.py:339) — pre-existing bug, unrelated to this change, left
  untouched.
* Prototype scope: labels are drawn without clipping/knock-out so a label near the
  canvas edge is cut off; multiple objects at the same spot overlap. Fine for a
  debug overlay.

## Reproduce

```console
cd /home/freakymustard/.hermes/cache/scratch/w5/v8
# clones
git clone /home/freakymustard/jev-rover pristine && git clone /home/freakymustard/jev-rover work
#   (work/viz.py carries the patch above; diff via: git -C work diff)
# pristine baseline render
cd pristine && PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python \
  /home/freakymustard/.hermes/cache/scratch/w5/v8/demo_semantics_overlay.py --out ../out/pristine
# patched render + all assertions vs that baseline
cd ../work && PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python \
  /home/freakymustard/.hermes/cache/scratch/w5/v8/demo_semantics_overlay.py --out ../out/work \
  --baseline ../out/pristine/canvas_off.png
# colour attribution (minimap vs camera view)
cd .. && PYTHONDONTWRITEBYTECODE=1 /home/freakymustard/jev-rover/.venv/bin/python \
  probe_colors.py out/work/canvas_on.png 1280 144
```