# Overview

How `exec/`, `rwg/`, and `configs/` fit together.

# exec

Orchestrator for Tune → Deploy → Run → Collect (see [README](README.md)). Hosts are split into generators (`rwg`) and K8s workload nodes. System-specific tuners live in this package (`rajomon_tuner.py`, …).

# rwg

HTTP/1.1 load generator, git submodule. Do not change it in this repo.

# configs

Per-benchmark trees: `config.json` (graph, SLOs), `experiments.json` (what to measure), and optional `merged.yaml` (overlay plots).
