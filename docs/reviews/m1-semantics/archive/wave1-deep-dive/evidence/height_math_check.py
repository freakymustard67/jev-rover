#!/usr/bin/env python3
"""Part-B check: is object height recoverable from the floor homography alone?

Synthetic overhead pinhole over the jev-rover room (6.4x3.6 m). H (image px ->
floor m) is fitted exactly the way calibrate.py floor does it: project 4 floor
points, getPerspectiveTransform. Then:

  1. h_est = Zc * D / (d + D)  vs true h   (D = |H(top_px)-H(bot_px)| along the ray,
                                            d = |P_b - C_xy|)
  2. H alone cannot give Zc: two cameras, (Zc, f) and (k*Zc, k*f), map the floor
     identically -> identical H, different height.
  3. error sensitivity: bbox top-pixel noise, Zc error, camera-ground-point error.
  4. failure modes: flat mat on the floor; bottle standing on a table.
Writes nothing.
"""
import numpy as np
import cv2

ROOM = (6.4, 3.6)
W, H_IMG = 1280, 720


def make_cam(center_xyz, tilt_deg_x, f, w=W, h=H_IMG, roll_deg=0.0):
    cx, cy = w / 2.0, h / 2.0
    K = np.array([[f, 0, cx], [0, f, cy], [0, 0, 1.0]])
    th = np.deg2rad(tilt_deg_x)
    zc = np.array([np.sin(th), 0.0, -np.cos(th)])
    xc = np.array([np.cos(th), 0.0, np.sin(th)])
    yc = np.cross(zc, xc)
    R_cw = np.stack([xc, yc, zc], axis=1)
    C = np.asarray(center_xyz, float)
    t = -R_cw.T @ C
    P = K @ np.hstack([R_cw.T, t[:, None]])
    return K, R_cw, C, P


def project(P, pts_xyz):
    pts = np.asarray(pts_xyz, float).reshape(-1, 3)
    hom = (P @ np.hstack([pts, np.ones((len(pts), 1))]).T).T
    return hom[:, :2] / hom[:, 2:3], hom[:, 2]


def fit_H(P, corners):
    img_pts, _ = project(P, [(x, y, 0.0) for x, y in corners])
    Hm = cv2.getPerspectiveTransform(np.float32(img_pts), np.asarray(corners, np.float32))
    return Hm, img_pts


def Himg(Hm, p):
    v = Hm @ np.array([p[0], p[1], 1.0])
    return v[:2] / v[2]


def h_estimate(Hm, P, C_xy, Zc, base_xy, top_px):
    """The formula under test."""
    pb = np.array(base_xy, float)
    pt = Himg(Hm, top_px)
    D = float(np.linalg.norm(pt - pb))
    d = float(np.hypot(*(pb - np.asarray(C_xy, float))))
    return Zc * D / (d + D), D, d


