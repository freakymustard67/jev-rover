"""One semantic pass: frame -> vision model -> floor coordinates. No rover moves.

The tool exists to debug an adapter in isolation — prompt/labels, thresholds,
latency, box space — before it is trusted inside the runner. It calls the model
directly (not through the semantics worker) and always uses
``Perception.frame_h`` (homography space) per the frame-space contract.

    # offline, with the fixture adapter:
    .venv/bin/python tools/smoke_semantics.py --config config/room.synthetic.json \
        --source synthetic --kind fake

    # once a real adapter lands (owner decision D1):
    .venv/bin/python tools/smoke_semantics.py --config config/room.json \
        --source camera --kind local --labels "mat,box,bottle"

Exit codes: 0 = at least one detection projected, 1 = model/argument failure,
2 = the model ran but found nothing.
"""
from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import RoomConfig
from perception import Camera, Perception
from semantics import build_vision, project_detections
from synthetic import SyntheticRoom

WARM_FRAMES = 6


def _frames(source: str, cfg: RoomConfig, seconds_warm: float):
    """Yield (frame, t) pairs to warm perception with."""
    if source == "synthetic":
        syn = SyntheticRoom(cfg)
        for i in range(WARM_FRAMES):
            yield syn.render(), i / 15.0
        return
    if source == "camera":
        cam = Camera(cfg.camera)
        try:
            n = 0
            deadline = time.time() + max(0.0, seconds_warm)
            while n < WARM_FRAMES or time.time() < deadline:
                ok, frame, _ = cam.read()
                if ok:
                    yield frame, n / 15.0
                    n += 1
                else:
                    time.sleep(0.02)
        finally:
            cam.release()
        return
    import cv2
    img = cv2.imread(source)
    if img is None:
        raise SystemExit(f"cannot read image {source!r}")
    for i in range(WARM_FRAMES):        # same frame repeats: floor sample + grid need it
        yield img, i / 15.0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="config/room.json")
    p.add_argument("--source", default="camera",
                   help="camera | synthetic | path to an image file")
    p.add_argument("--kind", choices=["fake", "local", "remote"], default=None,
                   help="override semantics.model.kind for this run")
    p.add_argument("--labels", default=None, help="comma-separated label list")
    p.add_argument("--seconds-warm", type=float, default=2.0,
                   help="extra camera warm-up time (floor sample + grid)")
    p.add_argument("--save-frame", default=None, help="write the homography-space frame here")
    p.add_argument("--shortest-edge", type=int, default=None,
                   help="override semantics.model.image_shortest_edge (processor size)")
    p.add_argument("--longest-edge", type=int, default=None,
                   help="override semantics.model.image_longest_edge (processor size)")
    args = p.parse_args(argv)

    cfg = RoomConfig.load(args.config)
    if args.kind:
        cfg.semantics.model.kind = args.kind
    if args.labels:
        cfg.semantics.model.labels = [s.strip() for s in args.labels.split(",") if s.strip()]
    if args.shortest_edge:
        cfg.semantics.model.image_shortest_edge = args.shortest_edge
    if args.longest_edge:
        cfg.semantics.model.image_longest_edge = args.longest_edge
    kind = cfg.semantics.model.kind

    perc = Perception(cfg)
    frame = None
    t = 0.0
    try:
        for frame, t in _frames(args.source, cfg, args.seconds_warm):
            perc.process(frame, t)
    except SystemExit as e:
        print(f"[smoke] {e}", file=sys.stderr)
        return 1
    if frame is None or perc.frame_h is None:
        print("[smoke] no frames captured", file=sys.stderr)
        return 1
    if args.save_frame:
        import cv2
        cv2.imwrite(args.save_frame, perc.frame_h)
        print(f"[smoke] homography-space frame saved to {args.save_frame}")

    print(f"[smoke] config={args.config} source={args.source} kind={kind} "
          f"labels={cfg.semantics.model.labels or '(all)'} "
          f"frame={perc.frame_h.shape[1]}x{perc.frame_h.shape[0]}")

    try:
        vision = build_vision(cfg.semantics, perc.homography)
    except (NotImplementedError, RuntimeError) as e:
        print(f"[smoke] cannot build adapter: {e}", file=sys.stderr)
        print("[smoke] for kind=local install requirements-vision.txt; for kind=remote "
              "set semantics.model.endpoint (see docs/reviews/m1-semantics/m2-design.md §2)",
              file=sys.stderr)
        return 1

    t0 = time.time()
    try:
        dets = vision.infer(perc.frame_h, labels=cfg.semantics.model.labels or None)
    except Exception as e:                       # adapter failure is the tool's job to show
        print(f"[smoke] {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    latency_ms = (time.time() - t0) * 1000.0
    peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    print(f"[smoke] model={vision.name} load_s={getattr(vision, 'load_s', None)} "
          f"latency_ms={latency_ms:.0f} detections={len(dets)} "
          f"peak_rss_mb={peak_rss_mb:.0f}")
    for d in dets:
        print(f"  det {d.label!r} bbox={d.bbox_px} score={d.score:.2f}")

    ctx = perc.semantic_context(t)
    frame_matches = (perc.frame_h.shape[1], perc.frame_h.shape[0]) == \
        (cfg.camera.width, cfg.camera.height)
    if not frame_matches:
        print(f"[smoke] note: frame {perc.frame_h.shape[1]}x{perc.frame_h.shape[0]} does not match "
              f"the configured camera {cfg.camera.width}x{cfg.camera.height}; floor projection "
              f"skipped for this run (detections only)")
        print(json.dumps({"model": vision.name, "latency_ms": round(latency_ms, 1),
                          "peak_rss_mb": round(peak_rss_mb, 1), "objects": [],
                          "projection": "skipped: frame/camera size mismatch",
                          "detections": [{"label": d.label, "bbox_px": list(d.bbox_px),
                                          "score": d.score} for d in dets]}))
        return 0 if dets else 2
    objects, rejected = project_detections(dets, perc.frame_h, ctx, cfg.semantics, t)
    for o in objects:
        print(f"  obj {o.label!r} -> ({o.x:.2f},{o.y:.2f}) m conf={o.confidence:.2f} "
              f"height_suspect={o.height_suspect}")
    print(f"[smoke] projected={len(objects)} rejected={rejected}")
    print(json.dumps({
        "model": vision.name,
        "latency_ms": round(latency_ms, 1),
        "peak_rss_mb": round(peak_rss_mb, 1),
        "shortest_edge": cfg.semantics.model.image_shortest_edge,
        "longest_edge": cfg.semantics.model.image_longest_edge,
        "objects": [{"label": o.label, "x": o.x, "y": o.y,
                     "confidence": o.confidence, "height_suspect": o.height_suspect}
                    for o in objects],
        "rejected": rejected,
    }))
    if not objects:
        print("[smoke] model ran but projected zero objects", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
