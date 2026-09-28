"""Prototype of the M1 semantics math for jev-rover (projection, merge/diff, destination ranking).

Standalone, no repo imports. Used to pre-validate the proposal:
/tmp/opencode/semantics-layer-proposal.md

Key questions answered:
- how accurately does bbox -> floor projection recover an object's true position?
  (bottom-center vs centroid; flat objects vs standing objects; elevated objects)
- does the height_suspect heuristic catch objects sitting on furniture?
- does the greedy merge keep stable ids and produce sane appeared/moved/vanished diffs?
- does the token-overlap destination ranking resolve / flag ambiguity correctly?
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

# --------------------------------------------------------------- camera model


def make_camera(pos, target, fovx_deg: float, width: int, height: int):
    """Pinhole camera. World -> cam: pc = (X - pos) @ R  (R columns = right, down, forward)."""
    pos = np.asarray(pos, float)
    target = np.asarray(target, float)
    fwd = target - pos
    fwd = fwd / np.linalg.norm(fwd)
    up0 = np.array([0.0, 0.0, 1.0])
    right = np.cross(fwd, up0)
    right = right / np.linalg.norm(right)
    down = np.cross(fwd, right)
    R = np.stack([right, down, fwd], axis=1)
    f = (width / 2) / math.tan(math.radians(fovx_deg / 2))
    K = np.array([[f, 0, width / 2.0], [0, f, height / 2.0], [0, 0, 1.0]])
    return K, R, pos


def project_points(K, R, pos, pts) -> np.ndarray:
    """World points (N,3) -> image pixels (N,2)."""
    pts = np.atleast_2d(np.asarray(pts, float))
    pc = (pts - pos) @ R
    uv = pc @ K.T
    return uv[:, :2] / uv[:, 2:3]


def floor_homography(K, R, pos) -> np.ndarray:
    """H maps world floor (x, y, 1) -> image (u, v, w) for the z=0 plane."""
    At = R.T
    M = np.column_stack([At[:, 0], At[:, 1], -At @ pos])
    return K @ M


def world_from_image(H: np.ndarray, uv) -> np.ndarray:
    """Image pixels -> world floor coordinates (intersection with z=0 plane)."""
    Hi = np.linalg.inv(H)
    uv = np.atleast_2d(np.asarray(uv, float))
    h = np.column_stack([uv, np.ones(len(uv))]) @ Hi.T
    return h[:, :2] / h[:, 2:3]


def bbox_from_points(uv: np.ndarray, jitter_px: float = 0.0, rng=None) -> tuple[float, float, float, float]:
    pts = uv
    if jitter_px and rng is not None:
        pts = pts + rng.normal(0, jitter_px, pts.shape)
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    return float(x0), float(y0), float(x1), float(y1)


def bbox_center_uv(bb) -> tuple[float, float]:
    x0, y0, x1, y1 = bb
    return (x0 + x1) / 2.0, (y0 + y1) / 2.0


def bbox_bottom_center_uv(bb) -> tuple[float, float]:
    x0, y0, x1, y1 = bb
    return (x0 + x1) / 2.0, y1


def circle_bool(uv, c, r):
    return (uv[..., 0] - c[0]) ** 2 + (uv[..., 1] - c[1]) ** 2 <= r * r


FURNITURE = [
    (0.384, 1.98, 1.024, 3.42),   # sofa
    (2.56, 0.144, 3.712, 0.792),  # table
    (4.224, 1.98, 4.736, 2.52),   # box
    (5.632, 2.16, 6.144, 3.312),  # shelf
]


def near_furniture(x: float, y: float, margin: float = 0.25) -> bool:
    for x0, y0, x1, y1 in FURNITURE:
        if x0 - margin <= x <= x1 + margin and y0 - margin <= y <= y1 + margin:
            return True
    return False


def ray_between_suspect(x: float, y: float, cam_xy: tuple[float, float],
                        max_back: float = 2.0, step: float = 0.05,
                        margin: float = 0.1) -> bool:
    """Better height check: an object raised off the floor projects BEYOND its true
    base, away from the camera. So walk from the projected floor point back toward
    the camera's ground point; if furniture lies on that segment, the object is
    probably standing on it -> height_suspect."""
    cx, cy = cam_xy
    dx, dy = cx - x, cy - y
    L = math.hypot(dx, dy)
    if L < 1e-6:
        return False
    n = int(min(max_back, L) / step)
    for i in range(1, n + 1):
        s = i * step / L
        if near_furniture(x + dx * s, y + dy * s, margin=margin):
            return True
    return False


# --------------------------------------------------------- merge / diff logic


@dataclass
class Detection:
    label: str
    x: float
    y: float
    score: float = 1.0
    height_suspect: bool = False


@dataclass
class SemObject:
    id: str
    label: str
    x: float
    y: float
    confidence: float
    first_seen_s: float
    last_seen_s: float
    misses: int = 0
    motion: str = "static"
    height_suspect: bool = False
    sources: list[str] = field(default_factory=lambda: ["vision"])
    prev_x: float | None = None
    prev_y: float | None = None


@dataclass
class Diff:
    appeared: list[str] = field(default_factory=list)
    moved: list[str] = field(default_factory=list)
    vanished: list[str] = field(default_factory=list)


class SemanticMap:
    def __init__(self, match_radius: float = 0.5, ema: float = 0.4,
                 move_thresh: float = 0.25, vanish_after: int = 2):
        self.objs: dict[str, SemObject] = {}
        self.next_id = 1
        self.match_radius = match_radius
        self.ema = ema
        self.move_thresh = move_thresh
        self.vanish_after = vanish_after

    def update(self, dets: list[Detection], t: float) -> Diff:
        diff = Diff()
        unmatched = list(dets)
        seen: set[str] = set()

        for oid, o in self.objs.items():
            best_i, best_d = None, self.match_radius
            for i, d in enumerate(unmatched):
                if d.label != o.label:
                    continue
                dd = math.hypot(d.x - o.x, d.y - o.y)
                if dd <= best_d:
                    best_i, best_d = i, dd
            if best_i is None:
                continue
            d = unmatched.pop(best_i)
            old_x, old_y = o.x, o.y
            o.x = (1 - self.ema) * o.x + self.ema * d.x
            o.y = (1 - self.ema) * o.y + self.ema * d.y
            o.confidence = d.score
            o.last_seen_s = t
            o.misses = 0
            o.height_suspect = d.height_suspect
            seen.add(oid)
            # motion uses the RAW displacement this pass (not the smoothed step),
            # otherwise the EMA (alpha=0.4) hides moves just above move_thresh
            disp = math.hypot(d.x - old_x, d.y - old_y)
            if disp > self.move_thresh:
                diff.moved.append(oid)
                o.motion = "moved"
            else:
                o.motion = "static"
            o.prev_x, o.prev_y = old_x, old_y

        for d in unmatched:
            oid = f"obj_{self.next_id:04d}"
            self.next_id += 1
            self.objs[oid] = SemObject(id=oid, label=d.label, x=d.x, y=d.y,
                                       confidence=d.score, first_seen_s=t,
                                       last_seen_s=t, height_suspect=d.height_suspect)
            diff.appeared.append(oid)
            seen.add(oid)

        for oid, o in self.objs.items():
            if oid in seen:
                continue
            o.misses += 1
            if o.misses == self.vanish_after:
                diff.vanished.append(oid)
        return diff


# ----------------------------------------------------- destination resolution

_STOP = {"go", "to", "the", "a", "an", "find", "near", "at", "please", "head", "towards", "toward"}


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in _STOP}


def rank_candidates(query: str, objs) -> list[tuple[SemObject, float]]:
    q = _tokens(query)
    out = []
    for o in objs:
        w = _tokens(o.label)
        if not (q & w):
            continue
        score = len(q & w) / len(q | w)
        out.append((o, score))
    out.sort(key=lambda p: (-p[1], p[0].id))
    return out


def resolve_destination(query: str, objs, eps: float = 0.15, min_score: float = 0.34):
    ranked = rank_candidates(query, objs)
    if not ranked or ranked[0][1] < min_score:
        return {"status": "none", "candidates": []}
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] <= eps:
        return {"status": "ambiguous",
                "candidates": [(o.label, round(s, 2)) for o, s in ranked[:3]]}
    o, s = ranked[0]
    return {"status": "ok", "object_id": o.id, "label": o.label,
            "x": round(o.x, 3), "y": round(o.y, 3), "score": round(s, 2)}
