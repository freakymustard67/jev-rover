"""Prompt helpers, label canonicalisation and box rescale (hermetic)."""
from vision import (DEFAULT_LOCAL_MODEL, build_prompt, canonical_label,
                    merge_prompt, rescale_boxes)


def test_build_prompt_normalises_and_collapses_whitespace():
    assert build_prompt(["  Blue Mat ", "BOTTLE"]) == ["blue mat", "bottle"]
    assert build_prompt(["", "   ", "box"]) == ["box"]


def test_build_prompt_attributes_repeats_class_token():
    assert build_prompt(["blue mat"], attributes=True) == ["blue mat", "mat"]
    assert build_prompt(["mat"], attributes=True) == ["mat"]


def test_merge_prompt_canonical_form():
    assert merge_prompt(["mat", "box"]) == "mat. box."
    assert merge_prompt(["blue mat", "mat"]) == "blue mat. mat."


def test_canonical_label_exact_then_subset():
    labels = ["mat", "box", "plastic bottle"]
    assert canonical_label("Mat", labels) == "mat"
    assert canonical_label("  blue   mat ", labels) == "mat"      # colour words, class mapped
    assert canonical_label("plastic bottle", labels) == "plastic bottle"
    assert canonical_label("plastic bottle cap", labels) == "plastic bottle"
    # the phrase must CONTAIN the configured label; a bare class noun of a
    # multi-word label is not silently upgraded
    assert canonical_label("bottle", labels) is None


def test_canonical_label_drops_unknown():
    assert canonical_label("llama", ["mat", "box"]) is None
    assert canonical_label("", ["mat"]) is None


def test_rescale_boxes_round_trip():
    assert rescale_boxes([(10, 20, 30, 40)], 0.5) == [(20, 40, 60, 80)]
    assert rescale_boxes([(10, 20, 30, 40)], 1.0) == [(10, 20, 30, 40)]


def test_default_model_is_the_owner_decision():
    # D1: MM-GDINO-T (Apache-2.0), per docs/reviews/m1-semantics/m2-design.md §2.1
    assert DEFAULT_LOCAL_MODEL == \
        "openmmlab-community/mm_grounding_dino_tiny_o365v1_goldg_v3det"
