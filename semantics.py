"""Triggered semantic layer, M1: offline skeleton.

Geometry (the existing `Scene`) says *where things are in metres*. This layer
says *what things are*, but only when asked: a vision model runs on triggers,
never per frame, and its output is projected onto the floor, merged into a
persistent per-room map, diffed between passes, and ranked for
destination-style queries ("go to the blue mat").

M1 ships the whole pipeline with `FakeVision` (deterministic fixtures) so it is
fully testable offline. Real adapters (`local`, `remote`) and the trigger
scheduler arrive in M2/M3; the sweep confirmation protocol in M4.

Worker discipline mirrors `tactics.py`: the caller builds an immutable snapshot
(`perception.SemanticContext` + a frame copy) before handing work to a
background thread; results are rejected when stale; one pass in flight; hard
budgets. A semantic pass must never block the control loop.

Measured semantics folded in (prototype: /tmp/opencode/semantics_proto/):
  * motion is computed from the RAW detection displacement, not the EMA step
    (a 0.35 m move reads as 0.14 m after smoothing and would be missed);
  * a per-label anchor override is supported because `bbox_bottom_center` is
    ~30 cm off for flat objects on an oblique camera while `centroid` is
    ~1.5 cm (and the reverse for standing objects);
  * `height_suspect` is a probe below the bbox base plus grid freshness, not
    grid overlap (an object on the floor overlaps non-floor cells by nature).
"""
from __future__ import annotations

import json
import math
import os
import queue
import re
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from config import ProjectConfig, RoomConfig, SemanticsConfig
from perception import Homography, SemanticContext
from scene import Destination, SemanticDiff, SemanticMap, SemanticObject

VISION_KINDS = ("fake", "local", "remote")
ANCHOR_POINTS = ("bbox_bottom_center", "bbox_center", "centroid")
MAX_DETECTIONS = 64
EVENT_LOG = "events.jsonl"


# ------------------------------------------------------------------ adapters

@dataclass
class Detection:
    label: str
    bbox_px: tuple[int, int, int, int]     # x0, y0, x1, y1 in FULL-RES camera pixels
    score: float = 1.0


class VisionModel(Protocol):
    name: str

    def infer(self, frame: np.ndarray, *, labels: list[str] | None = None) -> list[Detection]:
        ...


@dataclass
class WorldFixture:
    """A detection defined in room metres, projected to a bbox for fakes/replay."""

    label: str
    x: float
    y: float
    w: float = 0.3
    h: float = 0.3
    score: float = 0.9


class FakeVision:
    """Deterministic fixture adapter: never touches pixels beyond frame sanity."""

    def __init__(self, fixtures: list[Detection], name: str = "fake-vision-v0"):
        self.fixtures = list(fixtures)
        self.name = name

    def infer(self, frame: np.ndarray, *, labels: list[str] | None = None) -> list[Detection]:
        if frame is None or frame.ndim != 3:
            raise ValueError("FakeVision expects a BGR frame")
        if not labels:
            return list(self.fixtures)
        want = {canonical_label(lbl) for lbl in labels}
        return [d for d in self.fixtures if canonical_label(d.label) in want]

    @classmethod
    def from_world(cls, entries, homography: Homography,
                   name: str = "fake-vision-v0") -> "FakeVision":
        dets: list[Detection] = []
        for e in entries:
            hw, hh = e.w / 2.0, e.h / 2.0
            corners = np.array([
                [e.x - hw, e.y - hh], [e.x + hw, e.y - hh],
                [e.x + hw, e.y + hh], [e.x - hw, e.y + hh],
            ], float)
            px = homography.world_to_img(corners)
            x0, y0 = px.min(axis=0)
            x1, y1 = px.max(axis=0)
            dets.append(Detection(e.label, (int(round(x0)), int(round(y0)),
                                            int(round(x1)), int(round(y1))), float(e.score)))
        return cls(dets, name=name)


