#!/usr/bin/env python3
"""ToF sweep feasibility sim for jev-rover (marker-less localization).

Simulates a VL53L1X-class ToF on a 180-deg servo sweep:
- raycasts the room's boundary walls + the four furniture blocks
- sensor model: 4 m range, ~1.5 cm + 1% noise, 5% dropouts, 2% outliers
- optional motion smear: beams sampled while the rover drives during the sweep
Then recovers pose via scan matching:
- global search (no prior)      -> bootstrap
- local search  (IMU/prev prior)-> tracking
"""
import math

import numpy as np

MAX_RANGE = 4.0
ROAM = (0.15, 0.15, 6.25, 3.45)
WALL_T = 0.02
WALLS = [
    (ROAM[0] - WALL_T, ROAM[1] - WALL_T, ROAM[0], ROAM[3] + WALL_T),
    (ROAM[2], ROAM[1] - WALL_T, ROAM[2] + WALL_T, ROAM[3] + WALL_T),
    (ROAM[0] - WALL_T, ROAM[1] - WALL_T, ROAM[2] + WALL_T, ROAM[1]),
    (ROAM[0] - WALL_T, ROAM[3], ROAM[2] + WALL_T, ROAM[3] + WALL_T),
]
FURN = [
    (0.384, 1.98, 1.024, 3.42),    # sofa
    (2.56, 0.144, 3.712, 0.792),   # table
    (4.224, 1.98, 4.736, 2.52),    # box
    (5.632, 2.16, 6.144, 3.312),   # shelf
]
OBST = np.array(WALLS + FURN, float)  # (M,4)


def ray_aabb(ox, oy, dx, dy, boxes):
    """Ray vs axis-aligned boxes. ox..dy broadcastable to (N,1); boxes (M,4).
    Returns entry distance t (N,M), inf when no forward hit."""
    x0, y0, x1, y1 = boxes[None, :, 0], boxes[None, :, 1], boxes[None, :, 2], boxes[None, :, 3]
    with np.errstate(divide="ignore", invalid="ignore"):
        tx1 = (x0 - ox) / dx
        tx2 = (x1 - ox) / dx
        ty1 = (y0 - oy) / dy
        ty2 = (y1 - oy) / dy
    tmin = np.minimum(tx1, tx2)
    tmax = np.maximum(tx1, tx2)
    tymin = np.minimum(ty1, ty2)
    tymax = np.maximum(ty1, ty2)
    tmin = np.maximum(tmin, tymin)
    tmax = np.minimum(tmax, tymax)
    hit = (tmax >= np.maximum(tmin, 0.0)) & (tmin > 1e-9)
    t = np.where(hit, tmin, np.inf)
    return t


def ranges_for(cands, rel_deg, max_range=MAX_RANGE, motion=None, sweep_s=1.0):
    """Expected range per beam for candidate poses. cands (N,3) x,y,yaw_deg.

    motion: optional assumed (v_mps, w_deg_s) commanded during the sweep for
    desmear matching -- beam b is predicted from the pose advanced along the
    candidate's arc by (v, w) over sweep_s * frac(b).
    """
    N = cands.shape[0]
    yaw = np.deg2rad(cands[:, 2])
    ox = cands[:, 0:1]
    oy = cands[:, 1:2]
    out = np.full((N, len(rel_deg)), np.inf)
    B = len(rel_deg)
    for b, rel in enumerate(rel_deg):
        if motion is None:
            oxb, oyb, yawb = ox, oy, yaw
        else:
            v, w_deg = motion
            tb = sweep_s * (b / max(1, B - 1))
            w = np.deg2rad(w_deg)
            if abs(w_deg) < 1e-12:
                oxb = ox + (v * tb * np.cos(yaw))[:, None]
                oyb = oy + (v * tb * np.sin(yaw))[:, None]
                yawb = yaw
            else:
                th = yaw + w * tb
                R = v / w
                oxb = ox + (R * (np.sin(th) - np.sin(yaw)))[:, None]
                oyb = oy - (R * (np.cos(th) - np.cos(yaw)))[:, None]
                yawb = th
        ang = yawb + np.deg2rad(rel)
        t = ray_aabb(oxb, oyb, np.cos(ang)[:, None], np.sin(ang)[:, None], OBST)
        tmin = t.min(axis=1)
        out[:, b] = np.where(tmin <= max_range, tmin, np.inf)
    return out


