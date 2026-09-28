"""Merge/diff: ids, raw-displacement motion, smoothing, vanish timing, persistence."""
import json
import math
from dataclasses import replace

from config import SemanticsConfig
from scene import SemanticObject
from semantics import SemanticStore, load_map


def _store(tmp_path, **overrides) -> SemanticStore:
    cfg = replace(SemanticsConfig(), store_dir=str(tmp_path), **overrides)
    return SemanticStore(cfg, "testroom")


def _obj(label: str, x: float, y: float, score: float = 0.9, hs: bool = False) -> SemanticObject:
    return SemanticObject(id="", label=label, x=x, y=y, confidence=score, height_suspect=hs)


def test_appear_move_vanish(tmp_path):
    st = _store(tmp_path)
    m = st.merge([_obj("mat", 3.0, 1.2, 0.9), _obj("ball", 4.5, 2.0, 0.8)], 0, "fake", 0.0)
    assert len(m.diff.appeared) == 2 and not m.diff.moved and not m.diff.vanished
    ids = {o.label: o.id for o in m.objects}
    mat_id = ids["mat"]

    m = st.merge([_obj("mat", 3.03, 1.18, 0.9), _obj("ball", 4.52, 2.01, 0.8)], 0, "fake", 1.0)
    assert not m.diff.appeared and not m.diff.moved, "jitter must not count as movement"

    m = st.merge([_obj("mat", 3.35, 1.21, 0.9), _obj("ball", 4.5, 2.0, 0.8)], 0, "fake", 2.0)
    assert mat_id in m.diff.moved and not m.diff.appeared

    m = st.merge([_obj("ball", 4.5, 2.0, 0.8)], 0, "fake", 3.0)
    assert not m.diff.vanished, "one missed pass must not vanish an object"
    m = st.merge([_obj("ball", 4.5, 2.0, 0.8)], 0, "fake", 4.0)
    assert m.diff.vanished == [mat_id], "two missed passes must vanish exactly once"
    m = st.merge([_obj("ball", 4.5, 2.0, 0.8)], 0, "fake", 5.0)
    assert not m.diff.vanished, "vanish fires once, not on every later pass"


def test_motion_uses_raw_displacement_not_the_ema_step(tmp_path):
    """Finding #1: a 0.35 m move post-smoothing reads 0.14 m and is missed."""
    st = _store(tmp_path)                                   # ema 0.4, move threshold 0.25
    st.merge([_obj("mat", 3.0, 1.2)], 0, "fake", 0.0)
    m = st.merge([_obj("mat", 3.35, 1.2)], 0, "fake", 1.0)  # raw displacement 0.35 m
    assert m.diff.moved, "raw 0.35 m move must be reported despite EMA"
    # the smoothed step is only 0.4 * 0.35 = 0.14, below the threshold by design
    assert abs(m.objects[0].x - (0.6 * 3.0 + 0.4 * 3.35)) < 1e-6
    assert abs(m.objects[0].x - 3.0) < 0.25
    assert m.objects[0].motion == "moved"
    m = st.merge([_obj("mat", 3.36, 1.2)], 0, "fake", 2.0)  # raw 0.01 vs smoothed position
    assert not m.diff.moved and m.objects[0].motion == "static"


def test_large_jump_is_a_new_object(tmp_path):
    st = _store(tmp_path)
    st.merge([_obj("mat", 1.0, 1.0)], 0, "fake", 0.0)
    m = st.merge([_obj("mat", 1.9, 1.0)], 0, "fake", 1.0)   # 0.9 m > match radius
    assert m.diff.appeared and not m.diff.moved


def test_ids_stable_with_two_same_label_objects(tmp_path):
    st = _store(tmp_path)
    st.merge([_obj("mat", 1.0, 1.0), _obj("mat", 4.0, 1.0)], 0, "fake", 0.0)
    m = st.merge([_obj("mat", 4.06, 1.02), _obj("mat", 1.04, 0.99)], 0, "fake", 1.0)
    assert not m.diff.appeared, "same-label objects must match by proximity, not order"
    xs = sorted(o.x for o in m.objects)
    assert xs[0] < 1.5 and xs[1] > 3.5, xs


def test_label_matching_folds_case_and_plurals(tmp_path):
    st = _store(tmp_path)
    st.merge([_obj("Blue Mat", 3.0, 1.2)], 0, "fake", 0.0)
    m = st.merge([_obj("blue mats", 3.02, 1.19)], 0, "fake", 1.0)
    assert not m.diff.appeared and not m.diff.vanished, "one object, not two"
    assert len(m.objects) == 1 and m.objects[0].label == "Blue Mat", \
        "the display label keeps its original spelling"


def test_contested_detection_goes_to_the_nearest_object(tmp_path):
    """Distance-ordered assignment: insertion order must not decide a match."""
    st = _store(tmp_path)
    st.merge([_obj("mat", 1.0, 1.0), _obj("mat", 1.4, 1.0)], 0, "fake", 0.0)
    m = st.merge([_obj("mat", 1.3, 1.0)], 0, "fake", 1.0)  # 0.30 m from obj 1, 0.10 m from obj 2
    assert not m.diff.appeared
    assert st.objs["obj_0001"].x == 1.0, "the far object must not steal the detection"
    assert abs(st.objs["obj_0002"].x - (0.6 * 1.4 + 0.4 * 1.3)) < 1e-9


