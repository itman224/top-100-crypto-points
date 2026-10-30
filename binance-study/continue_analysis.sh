#!/bin/sh
set -u
cd "$(dirname "$0")"
exec 9>output/analysis.lock
flock -n 9 || { echo 'Another continuation holds the lock'; exit 1; }
attempt=0
while [ "$attempt" -lt 30 ]; do
  attempt=$((attempt+1))
  printf 'Ranking attempt %s %s\n' "$attempt" "$(date -u +%FT%TZ)"
  if python3 -u universe.py; then
    printf 'Processing selected universe %s\n' "$(date -u +%FT%TZ)"
    python3 -u pipeline.py --universe output/universe.json --output output || exit $?
    python3 -u manifest.py || exit $?
    python3 -u report.py || exit $?
    python3 -m unittest discover -v || exit $?
    exit 0
  fi
  [ "$attempt" -lt 30 ] && sleep 60
done
exit 1
