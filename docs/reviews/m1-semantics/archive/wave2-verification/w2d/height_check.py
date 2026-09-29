"""Re-verify report 05's core height-from-homography identity numerically.

Full pinhole simulation: build H (img->world, z=0 plane) from camera pose,
project a vertical segment, apply H to base/top pixels, check
  h_est = Zc * delta / (d + delta)  ==  h
at several positions/heights and camera tilts. Also show the flat-mat bias.
"""
import numpy as np

def look_at(eye, target, up=np.array([0.0, 0.0, 1.0])):
    f = target - eye; f /= np.linalg.norm(f)
    r = np.cross(f, up); r /= np.linalg.norm(r)
    u = np.cross(r, f)
    R = np.stack([r, u, f])           # world->cam (camera looks along +f)
    return R, -R @ eye

def make_H(K, R, t):
    # world (x,y,0,1) -> image: p ~ K [r1 r2 t]
    M = K @ np.column_stack([R[:, 0], R[:, 1], t])   # 3x3
    return np.linalg.inv(M)                          # img px -> world (x,y,1)

def img_of(K, R, t, X):
    p = K @ (R @ X + t)
    return p[:2] / p[2]

for Zc, tilt_deg, f in [(2.0, 35.0, 400.0), (1.6, 55.0, 340.0), (2.4, 25.0, 500.0)]:
    C = np.array([3.0, 2.0, Zc])
    th = np.radians(tilt_deg)
    tgt = C + np.array([0.0, -np.cos(th), -np.sin(th)]) * 2.0   # tilted down
    R, t = look_at(C, tgt)
    K = np.array([[f, 0, 640.0], [0, f, 360.0], [0, 0, 1.0]])
    H = make_H(K, R, t)
    print(f"--- Zc={Zc} tilt={tilt_deg} f={f} ---")
    errs = []
    for B in [(3.6, 1.2, 0.0), (2.4, 2.6, 0.0), (4.5, 0.8, 0.0), (3.0, 1.4, 0.0)]:
        for h in [0.15, 0.25, 0.45]:
            Bv = np.array(B)
            pb = img_of(K, R, t, Bv)
            pt = img_of(K, R, t, Bv + np.array([0, 0, h]))
            Pb = (H @ np.array([*pb, 1.0]))[:2] / (H @ np.array([*pb, 1.0]))[2]
            Pt = (H @ np.array([*pt, 1.0]))[:2] / (H @ np.array([*pt, 1.0]))[2]
            delta = float(np.linalg.norm(Pt - Pb))
            d = float(np.linalg.norm(Bv[:2] - C[:2]))
            h_est = Zc * delta / (d + delta)
            errs.append(abs(h_est - h))
    print("  max |h_est - h| over 12 cases:", f"{max(errs)*100:.2f} cm")
    # flat mat 0.6x0.4: bbox base = near edge centre, top = far edge centre
    Bm = np.array([3.6, 1.2, 0.0])          # near edge centre
    Pm = img_of(K, R, t, Bm + np.array([0.0, 0.4, 0.0]))
    Pbm = img_of(K, R, t, Bm)
    Pb = (H @ np.array([*Pbm, 1.0]))[:2] / (H @ np.array([*Pbm, 1.0]))[2]
    Pt = (H @ np.array([*Pm, 1.0]))[:2] / (H @ np.array([*Pm, 1.0]))[2]
    delta = float(np.linalg.norm(Pt - Pb))
    d = float(np.linalg.norm(Bm[:2] - C[:2]))
    print(f"  flat 0.4 m-deep mat at d={d:.2f} m -> h_est = {Zc*delta/(d+delta):.3f} m")
