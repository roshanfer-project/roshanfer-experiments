"""Configuration management for experiment execution framework.

All environment / infra specific values should live here so code does not hardcode
IP addresses, ports, namespaces, etc.

Extend the Config dataclass as needed; keep defaults reasonable for local dev.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict, replace
from pathlib import Path
import json
from typing import Any, Dict, Optional


@dataclass
class Config:
    """Strongly-typed configuration.

    All fields present in config.sample.json are explicitly represented so that
    IDEs / type checkers can help and so accidental typos are surfaced (left in
    `extra`). Add new fields here as the framework evolves.
    """

    # --- Core output / persistence ---
    output_base_dir: str = "./experiment_runs"
    # User-defined experiment index/id used instead of timestamp for run root naming.
    # Allows continuing to append results for the same logical experiment set.
    experiment_index: str = "default"  # e.g. "001", "baselineA", etc.

    # --- Telemetry / Prometheus ---
    prometheus_url: str = "http://localhost:9090"

    # --- Directory naming inside each run unit ---
    raw_artifact_subdir: str = "raw"
    metrics_subdir: str = "metrics"
    plot_output_subdir: str = "plots"

    # --- Retry / reliability ---
    default_retries: int = 10
    post_deploy_wait_sec: float = 0.1  # Wait after deploy before starting workload (avoids connection refused)

    # --- Remote execution (CloudLab Infrastructure) ---
    hosts_file: str = "hosts.txt"
    provisioning_script: str = "benchmarks/provisioning/provision.sh"
    k8s_script: str = "benchmarks/k8s/create.sh"
    num_generators: int = -1  # Required. Must be set > 0.

    # --- Workload generator (RWG) ---
    rwg_binary_path: str = "./rwg/rwg"  # Path to RWG binary
    ssh_binary: str = "ssh"
    git_root: str = ".."  # relative path to repo root (optional)
    # Path to repo on generator hosts (for SSH). If unset, uses cwd when host is localhost.
    remote_repo_path: Optional[str] = None

    # --- Feature toggles ---
    plotting_enabled: bool = True

    # --- Collector defaults (can be overridden per experiment) ---
    collector_step: str = "5s"  # default Prometheus step size if experiment does not specify
    collector_range: str = "60s"  # default lookback/window duration if experiment does not specify

    # --- Nested configuration groups (dicts) ---
    experiment_defaults: Dict[str, Any] = field(default_factory=dict)
    expansion: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    # Per-experiment metrics (experiment_type -> dict(metric_name -> query template))
    experiment_metrics: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    report: Dict[str, Any] = field(default_factory=dict)
    # SLO mappings (api_name -> slo_threshold_ms)
    slos: Dict[str, int] = field(default_factory=dict)

    # --- Misc ---
    notes: str = ""
    bench: Optional[str] = None  # benchmark name, used when experiments omit it

    # Any additional / unknown keys from the JSON file.
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


def load_config(path: Optional[str]) -> Config:
    """Load configuration from JSON file.

    Unknown keys are placed into `extra` for forward compatibility. Nested dict
    fields (experiment_defaults / expansion / metrics / report) are shallow merged
    with existing defaults (currently empty by default). You can extend this if
    you later add non-empty defaults that should not be lost.
    """
    cfg = Config()
    if not path:
        # If no config file provided, we can't validate inputs easily unless defaults are enough.
        # But user requires num_generators.
        # If running without config file is supported, user must provide args?
        # Framework seems to rely on config file generally.
        pass
        
    p = Path(path)
    if not p.exists():
        # If path provided but doesn't exist, generic error?
        # Or if path is None, we return default cfg.
        pass

    if path: 
        with p.open() as f:
            data = json.load(f)

        known_field_names = set(cfg.__dataclass_fields__.keys())  # type: ignore[attr-defined]

        init_kwargs: Dict[str, Any] = {}
        extras: Dict[str, Any] = {}
        for k, v in data.items():
            if k in known_field_names:
                init_kwargs[k] = v
            else:
                extras[k] = v

        # Create a base config with simple (non-nested) overrides first.
        simple_override_fields = {k: v for k, v in init_kwargs.items() if not isinstance(getattr(cfg, k, None), dict)}
        cfg = replace(cfg, **simple_override_fields)

        # Merge nested dict fields individually (shallow merge).
        for nested_key in ["experiment_defaults", "expansion", "metrics", "experiment_metrics", "report", "slos"]:
            if nested_key in init_kwargs and isinstance(init_kwargs[nested_key], dict):
                existing = getattr(cfg, nested_key)
                merged = {**existing, **init_kwargs[nested_key]}
                setattr(cfg, nested_key, merged)

        # Non-dict nested overrides already handled; store extras.
        if extras:
            cfg.extra.update(extras)

    # Validation
    if cfg.num_generators < 0:
        raise ValueError("Config error: 'num_generators' is required and must be >= 0.")
    
    return cfg