def build_vision(cfg: SemanticsConfig, homography: Homography) -> VisionModel:
    """Adapter factory. M1 ships only the fake; real models are M2."""
    kind = cfg.model.kind
    if kind == "fake":
        entries = list(cfg.model.fixtures)
        if cfg.model.labels:
            want = {canonical_label(lbl) for lbl in cfg.model.labels}
            entries = [e for e in entries if canonical_label(e.label) in want]
        return FakeVision.from_world(entries, homography)
    raise NotImplementedError(
        f"vision model kind {kind!r} is planned for M2; only 'fake' ships in M1")


# ---------------------------------------------------------------- projection

def resolve_anchor_kind(project: ProjectConfig, label: str) -> str:
    return project.point_by_label.get(label.strip().lower(), project.point)


def anchor_point(bbox: tuple[float, float, float, float], kind: str) -> tuple[float, float]:
    x0, y0, x1, y1 = bbox
    cx = (x0 + x1) / 2.0
    if kind == "bbox_bottom_center":
        return cx, y1
    # "bbox_center" and "centroid" both map to the bbox centre in M1; a real
    # adapter can later return a mask centroid for the latter.
    return cx, (y0 + y1) / 2.0


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    return inter / max(1e-9, area_a + area_b - inter)


def _centres_within_m(a: Detection, b: Detection, homography: Homography,
                      thr_m: float) -> bool:
    """World-space bbox-centre distance (the plan's 'centres < 0.15 m' clause)."""
    if thr_m <= 0.0:
        return False
    ca = np.array([[(a.bbox_px[0] + a.bbox_px[2]) / 2.0, (a.bbox_px[1] + a.bbox_px[3]) / 2.0]])
    cb = np.array([[(b.bbox_px[0] + b.bbox_px[2]) / 2.0, (b.bbox_px[1] + b.bbox_px[3]) / 2.0]])
    wa = homography.img_to_world(ca)[0]
    wb = homography.img_to_world(cb)[0]
    return float(math.hypot(wa[0] - wb[0], wa[1] - wb[1])) <= thr_m


def dedupe_detections(dets: list[Detection], iou_thr: float, *,
                      homography: Homography | None = None,
                      center_dist_m: float = 0.0) -> list[Detection]:
    """Same-label duplicates collapse to the highest score.

    Duplicates overlap (IoU > ``iou_thr``) or have projected centres within
    ``center_dist_m`` metres (near-coincident detector boxes that do not
    overlap); the world clause needs ``homography``.
    """
    kept: list[Detection] = []
    kept_canon: list[str] = []
    for d in sorted(dets, key=lambda d: -d.score):
        if d.bbox_px[2] <= d.bbox_px[0] or d.bbox_px[3] <= d.bbox_px[1]:
            continue
        canon = canonical_label(d.label)
        if any(canon == kc and (
                   _iou(k.bbox_px, d.bbox_px) > iou_thr
                   or (homography is not None
                       and _centres_within_m(k, d, homography, center_dist_m)))
               for kc, k in zip(kept_canon, kept)):
            continue
        kept.append(d)
        kept_canon.append(canon)
    return kept[:MAX_DETECTIONS]


def _inside_polygon(polygon_px: np.ndarray, x: float, y: float) -> bool:
    contour = polygon_px.reshape(-1, 1, 2).astype(np.float32)
    return cv2.pointPolygonTest(contour, (float(x), float(y)), False) >= 0


def _lab_at(frame: np.ndarray, x: int, y: int) -> np.ndarray:
    return cv2.cvtColor(frame[y, x].reshape(1, 1, 3), cv2.COLOR_BGR2LAB)[0, 0].astype(float)


def height_suspect(frame: np.ndarray, bbox: tuple[float, float, float, float],
                   wx: float, wy: float, ctx: SemanticContext,
                   project: ProjectConfig) -> bool:
    """Is the projection untrustworthy because the object may be elevated?

    Probe the pixels just BELOW the base of the bbox: floor-coloured means the
    object rests on the floor; anything else (furniture, another object) means
    it may be standing on something, which biases the floor projection. A
    ray-walk variant is proven for the oblique-camera case if this ever misses;
    the probe is the M1 rule.
    """
    if ctx.floor_lab is None:
        return True
    h, w = frame.shape[:2]
    x0, y0, x1, y1 = bbox
    px = int(round((x0 + x1) / 2.0))
    py = int(round(y1)) + int(project.probe_px)
    if px < 0 or px >= w or py >= h or py < 0:
        return True
    if not _inside_polygon(ctx.polygon_px, px, py):
        return True
    if float(np.linalg.norm(_lab_at(frame, px, py) - ctx.floor_lab)) >= ctx.floor_lab_tolerance:
        return True
    cell = ctx.cell_of(wx, wy)
    if cell is None:
        return True
    ix, iy = cell
    return not bool(ctx.observed()[iy, ix])


