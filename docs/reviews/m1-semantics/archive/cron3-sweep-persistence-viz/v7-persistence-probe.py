"""v7 probe — cross-session persistence edge cases of the M1 semantics store.

Investigative only (scratch clone): exercises the shipped persistence code
(`SemanticStore._save_latest`, `_append_events`, `semantics.load_map`) as-is.
Nothing here is production code; every check writes under tmp_path.
"""
import json
import time
from dataclasses import replace

import pytest

from config import SemanticsConfig
from scene import Destination, SemanticObject
from semantics import SemanticStore, load_map


def _store(tmp_path, room="probe-room", **overrides):
    cfg = replace(SemanticsConfig(), store_dir=str(tmp_path), **overrides)
    return SemanticStore(cfg, room)


def _obj(label="mat", x=3.0, y=1.2, **kw):
    return SemanticObject(id="", label=label, x=x, y=y,
                          confidence=kw.pop("confidence", 0.9), **kw)


# ---------------------------------------------------------------- round-trip

def test_round_trip_preserves_all_fields(tmp_path):
    st = _store(tmp_path)
    st.set_destination(Destination(label="blue mat", x=3.0, y=1.2, confidence=0.92,
                                   source="vision", object_id="obj_0001"))
    st.merge([_obj("blue mat", 3.0, 1.2, sources=["vision", "sweep"],
                   height_suspect=True, confidence=0.92)],
             rejected=3, model="fake-vision-v0", t_pass=4.0)
    path = tmp_path / "probe-room_latest.json"
    rebuilt = load_map(path)
    expected = st.snapshot(st.last_pass_t)
    assert rebuilt == expected, "every persisted field must round-trip"
    assert rebuilt.objects[0].sources == ["vision", "sweep"]
    assert rebuilt.objects[0].height_suspect is True
    assert rebuilt.destination == expected.destination
    assert rebuilt.passes == 1 and rebuilt.model == "fake-vision-v0"
    raw = json.loads(path.read_text())
    assert set(raw) == {"room", "saved_at", "map"}


def test_no_schema_version_anywhere(tmp_path):
    st = _store(tmp_path)
    st.merge([_obj()], 0, "fake", 1.0)
    raw = json.loads((tmp_path / "probe-room_latest.json").read_text())
    for obj in (raw, raw["map"], raw["map"]["objects"][0], raw["map"]["diff"]):
        assert not [k for k in obj if "version" in k.lower()], obj


# ---------------------------------------------------------- corrupt/partial

def _doc(objects=None, diff=None, destination=None, map_extra=None):
    m = {"age_s": 0.0, "passes": 1, "model": "fake",
         "objects": [_obj_dict()] if objects is None else objects,
         "destination": destination, "diff": diff or {"appeared": [], "moved": [],
                                                      "vanished": []}}
    if map_extra:
        m.update(map_extra)
    return json.dumps({"room": "r", "saved_at": time.time(), "map": m})


def _obj_dict(**extra):
    o = {"id": "obj_0001", "label": "mat", "x": 1.0, "y": 2.0, "confidence": 0.9,
         "sources": ["vision"], "plane_assumed": "floor", "height_suspect": False,
         "first_seen_s": 0.0, "last_seen_s": 1.0, "motion": "static"}
    o.update(extra)
    return o


CASES = {
    "empty_file": ("", json.JSONDecodeError),
    "whitespace": ("   \n\t ", json.JSONDecodeError),
    "truncated_json": ('{"room": "r", "saved_at": 1.0, "map": {"passes": 1,',
                       json.JSONDecodeError),
    "no_map_key": ('{"room": "r", "saved_at": 1.0}', KeyError),
    "map_null": ('{"room": "r", "saved_at": 1.0, "map": null}', AttributeError),
    "object_unknown_field": (_doc([_obj_dict(bbox_px=[0, 0, 1, 1])]), TypeError),
    "object_missing_required": (_doc([{"label": "mat", "x": 1.0, "y": 2.0,
                                       "confidence": 0.9}]), TypeError),
    "diff_unknown_field": (_doc(diff={"appeared": [], "moved": [], "vanished": [],
                                      "notable": []}), TypeError),
    "destination_unknown_field": (
        _doc(destination={"label": "m", "x": 1.0, "y": 2.0, "confidence": 0.9,
                          "sweep_confirmed": True}), TypeError),
}


