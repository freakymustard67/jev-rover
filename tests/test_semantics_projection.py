"""Projection: bbox -> floor metres, anchor overrides, rejection, height_suspect."""
import math
from dataclasses import replace

import numpy as np
import pytest

from config import SemanticsConfig
from perception import Homography, Perception
from semantics import (Detection, FakeVision, WorldFixture, anchor_point,
                       dedupe_detections, height_suspect, project_detections,
                       resolve_anchor_kind)
from synthetic import SyntheticRoom

WARM = 6


def _perception(synth_cfg, warm: int = WARM):
    perc = Perception(synth_cfg)
    syn = SyntheticRoom(synth_cfg)
    frame = None
    for i in range(warm):
        frame = syn.render()
        perc.process(frame, i / 15.0)
    return perc, syn, frame


def test_world_round_trip_uses_centroid_override(synth_cfg):
    perc, syn, frame = _perception(synth_cfg)
    ctx = perc.semantic_context(1.0)
    vision = FakeVision.from_world(
        [WorldFixture("blue mat", 3.0, 1.2, 0.5, 0.6, 0.92)], perc.homography)
    objects, rejected = project_detections(vision.fixtures, frame, ctx, synth_cfg.semantics, 1.0)
    assert rejected == 0 and len(objects) == 1
    o = objects[0]
    assert math.hypot(o.x - 3.0, o.y - 1.2) < 0.03, (o.x, o.y)
    assert o.height_suspect is False
    assert o.id == ""                       # the store assigns ids on merge


def test_bottom_center_vs_centroid_for_a_flat_object(synth_cfg):
    perc, syn, frame = _perception(synth_cfg)
    ctx = perc.semantic_context(1.0)
    entries = [WorldFixture("blue mat", 3.0, 1.2, 0.5, 0.6),   # override -> centroid
               WorldFixture("thing", 3.0, 1.2, 0.5, 0.6)]      # default -> bottom_center
    vision = FakeVision.from_world(entries, perc.homography)
    objects, _ = project_detections(vision.fixtures, frame, ctx, synth_cfg.semantics, 1.0)
    by_label = {o.label: o for o in objects}
    assert abs(by_label["blue mat"].y - 1.2) < 0.03            # centroid recovers the centre
    assert abs(by_label["thing"].y - 0.9) < 0.03               # bottom edge, h/2 below
    assert by_label["thing"].x == pytest.approx(by_label["blue mat"].x, abs=0.03)


def test_outside_polygon_is_rejected_not_clamped(synth_cfg):
    perc, syn, frame = _perception(synth_cfg)
    ctx = perc.semantic_context(1.0)
    vision = FakeVision.from_world(
        [WorldFixture("red box", 0.10, 1.0, 0.2, 0.2)], perc.homography)   # x < polygon 0.15
    objects, rejected = project_detections(vision.fixtures, frame, ctx, synth_cfg.semantics, 1.0)
    assert objects == [] and rejected == 1


def test_height_suspect_on_furniture_and_on_floor(synth_cfg):
    perc, syn, frame = _perception(synth_cfg)
    ctx = perc.semantic_context(1.0)
    on_floor = FakeVision.from_world(
        [WorldFixture("thing", 3.0, 1.2, 0.4, 0.4)], perc.homography)
    on_table = FakeVision.from_world(
        [WorldFixture("thing", 3.13, 0.47, 0.5, 0.5)], perc.homography)
    floor_objs, _ = project_detections(on_floor.fixtures, frame, ctx, synth_cfg.semantics, 1.0)
    table_objs, _ = project_detections(on_table.fixtures, frame, ctx, synth_cfg.semantics, 1.0)
    assert floor_objs[0].height_suspect is False
    assert table_objs[0].height_suspect is True


def test_height_suspect_when_probe_leaves_the_frame(synth_cfg):
    perc, syn, frame = _perception(synth_cfg)
    ctx = perc.semantic_context(1.0)
    h, w = frame.shape[:2]
    bbox = (600.0, 700.0, 640.0, float(h - 1))       # base at the very bottom row
    assert height_suspect(frame, bbox, 3.0, 0.2, ctx, synth_cfg.semantics.project) is True