def main():
    Zc = 2.80
    C = (ROOM[0] / 2, ROOM[1] / 2, Zc)
    K, R, C, P = make_cam(C, 0.0, 340.0)          # nadir overhead camera
    corners = [(0.3, 0.3), (6.1, 0.3), (6.1, 3.3), (0.3, 3.3)]
    Hm, img_pts = fit_H(P, corners)
    print("cam: nadir, Zc=2.80 m, f=340 px, 1280x720")
    print("H fitted from 4 floor correspondences like calibrate.py; corner px:",
          np.round(img_pts, 1).tolist())

    print("\n--- 1) rods on the floor: true h vs h_est = Zc*D/(d+D) ---")
    print(f"{'pos':>12} {'h_true':>7} {'d(m)':>6} {'D(m)':>7} {'h_est':>7} {'err(cm)':>8}")
    worst = 0.0
    for x, y in [(2.0, 1.0), (4.8, 2.6), (3.2, 0.7), (5.6, 1.2), (3.2, 3.0), (1.0, 2.9)]:
        for h in (0.10, 0.25, 0.50, 1.00):
            base_px, okb = project(P, [(x, y, 0.0)])
            top_px, okt = project(P, [(x, y, h)])
            if okb[0] <= 0 or okt[0] <= 0:
                continue
            hv, D, d = h_estimate(Hm, P, C[:2], Zc, (x, y), top_px[0])
            worst = max(worst, abs(hv - h))
            print(f"({x:.1f},{y:.1f}) {h:7.2f} {d:6.2f} {D:7.3f} {hv:7.3f} {100*(hv-h):+8.3f}")
    print(f"max |err| = {100*worst:.3f} cm  -> formula is exact (floating-point level)")

    print("\n--- 1b) same formula on a 22-deg TILTED camera ---")
    Kt, Rt, Ct, Pt = make_cam((3.0, 1.8, 2.6), 22.0, 420.0)
    Hmt, _ = fit_H(Pt, corners)
    for x, y in [(3.0, 1.0), (4.0, 2.4), (2.2, 2.8)]:
        h = 0.3
        bp, _ = project(Pt, [(x, y, 0.0)])
        tp, _ = project(Pt, [(x, y, h)])
        hv, D, d = h_estimate(Hmt, Pt, Ct[:2], 2.6, (x, y), tp[0])
        print(f"({x:.1f},{y:.1f}) h=0.30 -> h_est {hv:.4f} (err {100*(hv-h):+.3f} cm)")

    print("\n--- 2) two cameras, identical H, different height ---")
    k = 1.35
    Kc, Rc, Cc, Pc = make_cam((C[0], C[1], Zc * k), 0.0, 340.0 * k)
    Hmc, _ = fit_H(Pc, corners)
    dmax = np.abs(Hm / Hm[2, 2] - Hmc / Hmc[2, 2]).max()
    print(f"camA Zc={Zc:.2f} f=340 | camB Zc={Zc*k:.2f} f={340*k:.0f} ; "
          f"max |H_A/H_A33 - H_B/H_B33| = {dmax:.2e}")
    print("  -> the same H is consistent with any camera height; h is NOT in H")

    print("\n--- 3) error sensitivity (rod h=0.25 m at (4.0,2.0)) ---")
    x, y, h = 4.0, 2.0, 0.25
    bp, _ = project(P, [(x, y, 0.0)])
    tp, _ = project(P, [(x, y, h)])
    hv0, D, d = h_estimate(Hm, P, C[:2], Zc, (x, y), tp[0])
    print(f"reference: h_est={hv0:.4f} m; D={D:.4f} m; d={d:.2f} m; bbox height={bp[0][1]-tp[0][1]:.1f} px")
    for dp in [(2, 0), (-2, 0), (0, 2), (0, -2), (4, 0), (-4, 0)]:
        hv, _, _ = h_estimate(Hm, P, C[:2], Zc, (x, y), tp[0] + np.array(dp, float))
        print(f"  top pixel {dp}: h_est {hv:.4f}  (dh {100*(hv-h):+.3f} cm)")
    p0 = bp[0]
    s_u = np.linalg.norm(Himg(Hm, p0 + [1, 0]) - Himg(Hm, p0)) * 100
    s_v = np.linalg.norm(Himg(Hm, p0 + [0, 1]) - Himg(Hm, p0)) * 100
    print(f"  local floor scale: 1 px ~ {s_u:.2f} cm (u), {s_v:.2f} cm (v)")
    for dz in (-0.05, -0.02, 0.02, 0.05):
        hv = (Zc + dz) * D / (d + D)
        print(f"  Zc {dz:+.2f} m: h_est {hv:.4f} ({100*(hv-h):+.3f} cm)")
    for ddx, ddy in [(0.05, 0), (0, 0.05), (-0.05, 0), (0.10, 0)]:
        dd = float(np.hypot(*(np.array([x, y]) - (np.array(C[:2]) + [ddx, ddy]))))
        hv = Zc * D / (dd + D)
        print(f"  C_xy err ({ddx:+.2f},{ddy:+.2f}) m: h_est {hv:.4f} ({100*(hv-h):+.3f} cm)")

    print("\n--- 4a) flat mat 0.6x0.4 m ON the floor, naive formula ---")
    mx, my = 4.0, 2.0
    nb, _ = project(P, [(mx, my - 0.2, 0)]); fb, _ = project(P, [(mx, my + 0.2, 0)])
    Pn = Himg(Hm, nb[0]); Pf = Himg(Hm, fb[0])
    Dm = float(np.linalg.norm(Pf - Pn))
    dm = float(np.hypot(*(Pn - np.array(C[:2]))))
    print(f"  D(footprint)={Dm:.3f} m -> h_est={Zc*Dm/(dm+Dm):.3f} m (true 0.00) "
          f"-> false height_suspect unless the plane prior says 'mat'")

    print("\n--- 4b) PET bottle 0.25 m tall on a 0.75 m table ---")
    tx, ty = 4.0, 2.0
    bp2, _ = project(P, [(tx, ty - 0.15, 0.75)])
    tp2, _ = project(P, [(tx, ty, 1.00)])
    hv2, D2, d2 = h_estimate(Hm, P, C[:2], Zc, Himg(Hm, bp2[0]), tp2[0])
    print(f"  anchor floor point {np.round(Himg(Hm, bp2[0]),2).tolist()} (on the table footprint); "
          f"D={D2:.3f} m, d={d2:.2f} m -> h_est={hv2:.3f} m")
    print(f"  -> {hv2/0.25:.1f}x the bottle height: a 'tall/on-furniture' verdict from one view")

    print("\n--- 5) what H alone encodes ---")
    print("  horizon line (third row of H):", np.round(Hm[2, :], 8).tolist())
    pp, _ = project(P, [(C[0], C[1], 0.0)])
    print("  image of the camera's own floor point:", np.round(pp[0], 1).tolist(),
          "  (nadir -> principal point)")
    print("  H maps pixels to the floor plane; the vertical/parallax scale is not in H.")


if __name__ == "__main__":
    main()
