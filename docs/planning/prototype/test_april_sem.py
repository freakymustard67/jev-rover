"""Tests + measurements for the M1 semantics math prototype."""
import math

import numpy as np
import pytest

from april_sem import (
    FURNITURE, Detection, SemanticMap,
    bbox_bottom_center_uv, bbox_center_uv, bbox_from_points,
    floor_homography, make_camera, near_furniture, project_points,
    ray_between_suspect, resolve_destination, world_from_image,
)

# camera: ultrawide-ish, high placement behind the room (search: only this class of
# pose fits all four room corners in a 1920x1080 frame)
K, R, POS = make_camera(pos=(3.2, -2.5, 3.8), target=(3.2, 1.8, 0.0),
                        fovx_deg=90.0, width=1920, height=1080)
H = floor_homography(K, R, POS)
F = (K[0, 0], K[1, 1])  # focal length px


def proj(pts):
    return project_points(K, R, POS, pts)


def floor_err(uv, truth_xy):
    p = world_from_image(H, [uv])[0]
    return float(math.hypot(p[0] - truth_xy[0], p[1] - truth_xy[1])), p


def test_homography_round_trip():
    rng = np.random.default_rng(0)
    pts = np.column_stack([rng.uniform(0.3, 6.1, 30), rng.uniform(0.3, 3.3, 30), np.zeros(30)])
    uv = proj(pts)
    back = world_from_image(H, uv)
    err = np.max(np.linalg.norm(back - pts[:, :2], axis=1))
    assert err < 1e-9, f"round trip error {err}"


def test_room_corners_visible():
    corners = [(0, 0), (6.4, 0), (6.4, 3.6), (0, 3.6), (3.2, 1.8)]
    px = proj([(x, y, 0) for x, y in corners])
    for (x, y), (u, v) in zip(corners, px):
        print(f"  corner ({x},{y}) -> pixel ({u:7.1f},{v:7.1f})  {'IN' if 0<=u<1920 and 0<=v<1080 else 'OUT'}")
    # the room must be substantially visible
    inside = [(0 <= u < 1920 and 0 <= v < 1080) for u, v in px[:4]]
    assert sum(inside) >= 3, f"only {sum(inside)}/4 room corners visible; adjust camera sim pose"


def test_flat_object_estimators():
    """A flat mat: centroid of the projected silhouette should recover the center."""
    cx, cy = 3.0, 1.2
    hw, hh = 0.25, 0.30  # 0.5 x 0.6 m mat
    per = [(cx - hw, cy - hh), (cx + hw, cy - hh), (cx + hw, cy + hh), (cx - hw, cy + hh),
           (cx, cy - hh), (cx + hw, cy), (cx, cy + hh), (cx - hw, cy)]
    uv = proj([(x, y, 0) for x, y in per])
    bb = bbox_from_points(uv)

    e_cent, _ = floor_err(bbox_center_uv(bb), (cx, cy))
    e_bot, _ = floor_err(bbox_bottom_center_uv(bb), (cx, cy))
    print(f"\n  flat mat 0.5x0.6 m at ({cx},{cy}):")
    print(f"    centroid      -> {e_cent*100:5.1f} cm error")
    print(f"    bottom-center -> {e_bot*100:5.1f} cm error")
    assert e_cent < 0.05, f"centroid error too big: {e_cent}"
    assert e_bot > e_cent, "bottom-center bias not visible; test assumption broken"


def test_flat_object_noise_sensitivity():
    cx, cy = 3.0, 1.2
    hw, hh = 0.25, 0.30
    per = [(cx - hw, cy - hh), (cx + hw, cy - hh), (cx + hw, cy + hh), (cx - hw, cy + hh)]
    uv = proj([(x, y, 0) for x, y in per])
    rng = np.random.default_rng(1)
    errs = []
    for _ in range(100):
        bb = bbox_from_points(uv, jitter_px=3.0, rng=rng)
        e, _ = floor_err(bbox_center_uv(bb), (cx, cy))
        errs.append(e)
    e = np.array(errs)
    print(f"\n  flat mat with +/-3 px bbox jitter: p50 {np.median(e)*100:.1f} cm, p90 {np.percentile(e,90)*100:.1f} cm")
    assert np.percentile(e, 90) < 0.10


def test_standing_ball_estimators():
    """A standing ball: bottom-center should be closer to the contact point."""
    bx, by, r = 4.5, 2.0, 0.07
    c3 = np.array([bx, by, r])
    cu = proj([c3])[0]
    depth = float((c3 - POS) @ R[:, 2])
    rpx = F[0] * r / depth
    bb = (cu[0] - rpx, cu[1] - rpx, cu[0] + rpx, cu[1] + rpx)
    e_bot, p_bot = floor_err(bbox_bottom_center_uv(bb), (bx, by))
    e_cent, p_cent = floor_err(bbox_center_uv(bb), (bx, by))
    print(f"\n  ball r={r} m at ({bx},{by}):")
    print(f"    centroid      -> {e_cent*100:5.1f} cm error")
    print(f"    bottom-center -> {e_bot*100:5.1f} cm error")
    assert min(e_bot, e_cent) < 0.15


