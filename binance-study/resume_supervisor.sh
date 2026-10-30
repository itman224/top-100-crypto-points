#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p output
attempt=0
while [ "$attempt" -lt 20 ]; do
  attempt=$((attempt + 1))
  printf 'Universe attempt %s at %s\n' "$attempt" "$(date -u +%FT%TZ)"
  if python3 -u universe.py; then
    python3 -u pipeline.py --universe output/universe.json --output output
    python3 -u manifest.py
    python3 -u report.py
    python3 -m unittest discover -v
    exit 0
  fi
  [ "$attempt" -lt 20 ] && sleep 60
done
exit 1
