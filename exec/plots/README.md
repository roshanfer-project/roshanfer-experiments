# Plot Generation (Prototype)

This directory contains scripts to generate figures from persisted experiment results.

Currently implemented:
- `latency_rate_vs_time.py`: Generates stacked rate and latency line plots for single-API `latency-and-rate-vs-time` experiments.

Usage example:
```
python -m experiments.exec.plots.latency_rate_vs_time \
  --experiment-index 001 \
  --experiment-name latency-and-rate-vs-time-hotel-1-sidecar \
  --experiments-root experiment_runs \
  --output-dir generated_plots
```

Outputs:
- `generated_plots/<experiment-name>/rate_vs_time.png`
- `generated_plots/<experiment-name>/latency_vs_time.png`

Next steps (not yet implemented):
- Multi-API handling (aggregate per API or facet).
- Confidence intervals across repeats.
- SLO threshold annotations from configuration.
- Automatic invocation from main report generation.