def project_detections(dets: list[Detection], frame: np.ndarray, ctx: SemanticContext,
                       cfg: SemanticsConfig, t: float) -> tuple[list[SemanticObject], int]:
    """Detections -> floor coordinates (metres), rejecting anything off-floor."""
    h, w = frame.shape[:2]
    objects: list[SemanticObject] = []
    rejected = 0
    for d in dedupe_detections(dets, cfg.dedupe_iou, homography=ctx.homography,
                               center_dist_m=cfg.dedupe_center_m):
        x0 = max(0, min(w - 1, d.bbox_px[0]))
        y0 = max(0, min(h - 1, d.bbox_px[1]))
        x1 = max(0, min(w - 1, d.bbox_px[2]))
        y1 = max(0, min(h - 1, d.bbox_px[3]))
        bbox = (float(x0), float(y0), float(x1), float(y1))
        kind = resolve_anchor_kind(cfg.project, d.label)
        ax, ay = anchor_point(bbox, kind)
        world = ctx.homography.img_to_world(np.array([[ax, ay]]))[0]
        wx, wy = float(world[0]), float(world[1])
        if not (math.isfinite(wx) and math.isfinite(wy)) or not _inside_polygon(
                ctx.polygon_px, ax, ay):
            rejected += 1
            continue
        objects.append(SemanticObject(
            id="",                                       # assigned by the store on merge
            label=d.label,
            x=round(wx, 3),
            y=round(wy, 3),
            confidence=round(float(d.score), 3),
            height_suspect=height_suspect(frame, bbox, wx, wy, ctx, cfg.project),
            last_seen_s=round(float(t), 2),
        ))
    return objects, rejected


# --------------------------------------------------------------------- store

