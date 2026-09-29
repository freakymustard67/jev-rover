"""Verify the W3B enabled config loads under the current (pre/post-patch) code."""
import sys

from config import RoomConfig

c = RoomConfig.load(sys.argv[1])
print("load_ok enabled=", c.semantics.enabled, "audit_period_s=", c.semantics.audit_period_s,
      "min_interval_s=", c.semantics.min_interval_s,
      "max_misses attr:", hasattr(c.semantics, "max_misses"),
      "dedupe_center_m attr:", hasattr(c.semantics, "dedupe_center_m"))
