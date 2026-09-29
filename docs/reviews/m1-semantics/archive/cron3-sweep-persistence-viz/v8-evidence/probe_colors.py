"""Attribute the exact overlay colours to camera-view vs minimap regions."""
import sys

import cv2
import numpy as np

canvas = cv2.imread(sys.argv[1])
view_w = int(sys.argv[2]) if len(sys.argv) > 2 else 1280
mm_h = int(sys.argv[3]) if len(sys.argv) > 3 else 144
print(f"canvas {canvas.shape} view_w={view_w} mm_h(rows)={mm_h}")
for name, color in (("object magenta", (255, 0, 255)), ("dest yellow", (0, 255, 255)),
                    ("approach orange", (0, 200, 255))):
    m = np.all(canvas == color, axis=2)
    ys, xs = np.where(m)
    if len(xs) == 0:
        print(f"{name}: ABSENT")
        continue
    in_view = xs < view_w
    in_mm = (xs >= view_w) & (ys < mm_h)
    def bb(mask):
        if not mask.any():
            return "-"
        return f"x[{xs[mask].min()},{xs[mask].max()}] y[{ys[mask].min()},{ys[mask].max()}]"
    print(f"{name}: total={len(xs)} | camera-view={int(in_view.sum())} {bb(in_view)} | "
          f"minimap-area={int(in_mm.sum())} {bb(in_mm)}")