#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 -u universe.py
python3 -u pipeline.py --universe output/universe.json --output output
python3 -u manifest.py
python3 -u report.py
python3 -m unittest discover -v