def advance(pose, v, w_deg_s, t):
    """Arc advance of (x, y, yaw_deg) under constant (v, w) for t seconds."""
    x, y, yaw = pose
    w = math.radians(w_deg_s)
    if abs(w) < 1e-12:
        return x + v * t * math.cos(math.radians(yaw)), y + v * t * math.sin(math.radians(yaw)), yaw
    R = v / w
    th = math.radians(yaw) + w * t
    return (x + R * (math.sin(th) - math.sin(math.radians(yaw))),
            y - R * (math.cos(th) - math.cos(math.radians(yaw))),
            math.degrees(th))


def simulate_sweep(pose, rel_deg, rng, v=0.0, w_deg_s=0.0, sweep_s=1.0,
                   noise_abs=0.015, noise_rel=0.01, dropout=0.05, outlier=0.02):
    B = len(rel_deg)
    meas = np.full(B, np.nan)
    pose = np.asarray(pose, float)
    for b, rel in enumerate(rel_deg):
        tb = sweep_s * (b / max(1, B - 1))
        px, py, pyaw = advance(pose, v, w_deg_s, tb)
        ang = math.radians(pyaw + rel)
        t = ray_aabb(np.array([[px]]), np.array([[py]]),
                     np.array([[math.cos(ang)]]), np.array([[math.sin(ang)]]), OBST)
        d0 = float(t.min())
        if not np.isfinite(d0) or d0 > MAX_RANGE:
            continue
        if rng.random() < dropout:
            continue
        d = d0 * (1.0 + rng.normal(0, noise_rel)) + rng.normal(0, noise_abs)
        if rng.random() < outlier:
            d += rng.uniform(0.3, 1.0)
        if 0.04 <= d <= MAX_RANGE:
            meas[b] = d
    return meas


def score(meas, exp):
    """Robust loss: |residual| capped at 0.5 m; expectation-miss vs beam hit -> 0.5."""
    valid = ~np.isnan(meas)
    loss = np.zeros(exp.shape[0])
    e = exp[:, valid]
    m = meas[valid]
    r = np.abs(e - m)
    r = np.where(np.isfinite(e), np.minimum(r, 0.5), 0.5)
    return r.sum(axis=1)


def global_match(meas, rel_deg, step=0.2, yaw_step=10.0):
    xs = np.arange(ROAM[0], ROAM[2] + 1e-9, step)
    ys = np.arange(ROAM[1], ROAM[3] + 1e-9, step)
    yaws = np.arange(0, 360 + 1e-9, yaw_step)
    X, Y, Z = np.meshgrid(xs, ys, yaws, indexing="ij")
    cands = np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1)
    exp = ranges_for(cands, rel_deg)
    loss = score(meas, exp)
    i = int(np.argmin(loss))
    return cands[i], loss[i], cands / 1.0, loss


def local_match(meas, prior, rel_deg, dx=0.3, dy=0.3, dyaw=20.0, step=0.05, yaw_step=5.0,
                motion=None, sweep_s=1.0):
    xs = np.arange(-dx, dx + 1e-9, step) + prior[0]
    ys = np.arange(-dy, dy + 1e-9, step) + prior[1]
    yaws = np.arange(-dyaw, dyaw + 1e-9, yaw_step) + prior[2]
    X, Y, Z = np.meshgrid(xs, ys, yaws, indexing="ij")
    cands = np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1)
    exp = ranges_for(cands, rel_deg, motion=motion, sweep_s=sweep_s)
    loss = score(meas, exp)
    i = int(np.argmin(loss))
    return cands[i]


def wrap(a):
    return (a + 180.0) % 360.0 - 180.0


def random_free_pose(rng):
    while True:
        x = rng.uniform(ROAM[0] + 0.3, ROAM[2] - 0.3)
        y = rng.uniform(ROAM[1] + 0.3, ROAM[3] - 0.3)
        if any(x0 - 0.25 < x < x1 + 0.25 and y0 - 0.25 < y < y1 + 0.25
               for x0, y0, x1, y1 in FURN):
            continue
        return np.array([x, y, rng.uniform(0, 360)])


