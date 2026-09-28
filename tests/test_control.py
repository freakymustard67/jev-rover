"""Planner and executor: signs, detours, and the reflex that always wins."""
import math

import numpy as np

from config import RoomConfig
from control import Executor, Planner
from link import Cmd
from perception import OccupancyGrid
from scene import GoalInfo, Hardware, Pose, Scene


def _grid_with_wall():
    grid = OccupancyGrid(4.0, 2.0, 0.05)
    cx = (np.arange(grid.w) + 0.5) * grid.cell_m
    cy = (np.arange(grid.h) + 0.5) * grid.cell_m
    xx, yy = np.meshgrid(cx, cy)
    wall_ixs = (39, 40, 41)                                     # wall column around x=2.0 m
    wall = np.array([(cx[ix], cy[iy]) for ix in wall_ixs for iy in range(10, 30)])
    wall_set = {(round(x, 4), round(y, 4)) for x, y in wall}
    free = np.array([(x, y) for x, y in zip(xx.ravel(), yy.ravel())
                     if (round(x, 4), round(y, 4)) not in wall_set])
    grid.update(free, wall, t=0.0)
    return grid


def test_astar_routes_around_a_wall():
    grid = _grid_with_wall()
    planner = Planner(grid, 0.15, RoomConfig())
    path, through_unknown = planner.plan((0.2, 1.0), (3.8, 1.0), 0.0)
    assert path, "no path found"
    assert through_unknown is False
    for x, y in path:
        assert not (1.9 <= x <= 2.1 and 0.4 <= y <= 1.6), f"path crosses the wall at {(x, y)}"
    # the detour must be longer than the straight line
    length = sum(math.dist(path[i], path[i + 1]) for i in range(len(path) - 1))
    assert length > 3.7


def _scene_left_goal() -> Scene:
    scene = Scene()
    scene.pose = Pose(0.0, 0.0, 0.0)
    scene.goal = GoalInfo(x=1.0, y=1.0)
    return scene


def test_pure_pursuit_turns_left_for_a_left_goal():
    ex = Executor(RoomConfig(), OccupancyGrid(4.0, 4.0, 0.05))
    ex.path = [(1.0, 1.0)]
    cmd = ex._follow(_scene_left_goal())
    assert cmd.w_deg_s > 0, cmd


def test_pure_pursuit_turns_right_for_a_right_goal():
    ex = Executor(RoomConfig(), OccupancyGrid(4.0, 4.0, 0.05))
    ex.path = [(1.0, -1.0)]
    scene = _scene_left_goal()
    scene.goal = GoalInfo(x=1.0, y=-1.0)
    cmd = ex._follow(scene)
    assert cmd.w_deg_s < 0, cmd


def test_reflex_overrides_any_command():
    ex = Executor(RoomConfig(), OccupancyGrid(4.0, 4.0, 0.05))
    scene = Scene()
    scene.hardware = Hardware(dist_front_m=0.10, telemetry_age_s=0.05)
    out = ex._reflex(scene, Cmd(0.5, 10.0))
    assert out.v_mps == 0.0
    assert "tof_stop" in out.source
    assert scene.dynamics.hard_reflex


def test_map_brake_for_close_obstacle_ahead():
    ex = Executor(RoomConfig(), OccupancyGrid(4.0, 4.0, 0.05))
    scene = Scene()
    scene.nearest_m = 0.2
    scene.nearest_bearing_deg = 5.0
    out = ex._reflex(scene, Cmd(0.5, 10.0))
    assert out.v_mps == 0.0 and "map_brake" in out.source


def test_require_telemetry_stops_blind():
    ex = Executor(RoomConfig(), OccupancyGrid(4.0, 4.0, 0.05), require_telemetry=True)
    scene = Scene()  # no telemetry at all
    out = ex._reflex(scene, Cmd(0.5, 0.0))
    assert out.v_mps == 0.0 and "no_telemetry" in out.source


def test_back_and_turn_reverses():
    ex = Executor(RoomConfig(), OccupancyGrid(4.0, 4.0, 0.05))
    ex.mode = "back_and_turn"
    ex.mode_t = 0.0
    scene = Scene(goal=GoalInfo(x=1.0, y=0.0))
    scene.pose = Pose(0.0, 0.0, 0.0)
    judg = {"source": "jev", "age_s": 0.1, "maneuver": "back_and_turn", "risk": 1.0,
            "probabilities": {}}
    cmd = ex.update(scene, judg, 0.0, 0.05)
    assert cmd.v_mps < 0.0, cmd
