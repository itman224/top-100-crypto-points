# Quant Research Lab

Install dependencies using a Python 3.12 virtual environment: `python3 -m venv lab/.venv && lab/.venv/bin/pip install -r lab/requirements-indicators.txt`. Run `./lab/run.sh` to resume symbol checkpoints and regenerate the aggregate, research questions and tests. Check for an existing `lab/backtest.py` process before starting; the original full run was started directly, before `run.sh` acquired a lock. Serve the read-only panel using `python3 lab/ui/server.py` and open `http://127.0.0.1:8765` locally.

See `VALIDATION.md` for restrictions, execution assumptions, and independently checked scope. The initial screen implements 14 rule variants, not the entire indicator catalog. Per-symbol source coverage, execution results, and split labels are saved in `lab/results/`; raw market archives are not retained. Incomplete samples and unverified assumptions are not proof of profitability.

To resume the combined per-event annotation and exploratory TP/SL sensitivity pass, after the benchmark aggregate is final, run `PYTHONPATH=lab lab/.venv/bin/python -u lab/enrich_all.py`. This processes one symbol at a time and reuses feature and risk checkpoints where both match their source and code fingerprints. The fixed 1% stop and 2% take are exploratory, not pre-registered; full-bar MFE/MAE are bounds rather than necessarily realizable excursions before exit.

Risk checkpoints use an experiment version and do not embed a hash of the risk module itself. The enrichment launcher checks `risk_code.sha256` and refuses to reuse checkpoints if that module changes. If changing risk rules, deliberately migrate or regenerate risk checkpoints and then update that fingerprint; do not silently reuse old results.

After enrichment, regenerate the risk summary with `lab/.venv/bin/python lab/risk_aggregate.py`; rerun `lab/aggregate.py` and `lab/research_insights.py` only after appropriate benchmark checkpoints are present. Final event annotations are under `lab/results/event_features`, one gzip JSONL and metadata JSON per symbol. Check `VALIDATION.md` for warm-up exclusions, post-holdout exploratory sensitivity, and remaining research limitations.
