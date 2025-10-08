Experiment Execution Framework
==============================

Overview
--------
Modular system to run experiments end-to-end (execute -> collect -> report) without manual intervention.

Components
----------
1. executor.py: Orchestrates experiments from a JSON spec, manages run folder, reporting.
2. runner.py: Executes a RunUnit (script or custom logic) and stores raw artifacts.
3. collector.py: Fetches telemetry (Prometheus etc.) and persists metrics snapshots.
4. report.py: Builds Markdown + JSON summary; extend to call plotting scripts.
5. config.py: Centralizes configurable parameters (URLs, directories, retries...).
6. models.py: Shared dataclasses.

Usage
-----
python -m experiments.exec.executor --experiments-file experiments/exec/sample_experiments.json --config path/to/config.json

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

Placeholders (User Implementation Needed)
----------------------------------------
1. executor._expand_experiment: Break high-level configs into multiple RunUnit objects.
2. runner: Environment lifecycle (containers, services) and non-script experiment logic.
3. collector: Robust metric queries, failure detection, retry policy.
4. report: Invoke existing plotting scripts and embed image links.

Next Steps
----------
- Integrate your existing plot.py scripts inside report.py.
- Add richer metadata (git commit hash, system info) to run_details.
- Implement retry & health checks.