def test_probe_median_survives_a_single_dead_pixel(synth_cfg):
    perc, syn, frame = _perception(synth_cfg)
    ctx = perc.semantic_context(1.0)
    bb = FakeVision.from_world(
        [WorldFixture("thing", 3.0, 1.2, 0.4, 0.4)], perc.homography).fixtures[0].bbox_px
    bbox = (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
    px = int(round((bbox[0] + bbox[2]) / 2.0))
    py = int(round(bbox[3])) + int(synth_cfg.semantics.project.probe_px)
    noisy = frame.copy()
    noisy[py, px] = (0, 0, 0)                       # one dead pixel under the base
    assert height_suspect(noisy, bbox, 3.0, 1.2, ctx, synth_cfg.semantics.project) is False
    dark = frame.copy()
    dark[py - 1:py + 2, px - 3:px + 4] = 0          # a dark band still flags suspect
    assert height_suspect(dark, bbox, 3.0, 1.2, ctx, synth_cfg.semantics.project) is True


def test_height_suspect_without_a_floor_sample(synth_cfg):
    perc, syn, frame = _perception(synth_cfg)
    ctx = perc.semantic_context(1.0)
    ctx = replace(ctx, floor_lab=None)
    bbox = (600.0, 420.0, 650.0, 540.0)
    assert height_suspect(frame, bbox, 3.0, 0.9, ctx, synth_cfg.semantics.project) is True


def test_dedupe_collapses_overlapping_same_label():
    d1 = Detection("mat", (100, 100, 200, 200), 0.7)
    d2 = Detection("mat", (102, 101, 201, 199), 0.9)
    d3 = Detection("box", (102, 101, 201, 199), 0.5)
    vision = FakeVision([d1, d2, d3])
    dets = vision.infer(np.zeros((300, 300, 3), np.uint8))
    from semantics import dedupe_detections
    kept = dedupe_detections(dets, 0.5)
    assert len(kept) == 2                                   # one mat (best score), one box
    assert max(k.score for k in kept if k.label == "mat") == 0.9


def test_dedupe_collapses_close_world_centres_without_overlap():
    """The plan's 'centres < 0.15 m' clause, alongside IoU."""
    H = Homography([[0, 0], [100, 0], [100, 100], [0, 100]],
                   [[0, 0], [1, 0], [1, 1], [0, 1]])            # 100 px = 1 m
    a = Detection("mat", (10, 10, 20, 20), 0.7)                  # centre at (0.15, 0.15) m
    b = Detection("mat", (22, 10, 32, 20), 0.9)                  # 0.12 m away, no IoU
    kept = dedupe_detections([a, b], 0.5, homography=H, center_dist_m=0.15)
    assert len(kept) == 1 and kept[0].score == 0.9
    far = Detection("mat", (40, 10, 50, 20), 0.8)                # 0.30 m away
    assert len(dedupe_detections([a, far], 0.5, homography=H, center_dist_m=0.15)) == 2
    assert len(dedupe_detections([a, b], 0.5)) == 2, "no homography -> IoU only"


def test_projection_dedupes_near_coincident_same_label(synth_cfg):
    perc, syn, frame = _perception(synth_cfg)
    ctx = perc.semantic_context(1.0)
    vision = FakeVision.from_world([
        WorldFixture("mat", 3.00, 1.20, 0.10, 0.10, 0.7),
        WorldFixture("mat", 3.12, 1.20, 0.10, 0.10, 0.9),        # 12 cm away, no overlap
    ], perc.homography)
    objects, rejected = project_detections(vision.fixtures, frame, ctx, synth_cfg.semantics, 1.0)
    assert rejected == 0 and len(objects) == 1
    assert objects[0].confidence == 0.9


def test_canonical_label_folds_case_and_plurals():
    from semantics import canonical_label
    assert canonical_label("Blue Mats") == "blue mat"
    assert canonical_label("boxes") == "box"
    assert canonical_label("dishes") == "dish"
    assert canonical_label("berries") == "berry"


def test_dedupe_and_adapter_filter_are_label_case_insensitive():
    d1 = Detection("Blue Mat", (100, 100, 200, 200), 0.7)
    d2 = Detection("blue mats", (102, 101, 201, 199), 0.9)
    kept = dedupe_detections([d1, d2], 0.5)
    assert len(kept) == 1 and kept[0].score == 0.9

    vision = FakeVision([Detection("Blue Mat", (0, 0, 10, 10))])
    got = vision.infer(np.zeros((20, 20, 3), np.uint8), labels=["blue mats"])
    assert [d.label for d in got] == ["Blue Mat"], "adapter filter folds case + plurals"


def test_anchor_helpers():
    assert anchor_point((0, 0, 10, 20), "bbox_bottom_center") == (5.0, 20.0)
    assert anchor_point((0, 0, 10, 20), "bbox_center") == (5.0, 10.0)
    cfg = SemanticsConfig()
    assert resolve_anchor_kind(cfg.project, "mat") == "bbox_bottom_center"
    cfg.project.point_by_label["mat"] = "centroid"
    assert resolve_anchor_kind(cfg.project, "MAT") == "centroid"
