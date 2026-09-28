"""Natural-language missions, without an LLM in the control loop.

A mission like "patrol the kitchen and the hall" is a routing problem over a
*known* set of routes and waypoints. That is a Choice question with the route
names as options, plus a Noul for feasibility - typed, ~100 ms, and it cannot
invent a waypoint that does not exist.

An LLM belongs here only if you want open-ended dialogue; even then it should
emit a route name, not motor commands. Keep it off the control path.

Usage:
    .venv/bin/python mission.py --config config/room.json "patrol the kitchen and the hall"
"""
from __future__ import annotations

import argparse

from typesafe_sdk import Choice, Noul, TypeSafeClient

from config import RoomConfig

MODEL = "jev-latest"


def route_questions(cfg: RoomConfig) -> dict:
    routes = cfg.routes or {"patrol_all": list(cfg.waypoints)}
    described = {
        name: {"waypoints": [f"{wp} at {cfg.waypoint(wp)[:2]}" for wp in wps]}
        for name, wps in routes.items()
    }
    return {
        "route": Choice(
            instructions={
                "question": "Which of the available routes does the instruction describe?",
                "instruction": "`request.text`",
                "policy": "Only choose a route from the list. If no route matches, choose no_match.",
            },
            criteria={**described,
                      "no_match": {"what": "The instruction asks for something no route covers"}},
        ),
        "feasible": Noul(
            instructions={
                "question": "Can the rover carry out this instruction with the available routes?",
                "compare": ["`request.text`", "`available_routes`"],
                "focus": "Answer no if the instruction needs a waypoint, behavior, or capability "
                         "that does not exist in the list.",
            },
        ),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("text")
    p.add_argument("--config", default="config/room.json")
    args = p.parse_args(argv)

    cfg = RoomConfig.load(args.config)
    state = {
        "request": {"text": args.text},
        "available_routes": {name: list(wps) for name, wps in cfg.routes.items()},
        "waypoints": {name: wp[:2] for name, wp in cfg.waypoints.items()},
    }
    questions = route_questions(cfg)
    with TypeSafeClient() as client:
        r = client.system_one(state=state, model=MODEL, questions=questions)

    route = r.answers["route"]
    feasible = r.answers["feasible"]
    print(f"route:    {route.choice}  (confidence {route.confidence:.2f})")
    print("  probabilities:", {k: round(v, 3) for k, v in route.probabilities.items()})
    print(f"feasible: {feasible.noul:.3f}")
    if route.choice == "no_match" or feasible.noul < 0.5:
        print("-> not executable as specified; refine the route list or the instruction")
        return 2
    if route.confidence < 0.6:
        print("-> low confidence: confirm with the operator before driving")
        return 3
    print(f"-> run: python run.py --config {args.config} --mission patrol --route {route.choice}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
