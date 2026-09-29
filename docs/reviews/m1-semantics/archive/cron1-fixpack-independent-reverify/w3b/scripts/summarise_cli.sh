#!/usr/bin/env bash
# W3B: summarise pre/post CLI outputs.
set -u
W3B=/home/freakymustard/.hermes/cache/scratch/wave3/w3b
PHASE="$1"
O="$W3B/out/$PHASE"
echo "== $PHASE P1 (off override) =="
head -2 "$O/p1_off_override.txt"
grep -n '"passes"' "$O/p1_off_override.txt" | head -2
grep -n '"semantics": null' "$O/p1_off_override.txt" | head -1
echo "== $PHASE P2 (enabled, no --semantics-once) =="
head -1 "$O/p2_enabled.txt"
grep -n '"passes"' "$O/p2_enabled.txt" | head -2
echo "== $PHASE P3 (enabled, --semantics-once) =="
head -1 "$O/p3_once.txt"
grep -n '"passes"' "$O/p3_once.txt" | head -2
echo "== $PHASE P4 (--find, no --mission) =="
head -2 "$O/p4_find_no_mission.txt"
grep -n '"mission"' "$O/p4_find_no_mission.txt" | head -1
