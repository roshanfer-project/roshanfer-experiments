# Exec - Experiment Execution and Plotting

Framework for running experiments and generating plots.

## Running Experiments

Run experiments defined in a JSON file:

```bash
python -m exec.executor \
  --experiments-file configs/chain1/experimnts.json \
  --config configs/chain1/config.json
```

**Options:**
- `--only-names "exp1,exp2"`: Run specific experiments.
- `--only-types "type1"`: Run specific types.
- `--name-contains "substring"`: Filter by name.

## Generating Merged Plots

Generate combined plots (e.g., comparing multiple experiments) defined in a YAML file:

```bash
python -m exec.merged_plot_runner \
  --merged-config configs/chain1/merged.yaml \
  --experiments-file configs/chain1/experimnts.json \
  --config configs/chain1/config.json \
  --experiments-root exp_runs \
  --output-dir merged_plots \
  --experiment-index 001
```

## Generating Individual Plots

Generate plots for a single experiment run (e.g., time series, per-repeat metrics):

```bash
python -m exec.plot_runner \
  --experiment-index 001 \
  --config-file configs/chain1/config.json \
  --experiments-root exp_runs \
  --output-dir generated_plots
```

**Options:**
- `--experiment-name "name"`: Generate for a specific experiment only.

## Supported Plot Types

- `latency-vs-throughput`: P99 Latency vs Throughput with 95% CI.
- `latency-and-rate-vs-time`: Time series of latency and rate.
- `resource-waste-bar`: Resource usage comparison.
- `max-queue`: Max queue length comparison.

## Directory Structure

```
exp_runs/
└── exp-001/
    ├── run_summary.jsonl
    └── experiment_name/
        └── unit_name/
            └── repeat_000/
                └── output/
                    ├── overall-{api}.json
                    └── realtime-{api}.csv
```
