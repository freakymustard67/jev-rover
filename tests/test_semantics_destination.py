"""Destination resolution: Jaccard ranking, ambiguity -> Jev Choice, standoff."""
from config import SemanticsConfig
from scene import SemanticMap, SemanticObject
from semantics import (approach_point, rank_candidates, resolve_destination)


def _map() -> SemanticMap:
    return SemanticMap(passes=1, model="fake-vision-v0", objects=[
        SemanticObject(id="obj_0001", label="blue mat", x=3.2, y=1.1, confidence=0.9),
        SemanticObject(id="obj_0002", label="red mat", x=1.0, y=2.0, confidence=0.8),
        SemanticObject(id="obj_0003", label="blue box", x=4.5, y=2.3, confidence=0.85),
        SemanticObject(id="obj_0004", label="charger", x=5.0, y=3.0, confidence=0.7),
    ])


class FakeJev:
    """Records the call; returns one of the offered labels."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def system_one(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answer

        class _A:
            choice = answer

        class _R:
            answers = {"label": _A()}

        return _R()


def test_ranking_and_resolution():
    sem = _map()
    assert resolve_destination("go to the blue mat", sem).label == "blue mat"
    assert resolve_destination("find the llama", sem) is None
    assert resolve_destination("the blue box", sem).label == "blue box"
    assert resolve_destination("where is the charger", sem).label == "charger"


def test_bare_mat_is_ambiguous_and_jev_decides():
    sem = _map()
    ranked = rank_candidates("mat", sem.objects, SemanticsConfig())
    assert [c.obj.label for c in ranked[:2]] == ["blue mat", "red mat"]
    assert abs(ranked[0].score - ranked[1].score) <= 0.15

    jev = FakeJev("red mat")
    dest = resolve_destination("mat", sem, jev=jev, cfg=SemanticsConfig())
    assert dest is not None and dest.label == "red mat"
    assert dest.object_id == "obj_0002"
    assert len(jev.calls) == 1, "exactly one budgeted Choice call"
    question = jev.calls[0]["questions"]["label"]
    criteria = getattr(question, "criteria", None)
    assert criteria is not None and set(criteria) == {"blue mat", "red mat"}, \
        "code owns the candidate options"


def test_ambiguous_without_jev_picks_deterministically():
    sem = _map()
    dest = resolve_destination("mat", sem, jev=None)
    assert dest is not None and dest.label == "blue mat"


def test_unrelated_query_scores_below_threshold():
    sem = _map()
    assert rank_candidates("tidy the garage", sem.objects) == []
    assert resolve_destination("tidy the garage", sem) is None


def test_approach_point_standoff():
    from scene import Destination
    dest = Destination(label="mat", x=3.0, y=1.0, confidence=0.9)
    ax, ay = approach_point(dest, (1.0, 1.0), 0.35)
    assert abs((ax - 3.0) ** 2 + (ay - 1.0) ** 2 - 0.35 ** 2) < 1e-9
    assert ax < 3.0 and abs(ay - 1.0) < 1e-9
    # degenerate: standing on the object
    assert approach_point(dest, (3.0, 1.0), 0.35) == (3.0, 1.0)
