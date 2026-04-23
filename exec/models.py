"""Data models used across executor, runner, collector, and reporting.

Adjust / extend as needed for richer metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional
from datetime import datetime
import re


def offered_load_from_config_dict(cfg: Optional[Dict[str, Any]]) -> Optional[int]:
    """Peak rps for piecewise configs; two_step_sweep runs usually encode load in unit name (rate-*)."""
    if not cfg or not isinstance(cfg, dict):
        return None
    lg = cfg.get("load_generator")
    if isinstance(lg, dict) and lg.get("kind") == "piecewise":
        phases = lg.get("phases") or []
        if phases:
            return max(int(p["rps"]) for p in phases)
    return None


@dataclass
class LoadRange:
    """Represents a numeric load sweep (inclusive of end, matching range(start, end+1, step))."""
    start: int
    end: int
    step: int

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "LoadRange":
        return LoadRange(start=int(d["start"]), end=int(d["end"]), step=int(d["step"]))

    def to_dict(self) -> Dict[str, Any]:
        return {"start": self.start, "end": self.end, "step": self.step}


@dataclass
class LoadPhase:
    rps: int
    duration_sec: int

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "LoadPhase":
        return LoadPhase(rps=int(d["rps"]), duration_sec=int(d["duration_sec"]))

    def to_dict(self) -> Dict[str, Any]:
        return {"rps": self.rps, "duration_sec": self.duration_sec}


@dataclass
class LoadGeneratorConfig:
    """Required per experiment: two_step_sweep (K runs) or piecewise (one run, N phases)."""

    kind: str
    base_duration_sec: int = 0
    load_duration_sec: int = 0
    base_rps: int = 0
    sweep: Optional[LoadRange] = None
    phases: List[LoadPhase] = field(default_factory=list)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "LoadGeneratorConfig":
        if not isinstance(d, dict):
            raise ValueError("load_generator must be an object")
        kind = d.get("kind")
        if kind not in ("two_step_sweep", "piecewise"):
            raise ValueError(
                f"load_generator.kind must be 'two_step_sweep' or 'piecewise', got {kind!r}. "
                "See experiments.json migration (load_generator block)."
            )
        if kind == "two_step_sweep":
            sweep_raw = d.get("sweep")
            if not isinstance(sweep_raw, dict):
                raise ValueError("two_step_sweep requires load_generator.sweep {start,end,step}")
            sweep = LoadRange.from_dict(sweep_raw)
            if sweep.step <= 0:
                raise ValueError("load_generator.sweep.step must be positive")
            return LoadGeneratorConfig(
                kind=kind,
                base_duration_sec=int(d["base_duration_sec"]),
                load_duration_sec=int(d["load_duration_sec"]),
                base_rps=int(d["base_rps"]),
                sweep=sweep,
                phases=[],
            )
        # piecewise
        if "sweep" in d and d["sweep"] is not None:
            raise ValueError("piecewise load_generator must not include sweep")
        phases_raw = d.get("phases")
        if not isinstance(phases_raw, list) or len(phases_raw) < 1:
            raise ValueError("piecewise requires non-empty load_generator.phases")
        phases = [LoadPhase.from_dict(p) for p in phases_raw]
        return LoadGeneratorConfig(kind=kind, phases=phases)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"kind": self.kind}
        if self.kind == "two_step_sweep":
            out["base_duration_sec"] = self.base_duration_sec
            out["load_duration_sec"] = self.load_duration_sec
            out["base_rps"] = self.base_rps
            if self.sweep:
                out["sweep"] = self.sweep.to_dict()
        else:
            out["phases"] = [p.to_dict() for p in self.phases]
        return out


@dataclass
class FaultToleranceConfig:
    """Experiment fault-tolerance (deadline/retry). No SLO fields — use config.json slos."""

    deadline_mode: str = "none"
    retry_mode: str = "none"
    retry_bucket_capacity: Optional[str] = None

    @staticmethod
    def from_optional(raw: Any) -> Optional["FaultToleranceConfig"]:
        if not isinstance(raw, dict):
            return None
        d = raw
        ft = FaultToleranceConfig(
            deadline_mode=str(d.get("deadline_mode") or "none"),
            retry_mode=str(d.get("retry_mode") or "none"),
        )
        if "retry_bucket_capacity" in d and d["retry_bucket_capacity"] is not None:
            ft.retry_bucket_capacity = str(d["retry_bucket_capacity"])
        return ft

    def to_deploy_env(self) -> Dict[str, str]:
        m: Dict[str, str] = {
            "BENCH_RPC_DEADLINE_MODE": self.deadline_mode,
            "BENCH_RPC_RETRY_MODE": self.retry_mode,
        }
        if self.retry_bucket_capacity is not None:
            m["BENCH_RPC_RETRY_BUCKET_CAPACITY"] = self.retry_bucket_capacity
        return m


@dataclass
class FailSlowConfig:
    """gRPC-only fail-slow: arm via kubectl exec to pod localhost admin (see callgraph-framework README)."""

    pod: str
    after_sec: float
    duration_sec: float
    extra_ms: int
    container: str = "app"
    kubernetes_namespace: Optional[str] = None

    @staticmethod
    def from_optional(raw: Any) -> Optional["FailSlowConfig"]:
        if not isinstance(raw, dict):
            return None
        d = raw
        if "pod" not in d or "after_sec" not in d or "duration_sec" not in d or "extra_ms" not in d:
            return None
        ns = d.get("kubernetes_namespace")
        return FailSlowConfig(
            pod=str(d["pod"]),
            after_sec=float(d["after_sec"]),
            duration_sec=float(d["duration_sec"]),
            extra_ms=int(d["extra_ms"]),
            container=str(d.get("container") or "app"),
            kubernetes_namespace=str(ns) if ns else None,
        )


@dataclass
class ExperimentConfig:
    """Parsed high-level experiment specification."""

    name: str
    type: str
    load_generator: LoadGeneratorConfig
    script: Optional[str] = None
    bench: str = ""
    tag: str = ""
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
    fault_tolerance: Optional[FaultToleranceConfig] = None
    failslow: Optional[FailSlowConfig] = None
    params: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ExperimentConfig":
        params = d.get("params", {})
        merged = {**params, **{k: v for k, v in d.items() if k not in {"params"}}}
        lg_raw = merged.get("load_generator")
        if lg_raw is None:
            raise ValueError(
                "Experiment is missing required 'load_generator'. "
                "Migrate experiments.json: add load_generator with kind two_step_sweep or piecewise."
            )
        load_generator = LoadGeneratorConfig.from_dict(lg_raw)
        ft_raw = merged.get("fault-tolerance")
        if ft_raw is None:
            ft_raw = merged.get("fault_tolerance")
        ft = FaultToleranceConfig.from_optional(ft_raw)
        fs_raw = merged.get("failslow")
        failslow = FailSlowConfig.from_optional(fs_raw)
        return ExperimentConfig(
            name=merged.get("name", ""),
            type=merged["type"],
            load_generator=load_generator,
            script=merged.get("script"),
            bench=str(merged.get("bench", "")),
            tag=str(merged.get("tag", "") or ""),
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
            fault_tolerance=ft,
            failslow=failslow,
            params=merged,
        )

    @property
    def api(self) -> str:
        """Convenience: first api if list provided (executor currently expects single)."""
        return self.apis[0] if self.apis else ""


@dataclass
class RunUnit:
    """Concrete run unit after expansion. RWG schedule is phase_rates + phase_durations_sec."""

    name: str
    type: str
    script: Optional[str]
    phase_rates: List[int]
    phase_durations_sec: List[int]
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
    failslow: Optional[FailSlowConfig] = None
    params: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    repeats: int = 1
    generator_hosts: List[str] = field(default_factory=list)
    deployment_hosts: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.phase_rates) != len(self.phase_durations_sec):
            raise ValueError("phase_rates and phase_durations_sec must have the same length")
        if not self.phase_rates:
            raise ValueError("RunUnit requires at least one phase")

    @property
    def peak_rate(self) -> int:
        return max(self.phase_rates)

    @property
    def wall_duration_sec(self) -> int:
        return sum(self.phase_durations_sec)

    def base_name(self) -> str:
        return self.name

    def safe_name(self) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", self.name)

    def to_dict(self) -> Dict[str, Any]:
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
