"""Full sweep path over loopback UDP: command -> chunks -> assembly -> match."""
import sys
import time

import numpy as np
import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "sim"))
import tof_sim as sim  # noqa: E402
from fake_rover import FakeRover  # noqa: E402

from config import SweepMatchConfig  # noqa: E402
from link import Cmd, UDPLink, encode_scan_command  # noqa: E402
from sweep import RectWorld, SweepMatcher, beams_from_assembly  # noqa: E402


def test_full_scan_path_over_udp():
    truth = np.array([2.5, 1.8, 20.0])
    scan_id = 11
    with FakeRover(truth, sweep_s=1.0, noise_seed=11) as rover:
        link = UDPLink("127.0.0.1", rover.port, local_port=0)
        try:
            link.send(Cmd(0.0, 0.0, scan=encode_scan_command(scan_id, step_deg=6)), t=0.0)
            assembly = None
            t = 0.0
            deadline = time.time() + 5.0
            while time.time() < deadline and assembly is None:
                t += 0.05
                link.telemetry(t)
                scans = link.take_scans()
                if scans:
                    assembly = scans[0]
                time.sleep(0.01)

            assert assembly is not None, "no scan assembly arrived"
            assert assembly.complete, f"missing chunks: {assembly.missing}"
            assert assembly.scan_id == scan_id
            assert link.last_scan_seq == scan_id, "telemetry scan_seq echo was lost"
            assert rover.requests and rover.requests[0]["step_deg"] == 6

            beams = beams_from_assembly(assembly)
            assert beams.n_total == 31
            assert beams.n_valid >= 20
            assert beams.duration_s > 0.5, "chunk header timing must give real seconds"

            world = RectWorld(sim.OBST, max_range_m=sim.MAX_RANGE)
            prior = (truth[0] + 0.08, truth[1] - 0.06, truth[2] + 2.0)
            result = SweepMatcher(world, SweepMatchConfig()).match(beams, prior=prior)
            assert result is not None and result.mode == "local"
            err = float(np.hypot(result.x - truth[0], result.y - truth[1]))
            assert err < 0.20, (err, result.as_dict())
        finally:
            link.close()


def test_partial_scan_is_visible_not_silent():
    """A lost chunk must surface as an incomplete scan, never as a whole one."""
    truth = np.array([3.0, 1.5, 0.0])
    with FakeRover(truth, sweep_s=1.0) as rover:
        rover.drop_chunks = {1}           # 91 beams at 2 deg -> 2 chunks; drop the last
        link = UDPLink("127.0.0.1", rover.port, local_port=0)
        try:
            link.send(Cmd(0.0, 0.0, scan=encode_scan_command(13, step_deg=2)), t=0.0)
            scans = []
            t = 0.0
            deadline = time.time() + 4.0
            while time.time() < deadline and not scans:
                t += 0.05
                link.telemetry(t)
                scans = link.take_scans()
                time.sleep(0.02)
            assert scans, "the timed-out partial scan must still surface"
            partial = scans[0]
            assert partial.complete is False
            assert partial.missing == [1]
            assert partial.quality == pytest.approx(0.5)
            assert beams_from_assembly(partial).n_total == 64   # only chunk 0 arrived
            assert link.scan_rx >= 1 and link.stats()["scan_rx_errors"] == 0
        finally:
            link.close()
