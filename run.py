"""Episode loop: camera -> scene -> Jev -> commands -> rover, with metrics.

Examples (no hardware, no API key needed):
    .venv/bin/python run.py --config config/room.synthetic.json --source synthetic \
        --mission patrol --seconds 60 --video runs/synth.mp4

Real camera, no Jev (baseline ablation), driving the mock link:
    .venv/bin/python run.py --config config/room.json --source camera \
        --mission goto --waypoint dock --seconds 60

Real rover (sends commands over UDP):
    .venv/bin/python run.py --config config/room.json --link udp --arm \
        --esp32 192.168.4.1 --mission goto --waypoint kitchen
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path

import numpy as np

from config import RoomConfig
from control import Executor
from link import Cmd, DryRunLink, MockLink, UDPLink
from perception import Camera, Perception
from semantics import SemanticsRunner, approach_point, build_vision, resolve_destination
from synthetic import SyntheticRoom
from tactics import DEFAULT, Tactician
from viz import Renderer


class GoalManager:
    """Single goal, patrol route, or follow-target; writes scene.goal in place."""

    def __init__(self, cfg: RoomConfig, mission: str, waypoint: str | None = None,
                 route: str | None = None, goal_tol_m: float = 0.30,
                 instruction: str | None = None):
        self.cfg = cfg
        self.mission = mission
        self.goal_tol_m = goal_tol_m
        self.instruction = instruction
        self.queue: list[str] = []
        if mission == "goto":
            if not waypoint:
                raise SystemExit("--mission goto needs --waypoint NAME")
            self.queue = [waypoint]
        elif mission == "patrol":
            if route:
                if route not in cfg.routes:
                    raise SystemExit(f"unknown route '{route}'; have {list(cfg.routes)}")
                self.queue = list(cfg.routes[route])
            else:
                self.queue = list(cfg.waypoints)
            if not self.queue:
                raise SystemExit("no waypoints configured for patrol")
        self.index = 0
        self.goal_set_t = 0.0
        self.reached = 0

    @property
    def expected(self) -> int | None:
        return len(self.queue) if self.mission == "goto" else None

    def update(self, scene, t: float) -> None:
        g = scene.goal
        if self.mission == "track":
            tr = scene.target
            if tr.visible and tr.x is not None and tr.y is not None:
                # Stand off behind the target along the target->rover line.
                dx, dy = scene.pose.x - tr.x, scene.pose.y - tr.y
                d = math.hypot(dx, dy) or 1.0
                sx = self.cfg.target.standoff_m
                g.type, g.name = "track", "target"
                g.x = round(tr.x + dx / d * sx, 2)
                g.y = round(tr.y + dy / d * sx, 2)
                g.yaw_deg = None
                self.goal_set_t = self.goal_set_t or t
            g.progress_s = round(t - self.goal_set_t, 1) if self.goal_set_t else 0.0
        else:
            if self.index >= len(self.queue):
                self.index = 0 if self.mission == "patrol" else len(self.queue)
            if self.index >= len(self.queue):
                g.type, g.name, g.x, g.y = "done", None, None, None
                return
            name = self.queue[self.index]
            x, y, yaw = self.cfg.waypoint(name)
            range_m = math.hypot(x - scene.pose.x, y - scene.pose.y)
            if range_m < self.goal_tol_m:
                self.reached += 1
                self.index += 1
                self.goal_set_t = t
                if self.index >= len(self.queue):
                    if self.mission == "patrol":
                        self.index = 0
                    else:
                        g.type, g.name, g.x, g.y = "done", None, None, None
                        return
                name = self.queue[self.index]
                x, y, yaw = self.cfg.waypoint(name)
            g.type, g.name = self.mission, name
            g.index = self.index
            g.x, g.y, g.yaw_deg = x, y, yaw
            from scene import rover_frame
            _, _, bearing, rng = rover_frame(x - scene.pose.x, y - scene.pose.y, scene.pose.yaw_deg)
            g.bearing_deg, g.range_m = round(bearing, 1), round(rng, 2)
            g.progress_s = round(t - self.goal_set_t, 1)


class Metrics:
    def __init__(self):
        self.frames = 0
        self.distance_m = 0.0
        self.min_obstacle_ahead_m: float | None = None
        self.reflex_events = 0
        self.reflex_reasons: dict[str, int] = {}
        self.watchdog_bad_s = 0.0
        self.no_telemetry_s = 0.0
        self.stuck_s = 0.0
        self.lost_pose_s = 0.0
        self.last_reflex = ""

    def frame(self, scene, cmd: Cmd, dt: float) -> None:
        self.frames += 1
        self.distance_m += abs(scene.twist.v_mps) * dt
        if scene.nearest_m is not None and scene.nearest_bearing_deg is not None \
                and abs(scene.nearest_bearing_deg) < 35.0:
            v = scene.nearest_m
            self.min_obstacle_ahead_m = v if self.min_obstacle_ahead_m is None \
                else min(self.min_obstacle_ahead_m, v)
        reason = scene.dynamics.reflex_reason
        if reason and reason != self.last_reflex:
            self.reflex_events += 1
            self.reflex_reasons[reason] = self.reflex_reasons.get(reason, 0) + 1
        self.last_reflex = reason
        if scene.dynamics.no_progress_s > 0.5:
            self.stuck_s += dt
        if scene.quality.pose_source == "lost":
            self.lost_pose_s += dt
        hw = scene.hardware
        if hw.telemetry_age_s is not None and not hw.watchdog_ok:
            self.watchdog_bad_s += dt
        if hw.telemetry_age_s is None:
            self.no_telemetry_s += dt

    def summary(self, t_total: float, goals_reached: int, goals_expected: int | None) -> dict:
        return {
            "duration_s": round(t_total, 1),
            "frames": self.frames,
            "distance_m": round(self.distance_m, 2),
            "min_obstacle_ahead_m": self.min_obstacle_ahead_m,
            "collision_reflex_events": self.reflex_events,
            "reflex_reasons": self.reflex_reasons,
            "stuck_s": round(self.stuck_s, 1),
            "stuck_fraction": round(self.stuck_s / t_total, 3) if t_total > 0 else None,
            "watchdog_bad_s": round(self.watchdog_bad_s, 1),
            "no_telemetry_s": round(self.no_telemetry_s, 1),
            "lost_pose_s": round(self.lost_pose_s, 1),
            "goals_reached": goals_reached,
            "goals_expected": goals_expected,
        }


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", default="config/room.json")
    p.add_argument("--source", choices=["camera", "synthetic"], default="camera")
    p.add_argument("--camera", default=None, help="override camera.source")
    p.add_argument("--mission", choices=["goto", "patrol", "track"], default="goto")
    p.add_argument("--waypoint", default=None)
    p.add_argument("--route", default=None)
    p.add_argument("--instruction", default=None, help="natural-language mission text (logged)")
    p.add_argument("--goal-tol", type=float, default=0.30)
    p.add_argument("--seconds", type=float, default=120.0)
    p.add_argument("--no-jev", action="store_true")
    p.add_argument("--jev-hz", type=float, default=2.0)
    p.add_argument("--budget", type=int, default=400)
    p.add_argument("--link", choices=["mock", "udp"], default="mock")
    p.add_argument("--arm", action="store_true", help="allow real UDP commands to the rover")
    p.add_argument("--esp32", default="192.168.4.1")
    p.add_argument("--port", type=int, default=4210)
    p.add_argument("--local-port", type=int, default=4211)
    p.add_argument("--perception-hz", type=float, default=15.0)
    p.add_argument("--control-hz", type=float, default=20.0)
    p.add_argument("--semantics", choices=["off", "fake"], default="off",
                   help="off follows config.semantics.enabled; fake forces the fixture adapter (M1)")
    p.add_argument("--semantics-once", action="store_true",
                   help="single semantic pass at mission start, no further triggers (M1 default)")
    p.add_argument("--find", default=None, metavar="LABEL",
                   help="resolve a natural-language destination from the semantic map and print it")
    p.add_argument("--video", default=None)
    p.add_argument("--show", action="store_true")
    p.add_argument("--log", default=None, help="write a JSONL scene log here")
    p.add_argument("--trace", action="store_true")
    p.add_argument("--seed", type=int, default=0)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    cfg = RoomConfig.load(args.config)
    if args.camera:
        cfg.camera.source = args.camera
    os.makedirs("runs", exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    # ---- sources and sinks
    if args.source == "synthetic":
        syn = SyntheticRoom(cfg, seed=args.seed)
        cam = None
    else:
        syn = None
        cam = Camera(cfg.camera)

    if args.link == "udp":
        if args.arm:
            link = UDPLink(args.esp32, args.port, args.local_port,
                           cfg.rover.command_ttl_ms)
            print(f"[link] ARMED: sending commands to {args.esp32}:{args.port}")
        else:
            link = DryRunLink()
            print("[link] udp selected but not --arm: commands are logged, not sent")
    else:
        link = MockLink(cfg.rover.command_ttl_ms)

    perception = Perception(cfg)
    executor = Executor(cfg, perception.grid, require_telemetry=isinstance(link, UDPLink))
    goals = GoalManager(cfg, args.mission, args.waypoint, args.route, args.goal_tol,
                        args.instruction)
    tact = None
    if not args.no_jev:
        tact = Tactician(hz=args.jev_hz, budget=args.budget)

    renderer = Renderer(cfg, perception.grid, show=args.show, video_path=args.video)
    metrics = Metrics()

    runner = None
    if args.semantics == "fake" or args.find or cfg.semantics.enabled:
        vision = build_vision(cfg.semantics, perception.homography)
        runner = SemanticsRunner(cfg, cfg.name, vision)
        n_fixtures = len(getattr(vision, "fixtures", []))
        print(f"[semantics] enabled: model={vision.name} fixtures={n_fixtures} "
              f"(M1: one pass at mission start, no triggers)")
    semantics_fired = False
    find_done = False
    log_file = open(args.log, "a") if args.log else None

    # ---- loop
    t0 = time.time()
    next_perc = 0.0
    loop_t_prev: float | None = None
    last_cmd = Cmd(0.0, 0.0, "init")
    judg = dict(DEFAULT, source="none")
    scene = None
    frame_ready = False
    consecutive_read_failures = 0
    try:
        while True:
            now = time.time()
            t = now - t0
            if args.seconds and t >= args.seconds:
                break
            dt = 1.0 / args.control_hz if loop_t_prev is None \
                else max(1e-3, min(0.25, t - loop_t_prev))
            loop_t_prev = t

            if syn is not None:
                syn.step(last_cmd.v_mps, last_cmd.w_deg_s, dt)
                frame, frame_age = syn.render(), 0.0
                ok = True
            else:
                ok, frame, frame_age = cam.read()
                if not ok:
                    consecutive_read_failures += 1
                    if consecutive_read_failures > 30:
                        print("[fatal] camera stopped delivering frames")
                        break
                    time.sleep(0.05)
                    continue
                consecutive_read_failures = 0

            if t >= next_perc:
                scene = perception.process(frame, t, last_cmd.v_mps, last_cmd.w_deg_s, frame_age)
                goals.update(scene, t)
                scene.hardware = link.telemetry(t)
                if runner is not None:
                    runner.poll(t)
                    if not semantics_fired and t >= 1.0:
                        # Mission-start trigger (proposal §8); M3 adds the scheduler.
                        if runner.maybe_pass(t, perception.semantic_context(t), frame,
                                             kind="full", force=True):
                            semantics_fired = True
                    scene.semantics = runner.snapshot(t)
                    if args.find and not find_done and scene.semantics is not None:
                        dest = resolve_destination(args.find, scene.semantics, cfg=cfg.semantics)
                        if dest is not None:
                            ax, ay = approach_point(dest, (scene.pose.x, scene.pose.y),
                                                    cfg.destination.standoff_m)
                            runner.set_destination(dest)
                            print(f"[find] '{args.find}' -> {dest.label} at "
                                  f"({dest.x:.2f},{dest.y:.2f}) m conf={dest.confidence:.2f} "
                                  f"object={dest.object_id}; approach ({ax:.2f},{ay:.2f}) "
                                  f"standoff={cfg.destination.standoff_m} m")
                        else:
                            print(f"[find] '{args.find}': no destination resolved "
                                  f"(passes={scene.semantics.passes})")
                        find_done = True
                judg = tact.read(t) if tact is not None else dict(DEFAULT, source="none")
                next_perc = t + 1.0 / max(1.0, args.perception_hz)
                frame_ready = True

            if scene is None:
                continue

            cmd = executor.update(scene, judg, t, dt)
            if frame_ready and tact is not None:
                # Offer only after the executor has planned, so the state Jev
                # sees contains the planner's intent (path_valid, path_bearing).
                tact.offer(scene, t)
                if log_file:
                    log_file.write(json.dumps({"t": round(t, 3), "scene": scene.to_dict(),
                                               "judg": judg}) + "\n")
                frame_ready = False
            link.send(cmd, t)
            metrics.frame(scene, cmd, dt)

            if args.trace:
                print(f"t={t:6.2f} pose=({scene.pose.x:5.2f},{scene.pose.y:5.2f},"
                              f"{scene.pose.yaw_deg:+6.1f}) src={scene.quality.pose_source[:4]} "
                              f"near={scene.nearest_m} clear={scene.clear_ahead_m} "
                              f"occ={scene.quality.occlusion_risk} "
                              f"jev={judg.get('maneuver')}({judg.get('source')},"
                              f"{judg.get('age_s')}) risk={judg.get('risk')} "
                              f"cmd=({cmd.v_mps:+.2f},{cmd.w_deg_s:+.0f},{cmd.source})")
                print(trace_line)

            if args.video or args.show:
                canvas = renderer.draw(frame, scene, judg, cmd, executor.path)
                renderer.write(canvas)
            last_cmd = cmd

            # pace the control loop
            target_next = now + 1.0 / max(1.0, args.control_hz)
            sleep = target_next - time.time()
            if sleep > 0:
                time.sleep(sleep)
    except KeyboardInterrupt:
        print("\n[stop] interrupted")
    finally:
        for _ in range(3):
            link.send(Cmd(0.0, 0.0, "shutdown"), time.time() - t0)
            time.sleep(0.01)
        if cam is not None:
            cam.release()
        if tact is not None:
            tact.close()
        if runner is not None:
            runner.close()
        renderer.close()
        if log_file:
            log_file.close()

    t_total = time.time() - t0
    summary = {
        "config": args.config,
        "source": args.source,
        "mission": args.mission,
        "jevs": not args.no_jev,
        "link": type(link).__name__,
        "metrics": metrics.summary(t_total, goals.reached, goals.expected),
        "control": executor.stats,
        "jev": tact.stats() if tact else None,
        "semantics": runner.stats() if runner else None,
        "transport": link.stats(),
    }
    out = Path("runs") / f"summary_{stamp}.json"
    out.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    print(f"\nsummary written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