def test_corrupt_and_partial_inputs_exact_behaviour(tmp_path):
    seen = {}
    for name, (text, expected) in CASES.items():
        p = tmp_path / f"{name}.json"
        p.write_text(text)
        with pytest.raises(expected) as ei:
            load_map(p)
        seen[name] = f"{type(ei.value).__name__}: {ei.value}"
        print(f"  {name:28s} -> {seen[name][:150]}")
    assert len(seen) == len(CASES)


def test_map_level_unknown_keys_are_silently_ignored(tmp_path):
    p = tmp_path / "future.json"
    p.write_text(_doc(map_extra={"schema_version": 2}))
    loaded = load_map(p)          # forward-compat asymmetry vs object/diff level
    assert loaded.passes == 1
    print("map-level 'schema_version' ignored; object-level would raise TypeError")


# ------------------------------------------------------------- nasty inputs

def test_load_map_ignores_room_field_and_does_not_check_identity(tmp_path):
    st = _store(tmp_path, room="roomA")
    st.merge([_obj("mat", 1.0, 1.0)], 0, "fake", 1.0)
    src = tmp_path / "roomA_latest.json"
    dst = tmp_path / "roomB_latest.json"
    dst.write_text(src.read_text())          # wrong-room copy
    loaded = load_map(dst)                   # no cross-check anywhere
    payload = json.loads(dst.read_text())
    assert payload["room"] == "roomA"
    assert loaded.objects[0].label == "mat"
    print("loaded roomB_latest.json whose payload says room='roomA': accepted")


def test_room_name_is_used_raw_in_paths(tmp_path):
    st = _store(tmp_path, room="../escape")
    st.merge([_obj("x", 1.0, 1.0)], 0, "fake", 1.0)
    escaped = tmp_path.parent / "escape_latest.json"
    print("path used:", tmp_path / "../escape_latest.json", "-> exists:",
          escaped.exists())
    assert escaped.exists(), "no sanitisation of room_name in _save_latest"


def test_events_log_is_shared_across_rooms_and_has_no_room_field(tmp_path):
    _store(tmp_path, room="roomA").merge([_obj("a", 1.0, 1.0)], 0, "fake", 1.0)
    _store(tmp_path, room="roomB").merge([_obj("b", 2.0, 2.0)], 0, "fake", 2.0)
    lines = (tmp_path / "events.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2                    # one file, both rooms
    ev = json.loads(lines[0])
    assert "room" not in ev
    print("events.jsonl line keys:", sorted(ev), "| bytes/line ~",
          len(lines[0].encode()) + 1)


def test_saved_age_is_frozen_and_wall_clock_lives_in_saved_at(tmp_path):
    st = _store(tmp_path)
    st.merge([_obj()], 0, "fake", t_pass=30.0)
    raw = json.loads((tmp_path / "probe-room_latest.json").read_text())
    loaded = load_map(tmp_path / "probe-room_latest.json")
    assert raw["map"]["age_s"] == 0.0        # age at the saving pass, always 0 there
    assert loaded.age_s == 0.0
    assert raw["saved_at"] > 1e9             # wall clock: the only cross-session time
    print("map.age_s =", loaded.age_s, "| saved_at (wall) =", raw["saved_at"])


def test_save_swallows_oserror_silently(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file, not a directory")
    st = _store(str(blocker), room="r")      # store_dir is a file
    merged = st.merge([_obj()], 0, "fake", 1.0)   # must not raise
    assert merged.passes == 1
    print("store_dir pointing at a file: merge survived, no artifact, no signal")


# ------------------------------------------------------------------ timing

def test_timing_100_object_map(tmp_path):
    st = _store(tmp_path)
    objs = [_obj(f"label {i}", 0.01 * i, 0.02 * i, confidence=0.5 + 0.004 * i)
            for i in range(100)]
    merges = []
    for k in range(20):
        t0 = time.perf_counter()
        st.merge(objs, 0, "fake", float(k))
        merges.append((time.perf_counter() - t0) * 1000.0)
    saves, loads = [], []
    path = tmp_path / "probe-room_latest.json"
    for _ in range(20):
        t0 = time.perf_counter()
        st._save_latest()
        saves.append((time.perf_counter() - t0) * 1000.0)
        t0 = time.perf_counter()
        load_map(path)
        loads.append((time.perf_counter() - t0) * 1000.0)

    def med(xs):
        return sorted(xs)[len(xs) // 2]

    size = path.stat().st_size
    print(f"100 objects: merge(incl. save) median {med(merges):.2f} ms "
          f"max {max(merges):.2f} ms | save median {med(saves):.2f} ms | "
          f"load median {med(loads):.2f} ms | latest.json {size} bytes")
    assert med(merges) < 500.0 and med(loads) < 200.0