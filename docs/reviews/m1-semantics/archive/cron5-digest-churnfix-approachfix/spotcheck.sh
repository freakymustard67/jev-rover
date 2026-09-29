#!/bin/bash
# Spot-check runner for cron #5 (2026-09-29 0118) — re-run the three headline
# validations against the subagents' scratch clones. Read-only on the real repo.
set -u
cd /home/freakymustard/jev-rover-research/runs/20260929-0118 || exit 1
mkdir -p spotcheck
PY=/home/freakymustard/jev-rover/.venv/bin/python
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=""

echo "===== I6 patched driver, 10 seeds ====="
"$PY" i6-churn-hysteresis-evidence/i6-churn-driver.py \
  --repo /home/freakymustard/.hermes/cache/scratch/i6/repo \
  --tag spotcheck --seeds 10 \
  --out spotcheck/i6-spot.json \
  --store-root /home/freakymustard/.hermes/cache/scratch/i6/simstore_spot 2>&1 | tail -45
echo "I6 exit: $?"

echo "===== I5 measure (patched clone) ====="
"$PY" i5-state-digest-evidence/scripts/measure_digest.py \
  --root /home/freakymustard/.hermes/cache/scratch/i5/repo \
  --store /home/freakymustard/.hermes/cache/scratch/i5/store_spot \
  --out spotcheck/i5-measure-spot.json 2>&1 | tail -35
echo "I5 exit: $?"

echo "===== I7 e2e (patched clone) ====="
REPO=/home/freakymustard/.hermes/cache/scratch/i7/repo "$PY" \
  i7-approach-standoff-evidence/i7-approach-e2e.py spotcheck/i7-e2e-spot.json 2>&1 | tail -40
echo "I7 exit: $?"
