Experiment Execution Framework
==============================

Clone
-----
Benchmark wrappers (`benchmarks/provisioning`, `benchmarks/k8s`, test harnesses) live in the **`benchmarks`** submodule. After clone, initialize it (and `rwg` if you run generators from this tree):

```bash
git clone --recurse-submodules <repo-url>
# or, if you already cloned without submodules:
git submodule update --init benchmarks rwg
```

Overview
--------
Modular system to run experiments end-to-end (execute -> collect -> report) without manual intervention.

Components
----------
1. executor.py: Orchestrates experiments from **`experiments.json`** (each experiment needs **`load_generator`**: **`two_step_sweep`** or **`piecewise`**), manages run folder, reporting.
2. runner.py: Executes a RunUnit; sets **`RWG_RATES`** / **`RWG_DURATIONS`** for benchmark **`run.sh`** wrappers and stores raw artifacts.
3. collector.py: Fetches telemetry (Prometheus etc.) and persists metrics snapshots.
4. report.py: Builds Markdown + JSON summary; extend to call plotting scripts.
5. config.py: Centralizes configurable parameters (URLs, directories, retries...).
6. models.py: Shared dataclasses.

Usage
-----
python -m exec.executor --experiments-file configs/tests/<bench>/experiments.json --config configs/tests/<bench>/config.json

See **`exec/README.md`** for **`load_generator`** schema and CLI flags.

Config
------
See config.py for available fields. Provide a JSON file overriding any subset, e.g.:
{
  "output_base_dir": "./experiment_runs",
  "prometheus_url": "http://prometheus:9090"
}

Append-Only Storage
-------------------
Each invocation creates run-YYYYMMDD_HHMMSS under output_base_dir with per-unit subfolders. CSV + JSONL summaries are appended, never overwritten across invocations.

Extensions
----------
1. collector: Tighter metric queries, failure detection, retry policy where needed.
2. report: Extra plotting hooks and metadata (git hash, cluster id, …).

Next Steps
----------
- Integrate your existing plot.py scripts inside report.py.
- Add richer metadata (git commit hash, system info) to run_details.
- Implement retry & health checks.
