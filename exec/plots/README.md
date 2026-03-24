# Plot generation

- `latency_rate_vs_time.py`: stacked rate + latency lines for `latency-and-rate-vs-time` experiments (uses `plotting_primitives`).
- `data_loader.py`: RWG + `extract_series()` for legacy metrics JSON.
- `plotting_primitives.py`: shared ACM-style figures.

```bash
python -m exec.plots.latency_rate_vs_time \
  --experiment-index fan-out \
  --experiment-name latency-and-rate-vs-time-fan-out-sidecar \
  --experiments-root exp_runs_test/<ts>/fan-out \
  --output-dir generated_plots
```

Writes PDFs under `generated_plots/<experiment-name>/` (e.g. `rate_vs_time.pdf`, `latency_vs_time.pdf`).
