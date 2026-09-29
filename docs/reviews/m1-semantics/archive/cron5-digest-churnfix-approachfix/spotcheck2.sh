#!/bin/bash
# Spot-check part 2: I7 unpatched baseline + suite checks (cron #5).
set -u
cd /home/freakymustard/jev-rover-research/runs/20260929-0118 || exit 1
PY=/home/freakymustard/jev-rover/.venv/bin/python
export PYTHONDONTWRITEBYTECODE=1

echo "===== I7 unpatched e2e (repo-base) ====="
git -C /home/freakymustard/.hermes/cache/scratch/i7/repo-base log --oneline -1
REPO=/home/freakymustard/.hermes/cache/scratch/i7/repo-base "$PY" \
  i7-approach-standoff-evidence/i7-approach-e2e.py spotcheck/i7-e2e-base.json 2>&1 | tail -30

echo "===== I7 patched suite ====="
cd /home/freakymustard/.hermes/cache/scratch/i7/repo && "$PY" -m pytest -q -p no:cacheprovider 2>&1 | tail -3

echo "===== I5 patched suite ====="
cd /home/freakymustard/.hermes/cache/scratch/i5/repo && "$PY" -m pytest -q -p no:cacheprovider 2>&1 | tail -3

echo "===== I6 patched suite ====="
cd /home/freakymustard/.hermes/cache/scratch/i6/repo && "$PY" -m pytest -q -p no:cacheprovider 2>&1 | tail -3
