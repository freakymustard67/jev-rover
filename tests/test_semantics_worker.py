"""Worker discipline: non-blocking, one in flight, budgets, cooldown, staleness."""
import time
from dataclasses import replace

import numpy as np

from config import RoomConfig
from perception import Homography, SemanticContext
from semantics import SemanticsRunner


class SlowVision:
    name = "slow"

    def __init__(self, delay: float = 0.5):
        self.delay = delay

    def infer(self, frame, *, labels=None):
        time.sleep(self.delay)
        return []


class FastVision:
    name = "fast"

    def infer(self, frame, *, labels=None):
        return []


class BoomVision:
    name = "boom"

    def infer(self, frame, *, labels=None):
        raise RuntimeError("nope")


def _ctx(floor_lab=np.array([180.0, 128.0, 128.0])) -> SemanticContext:
    H = Homography([[0, 0], [100, 0], [100, 100], [0, 100]],
                   [[0, 0], [1, 0], [1, 1], [0, 1]])
    return SemanticContext(
        homography=H, room_w_m=1.0, room_h_m=1.0, cell_m=0.05,
        polygon_px=np.array([[0, 0], [100, 0], [100, 100], [0, 100]], float),
        floor_lab=floor_lab, floor_lab_tolerance=20.0,
        grid_log_odds=np.zeros((20, 20), np.float32),
        grid_last_seen=np.zeros((20, 20), np.float32),
        grid_t=0.0, occupied_thr=0.42, stale_s=3.0)


FRAME = np.zeros((120, 160, 3), np.uint8)


def _runner(vision, tmp_path, **sem_overrides) -> SemanticsRunner:
    cfg = RoomConfig()
    cfg.name = "workertest"
    cfg.semantics = replace(cfg.semantics, store_dir=str(tmp_path), **sem_overrides)
    return SemanticsRunner(cfg, "workertest", vision)


def _drain(runner, t: float, tries: int = 100):
    for _ in range(tries):
        result = runner.poll(t)
        if result is not None:
            return result
        time.sleep(0.01)
    return None


def test_maybe_pass_returns_immediately(tmp_path):
    runner = _runner(SlowVision(0.5), tmp_path)
    try:
        t0 = time.time()
        ok = runner.maybe_pass(1.0, _ctx(), FRAME, force=True)
        dt = time.time() - t0
        assert ok and dt < 0.05, f"maybe_pass blocked for {dt:.3f}s"
    finally:
        runner.close()


def test_one_pass_in_flight(tmp_path):
    runner = _runner(SlowVision(0.3), tmp_path, min_interval_s=0.0)
    try:
        assert runner.maybe_pass(1.0, _ctx(), FRAME, force=True)
        assert not runner.maybe_pass(1.1, _ctx(), FRAME, force=True)
        assert runner.skipped["inflight"] == 1
    finally:
        runner.close()


def test_stale_result_is_dropped(tmp_path):
    runner = _runner(SlowVision(0.3), tmp_path, max_age_s=1.0, min_interval_s=0.0)
    try:
        assert runner.maybe_pass(0.0, _ctx(), FRAME, force=True)
        result = _drain(runner, 10.0)                 # 10 s later: far beyond max_age_s
        assert result is None and runner.stale_dropped == 1
    finally:
        runner.close()


def test_budget_limits_passes_per_minute(tmp_path):
    runner = _runner(FastVision(), tmp_path, max_passes_per_min=2, min_interval_s=0.0)
    try:
        for t in (0.0, 1.0):
            assert runner.maybe_pass(t, _ctx(), FRAME, force=True)
            assert _drain(runner, t + 0.01) is not None
        assert not runner.maybe_pass(2.0, _ctx(), FRAME, force=True)
        assert runner.skipped["budget"] == 1
    finally:
        runner.close()


def test_failure_cooldown(tmp_path):
    runner = _runner(BoomVision(), tmp_path, failure_cooldown_s=5.0, min_interval_s=0.0)
    try:
        assert runner.maybe_pass(0.0, _ctx(), FRAME, force=True)
        assert _drain(runner, 0.05) is None
        assert runner.worker.errors == 1
        assert not runner.maybe_pass(1.0, _ctx(), FRAME, force=True), "cooldown must block"
        assert runner.skipped["cooldown"] == 1, "cooldown refusals are counted"
        assert runner.maybe_pass(20.0, _ctx(), FRAME, force=True), "cooldown must expire"
    finally:
        runner.close()


def test_pass_refused_without_floor_sample(tmp_path):
    runner = _runner(FastVision(), tmp_path)
    try:
        assert not runner.maybe_pass(1.0, _ctx(floor_lab=None), FRAME, force=True)
        assert runner.skipped["no_context"] == 1
    finally:
        runner.close()


def test_frame_resolution_mismatch_is_refused_once(tmp_path, capsys):
    runner = _runner(FastVision(), tmp_path)
    try:
        ctx = replace(_ctx(), camera_w=100, camera_h=100)     # FRAME is 160x120
        assert not runner.maybe_pass(1.0, ctx, FRAME, force=True)
        assert not runner.maybe_pass(2.0, ctx, FRAME, force=True)
        assert runner.skipped["resolution"] == 2
        assert capsys.readouterr().err.count("does not match") == 1, "warn once, not per refusal"
    finally:
        runner.close()


def test_frame_resolution_match_is_accepted(tmp_path):
    runner = _runner(FastVision(), tmp_path)
    try:
        ctx = replace(_ctx(), camera_w=160, camera_h=120)
        assert runner.maybe_pass(1.0, ctx, FRAME, force=True)
        assert runner.skipped["resolution"] == 0
    finally:
        runner.close()


class WarmVision:
    name = "warm"

    def __init__(self):
        self.warmed = 0

    def warmup(self):
        time.sleep(0.05)
        self.warmed += 1

    def infer(self, frame, *, labels=None):
        return []


def test_prewarm_loads_and_is_not_merged(tmp_path):
    vision = WarmVision()
    runner = _runner(vision, tmp_path)
    try:
        assert runner.prewarm(1.0), "prewarm must be accepted while idle"
        for _ in range(100):
            runner.poll(1.0)
            if runner.prewarmed:
                break
            time.sleep(0.01)
        assert runner.prewarmed and vision.warmed == 1
        assert runner.store.passes == 0, "a warm-up must never merge as a data pass"
        stats = runner.stats()
        assert stats["warmup_ms"] is not None and stats["prewarmed"] is True
    finally:
        runner.close()


def test_warmup_failure_arms_cooldown(tmp_path):
    class BoomWarm:
        name = "boomwarm"

        def warmup(self):
            raise RuntimeError("weights gone")

        def infer(self, frame, *, labels=None):
            return []

    runner = _runner(BoomWarm(), tmp_path, failure_cooldown_s=5.0, min_interval_s=0.0)
    try:
        assert runner.prewarm(0.0)
        for _ in range(100):
            runner.poll(0.05)
            if runner.worker.errors:
                break
            time.sleep(0.01)
        assert runner.worker.errors == 1
        assert not runner.maybe_pass(1.0, _ctx(), FRAME, force=True), "cooldown must arm"
        assert runner.skipped["cooldown"] >= 1
    finally:
        runner.close()


def test_stats_shape(tmp_path):
    runner = _runner(FastVision(), tmp_path)
    try:
        stats = runner.stats()
        assert stats["enabled"] is True and stats["passes"] == 0
        assert "median_ms" in stats and "skipped" in stats
        assert set(stats["skipped"]) == {"interval", "budget", "inflight", "no_context",
                                         "resolution", "cooldown"}
    finally:
        runner.close()
