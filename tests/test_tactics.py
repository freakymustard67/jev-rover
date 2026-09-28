"""The Jev layer: gating, state shape, fingerprints - with no live API calls."""
import copy
import json
import time

import pytest

import tactics as tactics_mod
from tactics import DEFAULT, THRESHOLDS, Tactician, build_state, decision_needed

from conftest import make_scene


def test_decision_gating():
    assert decision_needed(make_scene(), 0.5) is False
    assert decision_needed(make_scene(nearest=1.0), 0.5) is True
    assert decision_needed(make_scene(blocked=True), 0.5) is True
    assert decision_needed(make_scene(occlusion=0.5), 0.5) is True
    assert decision_needed(make_scene(no_progress=1.0), 0.5) is True
    assert decision_needed(make_scene(moving=1.0), 0.5) is True
    assert decision_needed(make_scene(), None) is True
    assert decision_needed(make_scene(), THRESHOLDS["refresh_s"] + 1) is True


def test_state_shape_and_serializability():
    scene = make_scene(nearest=1.2, t=3.0)
    state = build_state(scene)
    assert set(state) == {"robot", "mission", "observed"}
    assert "mission" not in state["observed"]
    assert "truly_stuck" not in json.dumps(state)  # questions are not in the state
    json.dumps(state)  # must be serializable by construction
    assert state["observed"]["nearest_m"] == 1.2


def test_fingerprint_tracks_material_change():
    a = make_scene(nearest=5.0)
    b = make_scene(nearest=1.0)
    assert Tactician._key(a) != Tactician._key(b)
    c = copy.deepcopy(a)
    assert Tactician._key(a) == Tactician._key(c)
    c.quality.pose_source = "lost"
    assert Tactician._key(a) != Tactician._key(c)


class _FakeAnswer:
    def __init__(self, choice=None, score=0.0, noul=0.0):
        self.choice = choice or "veer_left"
        self.confidence = 0.8
        self.probabilities = {"veer_left": 0.7, "hold_course": 0.3}
        self.score = score
        self.noul = noul


class _FakeUsage:
    input_tokens = 120
    output_tokens = 30


class _FakeResponse:
    def __init__(self):
        self.answers = {
            "maneuver": _FakeAnswer(choice="veer_left"),
            "risk": _FakeAnswer(score=1.6),
            "truly_stuck": _FakeAnswer(noul=0.2),
            "path_obstructed": _FakeAnswer(noul=0.8),
            "observation_unreliable": _FakeAnswer(noul=0.1),
            "path_still_good": _FakeAnswer(noul=0.9),
        }
        self.usage = _FakeUsage()


class _FakeClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def system_one(self, **kwargs):
        assert "state" in kwargs and "questions" in kwargs
        return _FakeResponse()

    def close(self):
        pass


def test_worker_parses_judgment_without_network(monkeypatch):
    monkeypatch.setattr(tactics_mod, "TypeSafeClient", _FakeClient)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    tact = Tactician(hz=100.0, budget=10)
    try:
        scene = make_scene(nearest=1.0)
        tact.offer(scene, 1.0)
        judgment = None
        deadline = time.time() + 3.0
        while time.time() < deadline:
            judgment = tact.read(1.0)
            if judgment["source"] == "jev":
                break
            time.sleep(0.02)
        assert judgment is not None and judgment["source"] == "jev", judgment
        assert judgment["maneuver"] == "veer_left"
        assert judgment["risk"] == 1.6
        assert judgment["path_obstructed"] == 0.8
        assert judgment["path_still_good"] == 0.9
        stats = tact.stats()
        assert stats["calls"] == 1 and stats["tokens"] == 150
    finally:
        tact.close()


def test_worker_survives_api_errors(monkeypatch):
    class _BoomClient(_FakeClient):
        def system_one(self, **kwargs):
            raise RuntimeError("network down")

    monkeypatch.setattr(tactics_mod, "TypeSafeClient", _BoomClient)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    tact = Tactician(hz=100.0, budget=10)
    try:
        tact.offer(make_scene(nearest=1.0), 1.0)
        judgment = None
        deadline = time.time() + 3.0
        while time.time() < deadline:
            judgment = tact.read(1.0)
            if judgment["source"].startswith("error:"):
                break
            time.sleep(0.02)
        assert judgment is not None and judgment["source"].startswith("error:")
        assert tact.stats()["errors"] == 1
    finally:
        tact.close()