class SemanticStore:
    """Persistent per-room map: merge passes, diff them, write artifacts."""

    def __init__(self, cfg: SemanticsConfig, room_name: str):
        self.cfg = cfg
        self.room_name = room_name
        self.objs: dict[str, SemanticObject] = {}
        self.next_id = 1
        self.passes = 0
        self.model = ""
        self.last_pass_t = 0.0
        self.rejected_total = 0
        self.diff = SemanticDiff()
        self.destination: Destination | None = None
        self._misses: dict[str, int] = {}
        self._hs_hist: dict[str, list[bool]] = {}
        self._labels: dict[str, str] = {}
        self.store_dir = Path(cfg.store_dir)

    # -- merge --------------------------------------------------------------
    def merge(self, objects: list[SemanticObject], rejected: int, model: str,
              t_pass: float) -> SemanticMap:
        self.passes += 1
        self.model = model
        self.last_pass_t = t_pass
        self.rejected_total += rejected
        diff = SemanticDiff()
        unmatched = list(objects)
        seen: set[str] = set()
        ema = self.cfg.ema_alpha

        # Distance-ordered best-first assignment: collect every (object,
        # detection) pair inside the match radius and consume the globally
        # nearest pair first, so two same-label objects cannot swap identities
        # because of dict insertion order.
        pairs: list[tuple[float, str, int]] = []
        det_labels = [canonical_label(d.label) for d in unmatched]
        for oid, prev in self.objs.items():
            prev_label = canonical_label(prev.label)
            for i, d in enumerate(unmatched):
                if det_labels[i] != prev_label:
                    continue
                dd = math.hypot(d.x - prev.x, d.y - prev.y)
                if dd <= self.cfg.match_radius_m:
                    pairs.append((dd, oid, i))
        pairs.sort(key=lambda p: p[0])

        taken: set[int] = set()
        for _, oid, i in pairs:
            if oid in seen or i in taken:
                continue
            d = unmatched[i]
            taken.add(i)
            prev = self.objs[oid]
            prev.last_seen_s = round(t_pass, 2)
            self._misses[oid] = 0
            seen.add(oid)
            if d.confidence < self.cfg.min_confidence:
                # Freshness only: a sub-threshold hit keeps the object alive but
                # must not move it or overwrite its flags.
                continue
            prev_x, prev_y = prev.x, prev.y
            prev.x = (1.0 - ema) * prev.x + ema * d.x
            prev.y = (1.0 - ema) * prev.y + ema * d.y
            prev.confidence = d.confidence
            # height_suspect needs a 2-of-3 majority (hysteresis): a single
            # noisy probe must not flap the flag in either direction.
            hist = self._hs_hist.setdefault(oid, [])
            hist.append(bool(d.height_suspect))
            del hist[:-3]
            if sum(hist) >= 2:
                prev.height_suspect = True
            elif len(hist) - sum(hist) >= 2:
                prev.height_suspect = False
            # RAW displacement this pass, before any smoothing: the EMA step is
            # only alpha * raw and hides moves just above the threshold.
            raw = math.hypot(d.x - prev_x, d.y - prev_y)
            prev.motion = "moved" if raw > self.cfg.move_threshold_m else "static"
            if prev.motion == "moved":
                diff.moved.append(oid)

        unmatched = [d for i, d in enumerate(unmatched) if i not in taken]

        for d in unmatched:
            oid = f"obj_{self.next_id:04d}"
            self.next_id += 1
            obj = replace(d, id=oid, first_seen_s=round(t_pass, 2), last_seen_s=round(t_pass, 2),
                          sources=list(d.sources))
            self.objs[oid] = obj
            self._misses[oid] = 0
            self._labels[oid] = obj.label
            if obj.confidence >= self.cfg.min_confidence:
                diff.appeared.append(oid)
            seen.add(oid)

        evicted: list[str] = []
        for oid in self.objs:
            if oid in seen:
                continue
            self._misses[oid] = self._misses.get(oid, 0) + 1
            if self._misses[oid] == self.cfg.vanish_passes:
                diff.vanished.append(oid)
            if self._misses[oid] >= self.cfg.max_misses:
                evicted.append(oid)
        for oid in evicted:
            # Cap resurrection: an object gone this long gets a new id if it
            # comes back, and churn cannot grow the map without bound.
            del self.objs[oid]
            self._misses.pop(oid, None)
            self._labels.pop(oid, None)
            self._hs_hist.pop(oid, None)

        self.diff = diff
        if diff.appeared or diff.moved or diff.vanished:
            self._append_events(diff, t_pass)
        self._save_latest()
        return self.snapshot(t_pass)

    # -- views --------------------------------------------------------------
    def snapshot(self, t: float) -> SemanticMap:
        age = max(0.0, t - self.last_pass_t) if self.passes else 0.0
        objects = [replace(o, sources=list(o.sources)) for o in self.objs.values()]
        return SemanticMap(
            age_s=round(age, 2),
            passes=self.passes,
            model=self.model,
            objects=objects,
            destination=replace(self.destination) if self.destination else None,
            diff=SemanticDiff(list(self.diff.appeared), list(self.diff.moved),
                              list(self.diff.vanished)),
        )

    def set_destination(self, dest: Destination | None) -> None:
        self.destination = dest

    def find_object(self, object_id: str) -> SemanticObject | None:
        return self.objs.get(object_id)

    # -- persistence --------------------------------------------------------
    def _save_latest(self) -> None:
        try:
            self.store_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "room": self.room_name,
                "saved_at": time.time(),
                "map": asdict(self.snapshot(self.last_pass_t)),
            }
            tmp = self.store_dir / f"{self.room_name}_latest.json.tmp"
            tmp.write_text(json.dumps(payload, indent=2) + "\n")
            os.replace(tmp, self.store_dir / f"{self.room_name}_latest.json")
        except OSError:
            pass  # artifacts are diagnostics; never fail the pass over disk

    def _append_events(self, diff: SemanticDiff, t_pass: float) -> None:
        try:
            self.store_dir.mkdir(parents=True, exist_ok=True)
            line = {
                "t": round(t_pass, 2),
                "appeared": diff.appeared,
                "moved": diff.moved,
                "vanished": diff.vanished,
                "labels": {oid: self._labels.get(oid, "") for oid in
                           diff.appeared + diff.moved + diff.vanished},
            }
            with (self.store_dir / EVENT_LOG).open("a") as fh:
                fh.write(json.dumps(line) + "\n")
        except OSError:
            pass


