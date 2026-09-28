"""End-to-end on the synthetic room: frames -> FakeVision -> scene.semantics."""
import math
import time
from dataclasses import replace

from perception import Perception
from scene import Scene
from semantics import (FakeVision, SemanticsRunner, WorldFixture, resolve_destination)
from synthetic import SyntheticRoom


def _warm(synth_cfg, tmp_path):
    cfg = replace(synth_cfg, semantics=replace(synth_cfg.semantics, store_dir=str(tmp_path)))
    perc = Perception(cfg)
    syn = SyntheticRoom(cfg)
    frame = None
    for i in range(6):
        frame = syn.render()
        perc.process(frame, i / 15.0)
    return cfg, perc, frame


def _pass(runner, perc, frame, t: float):
    ctx = perc.semantic_context(t)
    assert runner.maybe_pass(t, ctx, frame, force=True)
    deadline = time.time() + 3.0
    result = None
    while time.time() < deadline and result is None:
        result = runner.poll(t + 0.001)
        if result is None:
            time.sleep(0.01)
    return result


def test_end_to_end_pass_and_destination(synth_cfg, tmp_path):
    cfg, perc, frame = _warm(synth_cfg, tmp_path)
    vision = FakeVision.from_world(
        [WorldFixture("blue mat", 3.0, 1.2, 0.5, 0.6, 0.92)], perc.homography)
    runner = SemanticsRunner(cfg, "synth", vision)
    try:
        merged = _pass(runner, perc, frame, 1.0)
        assert merged is not None and merged.passes == 1
        o = merged.objects[0]
        assert o.id == "obj_0001" and o.label == "blue mat"
        assert math.hypot(o.x - 3.0, o.y - 1.2) < 0.05, (o.x, o.y)
        assert o.height_suspect is False
        assert merged.diff.appeared == ["obj_0001"]

        snap = runner.snapshot(2.5)
        assert snap is not None and abs(snap.age_s - 1.5) < 0.01
        assert (tmp_path / "synth_latest.json").exists()
        assert (tmp_path / "events.jsonl").exists()

        dest = resolve_destination("go to the blue mat", snap)
        assert dest is not None and dest.object_id == "obj_0001"
        assert math.hypot(dest.x - 3.0, dest.y - 1.2) < 0.05
    finally:
        runner.close()


def test_two_passes_with_a_move(tmp_path, synth_cfg):
    cfg, perc, frame = _warm(synth_cfg, tmp_path)
    vision = FakeVision.from_world(
        [WorldFixture("blue mat", 3.0, 1.2, 0.5, 0.6, 0.92)], perc.homography)
    runner = SemanticsRunner(cfg, "synth", vision)
    try:
        m1 = _pass(runner, perc, frame, 1.0)
        assert m1.diff.appeared == ["obj_0001"] and not m1.diff.moved

        vision.fixtures = FakeVision.from_world(
            [WorldFixture("blue mat", 3.35, 1.2, 0.5, 0.6, 0.92)], perc.homography).fixtures
        m2 = _pass(runner, perc, frame, 2.0)
        assert m2.diff.moved == ["obj_0001"], "raw 0.35 m move must be detected end to end"
        assert m2.objects[0].motion == "moved"
        assert m2.diff.appeared == []
        assert m2.passes == 2
    finally:
        runner.close()


def test_slow_vision_never_blocks_the_caller(synth_cfg, tmp_path):
    cfg, perc, frame = _warm(synth_cfg, tmp_path)

    class SlowVision:
        name = "slow"

        def infer(self, frame_, *, labels=None):
            time.sleep(0.4)
            return []

    runner = SemanticsRunner(cfg, "synth", SlowVision())
    try:
        t0 = time.time()
        ok = runner.maybe_pass(1.0, perc.semantic_context(1.0), frame, force=True)
        dt = time.time() - t0
        assert ok and dt < 0.05, f"submit blocked for {dt:.3f}s"

        t0 = time.time()
        for _ in range(30):
            runner.poll(1.0)
        assert (time.time() - t0) < 0.2, "polling must stay cheap while in flight"
    finally:
        runner.close()


def test_semantics_disabled_by_default(synth_cfg):
    scene = Scene(t=0.0)
    assert scene.semantics is None and scene.sweep is None
    assert synth_cfg.semantics.enabled is False
