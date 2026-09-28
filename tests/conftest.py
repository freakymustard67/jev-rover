import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pytest

from config import RoomConfig
from scene import ObstacleTrack, build_scene, summarize_rays


@pytest.fixture
def synth_cfg() -> RoomConfig:
    return RoomConfig.load(ROOT / "config" / "room.synthetic.json")


def make_scene(nearest: float = 5.0, blocked: bool = False, occlusion: float = 0.0,
               no_progress: float = 0.0, moving: float | None = None,
               goal_bearing: float = 0.0, t: float = 1.0):
    """A fully-observed open room scene, with single knobs to make it interesting."""
    angles = np.arange(-90.0, 90.01, 15.0)
    ranges = [5.0] * len(angles)
    scene = build_scene(summarize_rays(angles.tolist(), ranges), t=t)
    scene.nearest_m = nearest
    scene.nearest_bearing_deg = 0.0
    if blocked:
        scene.sectors[3].blocked = True
    scene.quality.occlusion_risk = occlusion
    scene.dynamics.no_progress_s = no_progress
    scene.goal.bearing_deg = goal_bearing
    if moving is not None:
        scene.tracks.append(ObstacleTrack(
            id=1, x=1.0, y=0.0, vx=0.5, vy=0.0, radius_m=0.2, area_m2=0.1,
            bearing_deg=0.0, range_m=moving, kind="moving", age_s=1.0, seen_s_ago=0.0))
    return scene