def run_trials(n, rel_deg, mode, rng, v=0.0, w=0.0, sweep_s=1.0, err_v=0.0, err_w=0.0):
    errs = []
    for _ in range(n):
        pose = random_free_pose(rng)
        meas = simulate_sweep(pose, rel_deg, rng, v=v, w_deg_s=w, sweep_s=sweep_s)
        if mode == "global":
            est, _, _, _ = global_match(meas, rel_deg)
        else:
            # prior = truth + small uncertainty (~5 cm, ~3 deg)
            prior = pose + np.array([rng.normal(0, 0.05), rng.normal(0, 0.05), rng.normal(0, 3)])
            motion = None
            if mode == "desmear":
                motion = (v * (1.0 + err_v), w * (1.0 + err_w))
            est = local_match(meas, prior, rel_deg, motion=motion, sweep_s=sweep_s)
        perr = float(np.hypot(est[0] - pose[0], est[1] - pose[1]))
        yerr = abs(float(wrap(est[2] - pose[2])))
        errs.append((perr, min(yerr, 180)))
    errs = np.array(errs)
    return errs


def report(name, errs):
    p = np.percentile(errs[:, 0], [50, 90])
    y = np.percentile(errs[:, 1], [50, 90])
    ok = float(np.mean((errs[:, 0] < 0.15) & (errs[:, 1] < 15.0)) * 100)
    print(f"{name:34s}  pos p50/p90 = {p[0]*100:4.1f}/{p[1]*100:4.1f} cm   "
          f"yaw p50/p90 = {y[0]:4.1f}/{y[1]:4.1f} deg   success {ok:4.0f}%")


def main():
    rng = np.random.default_rng(7)
    # sanity: straight ahead from (3.0, 2.0) facing +x -> right wall
    t = ray_aabb(np.array([[3.0]]), np.array([[2.0]]), np.array([[1.0]]),
                 np.array([[0.0]]), OBST).min()
    print(f"sanity: range to right wall from (3.0,2.0) facing +x: {t:.3f} m (expect ~3.25)")

    rel2 = np.arange(-90, 90 + 1e-9, 2.0)    # 91 beams, slow full-quality sweep
    rel4 = np.arange(-90, 90 + 1e-9, 4.0)    # 46 beams, fast/global
    rel6 = np.arange(-90, 90 + 1e-9, 6.0)    # 31 beams, ~1 s sweep at 30 Hz sensor
    n = 30

    print("\n--- bootstrap (no prior, global search) ---")
    report("global, 4deg beams", run_trials(n, rel4, "global", rng))

    print("\n--- tracking (prior from IMU/prev: 5cm/3deg) ---")
    report("local, stationary, 2deg sweep", run_trials(n, rel2, "local", rng))
    report("local, stationary, 4deg sweep", run_trials(n, rel4, "local", rng))
    report("local, stationary, 6deg sweep", run_trials(n, rel6, "local", rng))
    report("local, moving 0.45 m/s, 1.0 s sweep", run_trials(n, rel2, "local", rng, v=0.45, sweep_s=1.0))
    report("local, moving 0.45 m/s, 0.5 s sweep", run_trials(n, rel2, "local", rng, v=0.45, sweep_s=0.5))
    report("local, turning 30 deg/s, 1.0 s sweep", run_trials(n, rel2, "local", rng, w=30, sweep_s=1.0))

    print("\n--- desmear: matcher models the commanded motion (v, w) ---")
    report("desmear, drive 0.45 m/s", run_trials(n, rel2, "desmear", rng, v=0.45, sweep_s=1.0))
    report("desmear, drive +10% speed err", run_trials(n, rel2, "desmear", rng, v=0.45, sweep_s=1.0, err_v=0.10))
    report("desmear, drive +25% speed err", run_trials(n, rel2, "desmear", rng, v=0.45, sweep_s=1.0, err_v=0.25))
    report("desmear, pivot 30 deg/s", run_trials(n, rel2, "desmear", rng, w=30, sweep_s=1.0))
    report("desmear, drive 0.3 + turn 20 deg/s", run_trials(n, rel2, "desmear", rng, v=0.3, w=20, sweep_s=1.0))
    report("desmear, drive+turn, +10% v +15% w", run_trials(n, rel2, "desmear", rng, v=0.3, w=20, sweep_s=1.0, err_v=0.10, err_w=0.15))
    report("desmear, pivot 60 deg/s (hard)", run_trials(n, rel2, "desmear", rng, w=60, sweep_s=1.0))
    report("desmear, 4deg, pivot 30 deg/s", run_trials(n, rel4, "desmear", rng, w=30, sweep_s=1.0))


if __name__ == "__main__":
    main()