def test_elevated_object_is_dangerous_and_flagged():
    """Object on the table: floor projection is wrong; near_furniture must catch it."""
    tx0, ty0, tx1, ty1 = 2.56, 0.144, 3.712, 0.792    # table
    base = (3.0, 0.5)
    z0, z1 = 0.75, 1.05  # object 0.3 m tall sitting on the table top
    hw = 0.10
    pts = []
    for (dx, dy) in [(-hw, -hw), (hw, -hw), (hw, hw), (-hw, hw)]:
        pts.append((base[0] + dx, base[1] + dy, z0))
        pts.append((base[0] + dx, base[1] + dy, z1))
    uv = proj(pts)
    bb = bbox_from_points(uv)
    e, p = floor_err(bbox_bottom_center_uv(bb), base)
    prox = near_furniture(float(p[0]), float(p[1]), margin=0.25)
    ray = ray_between_suspect(float(p[0]), float(p[1]), cam_xy=(POS[0], POS[1]))
    print(f"\n  object on table at ({base[0]},{base[1]}) top z=0.75:")
    print(f"    floor projection -> ({p[0]:.2f},{p[1]:.2f})  error {e*100:5.1f} cm")
    print(f"    proximity flag (0.25 m margin) -> {prox}")
    print(f"    ray-between flag (walk back toward camera) -> {ray}")
    assert e > 0.30, "expected a large error for an elevated object"
    assert ray, "ray-between height_suspect check failed to catch it"


# ------------------------------------------------------------- merge / diff


def test_merge_appear_move_vanish():
    sm = SemanticMap()
    d = sm.update([Detection("mat", 3.0, 1.2, 0.9), Detection("ball", 4.5, 2.0, 0.8)], t=0.0)
    assert len(d.appeared) == 2 and not d.moved and not d.vanished
    ids = {o.label: oid for oid, o in sm.objs.items()}
    mat_id = ids["mat"]

    d = sm.update([Detection("mat", 3.03, 1.18, 0.9), Detection("ball", 4.52, 2.01, 0.8)], t=1.0)
    assert not d.appeared and not d.moved, "jitter must not count as movement"

    # NOTE: movement must stay within match_radius (0.5 m) or the object reads
    # as vanish+appear instead of a move -- a real design tradeoff, see the
    # large-jump test below.
    d = sm.update([Detection("mat", 3.35, 1.21, 0.9), Detection("ball", 4.5, 2.0, 0.8)], t=2.0)
    assert mat_id in d.moved, "displacement above threshold must be reported"

    d = sm.update([Detection("mat", 3.35, 1.21, 0.9)], t=3.0)
    assert not d.vanished, "one missed pass must not vanish an object yet"
    d = sm.update([Detection("mat", 3.35, 1.21, 0.9)], t=4.0)
    assert len(d.vanished) == 1, "two missed passes must vanish the object"


def test_merge_large_jump_is_new_object():
    """Jump > match_radius: documented behavior = vanish+appear, not a move."""
    sm = SemanticMap()
    sm.update([Detection("mat", 1.0, 1.0)], t=0)
    d = sm.update([Detection("mat", 1.9, 1.0)], t=1)  # 0.9 m jump
    assert d.appeared, "far jump should appear as a new object"
    assert not d.moved


def test_merge_ids_stable_with_two_same_label():
    sm = SemanticMap()
    sm.update([Detection("mat", 1.0, 1.0), Detection("mat", 4.0, 1.0)], t=0)
    a, b = sorted(sm.objs)
    d = sm.update([Detection("mat", 4.06, 1.02), Detection("mat", 1.04, 0.99)], t=1)  # order flipped
    assert not d.appeared, "same-label objects must match by proximity, not order"
    pa = sm.objs[a]
    pb = sm.objs[b]
    assert abs(pa.x - pb.x) > 2.0, "ids swapped"


def test_merge_smoothing_converges():
    sm = SemanticMap()
    sm.update([Detection("mat", 1.0, 1.0)], t=0)
    for i in range(30):
        sm.update([Detection("mat", 1.3, 1.0)], t=1 + i)  # within match radius
    o = next(iter(sm.objs.values()))
    assert abs(o.x - 1.3) < 0.02, f"EMA failed to converge: {o.x}"


# --------------------------------------------------------- destination rank


def _objs():
    sm = SemanticMap()
    sm.update([Detection("blue mat", 3.2, 1.1), Detection("red mat", 1.0, 2.0),
               Detection("blue box", 4.5, 2.3), Detection("charger", 5.0, 3.0)], t=0)
    return list(sm.objs.values())


def test_destination_resolution():
    objs = _objs()

    r = resolve_destination("go to the blue mat", objs)
    print(f"\n  'go to the blue mat' -> {r}")
    assert r["status"] == "ok" and r["label"] == "blue mat"

    r = resolve_destination("mat", objs)
    print(f"  'mat'                -> {r}")
    assert r["status"] == "ambiguous"

    r = resolve_destination("find the llama", objs)
    print(f"  'find the llama'     -> {r}")
    assert r["status"] == "none"

    r = resolve_destination("the blue box", objs)
    print(f"  'the blue box'       -> {r}")
    assert r["status"] == "ok" and r["label"] == "blue box"
