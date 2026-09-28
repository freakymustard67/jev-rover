"""End-to-end on synthetic frames: pixels -> pose, obstacles, sectors, tracks.

This is the flagship test: the same code path that will run on the real camera
runs here against a known-ground-truth room, so geometry and sign errors cannot
hide behind "the hardware is weird".
"""
import math

from perception import Perception
from scene import Pose, wrap_deg
from synthetic import SyntheticRoom


def _warm(perc: Perception, syn: SyntheticRoom, n: int = 8, dt: float = 1.0 / 15.0):
    """Run n frames so the occupancy grid has observations to work with."""
    scene = None
    for i in range(n):
        scene = perc.process(syn.render(), i * dt)
    return scene


def test_tag_pose_matches_ground_truth(synth_cfg):
    syn = SyntheticRoom(synth_cfg)
    syn.rover = Pose(2.0, 1.0, 37.0)
    perc = Perception(synth_cfg)
    scene = perc.process(syn.render(), 0.0)
    assert scene.quality.tag_visible
    assert scene.quality.pose_source == "tag"
    assert abs(scene.pose.x - 2.0) < 0.04, scene.pose
    assert abs(scene.pose.y - 1.0) < 0.04, scene.pose
    assert abs(wrap_deg(scene.pose.yaw_deg - 37.0)) < 4.0, scene.pose


def test_tag_pose_round_trip_angles(synth_cfg):
    for yaw in (0.0, 90.0, 180.0, -90.0, 123.0):
        syn = SyntheticRoom(synth_cfg)
        syn.rover = Pose(3.0, 2.0, yaw)
        perc = Perception(synth_cfg)          # fresh estimator: no blending history
        scene = perc.process(syn.render(), 0.0)
        assert scene.quality.tag_visible, f"tag lost at yaw {yaw}"
        assert abs(wrap_deg(scene.pose.yaw_deg - yaw)) < 5.0, (yaw, scene.pose)


def test_pose_converges_after_a_sudden_turn(synth_cfg):
    syn = SyntheticRoom(synth_cfg)
    syn.rover = Pose(3.0, 2.0, 0.0)
    perc = Perception(synth_cfg)
    for i in range(5):
        perc.process(syn.render(), i / 15.0)
    syn.rover = Pose(3.0, 2.0, 90.0)          # a fast in-place spin, sampled badly
    scene = None
    for i in range(6, 12):
        scene = perc.process(syn.render(), i / 15.0)
    assert abs(wrap_deg(scene.pose.yaw_deg - 90.0)) < 5.0, scene.pose


def test_table_ahead_is_blocked_and_free_behind(synth_cfg):
    syn = SyntheticRoom(synth_cfg)
    # Controlled scene: one table squarely ahead, clear space to both sides.
    syn.furniture = [("table", 2.4, 0.8, 3.6, 1.6, (58, 96, 58))]
    syn.rover = Pose(1.2, 1.2, 0.0)
    perc = Perception(synth_cfg)
    scene = _warm(perc, syn)

    # the table must show up as a track ahead of the rover
    ahead_tracks = [tr for tr in scene.tracks if abs(tr.bearing_deg) < 30.0]
    assert ahead_tracks, [(tr.x, tr.y, tr.bearing_deg) for tr in scene.tracks]
    assert ahead_tracks[0].range_m < 2.0
    # the ahead sector is limited by it, the sides are freer
    ahead = scene.sector("ahead")
    assert ahead is not None and ahead < 1.6, ahead
    left = scene.sector("far_left") or 0.0
    right = scene.sector("far_right") or 0.0
    assert max(left, right) > ahead + 0.5, (ahead, left, right)


def test_table_track_present_and_rover_excluded(synth_cfg):
    syn = SyntheticRoom(synth_cfg)
    syn.rover = Pose(2.0, 0.6, 0.0)
    perc = Perception(synth_cfg)
    scene = _warm(perc, syn)

    table_c = (3.14, 0.47)
    assert any(math.hypot(tr.x - table_c[0], tr.y - table_c[1]) < 0.5 for tr in scene.tracks), \
        [(tr.x, tr.y) for tr in scene.tracks]
    assert all(math.hypot(tr.x - 2.0, tr.y - 0.6) > 0.5 for tr in scene.tracks), \
        "the rover's own body leaked into the obstacle map"


def test_empty_room_sees_only_walls(synth_cfg):
    syn = SyntheticRoom(synth_cfg)
    syn.furniture = []
    syn.rover = Pose(3.2, 1.8, 0.0)
    perc = Perception(synth_cfg)
    scene = _warm(perc, syn)
    assert not scene.tracks, f"phantom objects in an empty room: {scene.tracks}"
    # The only thing to hit is the wall ring: ~1.7 m ahead, no phantoms closer.
    assert scene.nearest_m is None or scene.nearest_m > 1.4, scene.nearest_m
    far_right = scene.sector("far_right")
    assert far_right is not None and 1.2 < far_right < 2.3, far_right
    assert scene.quality.occlusion_risk < 0.35, scene.quality.occlusion_risk
