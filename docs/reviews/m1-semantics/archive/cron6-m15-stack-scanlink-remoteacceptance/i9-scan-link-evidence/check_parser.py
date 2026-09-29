#!/usr/bin/env python3
"""Scan-packet vs the current telemetry parser (link.py:99-126)."""
import json

cases = {
    "real chunk (angle=-45 -> 0xD3, range 300 cm)":
        bytes([0x53, 0x01, 0x29, 0x00, 0x00, 0x03, 0x00, 0x40, 0xD3, 0x2C, 0x01, 0x00]),
    "all-ASCII chunk (angles 0,1,2)":
        bytes([0x53, 0x01, 0x29, 0x00, 0x00, 0x03, 0x00, 0x40, 0x00, 0x2C, 0x01, 0x01]),
}
for name, pkt in cases.items():
    try:
        json.loads(pkt)
        print(f"{name:52s} -> parsed (unexpected)")
    except json.JSONDecodeError:
        print(f"{name:52s} -> JSONDecodeError (caught by link.py:109 -> packet silently skipped)")
    except UnicodeDecodeError as e:
        print(f"{name:52s} -> UnicodeDecodeError (NOT caught by link.py:109 -> escapes telemetry(), crashes run.py loop)")

# confirm subclass relation
print("UnicodeDecodeError is ValueError:", issubclass(UnicodeDecodeError, ValueError),
      "| is json.JSONDecodeError:", issubclass(UnicodeDecodeError, json.JSONDecodeError))
