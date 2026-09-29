"""Loop-rate invariant + budget/stale discipline for the semantics layer.

The M1 plan promises (docs/planning/m1-plan.md §6): "Loop-rate invariant: with a
0.3 s sleeping vision and semantics enabled, frames/second over 1 s within 20% of
the disabled baseline." This module delivers that invariant with a much slower
(5 s) detector, plus the documented hardening claims executed in V9:

  * insulation: a detector whose infer() sleeps 5 s must not stall the main tick
    loop (15 Hz target: within 20% of the semantics-off baseline cadence, and no
    added inter-tick stall);
  * budgets: one pass in flight, min_interval_s, max_passes_per_min (rolling 60 s)
    block as documented (semantics.py:500-530), and the fail-cooldown path
    (semantics.py:511-512) refuses the offer without incrementing any counter;
  * completion: a finished pass merges into the store and yields a snapshot;
  * staleness: a result older than max_age_s=30.0 is dropped at poll
    (semantics.py:542), store.passes stays 0 and no map is produced.

All sleeps here are short; the >30 s latency case is exercised by advancing the
caller-supplied timestamps exactly as run.py does (t = now - t0), so the real
staleness code path runs without a 30 s wall-clock test.
"""
import time
from dataclasses import replace

from perception import Perception
from semantics import FakeVision, SemanticsRunner, WorldFixture
from synthetic import SyntheticRoom

TARGET_HZ = 15.0            # run.py --perception-hz / control loop target
PERCEPTION_PERIOD = 1.0 / TARGET_HZ


class SleepVision:
    """Stub detector: infer() sleeps ``delay`` seconds, then returns fixtures."""

    def __init__(self, delay: float, fixtures=()):
        self.delay = float(delay)
        self.fixtures = list(fixtures)
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


def _warm(synth_cfg, tmp_path, store_dir=None, **sem_overrides):
    cfg = replace(synth_cfg,
                  semantics=replace(synth_cfg.semantics,
                                    store_dir=str(store_dir or tmp_path),
                                    **sem_overrides))
    perc = Perception(cfg)
    syn = SyntheticRoom(cfg)
    frame = None
    for i in range(6):                       # grid/floor warm-up, as in the e2e test
        frame = syn.render()
        perc.process(frame, i / TARGET_HZ)
    return cfg, perc, syn, frame


def _fixtures(perc):
    return FakeVision.from_world(
        [WorldFixture("blue mat", 3.0, 1.2, 0.5, 0.6, 0.92)], perc.homography).fixtures


def _tick_loop(perc, syn, frame, *, seconds: float, runner=None, offer_period: float = 2.0):
    """A 15 Hz-paced perception/tick loop shaped like run.py --source synthetic.

    Offers mirror run.py: a semantics attempt at t>=1.0 and then at most one per
    ``offer_period`` (>= min_interval_s) — a caller-side scheduler, with the
    runner's own gates still in charge. Returns achieved Hz and the inter-tick
    gap distribution (s).
    """
    t0 = time.time()
    next_wall = t0
    next_perc = 0.0
    next_offer = 1.0
    ticks: list[float] = []
    gaps: list[float] = []
    prev_t = None
    while True:
        now = time.time()
        t = now - t0
        if t >= seconds:
            break
        dt = PERCEPTION_PERIOD if prev_t is None else max(1e-3, min(0.25, t - prev_t))
        prev_t = t
        syn.step(0.0, 0.0, dt)
        frame = syn.render()
        if t >= next_perc:
            perc.process(frame, t)
            if runner is not None:
                runner.poll(t)
                if t >= next_offer:
                    runner.maybe_pass(t, perc.semantic_context(t), frame, kind="full")
                    next_offer = t + offer_period
                runner.snapshot(t)
            next_perc = t + PERCEPTION_PERIOD
        ticks.append(t)
        next_wall += PERCEPTION_PERIOD
        sleep = next_wall - time.time()
        if sleep > 0:
            time.sleep(sleep)
        else:
            next_wall = time.time()          # fell behind: re-anchor
    if len(ticks) > 1:
        gaps = [ticks[i] - ticks[i - 1] for i in range(1, len(ticks))]
    span = ticks[-1] - ticks[0] if len(ticks) > 1 else 0.0
    return {
        "achieved_hz": (len(ticks) - 1) / span if span > 0 else 0.0,
        "max_gap": max(gaps) if gaps else 0.0,
        "p95_gap": sorted(gaps)[int(0.95 * (len(gaps) - 1))] if gaps else 0.0,
        "n_ticks": len(ticks),
    }


# --------------------------------------------------------------------- tests

