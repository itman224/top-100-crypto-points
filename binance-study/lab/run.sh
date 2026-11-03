#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p lab/results
exec 9>lab/results/research.lock
flock -n 9 || { echo 'Research run is already active'; exit 1; }
lab/.venv/bin/python -u lab/backtest.py
lab/.venv/bin/python lab/aggregate.py
lab/.venv/bin/python lab/research_insights.py
PYTHONPATH=lab lab/.venv/bin/python -m unittest discover -s lab -p 'test_*.py'