def load_map(path: str | Path) -> SemanticMap:
    """Rebuild a persisted map (for tests and, later, cross-session memory)."""
    data = json.loads(Path(path).read_text())
    m = data["map"]
    return SemanticMap(
        age_s=float(m.get("age_s", 0.0)),
        passes=int(m.get("passes", 0)),
        model=str(m.get("model", "")),
        objects=[SemanticObject(**o) for o in m.get("objects", [])],
        destination=Destination(**m["destination"]) if m.get("destination") else None,
        diff=SemanticDiff(**m.get("diff", {})),
    )


# -------------------------------------------------------------------- worker

@dataclass
class PassRequest:
    pass_id: int
    t: float                                   # loop clock at submit
    frame: np.ndarray
    ctx: SemanticContext
    labels: list[str] | None
    kind: str                                  # full | roi | destination


@dataclass
class PassResult:
    pass_id: int
    t_submit: float
    kind: str
    labels: list[str] | None
    objects: list[SemanticObject] = field(default_factory=list)
    rejected: int = 0
    detections: int = 0
    model: str = ""
    error: str = ""
    t_done_wall: float = 0.0


class SemanticsWorker:
    """One background thread, one pass in flight, latest result wins."""

    def __init__(self, vision: VisionModel, cfg: SemanticsConfig):
        self.vision = vision
        self.cfg = cfg
        self.started = 0
        self.completed = 0
        self.errors = 0
        self.last_error: str | None = None
        self.latencies: list[float] = []
        self._q: queue.Queue[PassRequest] = queue.Queue(maxsize=1)
        self._out: queue.Queue[PassResult] = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def offer(self, req: PassRequest) -> bool:
        try:
            self._q.put_nowait(req)
            self.started += 1
            return True
        except queue.Full:
            return False

    def poll(self) -> PassResult | None:
        try:
            return self._out.get_nowait()
        except queue.Empty:
            return None

    def _push(self, res: PassResult) -> None:
        try:
            self._out.put_nowait(res)
        except queue.Full:                         # keep the newest result only
            try:
                self._out.get_nowait()
            except queue.Empty:
                pass
            self._out.put_nowait(res)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                req = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            t0 = time.time()
            try:
                dets = self.vision.infer(req.frame, labels=req.labels)
                objects, rejected = project_detections(dets, req.frame, req.ctx, self.cfg, req.t)
                self.completed += 1
                self.latencies.append(time.time() - t0)
                self._push(PassResult(
                    pass_id=req.pass_id, t_submit=req.t, kind=req.kind, labels=req.labels,
                    objects=objects, rejected=rejected, detections=len(dets),
                    model=self.vision.name, t_done_wall=time.time()))
            except Exception as e:                 # degrade, never kill the run
                self.errors += 1
                self.last_error = f"{type(e).__name__}: {e}"[:200]
                self._push(PassResult(
                    pass_id=req.pass_id, t_submit=req.t, kind=req.kind, labels=req.labels,
                    model=self.vision.name, error=self.last_error, t_done_wall=time.time()))

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)