def test_loop_cadence_insulated_from_busy_5s_detector(synth_cfg, tmp_path):
    """The promised loop-rate invariant (m1-plan §6), with a 5 s detector.

    Shipped 1280x720 synthetic config. The absolute rate there is bounded by
    render+perception on the host, so the assertion is the documented relative
    one (within 20% of the semantics-off baseline); a companion test pins the
    absolute >= 80%-of-target at half resolution.
    """
    cfg, perc, syn, frame = _warm(synth_cfg, tmp_path)

    base = _tick_loop(perc, syn, frame, seconds=4.0, runner=None)
    assert base["achieved_hz"] > 0.5 * TARGET_HZ, base   # machine sanity, not the claim

    runner = SemanticsRunner(cfg, "looptest", SleepVision(5.0, _fixtures(perc)))
    try:
        busy = _tick_loop(perc, syn, frame, seconds=4.0, runner=runner)
        stats = runner.stats()
    finally:
        runner.close()

    # (1) the 5 s detector was genuinely in flight for the whole loop ...
    assert runner.worker.started == 1, stats
    assert stats["skipped"]["inflight"] >= 1, stats
    assert runner.worker.completed == 0, "a 5 s infer cannot finish inside 4 s"
    # (2) ... and the cadence stayed within 20% of the semantics-off baseline
    ratio = busy["achieved_hz"] / base["achieved_hz"]
    assert ratio >= 0.8, (
        f"loop cadence collapsed with a busy 5 s detector: "
        f"baseline={base['achieved_hz']:.2f} Hz, busy={busy['achieved_hz']:.2f} Hz")
    # (3) jitter: no added inter-tick stall (p95 gap within 20% + 10 ms)
    assert busy["p95_gap"] <= base["p95_gap"] * 1.2 + 0.010, (base, busy)


def test_loop_cadence_absolute_target_with_busy_5s_detector(synth_cfg, tmp_path):
    """Same invariant at half resolution (640x360), where 15 Hz is reachable.

    Asserts the absolute form: >= 80% of the 15 Hz target while a 5 s detector
    is busy, plus the relative form against the semantics-off baseline.
    """
    half = replace(synth_cfg,
                   camera=replace(synth_cfg.camera, width=640, height=360),
                   homography=replace(synth_cfg.homography, image_points_px=[
                       [x * 0.5, y * 0.5] for x, y in synth_cfg.homography.image_points_px]),
                   floor=replace(synth_cfg.floor, polygon_px=[
                       [x * 0.5, y * 0.5] for x, y in synth_cfg.floor.polygon_px]),
                   synthetic=replace(synth_cfg.synthetic,
                                     px_per_m=synth_cfg.synthetic.px_per_m * 0.5))
    cfg, perc, syn, frame = _warm(half, tmp_path, store_dir=str(tmp_path / "half"))
    base = _tick_loop(perc, syn, frame, seconds=3.0, runner=None)
    assert base["achieved_hz"] >= 0.9 * TARGET_HZ, base

    runner = SemanticsRunner(cfg, "looptest", SleepVision(5.0, _fixtures(perc)))
    try:
        busy = _tick_loop(perc, syn, frame, seconds=3.0, runner=runner)
        stats = runner.stats()
    finally:
        runner.close()
    assert runner.worker.started == 1, stats
    assert busy["achieved_hz"] >= 0.8 * TARGET_HZ, busy
    assert busy["achieved_hz"] >= 0.8 * base["achieved_hz"], (base, busy)


def test_budgets_one_inflight_interval_and_per_minute(synth_cfg, tmp_path):
    """One in flight + min_interval_s + max_passes_per_min, per semantics.py:500-530."""
    cfg, perc, _, frame = _warm(synth_cfg, tmp_path)      # default min_interval_s=2.0
    assert cfg.semantics.max_passes_per_min == 4
    runner = SemanticsRunner(cfg, "looptest", SleepVision(0.4, _fixtures(perc)))

    def drain(t):
        deadline = time.time() + 3.0
        while time.time() < deadline:
            res = runner.poll(t + 1.0)
            if res is not None:
                return res
            time.sleep(0.01)
        return None

    try:
        ctx = lambda t: perc.semantic_context(t)          # noqa: E731
        assert runner.maybe_pass(1.0, ctx(1.0), frame) is True
        # one pass in flight: second offer refused, counted
        assert runner.maybe_pass(1.05, ctx(1.05), frame, force=True) is False
        assert runner.skipped["inflight"] == 1
        assert drain(1.0) is not None and runner.store.passes == 1
        # min interval: inside 2.0 s window the next offer is refused, counted
        assert runner.maybe_pass(2.0, ctx(2.0), frame) is False
        assert runner.skipped["interval"] == 1
        # force bypasses the interval gate but not the rolling budget
        assert runner.maybe_pass(3.5, ctx(3.5), frame, force=True) is True
        assert drain(3.5) is not None and runner.store.passes == 2
        for t in (6.0, 9.0):                              # 2 more within 60 s
            assert runner.maybe_pass(t, ctx(t), frame) is True
            assert drain(t) is not None
        assert runner.store.passes == 4
        # budget: 4 offers in the rolling 60 s window block the 5th, forced or not
        assert runner.maybe_pass(12.0, ctx(12.0), frame) is False
        assert runner.maybe_pass(12.1, ctx(12.1), frame, force=True) is False
        assert runner.skipped["budget"] == 2
        assert runner.worker.started == 4
    finally:
        runner.close()


