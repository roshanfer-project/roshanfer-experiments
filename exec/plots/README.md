# Plot generation

ACM-style matplotlib plots for experiment results.

## Repository layout

```text
plots/
├── plotting_primitives.py     shared ACM-style figures
├── data_loader.py             RWG + extract_series() for legacy metrics JSON
├── latency_rate_vs_time.py    stacked rate + latency vs time
├── aggregation.py             series aggregation helpers
└── plugins/                   per-experiment / per-unit plot plugins
```

```bash
python -m exec.plots.latency_rate_vs_time \
  --experiment-index fan-out \
  --experiment-name latency-and-rate-vs-time-fan-out-roshanfer \
  --experiments-root exp_runs_test/<ts>/fan-out \
  --output-dir generated_plots
```

Writes PDFs under `generated_plots/<experiment-name>/` (e.g. `rate_vs_time.pdf`, `latency_vs_time.pdf`).