def test_smoothing_converges(tmp_path):
    st = _store(tmp_path)
    st.merge([_obj("mat", 1.0, 1.0)], 0, "fake", 0.0)
    for i in range(30):
        st.merge([_obj("mat", 1.3, 1.0)], 0, "fake", 1.0 + i)
    o = st.snapshot(32.0).objects[0]
    assert abs(o.x - 1.3) < 0.02


def test_appeared_respects_min_confidence(tmp_path):
    st = _store(tmp_path)
    m = st.merge([_obj("ghost", 2.0, 2.0, score=0.3)], 0, "fake", 0.0)
    assert len(m.objects) == 1, "low-confidence objects still enter the map"
    assert m.diff.appeared == [], "...but they are not announced as appeared"


def test_sub_threshold_hits_only_refresh_freshness(tmp_path):
    """A low-score detection must not move the object or overwrite its flags."""
    st = _store(tmp_path)
    st.merge([_obj("mat", 3.0, 1.2, 0.9, hs=True)], 0, "fake", 0.0)
    m = st.merge([_obj("mat", 3.4, 1.2, score=0.2)], 0, "fake", 1.0)
    o = m.objects[0]
    assert o.x == 3.0 and o.confidence == 0.9 and o.height_suspect is True
    assert o.last_seen_s == 1.0, "sub-threshold hits still count as seen"
    m = st.merge([_obj("mat", 3.4, 1.2, score=0.2)], 0, "fake", 2.0)
    assert not m.diff.vanished, "freshness from low-confidence hits keeps it alive"
    assert m.diff.moved == [], "no motion is reported from a rejected detection"


def test_height_suspect_flips_only_on_two_of_three(tmp_path):
    """Hysteresis: one noisy probe must not flap the flag either way."""
    st = _store(tmp_path)
    st.merge([_obj("box", 3.0, 1.0)], 0, "fake", 0.0)
    m = st.merge([_obj("box", 3.0, 1.0, hs=True)], 0, "fake", 1.0)
    assert m.objects[0].height_suspect is False, "1 of 1 is not a majority"
    m = st.merge([_obj("box", 3.0, 1.0, hs=True)], 0, "fake", 2.0)
    assert m.objects[0].height_suspect is True, "2 of 3 agree -> flip up"
    m = st.merge([_obj("box", 3.0, 1.0, hs=False)], 0, "fake", 3.0)
    assert m.objects[0].height_suspect is True, "one clean probe must not flip it back"
    m = st.merge([_obj("box", 3.0, 1.0, hs=False)], 0, "fake", 4.0)
    assert m.objects[0].height_suspect is False, "2 of 3 clear -> flip down"


def test_height_suspect_and_rejected_propagate(tmp_path):
    st = _store(tmp_path)
    m = st.merge([_obj("box", 3.0, 1.0, hs=True)], rejected=2, model="fake", t_pass=0.0)
    assert m.objects[0].height_suspect is True
    assert st.rejected_total == 2
    assert m.age_s == 0.0


def test_eviction_caps_resurrection_and_map_growth(tmp_path):
    """After max_misses missed passes the object is gone; a return is a new id."""
    st = _store(tmp_path, max_misses=3)
    st.merge([_obj("mat", 3.0, 1.2)], 0, "fake", 0.0)
    for i in (1.0, 2.0):
        st.merge([], 0, "fake", i)
    assert "obj_0001" in st.objs, "not evicted before the cap"
    m = st.merge([], 0, "fake", 3.0)                       # third miss -> evict
    assert "obj_0001" not in st.objs
    assert m.diff.vanished == [], "the vanish event already fired at vanish_passes"
    m = st.merge([_obj("mat", 3.0, 1.2)], 0, "fake", 4.0)
    assert m.diff.appeared == ["obj_0002"], "a returning object is a new identity"
    assert len(m.objects) == 1, "the map does not grow from churn"


def test_persistence_round_trip(tmp_path):
    st = _store(tmp_path)
    st.merge([_obj("mat", 3.0, 1.2)], 1, "fake", 0.5)
    latest = tmp_path / "testroom_latest.json"
    assert latest.exists()
    raw = json.loads(latest.read_text())
    assert raw["map"]["objects"][0]["label"] == "mat"
    assert raw["map"]["passes"] == 1

    rebuilt = load_map(latest)
    assert rebuilt.objects[0].label == "mat"
    assert rebuilt.passes == 1
    assert math.hypot(rebuilt.objects[0].x - 3.0, rebuilt.objects[0].y - 1.2) < 1e-9

    events = (tmp_path / "events.jsonl").read_text().strip().splitlines()
    assert len(events) == 1
    ev = json.loads(events[0])
    assert ev["appeared"] == ["obj_0001"] and ev["labels"]["obj_0001"] == "mat"


def test_snapshot_is_a_copy(tmp_path):
    st = _store(tmp_path)
    st.merge([_obj("mat", 3.0, 1.2)], 0, "fake", 0.0)
    snap = st.snapshot(0.0)
    snap.objects[0].x = 99.0
    assert st.objs["obj_0001"].x == 3.0, "snapshots must not alias live state"
    snap.diff.appeared.append("junk")
    assert st.diff.appeared == ["obj_0001"], "diffs must not alias either"
