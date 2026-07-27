"""Data models used across executor, runner, collector, and reporting.

Adjust / extend as needed for richer metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime
import re

APPROX_SYSTEMS = ("approx", "approx-fcfs", "approx-edf")

SYSTEM_DISPLAY_LABELS = {
    "approx": "Approx",
    "approx-fcfs": "Approx-FCFS",
    "approx-edf": "Approx-EDF",
    "p2c": "P2C",
    "wrr": "WRR",
}


def is_approx_system(system: str) -> bool:
    return system in APPROX_SYSTEMS


def is_sidecar_family(system: str) -> bool:
    """sidecar (non-LB) or any approx* LB mode."""
    return system == "sidecar" or is_approx_system(system)


def resolve_plot_label(exp_cfg: Dict[str, Any], exp_name: str, exp_def: Optional[Dict[str, Any]] = None) -> str:
    if exp_cfg.get("label"):
        return exp_cfg["label"]
    system = ""
    if exp_def:
        system = str(exp_def.get("system", ""))
    if not system:
        system = str(exp_cfg.get("system", ""))
    if system in SYSTEM_DISPLAY_LABELS:
        return SYSTEM_DISPLAY_LABELS[system]
    return exp_name


@dataclass
class LoadRange:
    """Represents a numeric load sweep (inclusive)."""
    start: int
    end: int
    step: int

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "LoadRange":
        return LoadRange(start=int(d["start"]), end=int(d["end"]), step=int(d["step"]))


@dataclass
class LoadPhase:
    rate: int
    duration_sec: int

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "LoadPhase":
        return LoadPhase(
            rate=int(d["rate"]),
            duration_sec=int(d.get("duration_sec", d.get("duration", 0))),
        )


@dataclass
class ApiLoadSpec:
    loads: Optional[LoadRange] = None
    base_rate: Optional[int] = None
    phases: List[LoadPhase] = field(default_factory=list)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ApiLoadSpec":
        loads_obj = d.get("loads")
        loads = LoadRange.from_dict(loads_obj) if isinstance(loads_obj, dict) else None
        base_rate = d.get("base_rate")
        if base_rate is not None:
            base_rate = int(base_rate)
        phases_raw = d.get("phases", [])
        phases = [LoadPhase.from_dict(p) for p in phases_raw] if phases_raw else []
        return ApiLoadSpec(loads=loads, base_rate=base_rate, phases=phases)


@dataclass
class ExperimentConfig:
    """Parsed high-level experiment specification.

    New schema (sample_experiments.json) places common knobs at top-level instead of params{}.
    We keep a params dict for backward compatibility and to pass through any extra keys.
    """

    name: str
    type: str
    script: Optional[str] = None
    loads: Optional[LoadRange] = None
    load_mode: Optional[str] = None
    warmup_duration_sec: int = 2
    api_loads: Dict[str, ApiLoadSpec] = field(default_factory=dict)
    base_rate: int = 0
    duration_sec: int = 0
    bench: str = ""
    apis: List[str] = field(default_factory=list)
    system: str = ""
    repeat: int = 1
    collector_step: str = ""
    collector_range: str = ""
    collector_freq: int = 0
    warmup: int = 0
    cooldown: int = 0
    services: List[str] = field(default_factory=list)
    cleanup_args: List[str] = field(default_factory=list)
    execution_args: List[str] = field(default_factory=list)
    params: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ExperimentConfig":
        # Allow legacy nested params usage; merge into top-level fields where sensible.
        params = d.get("params", {})
        merged = {**params, **{k: v for k, v in d.items() if k not in {"params"}}}
        loads_obj = merged.get("loads")
        loads = LoadRange.from_dict(loads_obj) if isinstance(loads_obj, dict) else None
        load_mode = merged.get("load_mode") or None
        if load_mode is not None:
            load_mode = str(load_mode)
        api_loads_raw = merged.get("api_loads", {})
        api_loads = {
            str(k): ApiLoadSpec.from_dict(v)
            for k, v in api_loads_raw.items()
            if isinstance(v, dict)
        }
        return ExperimentConfig(
            name=merged.get("name", ""),
            type=merged["type"],
            script=merged.get("script"),
            loads=loads,
            load_mode=load_mode,
            warmup_duration_sec=int(merged.get("warmup_duration_sec", 2) or 2),
            api_loads=api_loads,
            base_rate=int(merged.get("base_rate", merged.get("base", 0)) or 0),
            duration_sec=int(merged.get("duration_sec", merged.get("duration", 0)) or 0),
            bench=str(merged.get("bench", "")),
            apis=list(merged.get("apis", [])),
            system=str(merged.get("system", "")),
            repeat=int(merged.get("repeat", params.get("repeat", 1)) or 1),
            collector_step=str(merged.get("collector_step", "")),
            collector_range=str(merged.get("collector_range", "")),
            collector_freq=int(merged.get("collector_freq", 0) or 0),
            warmup=int(merged.get("warmup", 0) or 0),
            cooldown=int(merged.get("cooldown", 0) or 0),
            services=list(merged.get("services", [])),
            cleanup_args=list(merged.get("cleanup_args", [])),
            execution_args=list(merged.get("execution_args", [])),
            params=merged,  # store everything for downstream flexibility
        )

    @property
    def api(self) -> str:
        """Convenience: first api if list provided (executor currently expects single)."""
        return self.apis[0] if self.apis else ""

    @property
    def duration(self) -> int:
        """Alias used by existing executor expansion code."""
        return self.duration_sec


@dataclass
class RunUnit:
    """Concrete run unit after expansion.

    Expanded fields (base, rate, duration, system, api, bench) are explicit to match
    the current executor._expand_experiment implementation.
    All original key/values are also retained in params for generic logic (repeats, etc.).
    """
    name: str
    type: str
    script: Optional[str]
    base: int
    rate: int
    duration: int
    system: str
    apis: List[str] = field(default_factory=list)
    bench: str = ""
    collector_step: str = ""
    collector_range: str = ""
    collector_freq: int = 0
    warmup: int = 0
    cooldown: int = 0
    services: List[str] = field(default_factory=list)
    cleanup_args: List[str] = field(default_factory=list)
    execution_args: List[str] = field(default_factory=list)
    params: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    repeats: int = 1
    generator_hosts: List[str] = field(default_factory=list)
    deployment_hosts: List[str] = field(default_factory=list)
    api_phases: Dict[str, List[LoadPhase]] = field(default_factory=dict)

    def base_name(self) -> str:
        return self.name

    def safe_name(self) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", self.name)

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serializable representation.

        (Hook point: if future fields become non-serializable, coerce them here.)
        """
        d = asdict(self)
        return d


@dataclass
class RunResult:
    unit_name: str
    status: str  # e.g., success, error
    raw_artifact_dir: str
    details: Dict[str, Any] = field(default_factory=dict)
    repeat_index: int = 0  # 0-based repeat index
    total_repeats: int = 1
    group_name: str = ""  # base grouping name (same for all repeats of a unit variant)
    # Optional explicit timestamps (e.g., workload generation window) separate from internal started_at/ended_at in details.
    start_timestamp: Any | None = None  # Accept datetime or string; user code sets.
    end_timestamp: Any | None = None
    output_file: str = ""  # Path to the output file generated by RWG

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Normalize top-level timestamp fields
        for k in ("start_timestamp", "end_timestamp"):
            v = d.get(k)
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        # Normalize any datetime values inside details (shallow)
        details = d.get("details", {})
        if isinstance(details, dict):
            for dk, dv in list(details.items()):
                if isinstance(dv, datetime):
                    details[dk] = dv.isoformat()
        return d


@dataclass
class CollectorResult:
    unit_name: str
    metrics_dir: str
    metrics_files: List[str]
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
