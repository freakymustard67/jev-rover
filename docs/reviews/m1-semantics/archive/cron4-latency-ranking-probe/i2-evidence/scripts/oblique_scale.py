#!/usr/bin/env python3
"""I2: metres covered by a 6 px probe offset, at several image rows.

Shipped configs (room.synthetic.json, room.example.json) are top-down scale-only
(200 px/m), so 6 px = 3.0 cm everywhere.  A fixed overhead camera is oblique in
practice; this models one (pinhole, f=900 px, 1280x720, camera at 2.0 m, looking
down 35.5 deg) and reports the local metres-per-6 px from the induced floor
homography, at full res and at proc_scale=0.5.  Run against any clone.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

CLONE = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(CLONE))
from perception import Homography  # noqa: E402


def rig_homography(pitch_deg=35.5, cam=(3.2, -0.8, 2.0), f=900.0, w=1280, h=720):
    th = np.radians(pitch_deg)
    c, s = np.cos(th), np.sin(th)
    R = np.array([[1, 0, 0], [0, -s, -c], [0, c, -s]])       # rows: right, down, forward
    C = np.asarray(cam, float)
    K = np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1.0]])

    def project(P):
        pc = R @ (np.asarray(P, float) - C)
        return K @ (pc / pc[2])

    # floor points on z=0 -> 4 corners of the room
    img, world = [], []
    for (x, y) in ((0.0, 0.0), (6.4, 0.0), (6.4, 3.6), (0.0, 3.6)):
        uv = project((x, y, 0.0))
        img.append([uv[0], uv[1]])
        world.append([x, y])
    H = Homography([list(map(float, p)) for p in img], world)
    err = H.error_m([list(map(float, p)) for p in img], world)
    return H, err


H, err = rig_homography()
print(f"corner-fit error {err:.4f} m (4 exact corners -> H is exact up to numerics)")

Hs = Homography.for_scale(H, 0.5)
print("\nshipped top-down config (200 px/m, room.synthetic.json):")
Ht = Homography([[0, 0], [1280, 0], [1280, 720], [0, 720]],
                [[0, 3.6], [6.4, 3.6], [6.4, 0], [0, 0]])
for lab, Hx, note in (("full-res", Ht, "probe frame as run.py passes it"),
                      ("half-res", Homography.for_scale(Ht, 0.5), "if H were for_scale(0.5)")):
    a = Hx.img_to_world([[640, 361]])[0]
    b = Hx.img_to_world([[640, 360]])[0]
    print(f"  {lab:8s} {note:38s} 6 px = {np.linalg.norm(a-b)*6*100:5.2f} cm")

print("\noblique rig (pitch 35.5 deg, cam 2.0 m, f=900):")
print(" row  world-y   m/px    6 px full-res   6 px at proc_scale=0.5 (12 src px)")
for row in (60, 120, 240, 360, 480, 600, 700):
    p0 = H.img_to_world([[640, row]])[0]
    p1 = H.img_to_world([[640, row + 1]])[0]
    mpp = float(np.linalg.norm(p1 - p0))
    mpp_s = float(np.linalg.norm(Hs.img_to_world([[320, row / 2 + 0.5]])[0]
                                 - Hs.img_to_world([[320, row / 2]])[0]))
    print(f" {row:4d}  {p0[1]:6.2f}   {mpp*100:5.3f} cm   {mpp*6*100:6.2f} cm      {mpp_s*12*100:6.2f} cm")

# near/far ratio
near = np.linalg.norm(H.img_to_world([[640, 701]])[0] - H.img_to_world([[640, 700]])[0])
far = np.linalg.norm(H.img_to_world([[640, 61]])[0] - H.img_to_world([[640, 60]])[0])
print(f"\nnear/far metres-per-pixel ratio: {near/far:.2f}x  "
      f"(6 px at row 700 = {near*6*100:.2f} cm vs row 60 = {far*6*100:.2f} cm)")
print("world-y at the probe rows above tells the depth span this rig sees:", end=" ")
print(f"{H.img_to_world([[640,700]])[0][1]:.2f} m .. {H.img_to_world([[640,60]])[0][1]:.2f} m")
