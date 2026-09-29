"""Refined frame-copy timing: warm-up, min/median/p90, and copyto-into-buffer variant."""
import statistics
import time

import numpy as np

for (w, h) in ((1920, 1080), (1280, 720)):
    frame = np.random.randint(0, 255, (h, w, 3), np.uint8)
    buf = np.empty_like(frame)

    def timed(fn, n=300):
        ts = []
        for _ in range(n):
            t0 = time.perf_counter()
            fn()
            ts.append((time.perf_counter() - t0) * 1e6)
        ts.sort()
        return ts[0], statistics.median(ts), ts[int(0.9 * len(ts))]

    for _ in range(20):  # warm caches
        _ = frame.copy()
        np.copyto(buf, frame)

    mn, md, p90 = timed(lambda: frame.copy())
    mnb, mdb, p90b = timed(lambda: np.copyto(buf, frame))
    print(f"{w}x{h}x3 uint8 {frame.nbytes} B ({frame.nbytes/2**20:.2f} MiB):")
    print(f"  frame.copy()      min {mn:7.0f} us  median {md:7.0f} us  p90 {p90:7.0f} us")
    print(f"  np.copyto(buf,f)  min {mnb:7.0f} us  median {mdb:7.0f} us  p90 {p90b:7.0f} us")
    print(f"  effective GB/s    copy {frame.nbytes/md/1e3:.2f}  copyto {frame.nbytes/mdb/1e3:.2f}")
