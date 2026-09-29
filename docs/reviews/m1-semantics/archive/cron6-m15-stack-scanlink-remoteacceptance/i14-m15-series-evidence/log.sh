#!/bin/bash
# usage: log.sh "description" command...
TX=/home/freakymustard/.hermes/cache/scratch/c6/tx/transcript.log
{
  echo
  echo "=== $(date -Is) $1 ==="
  shift
  echo "$ $*"
} >> "$TX"
"$@" >> "$TX" 2>&1
rc=$?
echo "[exit=$rc]" >> "$TX"
exit $rc
