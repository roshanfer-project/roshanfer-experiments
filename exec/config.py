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
    """Fields the executor, runner, collector, and tuners actually read.

    Unknown JSON keys go into `extra`. Campaign output dir is a caller concern
    (CLI `--output-base-dir` or the dataclass default).
    """

    output_base_dir: str = "./experiment_runs"
    experiment_index: str = "default"

    raw_artifact_subdir: str = "raw"
    metrics_subdir: str = "metrics"
    post_deploy_wait_sec: float = 0.1

    hosts_file: str = "hosts.txt"
    provisioning_script: str = "benchmarks/provisioning/provision.sh"
    k8s_script: str = "benchmarks/k8s/create.sh"
    num_generators: int = -1  # Required. Must be set >= 0.

    rwg_binary_path: str = "./rwg/rwg"
    remote_repo_path: Optional[str] = None

    nanolog_debug: bool = False
    # Deploy sidecar/approx* with deploy.sh debug (glog + debug restart behavior).
    sidecar_deploy_debug: bool = False
    # Same branch name for roshanfer-experments and benchmarks on remotes.
    branch: Optional[str] = None

    slos: Dict[str, int] = field(default_factory=dict)
    tuner: Dict[str, Any] = field(default_factory=dict)
    bench: Optional[str] = None

    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def load_config(path: Optional[str]) -> Config:
    """Load configuration from JSON file.

    Unknown keys go into `extra`. Nested dicts (`slos`, `tuner`) are shallow-merged.
    """
    cfg = Config()

    if path:
        p = Path(path)
        if p.exists():
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

            simple_override_fields = {
                k: v for k, v in init_kwargs.items()
                if not isinstance(getattr(cfg, k, None), dict)
            }
            cfg = replace(cfg, **simple_override_fields)

            for nested_key in ["slos", "tuner"]:
                if nested_key in init_kwargs and isinstance(init_kwargs[nested_key], dict):
                    existing = getattr(cfg, nested_key)
                    setattr(cfg, nested_key, {**existing, **init_kwargs[nested_key]})

            if extras:
                cfg.extra.update(extras)

    if cfg.num_generators < 0:
        raise ValueError("Config error: 'num_generators' is required and must be >= 0.")

    return cfg
