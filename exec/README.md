# Exec - Experiment Execution and Plotting

Framework for running experiments and generating plots, designed for CloudLab environments.

## Architecture

The execution framework orchestrates experiments across a set of remote hosts ("CloudLab nodes").
It follows a **Tune -> Deploy -> Run -> Collect** cycle.

1.  **Partition**: Hosts are split into **Generators** and **Deployment** nodes.
2.  **Tune**: System-specific tuners find optimal parameters (e.g., resource limits).
3.  **Deploy**: The system is deployed ONCE per benchmark/system.
4.  **Run**: Workload generators run remotely on generator nodes.
5.  **Collect**: Logs and metrics are pulled to the local machine.

## Prerequisites

1.  **Hosts File**: Create `hosts.txt` with a list of SSH-accessible hosts (one per line).
    ```text
    user@node1.cloudlab.us
    user@node2.cloudlab.us
    ...
    ```
2.  **Provisioning**: Ensure `benchmarks/provisioning/provision.sh` exists and is idempotent.

## Running Experiments

Run experiments defined in a JSON file:

```bash
python -m exec.executor \
  --experiments-file configs/chain1/experimnts.json \
  --config configs/chain1/config.json
```

**Options:**
- `--only-names "exp1,exp2"`: Run specific experiments (use derived names).
- `--only-types "type1"`: Run specific types.
- `--name-contains "substring"`: Filter by name substring.

Experiment names are derived from `type`, `bench`, and `system`: `{type}-{bench}-{system}` (or `{type}-{bench}-{n}-{system}` for multi-API). Examples:
```bash
--only-names "latency-vs-throughput-one-service-plain,latency-vs-throughput-one-service-sidecar"
--name-contains "sidecar"
--only-types "latency-vs-throughput" --name-contains "plain"
```

## Tuning

The orchestrator automatically looks for a tuner script in `tuner/<system>_tuner.py` or `exec/<system>_tuner.py`.
If found, it runs the tuner before deploying the system.
Results are saved to `exp_runs/exp-<id>/tuning/<system>.json`.

To create a new tuner, copy `exec/tuner_template.py` to `exec/<system>_tuner.py` and implement your optimization logic.

## Config (`config.json`)

Ensure your config includes:
```json
{
  "hosts_file": "hosts.txt",
  "provisioning_script": "benchmarks/provisioning/provision.sh",
  "experiment_index": "001",
  "output_base_dir": "experiment_runs"
}
```

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

## Directory Structure

```
experiment_runs/
└── exp-001/
    ├── run_summary.jsonl
    ├── tuning/
    │   └── <system>.json
    └── experiment_name/
        └── unit_name/
            └── repeat_000/
                ├── output/            # CSV results and parsed JSON
                │   ├── overall-{api}.json
                │   └── realtime-{api}.csv
                ├── metrics/           # JSON copies for Plotting
                ├── raw/               # Service Logs & Raw Output
                └── run_details.json
```
