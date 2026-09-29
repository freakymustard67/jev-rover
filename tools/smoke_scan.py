"""One sweep end to end: command -> chunks -> match -> confirm. No rover motion.

Modes
  synthetic  replay the in-repo room + sensor model (tools/sim/tof_sim.py);
             no hardware, ground truth known - use it to measure the matcher
  udp        talk to a real rover with spec-v1 scan commands, or to the
             in-process fake rover (--loopback) to prove the wire path

Examples
  .venv/bin/python tools/smoke_scan.py --mode synthetic --pose 2.5,1.8,20 --step-deg 6
  .venv/bin/python tools/smoke_scan.py --mode synthetic --pose 2.5,1.8,20 \
      --motion 0.45,0 --desmear                 # the driving case
  .venv/bin/python tools/smoke_scan.py --mode udp --loopback
  .venv/bin/python tools/smoke_scan.py --mode udp --esp32 192.168.4.1 \
      --prior 3.0,1.8,0 --step-deg 6

The tool never commands motion (v=w=0 in every datagram).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "tools", ROOT / "tools" / "sim"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import numpy as np

import tof_sim as sim
from config import RoomConfig, SweepMatchConfig
from link import Cmd, UDPLink, encode_scan_command, scan_beam_count
from scan_payload import ScanAssembly
from scene import SemanticObject
from sweep import RectWorld, ScanBeams, SweepMatcher, beams_from_assembly, confirm_objects


def _objects_from_args(specs: list[str]) -> list[SemanticObject]:
    out = []
    for i, spec in enumerate(specs or []):
        parts = spec.split(",")
        if len(parts) < 2:
            raise SystemExit(f"--object expects x,y[,label], got {spec!r}")
        x, y = float(parts[0]), float(parts[1])
        label = parts[2] if len(parts) > 2 else f"object{i}"
        out.append(SemanticObject(id=f"obj_{i:04d}", label=label, x=x, y=y, confidence=0.9))
    return out


def _report(result, objects, beams, truth=None) -> int:
    if result is None:
        print("[scan] matcher refused: too few valid beams")
        return 1
    body = {"match": result.as_dict()}
    if truth is not None:
        body["truth"] = {"x": round(float(truth[0]), 3), "y": round(float(truth[1]), 3),
                         "yaw_deg": round(float(truth[2]), 1)}
        body["error_m"] = round(float(np.hypot(result.x - truth[0], result.y - truth[1])), 3)
    if objects:
        confirmations = confirm_objects(beams, (result.x, result.y, result.yaw_deg), objects)
        body["confirmations"] = [{"object_id": c.object_id, "result": c.result,
                                  "range_err_m": c.range_err_m} for c in confirmations]
        for c in confirmations:
            print(f"[scan]   {c.object_id}: {c.result} (range_err {c.range_err_m} m)")
    print(f"[scan] match ({result.mode}{', desmeared' if result.desmeared else ''}): "
          f"({result.x:.2f},{result.y:.2f},{result.yaw_deg:+.1f}deg) "
          f"conf={result.confidence:.2f} rmse={result.rmse_m:.2f} m "
          f"beams={result.n_valid}/{result.n_total}")
    if "error_m" in body:
        print(f"[scan] truth ({truth[0]:.2f},{truth[1]:.2f},{truth[2]:+.1f}deg) -> "
              f"error {body['error_m']:.3f} m")
    print(json.dumps(body))
    return 0


def run_synthetic(args, cfg: RoomConfig) -> int:
    truth = np.array([float(v) for v in args.pose.split(",")])
    v, w = (float(z) for z in args.motion.split(","))
    step = args.step_deg or cfg.sweep.sensor.step_deg
    bearings = np.arange(cfg.sweep.sensor.start_deg, cfg.sweep.sensor.end_deg + 1e-9, step)
    rng = np.random.default_rng(args.seed)
    ranges = sim.simulate_sweep(truth, bearings, rng, v=v, w_deg_s=w, sweep_s=args.sweep_s)
    beams = ScanBeams(bearings_deg=np.asarray(bearings, float), ranges_m=ranges,
                      t_s=args.sweep_s * np.arange(len(bearings)) / max(1, len(bearings) - 1))
    world = RectWorld(sim.OBST, max_range_m=sim.MAX_RANGE)
    matcher = SweepMatcher(world, SweepMatchConfig(), desmear=args.desmear)
    prior = None
    if args.prior:
        prior = tuple(float(q) for q in args.prior.split(","))
    elif not args.global_search:
        prior = (truth[0] + args.prior_noise, truth[1] - args.prior_noise, truth[2] + 2.0)
    result = matcher.match(beams, prior=prior, motion=(v, w) if (v or w) else None)
    print(f"[scan] synthetic: {beams.n_valid}/{beams.n_total} beams valid, "
          f"motion=({v},{w}) desmear={'on' if args.desmear else 'off'}")
    return _report(result, _objects_from_args(args.object), beams, truth=truth)


def run_udp(args, cfg: RoomConfig) -> int:
    link = None
    rover = None
    try:
        if args.loopback:
            from fake_rover import FakeRover
            truth = np.array([float(q) for q in args.pose.split(",")])
            v, w = (float(z) for z in args.motion.split(","))
            rover = FakeRover(truth, port=0, sweep_s=args.sweep_s).start()
            rover.motion = (v, w)
            host, port = "127.0.0.1", rover.port
            print(f"[scan] loopback fake rover at {host}:{port} truth={tuple(truth)} motion=({v},{w})")
        else:
            host, port = args.esp32, args.port
        link = UDPLink(host, port, local_port=0)

        scan_id = args.scan_id
        step = args.step_deg or cfg.sweep.sensor.step_deg
        n_beams = scan_beam_count(cfg.sweep.sensor.start_deg, cfg.sweep.sensor.end_deg, step)
        print(f"[scan] requesting scan id={scan_id} step={step}deg ({n_beams} beams)")

        assembly: ScanAssembly | None = None
        t = 0.0
        last_send = -1.0
        deadline = time.time() + args.timeout
        while time.time() < deadline and assembly is None:
            t += 0.05
            if t - last_send >= 0.05:          # 20 Hz, until the echo confirms
                seq, state = link.scan_progress()
                if seq != scan_id or state != "scanning":
                    link.send(Cmd(0.0, 0.0, source="bench", note="scan",
                                  scan=encode_scan_command(scan_id, step_deg=int(step))), t)
                last_send = t
            link.telemetry(t)
            scans = link.take_scans()
            if scans:
                assembly = scans[0]
            time.sleep(0.01)
        if assembly is None:
            print("[scan] no scan assembly arrived (timeout) - is the rover running "
                  "the scan firmware? see firmware/README.md", file=sys.stderr)
            return 1
        print(f"[scan] assembly id={assembly.scan_id} complete={assembly.complete} "
              f"missing={assembly.missing} quality={assembly.quality:.2f}")
        beams = beams_from_assembly(assembly, max_range_m=cfg.sweep.sensor.max_range_m)
        # No ground truth in udp mode. The loopback fake replays the simulated
        # room, so RectWorld is honest here; a real room should pass a grid
        # snapshot (M4 wiring) - this is the bench, not the runner.
        world = RectWorld(sim.OBST, max_range_m=sim.MAX_RANGE)
        matcher = SweepMatcher(world, SweepMatchConfig(), desmear=cfg.sweep.desmear)
        prior = tuple(float(q) for q in args.prior.split(",")) if args.prior else None
        result = matcher.match(beams, prior=prior, motion=None)
        print(f"[scan] link: scan_rx={link.stats()['scan_rx']} "
              f"errors={link.stats()['scan_rx_errors']} "
              f"watchdog_ok={link.telemetry(t).watchdog_ok} scan_state={link.scan_state}")
        return _report(result, _objects_from_args(args.object), beams)
    finally:
        if link is not None:
            link.close()
        if rover is not None:
            rover.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["synthetic", "udp"], default="synthetic")
    p.add_argument("--config", default="config/room.synthetic.json")
    p.add_argument("--pose", default="3.0,1.8,0", help="synthetic/loopback truth x,y,yaw_deg")
    p.add_argument("--motion", default="0,0", help="commanded v,w during the sweep")
    p.add_argument("--desmear", action="store_true", help="use the motion model")
    p.add_argument("--prior", default=None, help="prior x,y,yaw_deg (tracking match)")
    p.add_argument("--prior-noise", type=float, default=0.05,
                   help="synthetic prior offset when --prior is not given")
    p.add_argument("--global-search", action="store_true", help="no prior at all")
    p.add_argument("--step-deg", type=float, default=None)
    p.add_argument("--sweep-s", type=float, default=1.0)
    p.add_argument("--objects", default="", help="semicolon-separated x,y[,label] to confirm")
    p.add_argument("--object", action="append", default=[], help="x,y[,label] (repeatable)")
    p.add_argument("--esp32", default="192.168.4.1")
    p.add_argument("--port", type=int, default=4210)
    p.add_argument("--loopback", action="store_true", help="udp mode against the fake rover")
    p.add_argument("--scan-id", type=int, default=1)
    p.add_argument("--timeout", type=float, default=20.0)
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args(argv)
    if args.objects:
        args.object.extend(args.objects.split(";"))

    cfg = RoomConfig.load(args.config)
    if args.mode == "synthetic":
        return run_synthetic(args, cfg)
    return run_udp(args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