class SemanticsRunner:
    """Main-thread facade: budgets, one in-flight pass, stale rejection, merge."""

    def __init__(self, cfg: RoomConfig, room_name: str, vision: VisionModel):
        self.cfg = cfg
        self.semantics_cfg = cfg.semantics
        self.vision = vision
        self.store = SemanticStore(self.semantics_cfg, room_name)
        self.worker = SemanticsWorker(vision, self.semantics_cfg)
        self._next_pass_id = 1
        self._inflight: int | None = None
        self._offer_times: list[float] = []
        self.last_offer_t = float("-inf")
        self.fail_cooldown_until = float("-inf")
        self.skipped = {"interval": 0, "budget": 0, "inflight": 0, "no_context": 0}
        self.stale_dropped = 0

    # -- passes -------------------------------------------------------------
    def maybe_pass(self, t: float, ctx: SemanticContext, frame: np.ndarray, *,
                   labels: list[str] | None = None, kind: str = "full",
                   force: bool = False) -> bool:
        if self._inflight is not None:
            self.skipped["inflight"] += 1
            return False
        if ctx.floor_lab is None:
            self.skipped["no_context"] += 1
            return False
        # Cooldown and budget are hard limits: even an explicit trigger respects
        # them, so a bug cannot run up a bill or hammer a failing model.
        if t < self.fail_cooldown_until:
            return False
        if not force and t - self.last_offer_t < self.semantics_cfg.min_interval_s:
            self.skipped["interval"] += 1
            return False
        recent = [s for s in self._offer_times if t - s <= 60.0]
        self._offer_times = recent
        if len(recent) >= self.semantics_cfg.max_passes_per_min:
            self.skipped["budget"] += 1
            return False
        pid = self._next_pass_id
        self._next_pass_id += 1
        req = PassRequest(pid, t, frame.copy(), ctx, labels, kind)
        if not self.worker.offer(req):
            self.skipped["inflight"] += 1
            return False
        self._inflight = pid
        self.last_offer_t = t
        self._offer_times.append(t)
        return True

    def poll(self, t: float) -> SemanticMap | None:
        """Merge a finished pass if one is ready and fresh. Non-blocking."""
        res = self.worker.poll()
        if res is None:
            return None
        if self._inflight == res.pass_id:
            self._inflight = None
        if res.error:
            self.fail_cooldown_until = t + self.semantics_cfg.failure_cooldown_s
            return None
        if t - res.t_submit > self.semantics_cfg.max_age_s:
            self.stale_dropped += 1
            return None
        return self.store.merge(res.objects, res.rejected, res.model, res.t_submit)

    def snapshot(self, t: float) -> SemanticMap | None:
        return self.store.snapshot(t) if self.store.passes else None

    def set_destination(self, dest: Destination | None) -> None:
        self.store.set_destination(dest)

    def close(self) -> None:
        self.worker.close()

    def stats(self) -> dict:
        lat = sorted(self.worker.latencies)
        return {
            "enabled": True,
            "model": self.vision.name,
            "passes": self.store.passes,
            "passes_started": self.worker.started,
            "errors": self.worker.errors,
            "last_error": self.worker.last_error,
            "rejected_total": self.store.rejected_total,
            "stale_dropped": self.stale_dropped,
            "skipped": dict(self.skipped),
            "median_ms": round(lat[len(lat) // 2] * 1000, 1) if lat else None,
        }


# ------------------------------------------------------------- destinations

_STOP_WORDS = {
    "go", "to", "the", "a", "an", "find", "near", "at", "please", "head", "towards",
    "toward", "patrol", "until", "stop", "by", "drive", "navigate", "reach", "next",
    "then", "and", "of", "on", "in", "it",
    # question-shaped instructions ("where is the charger")
    "where", "is", "are", "was", "were", "what", "which", "how", "do", "does",
    "me", "my", "our", "can", "could", "would", "you", "i",
}


def _fold(word: str) -> str:
    """Tiny plural fold; enough for 'mats'/'boxes'/'berries' without a stemmer."""
    if len(word) > 4 and word.endswith("ies") and word[-4] not in "aeiou":
        return word[:-3] + "y"                    # berries -> berry
    if len(word) > 4 and word.endswith("es") and (
            word[-3] in "sxz" or word.endswith(("ches", "shes"))):
        return word[:-2]                          # boxes -> box, dishes -> dish
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]                          # mats -> mat
    return word


def canonical_label(label: str) -> str:
    """Casefold + plural fold, used for label comparisons only.

    'Blue Mats' -> 'blue mat'; the display label stored in the map keeps its
    original spelling. A real stemmer is out of scope (M2 concern).
    """
    return " ".join(_fold(w) for w in re.findall(r"[a-z0-9]+", label.casefold()))


def _tokens(text: str) -> set[str]:
    return {_fold(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP_WORDS}


@dataclass
class ScoredCandidate:
    obj: SemanticObject
    score: float


def rank_candidates(query: str, objects: list[SemanticObject],
                    cfg: SemanticsConfig | None = None) -> list[ScoredCandidate]:
    """Jaccard token overlap, best object per label, deterministic order.

    Jaccard (not weighted recall) is what the prototype measured: 'go to the
    blue mat' -> 0.67, bare 'mat' -> 0.5 for both mats (ambiguous), and a
    non-existent object -> empty.
    """
    cfg = cfg or SemanticsConfig()
    q = _tokens(query)
    if not q:
        return []
    best_by_label: dict[str, ScoredCandidate] = {}
    for o in objects:
        w = _tokens(o.label)
        if not w:
            continue
        union = q | w
        score = len(q & w) / len(union) if union else 0.0
        if score < cfg.min_label_score:
            continue
        prev = best_by_label.get(o.label)
        if prev is None or (score, o.confidence, o.id) > (prev.score, prev.obj.confidence, prev.obj.id):
            best_by_label[o.label] = ScoredCandidate(o, score)
    return sorted(best_by_label.values(), key=lambda c: (-c.score, c.obj.id))


def _ask_jev_label(text: str, candidates: list[ScoredCandidate], jev) -> str | None:
    """One budgeted Choice over the candidate labels; code owns the options."""
    from typesafe_sdk import Choice  # local import: pure functions stay SDK-free

    questions = {
        "label": Choice(
            instructions={
                "question": "Which object does the instruction refer to?",
                "instruction": text,
                "policy": "Choose exactly one of the listed labels; never invent one.",
            },
            criteria={c.obj.label: {"what": f"the {c.obj.label} at "
                                            f"({c.obj.x:.1f}, {c.obj.y:.1f}) m"}
                      for c in candidates},
        ),
    }
    state = {"objects": [{"label": c.obj.label, "x": c.obj.x, "y": c.obj.y,
                          "confidence": c.obj.confidence} for c in candidates]}
    try:
        resp = jev.system_one(state=state, model="jev-latest", questions=questions)
        choice = str(resp.answers["label"].choice)
    except Exception:
        return None
    return choice if any(c.obj.label == choice for c in candidates) else None


def resolve_destination(text: str, semantic: SemanticMap, jev=None,
                        cfg: SemanticsConfig | None = None) -> Destination | None:
    """NL text -> Destination, or None. Ambiguity goes to Jev once if available."""
    cfg = cfg or SemanticsConfig()
    ranked = rank_candidates(text, semantic.objects, cfg)
    if not ranked:
        return None
    top = ranked[0]
    if len(ranked) > 1 and (top.score - ranked[1].score) <= cfg.ambiguity_epsilon:
        picked = None
        if jev is not None:
            picked = _ask_jev_label(text, ranked[:3], jev)
        if picked is None:
            # Deterministic fallback: best (score, confidence) among the tied set.
            top = max(ranked, key=lambda c: (c.score, c.obj.confidence))
        else:
            top = next(c for c in ranked if c.obj.label == picked)
    o = top.obj
    return Destination(label=o.label, x=o.x, y=o.y, confidence=o.confidence,
                       source="vision", object_id=o.id or None)


def approach_point(dest: Destination, from_xy: tuple[float, float],
                   standoff_m: float) -> tuple[float, float]:
    dx, dy = dest.x - from_xy[0], dest.y - from_xy[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return dest.x, dest.y
    return (dest.x - dx / length * standoff_m, dest.y - dy / length * standoff_m)


__all__ = [
    "Detection", "FakeVision", "VisionModel", "WorldFixture", "build_vision",
    "project_detections", "height_suspect", "anchor_point", "resolve_anchor_kind",
    "SemanticStore", "SemanticsWorker", "SemanticsRunner", "PassRequest", "PassResult",
    "rank_candidates", "resolve_destination", "approach_point", "load_map",
    "ScoredCandidate",
]