def test_fail_cooldown_blocks_uncounted(synth_cfg, tmp_path):
    """semantics.py:511-512: the cooldown refusal must not move any counter."""
    cfg, perc, _, frame = _warm(synth_cfg, tmp_path, min_interval_s=0.0,
                                failure_cooldown_s=5.0)
    runner = SemanticsRunner(cfg, "looptest", BoomVision())
    try:
        assert runner.maybe_pass(1.0, perc.semantic_context(1.0), frame, force=True) is True
        deadline = time.time() + 3.0
        while runner.worker.errors == 0 and time.time() < deadline:
            time.sleep(0.01)
        assert runner.poll(1.01) is None and runner.worker.errors == 1
        assert runner.fail_cooldown_until == 1.01 + 5.0
        before = (dict(runner.skipped), runner.stale_dropped, runner.store.passes)
        assert runner.maybe_pass(2.0, perc.semantic_context(2.0), frame, force=True) is False
        after = (dict(runner.skipped), runner.stale_dropped, runner.store.passes)
        assert before == after, "cooldown path must stay uncounted"
        # cooldown expires: the next offer is accepted
        assert runner.maybe_pass(7.0, perc.semantic_context(7.0), frame, force=True) is True
    finally:
        runner.close()


def test_pass_completes_and_merges(synth_cfg, tmp_path):
    """A (fast) detector pass merges: passes==1, objects project, files written."""
    cfg, perc, _, frame = _warm(synth_cfg, tmp_path)
    runner = SemanticsRunner(cfg, "looptest", SleepVision(0.05, _fixtures(perc)))
    try:
        assert runner.maybe_pass(1.0, perc.semantic_context(1.0), frame, force=True) is True
        deadline = time.time() + 3.0
        merged = None
        while time.time() < deadline and merged is None:
            merged = runner.poll(1.1)
            if merged is None:
                time.sleep(0.01)
        assert merged is not None and merged.passes == 1
        assert merged.objects and merged.objects[0].label == "blue mat"
        snap = runner.snapshot(1.2)
        assert snap is not None and snap.passes == 1
        assert (tmp_path / "looptest_latest.json").exists()
        assert (tmp_path / "events.jsonl").exists()
        assert runner.stale_dropped == 0
    finally:
        runner.close()


def test_stale_result_beyond_max_age_is_dropped_without_a_map(synth_cfg, tmp_path):
    """Detector latency > max_age_s=30.0: result dropped, store.passes stays 0.

    The caller clock is advanced past max_age_s (run.py owns `t`), so the real
    staleness branch (semantics.py:542-544) runs without a 30 s wall test.
    """
    cfg, perc, _, frame = _warm(synth_cfg, tmp_path)
    assert cfg.semantics.max_age_s == 30.0
    runner = SemanticsRunner(cfg, "looptest", SleepVision(0.2, _fixtures(perc)))
    try:
        assert runner.maybe_pass(1.0, perc.semantic_context(1.0), frame, force=True) is True
        deadline = time.time() + 5.0
        while runner.worker.completed == 0 and time.time() < deadline:
            time.sleep(0.005)
        assert runner.worker.completed == 1
        assert runner.poll(32.0) is None            # age 31.0 s > max_age_s 30.0
        assert runner.stale_dropped == 1
        assert runner.store.passes == 0             # nothing merged
        assert runner.snapshot(32.0) is None        # no map is produced
        assert runner.stats()["stale_dropped"] == 1
        assert not (tmp_path / "looptest_latest.json").exists()
        # the slot is freed: the next offer is accepted again
        assert runner.maybe_pass(33.0, perc.semantic_context(33.0), frame, force=True) is True
    finally:
        runner.close()
