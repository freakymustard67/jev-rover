#!/usr/bin/env python3
"""V9 latency-budget harness (runs ONLY inside the scratch clone).

Drives a 15 Hz synthetic tick loop (SyntheticRoom + Perception, as run.py
--source synthetic) with a stub vision detector whose infer() sleeps L seconds,
and measures: achieved loop Hz, jitter/lag, pass cadence, skipped counters,
main-thread per-offer / poll / snapshot costs, and the stale-drop coupling.

Subcommands:
  loop       --L 5.0 --seconds 24 --repeats 3 --hz 15 --scale 1.0 [--offer eager|single]
  costs      --scale 1.0
  stale      --L 31 --seconds 34            # real-time run (waits L)
  stale-fast --repeats 3                    # caller-timestamp injection, wall-fast
  cooldown                                  # fail-cooldown counter check

Raw logs: appends one JSON line per measurement to v9_logs/<tag>.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from dataclasses import replace

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from config import RoomConfig                       # noqa: E402
from perception import Perception                   # noqa: E402
from semantics import (Detection, FakeVision, SemanticsRunner,  # noqa: E402
                       WorldFixture)
from synthetic import SyntheticRoom                 # noqa: E402

LOGDIR = os.path.join(HERE, "v9_logs")


def log(tag: str, record: dict) -> None:
    os.makedirs(LOGDIR, exist_ok=True)
    path = os.path.join(LOGDIR, f"{tag}.jsonl")
    with open(path, "a") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    print(json.dumps(record, sort_keys=True))


def scaled_cfg(scale: float, store_dir: str) -> RoomConfig:
    cfg = RoomConfig.load(os.path.join(HERE, "config", "room.synthetic.json"))
    cfg.semantics = replace(cfg.semantics, store_dir=store_dir)
    if scale != 1.0:
        cfg.camera = replace(cfg.camera,
                             width=int(round(cfg.camera.width * scale)),
                             height=int(round(cfg.camera.height * scale)))
        cfg.homography = replace(
            cfg.homography,
            image_points_px=[[x * scale, y * scale] for x, y in cfg.homography.image_points_px])
        cfg.floor = replace(cfg.floor,
                            polygon_px=[[x * scale, y * scale] for x, y in cfg.floor.polygon_px])
        cfg.synthetic = replace(cfg.synthetic, px_per_m=cfg.synthetic.px_per_m * scale)
    return cfg


class SleepVision:
    """Slow stub detector: infer() sleeps L seconds, then returns fixtures."""

    def __init__(self, delay: float, fixtures: list[Detection] | None = None):
        self.delay = float(delay)
        self.fixtures = list(fixtures or [])
        self.name = f"sleep-{delay}"

    def infer(self, frame, *, labels=None):
        time.sleep(self.delay)
        if not labels:
            return list(self.fixtures)
        want = {lbl.strip().lower() for lbl in labels}
        return [d for d in self.fixtures if d.label.strip().lower() in want]


class BoomVision:
    name = "boom"

    def infer(self, frame, *, labels=None):
        raise RuntimeError("boom")


def warm(cfg: RoomConfig, n: int = 6):
    perc = Perception(cfg)
    syn = SyntheticRoom(cfg)
    frame = None
    for i in range(n):
        frame = syn.render()
        perc.process(frame, i / 15.0)
    return perc, syn, frame


def fixtures_for(perc, cfg):
    return FakeVision.from_world(
        [WorldFixture("blue mat", 3.0, 1.2, 0.5, 0.6, 0.92),
         WorldFixture("red box", 4.4, 1.0, 0.3, 0.3, 0.85)],
        perc.homography).fixtures


def pcts(xs: list[float], qs=(50, 95, 99)):
    if not xs:
        return {}
    s = sorted(xs)
    out = {}
    for q in qs:
        idx = min(len(s) - 1, int(round(q / 100.0 * (len(s) - 1))))
        out[f"p{q}"] = s[idx]
    return out


# ------------------------------------------------------------------ loop

def run_loop(scale: float, seconds: float, hz: float, L: float | None,
             offer_mode: str, tag: str, rep: int) -> dict:
    store_dir = tempfile.mkdtemp(prefix="v9store_", dir=HERE)
    cfg = scaled_cfg(scale, store_dir)
    perc, syn, frame0 = warm(cfg)
    vision = None
    if L is not None:
        vision = SleepVision(L, fixtures_for(perc, cfg))
    runner = SemanticsRunner(cfg, f"v9loop{rep}", vision) if L is not None else None

    period = 1.0 / hz
    perception_period = 1.0 / 15.0
    t0 = time.time()
    next_wall = t0
    next_perc = 0.0
    loop_t_prev = None
    wall = time.time()
    ticks: list[float] = []            # tick timestamps t (s since t0)
    dts: list[float] = []              # inter-tick spacing (s)
    blocks: list[float] = []           # perception+semantics block duration (s)
    sems: list[float] = []             # semantics-only (poll+offer+snapshot) duration (s)
    merges: list[float] = []           # t of merged passes
    offers: list[tuple[float, bool]] = []
    offered = 0
    next_offer_t = 1.0
    frame = frame0
    v_mps, w_deg = 0.0, 0.0
    last_cmd = (0.0, 0.0)
    while True:
        now = time.time()
        t = now - t0
        if t >= seconds:
            break
        dt = period if loop_t_prev is None else max(1e-3, min(0.25, t - loop_t_prev))
        loop_t_prev = t
        syn.step(last_cmd[0], last_cmd[1], dt)
        frame = syn.render()
        if t >= next_perc:
            b0 = time.perf_counter()
            perc.process(frame, t, last_cmd[0], last_cmd[1])
            if runner is not None:
                s0 = time.perf_counter()
                merged = runner.poll(t)
                if merged is not None:
                    merges.append(t)
                do_offer = False
                if offer_mode == "eager":
                    do_offer = True
                elif offer_mode == "single":
                    do_offer = (offered == 0 and t >= 1.0)
                elif offer_mode == "paced":
                    do_offer = (t >= next_offer_t)
                if do_offer:
                    ok = runner.maybe_pass(t, perc.semantic_context(t), frame,
                                           kind="full")
                    offered += 1
                    offers.append((round(t, 3), bool(ok)))
                    if offer_mode == "paced":
                        next_offer_t = t + cfg.semantics.min_interval_s
                sems.append(time.perf_counter() - s0)
            blocks.append(time.perf_counter() - b0)
            next_perc = t + perception_period
        ticks.append(t)
        # pace
        next_wall += period
        sleep = next_wall - time.time()
        if sleep > 0:
            time.sleep(sleep)
        else:
            next_wall = time.time()          # fell behind: re-anchor, keep run bounded

    wall_span = ticks[-1] - ticks[0] if len(ticks) > 1 else 0.0
    achieved = (len(ticks) - 1) / wall_span if wall_span > 0 else 0.0
    dts = [ticks[i] - ticks[i - 1] for i in range(1, len(ticks))]
    jit = pcts(dts)
    max_dt = max(dts) if dts else 0.0
    rec = {
        "rec": "loop", "tag": tag, "rep": rep, "scale": scale, "L": L,
        "offer_mode": offer_mode, "seconds": seconds, "hz_target": hz,
        "res": f"{frame.shape[1]}x{frame.shape[0]}",
        "ticks": len(ticks), "achieved_hz": round(achieved, 3),
        "hz_ratio_vs_target": round(achieved / hz, 4),
        "dt_p50_ms": round(jit.get("p50", 0) * 1000, 2),
        "dt_p95_ms": round(jit.get("p95", 0) * 1000, 2),
        "dt_p99_ms": round(jit.get("p99", 0) * 1000, 2),
        "dt_max_ms": round(max_dt * 1000, 2),
        "dt_std_ms": round(statistics.pstdev(dts) * 1000, 2) if len(dts) > 1 else 0.0,
        "max_lag_ms": round((max_dt - period) * 1000, 2),
        "block_p50_ms": round(pcts(blocks).get("p50", 0) * 1000, 2),
        "block_p99_ms": round(pcts(blocks).get("p99", 0) * 1000, 2),
        "block_max_ms": round(max(blocks) * 1000, 2) if blocks else 0.0,
        "sem_p50_ms": round(pcts(sems).get("p50", 0) * 1000, 3) if sems else 0.0,
        "sem_p99_ms": round(pcts(sems).get("p99", 0) * 1000, 3) if sems else 0.0,
        "sem_max_ms": round(max(sems) * 1000, 3) if sems else 0.0,
        "offers": len(offers), "offers_ok": sum(1 for _, ok in offers if ok),
        "offer_times": [t for t, _ in offers],
        "merge_times": [round(x, 3) for x in merges],
    }
    if runner is not None:
        st = runner.stats()
        rec["stats"] = st
        rec["worker_completed"] = runner.worker.completed
        rec["pass_cadence_s"] = (
            round((merges[-1] - merges[0]) / (len(merges) - 1), 3) if len(merges) > 1 else None)
        runner.close()
    log(f"v9_loop_scale{scale}", rec)
    return rec


# ----------------------------------------------------------------- costs

def run_costs(scale: float, reps: int) -> None:
    store_dir = tempfile.mkdtemp(prefix="v9store_", dir=HERE)
    cfg = scaled_cfg(scale, store_dir)
    perc, syn, frame = warm(cfg)
    h, w = frame.shape[:2]
    res = f"{w}x{h}"

    # 0. per-tick building blocks: render(), process(), semantic_context()
    for name, fn in (("render", lambda: syn.render()),
                     ("process", lambda: perc.process(frame, 0.0)),
                     ("semantic_context", lambda: perc.semantic_context(0.0))):
        fn()
        xs = []
        for _ in range(reps):
            t0 = time.perf_counter()
            fn()
            xs.append(time.perf_counter() - t0)
        log(f"v9_costs_scale{scale}", {"rec": f"block_{name}", "res": res, "reps": reps,
            "mean_ms": round(statistics.mean(xs) * 1000, 4),
            "p50_ms": round(sorted(xs)[len(xs) // 2] * 1000, 4),
            "p95_ms": round(sorted(xs)[int(0.95 * (len(xs) - 1))] * 1000, 4),
            "max_ms": round(max(xs) * 1000, 4)})

    # 1. frame.copy() at the real frame size (semantics.py:523)
    for _ in range(50):
        frame.copy()
    c = []
    for _ in range(reps):
        t0 = time.perf_counter()
        _ = frame.copy()
        c.append(time.perf_counter() - t0)
    log(f"v9_costs_scale{scale}", {"rec": "copy", "res": res, "reps": reps,
        "mean_ms": round(statistics.mean(c) * 1000, 3),
        "p50_ms": round(sorted(c)[len(c) // 2] * 1000, 3),
        "p95_ms": round(sorted(c)[int(0.95 * (len(c) - 1))] * 1000, 3),
        "max_ms": round(max(c) * 1000, 3)})

    fixtures = fixtures_for(perc, cfg)
    # 2. maybe_pass full submit path (copy + PassRequest + queue put), gates open
    fast_cfg = replace(cfg, semantics=replace(cfg.semantics, store_dir=store_dir,
                                              min_interval_s=0.0, max_passes_per_min=100000))
    runner = SemanticsRunner(fast_cfg, "v9cost", SleepVision(0.0, fixtures))
    try:
        ts, ok_count = [], 0
        for i in range(reps):
            deadline = time.time() + 2.0
            while runner._inflight is not None and time.time() < deadline:
                runner.poll(i * 0.001)
                time.sleep(0.0002)
            t0 = time.perf_counter()
            ok = runner.maybe_pass(float(i), perc.semantic_context(float(i)), frame, force=True)
            ts.append(time.perf_counter() - t0)
            ok_count += bool(ok)
        log(f"v9_costs_scale{scale}", {"rec": "maybe_pass_submit_path", "res": res,
            "reps": reps, "ok": ok_count,
            "mean_ms": round(statistics.mean(ts) * 1000, 3),
            "p95_ms": round(sorted(ts)[int(0.95 * (len(ts) - 1))] * 1000, 3),
            "max_ms": round(max(ts) * 1000, 3)})
    finally:
        runner.close()

    # 3. gated paths: inflight-busy (slow detector in flight) and interval-gated
    slow_cfg = replace(cfg, semantics=replace(cfg.semantics, store_dir=store_dir,
                                              min_interval_s=0.0))
    runner = SemanticsRunner(slow_cfg, "v9gate", SleepVision(2.0, fixtures))
    try:
        assert runner.maybe_pass(1.0, perc.semantic_context(1.0), frame, force=True)
        g = []
        for _ in range(reps):
            t0 = time.perf_counter()
            ok = runner.maybe_pass(1.001, perc.semantic_context(1.001), frame, force=True)
            g.append(time.perf_counter() - t0)
            assert ok is False
        log(f"v9_costs_scale{scale}", {"rec": "maybe_pass_gated_inflight", "res": res,
            "reps": reps,
            "mean_us": round(statistics.mean(g) * 1e6, 2),
            "max_us": round(max(g) * 1e6, 2),
            "skipped": dict(runner.skipped)})
        # poll() while busy (result not ready)
        p = []
        for _ in range(reps):
            t0 = time.perf_counter()
            runner.poll(1.002)
            p.append(time.perf_counter() - t0)
        log(f"v9_costs_scale{scale}", {"rec": "poll_empty", "res": res, "reps": reps,
            "mean_us": round(statistics.mean(p) * 1e6, 2), "max_us": round(max(p) * 1e6, 2)})
        # snapshot() while no pass merged yet (store empty) -> None
        s = []
        for _ in range(reps):
            t0 = time.perf_counter()
            runner.snapshot(1.002)
            s.append(time.perf_counter() - t0)
        log(f"v9_costs_scale{scale}", {"rec": "snapshot_no_map", "res": res, "reps": reps,
            "mean_us": round(statistics.mean(s) * 1e6, 2), "max_us": round(max(s) * 1e6, 2)})
    finally:
        runner.close()

    # 4. poll() merge path + snapshot() with a map (2 objects)
    fast_cfg2 = replace(cfg, semantics=replace(cfg.semantics, store_dir=store_dir,
                                               min_interval_s=0.0, max_passes_per_min=100000))
    runner = SemanticsRunner(fast_cfg2, "v9merge", SleepVision(0.02, fixtures))
    try:
        for i in range(6):
            runner.maybe_pass(float(i), perc.semantic_context(float(i)), frame, force=True)
            deadline = time.time() + 2.0
            while time.time() < deadline:
                if runner.poll(float(i) + 0.001) is not None:
                    break
                time.sleep(0.001)
        assert runner.store.passes >= 1, "cost harness needs one merged pass"
        m = []
        for i in range(reps):
            runner.maybe_pass(float(100 + i), perc.semantic_context(float(100 + i)), frame, force=True)
            t0 = time.perf_counter()
            deadline = time.time() + 2.0
            while time.time() < deadline:
                r = runner.poll(float(100 + i) + 0.001)
                if r is not None:
                    break
                time.sleep(0.0005)
            m.append(time.perf_counter() - t0)
        log(f"v9_costs_scale{scale}", {"rec": "poll_merge_path", "res": res, "reps": reps,
            "mean_ms": round(statistics.mean(m) * 1000, 3),
            "p95_ms": round(sorted(m)[int(0.95 * (len(m) - 1))] * 1000, 3),
            "max_ms": round(max(m) * 1000, 3),
            "passes": runner.store.passes})
        ss = []
        for _ in range(reps):
            t0 = time.perf_counter()
            runner.snapshot(1000.0)
            ss.append(time.perf_counter() - t0)
        log(f"v9_costs_scale{scale}", {"rec": "snapshot_with_map", "res": res, "reps": reps,
            "objects": len(runner.store.objs),
            "mean_us": round(statistics.mean(ss) * 1e6, 2),
            "p95_us": round(sorted(ss)[int(0.95 * (len(ss) - 1))] * 1e6, 2)})
    finally:
        runner.close()


# ----------------------------------------------------------------- stale

def run_stale_real(scale: float, L: float, seconds: float) -> None:
    """Real-time: offer once at t>=1, wait L, poll after; L > max_age_s=30."""
    store_dir = tempfile.mkdtemp(prefix="v9store_", dir=HERE)
    cfg = scaled_cfg(scale, store_dir)
    perc, syn, frame = warm(cfg)
    vision = SleepVision(L, fixtures_for(perc, cfg))
    runner = SemanticsRunner(cfg, "v9stale", vision)
    try:
        t0 = time.time()
        t_offer = None
        while time.time() - t0 < seconds:
            t = time.time() - t0
            frame = syn.render()
            perc.process(frame, t)
            merged = runner.poll(t)
            if t_offer is None and t >= 1.0:
                ok = runner.maybe_pass(t, perc.semantic_context(t), frame, force=True)
                t_offer = t
                print(json.dumps({"rec": "stale_real_offer", "t": round(t, 3), "ok": ok}))
            time.sleep(0.05)
        time.sleep(0.2)
        rec = {
            "rec": "stale_real", "scale": scale, "L": L, "max_age_s": cfg.semantics.max_age_s,
            "t_offer": round(t_offer or -1, 3), "seconds": seconds,
            "store_passes": runner.store.passes, "stale_dropped": runner.stale_dropped,
            "worker_completed": runner.worker.completed,
            "snapshot": runner.snapshot(time.time() - t0) is not None,
            "stats": runner.stats(),
            "store_files": sorted(os.listdir(store_dir)) if os.path.isdir(store_dir) else [],
        }
        log(f"v9_stale", rec)
    finally:
        runner.close()


def run_stale_fast(scale: float, reps: int) -> None:
    """Caller-timestamp injection: stub sleeps 0.2 s real; caller clock jumps > max_age_s."""
    fixtures = None
    for i in range(reps):
        store_dir = tempfile.mkdtemp(prefix="v9store_", dir=HERE)
        cfg = scaled_cfg(scale, store_dir)
        perc, syn, frame = warm(cfg)
        if fixtures is None:
            fixtures = fixtures_for(perc, cfg)
        vision = SleepVision(0.2, fixtures)
        runner = SemanticsRunner(cfg, "v9stalefast", vision)
        try:
            ok = runner.maybe_pass(1.0, perc.semantic_context(1.0), frame, force=True)
            deadline = time.time() + 5.0
            while runner.worker.completed == 0 and time.time() < deadline:
                time.sleep(0.005)
            merged = runner.poll(32.0)          # caller clock 31 s after submit
            rec = {
                "rec": "stale_fast", "rep": i, "scale": scale, "ok": ok,
                "worker_completed": runner.worker.completed,
                "poll_at_t": 32.0, "age_s": 31.0,
                "max_age_s": cfg.semantics.max_age_s,
                "merged": merged is not None, "store_passes": runner.store.passes,
                "stale_dropped": runner.stale_dropped,
                "snapshot_not_none": runner.snapshot(32.0) is not None,
                "stats": runner.stats(),
                "store_files": sorted(os.listdir(store_dir)) if os.path.isdir(store_dir) else [],
            }
            log(f"v9_stale_fast_scale{scale}", rec)
        finally:
            runner.close()


def run_cooldown() -> None:
    store_dir = tempfile.mkdtemp(prefix="v9store_", dir=HERE)
    cfg = scaled_cfg(1.0, store_dir)
    perc, syn, frame = warm(cfg)
    cfg2 = replace(cfg, semantics=replace(cfg.semantics, store_dir=store_dir,
                                          min_interval_s=0.0, failure_cooldown_s=5.0))
    runner = SemanticsRunner(cfg2, "v9cool", BoomVision())
    try:
        assert runner.maybe_pass(1.0, perc.semantic_context(1.0), frame, force=True)
        deadline = time.time() + 3.0
        while runner.worker.errors == 0 and time.time() < deadline:
            time.sleep(0.005)
        time.sleep(0.05)
        runner.poll(1.01)                       # consume error result -> cooldown starts
        before = dict(runner.skipped), runner.stale_dropped, runner.store.passes
        blocked = runner.maybe_pass(2.0, perc.semantic_context(2.0), frame, force=True)
        after = dict(runner.skipped), runner.stale_dropped, runner.store.passes
        allowed_after = runner.maybe_pass(100.0, perc.semantic_context(100.0), frame, force=True)
        log("v9_cooldown", {
            "rec": "cooldown", "blocked_during_cooldown": blocked is False,
            "skipped_before": before[0], "skipped_after": after[0],
            "counters_unchanged": before == after,
            "stale_dropped": runner.stale_dropped, "store_passes": runner.store.passes,
            "allowed_after_expiry": bool(allowed_after),
            "errors": runner.worker.errors,
            "cooldown_until": round(runner.fail_cooldown_until, 3),
        })
    finally:
        runner.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("loop")
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--seconds", type=float, default=24.0)
    p.add_argument("--hz", type=float, default=15.0)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--offer", default="eager", choices=["eager", "single", "paced"])
    p.add_argument("--L", type=float, nargs="*", default=None)
    p = sub.add_parser("costs")
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--reps", type=int, default=300)
    p = sub.add_parser("stale")
    p.add_argument("--scale", type=float, default=0.5)
    p.add_argument("--L", type=float, default=31.0)
    p.add_argument("--seconds", type=float, default=34.0)
    p = sub.add_parser("stale-fast")
    p.add_argument("--scale", type=float, default=0.5)
    p.add_argument("--repeats", type=int, default=3)
    sub.add_parser("cooldown")
    args = ap.parse_args()

    if args.cmd == "loop":
        Ls = [None] + (args.L if args.L else [0.05, 0.5, 2.0, 5.0])
        for L in Ls:
            for rep in range(args.repeats):
                run_loop(args.scale, args.seconds, args.hz, L, args.offer,
                         f"scale{args.scale}_L{L}", rep)
    elif args.cmd == "costs":
        run_costs(args.scale, args.reps)
    elif args.cmd == "stale":
        run_stale_real(args.scale, args.L, args.seconds)
    elif args.cmd == "stale-fast":
        run_stale_fast(args.scale, args.repeats)
    elif args.cmd == "cooldown":
        run_cooldown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
