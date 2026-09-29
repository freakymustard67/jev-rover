#!/usr/bin/env bash
# W3B CLI check phase runner. Usage: cli_check.sh <pre|post> [clone_dir]
set -u
PHASE="$1"
W3B=/home/freakymustard/.hermes/cache/scratch/wave3/w3b
PY=/home/freakymustard/jev-rover/.venv/bin/python
E="$W3B/out/enabled.json"
O="$W3B/out/$PHASE"
CLONE="${2:-$W3B/clone}"
cd "$CLONE" || exit 9
mkdir -p "$O"

export PYTHONDONTWRITEBYTECODE=1

timeout 90 "$PY" run.py --config "$E" --source synthetic --mission patrol --no-jev --seconds 3 --semantics off --find "blue mat" > "$O/p1_off_override.txt" 2>&1
echo "P1 exit=$?"

timeout 90 "$PY" run.py --config "$E" --source synthetic --mission patrol --no-jev --seconds 6 > "$O/p2_enabled.txt" 2>&1
echo "P2 exit=$?"

timeout 90 "$PY" run.py --config "$E" --source synthetic --mission patrol --no-jev --seconds 6 --semantics-once > "$O/p3_once.txt" 2>&1
echo "P3 exit=$?"

timeout 90 "$PY" run.py --config config/room.synthetic.json --source synthetic --no-jev --semantics fake --semantics-once --find "blue mat" --seconds 3 > "$O/p4_find_no_mission.txt" 2>&1
echo "P4 exit=$?"
