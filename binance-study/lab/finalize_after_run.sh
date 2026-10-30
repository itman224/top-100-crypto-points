#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
target_pid="$1"
while kill -0 "$target_pid" 2>/dev/null; do sleep 20; done
lab/.venv/bin/python lab/aggregate.py
lab/.venv/bin/python lab/research_insights.py
PYTHONPATH=lab lab/.venv/bin/python -m unittest discover -s lab -p 'test_*.py'
