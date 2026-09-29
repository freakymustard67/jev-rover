"""Scan command encoding/validation mirror (scan spec §5.5) + link plumbing."""
import pytest

from link import Cmd, MockLink, encode_scan_command, scan_beam_count


def test_valid_start_and_stop():
    cmd = encode_scan_command(7, start_deg=-90, end_deg=90, step_deg=6, rate_hz=20)
    assert cmd == {"id": 7, "action": "start", "start_deg": -90, "end_deg": 90,
                   "step_deg": 6, "rate_hz": 20}
    assert encode_scan_command(7, action="stop") == {"id": 7, "action": "stop"}


@pytest.mark.parametrize("kwargs", [
    dict(scan_id=-1),
    dict(scan_id=70000),
    dict(scan_id=1.5),
    dict(scan_id=True),
    dict(action="explode"),
    dict(start_deg=-100.0),
    dict(end_deg=95.0),
    dict(start_deg=30.0, end_deg=30.0),
    dict(start_deg=40.0, end_deg=10.0),
    dict(step_deg=0),
    dict(step_deg=181),
    dict(step_deg=2.5),
    dict(rate_hz=0),
    dict(rate_hz=51),
    dict(rate_hz=20.5),
])
def test_invalid_scan_objects_raise(kwargs):
    with pytest.raises(ValueError):
        encode_scan_command(kwargs.pop("scan_id", 1), **kwargs)


def test_beam_count_matches_the_spec_grid():
    assert scan_beam_count(-90, 90, 6) == 31
    assert scan_beam_count(-90, 90, 2) == 91
    assert scan_beam_count(-90, 90, 5) == 37


def test_cmd_carries_the_scan_object():
    cmd = Cmd(0.2, 5.0, scan=encode_scan_command(3, step_deg=6))
    as_dict = cmd.as_dict()
    assert as_dict["scan"]["id"] == 3 and as_dict["scan"]["step_deg"] == 6
    assert "scan" not in Cmd(0.0, 0.0).as_dict()


def test_mocklink_echoes_scan_state():
    link = MockLink()
    link.send(Cmd(0.0, 0.0, scan=encode_scan_command(3, step_deg=6)), t=0.0)
    assert link.scan_progress() == (3, "scanning")
    link.telemetry(1.0)
    assert link.scan_progress() == (3, "scanning")
    link.telemetry(4.0)
    assert link.scan_progress() == (3, "idle")
    link.send(Cmd(0.0, 0.0, scan=encode_scan_command(4, action="stop")), t=5.0)
    assert link.scan_progress() == (4, "idle")
