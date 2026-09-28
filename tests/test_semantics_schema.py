"""Schema, config strictness, backward compatibility, and state-size bound."""
import json

import pytest

from config import ConfigError, RoomConfig
from scene import (Destination, Scene, SemanticDiff, SemanticMap,
                   SemanticObject, SweepState)
from tactics import build_state

from conftest import ROOT


def test_scene_semantics_defaults_to_none():
    scene = Scene(t=1.0)
    d = scene.to_dict()
    assert d["semantics"] is None and d["sweep"] is None
    json.dumps(d)


def test_scene_with_semantics_serializes():
    scene = Scene(t=2.0)
    scene.semantics = SemanticMap(
        age_s=1.0, passes=2, model="fake-vision-v0",
        objects=[SemanticObject(id="obj_0001", label="blue mat", x=3.0, y=1.2, confidence=0.9)],
        destination=Destination(label="blue mat", x=3.0, y=1.2, confidence=0.9,
                                source="vision", object_id="obj_0001"),
        diff=SemanticDiff(appeared=["obj_0001"], moved=["obj_0002"], vanished=["obj_0003"]))
    scene.sweep = SweepState(age_s=0.5, last_seq=4, pose_sigma=[0.02, 0.02, 1.0],
                             backend="none")
    d = scene.to_dict()
    assert d["semantics"]["objects"][0]["label"] == "blue mat"
    assert d["semantics"]["diff"]["moved"] == ["obj_0002"]
    assert d["sweep"]["last_seq"] == 4
    json.dumps(d)


def test_old_config_without_new_sections_loads(synth_cfg):
    raw = synth_cfg.to_dict()
    for key in ("semantics", "destination", "sweep", "confirmation"):
        raw.pop(key)
    cfg = RoomConfig.from_dict(raw)
    assert cfg.semantics.enabled is False
    assert cfg.semantics.model.kind == "fake"
    assert cfg.destination.standoff_m == 0.35
    assert cfg.sweep.enabled is False
    assert cfg.confirmation.backend == "none"


def test_synthetic_config_fixtures_parse(synth_cfg):
    labels = [f.label for f in synth_cfg.semantics.model.fixtures]
    assert labels == ["blue mat", "red box", "green ball"]
    assert synth_cfg.semantics.project.point_by_label["blue mat"] == "centroid"


def test_strict_semantics_rejects_typo(synth_cfg):
    raw = synth_cfg.to_dict()
    raw["semantics"]["model"]["endpointt"] = "oops"
    with pytest.raises(ConfigError, match="unknown key"):
        RoomConfig.from_dict(raw)
    raw = synth_cfg.to_dict()
    raw["semantics"]["model"]["fixtures"][0]["labell"] = "oops"
    with pytest.raises(ConfigError, match="unknown key"):
        RoomConfig.from_dict(raw)


def test_legacy_sections_stay_tolerant(synth_cfg):
    raw = synth_cfg.to_dict()
    raw["rover"]["some_future_key"] = 1     # legacy sections ignore unknown keys
    RoomConfig.from_dict(raw)


def test_semantics_validation(synth_cfg):
    for mutate in (
        lambda r: r["semantics"]["project"].__setitem__("point", "nope"),
        lambda r: r["semantics"].__setitem__("ema_alpha", 2.0),
        lambda r: r["semantics"].__setitem__("max_passes_per_min", 0),
        lambda r: r["semantics"]["model"]["fixtures"][0].__setitem__("w", 0),
        lambda r: r["semantics"]["model"].__setitem__("kind", "magic"),
        lambda r: r["destination"].__setitem__("standoff_m", -1),
        lambda r: r["sweep"]["sensor"].__setitem__("max_range_m", 0.01),
    ):
        raw = synth_cfg.to_dict()
        mutate(raw)
        with pytest.raises(ConfigError):
            RoomConfig.from_dict(raw)


def test_build_state_carries_semantics_and_stays_bounded():
    scene = Scene(t=5.0)
    scene.semantics = SemanticMap(
        age_s=0.4, passes=3, model="fake-vision-v0",
        objects=[SemanticObject(id=f"obj_{i:04d}", label=f"thing {i}",
                                x=1.0 + 0.1 * i, y=2.0, confidence=0.9)
                 for i in range(10)])
    state = build_state(scene)
    assert state["observed"]["semantics"]["objects"][0]["label"] == "thing 0"
    size = len(json.dumps(state))
    assert size < 6000, f"state grew too much with semantics: {size} bytes"


def test_room_config_default_paths_still_load():
    cfg = RoomConfig.load(ROOT / "config" / "room.synthetic.json")
    assert cfg.name == "synthetic-room"
