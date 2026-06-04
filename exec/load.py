"""Load specification parsing and expansion for experiments."""

from __future__ import annotations

from typing import Dict, List, Optional

from .models import ApiLoadSpec, ExperimentConfig, LoadPhase, LoadRange


def load_range_values(rng: LoadRange) -> List[int]:
    return list(range(rng.start, rng.end + 1, rng.step))


def sweep_phases(
    *,
    warmup_rate: int,
    warmup_duration_sec: int,
    steady_rate: int,
    steady_duration_sec: int,
) -> List[LoadPhase]:
    return [
        LoadPhase(rate=warmup_rate, duration_sec=warmup_duration_sec),
        LoadPhase(rate=steady_rate, duration_sec=steady_duration_sec),
    ]


def validate_api_loads(exp: ExperimentConfig) -> None:
    if not exp.load_mode:
        return
    if exp.load_mode not in ("sweep", "phases"):
        raise ValueError(f"Unknown load_mode: {exp.load_mode!r} (expected 'sweep' or 'phases')")
    if not exp.apis:
        raise ValueError(f"Experiment {exp.name!r}: load_mode set but apis is empty")
    api_set = set(exp.apis)
    for api in exp.apis:
        if api not in exp.api_loads:
            raise ValueError(f"Experiment {exp.name!r}: api_loads missing entry for api {api!r}")
    for api in exp.api_loads:
        if api not in api_set:
            raise ValueError(f"Experiment {exp.name!r}: unknown api {api!r} in api_loads")
    if exp.load_mode == "phases":
        for api in exp.apis:
            spec = exp.api_loads[api]
            if not spec.phases:
                raise ValueError(f"Experiment {exp.name!r}: api_loads[{api!r}] must have phases")
    elif exp.load_mode == "sweep":
        for api in exp.apis:
            spec = exp.api_loads[api]
            if spec.loads is None:
                raise ValueError(f"Experiment {exp.name!r}: api_loads[{api!r}] must have loads")
        counts = {api: len(load_range_values(exp.api_loads[api].loads)) for api in exp.apis}
        unique = set(counts.values())
        if len(unique) > 1:
            raise ValueError(
                f"Experiment {exp.name!r}: per-API load sweeps must have same step count, got {counts}"
            )


def expand_sweep_steady_rates(exp: ExperimentConfig) -> List[Dict[str, int]]:
    """Zip-aligned steady rates per sweep index (explicit sweep mode)."""
    validate_api_loads(exp)
    per_api = {api: load_range_values(exp.api_loads[api].loads) for api in exp.apis}
    n = len(next(iter(per_api.values())))
    result: List[Dict[str, int]] = []
    for i in range(n):
        result.append({api: per_api[api][i] for api in exp.apis})
    return result


def resolve_sweep_api_phases(
    exp: ExperimentConfig,
    steady_rates: Dict[str, int],
) -> Dict[str, List[LoadPhase]]:
    warmup = exp.warmup_duration_sec
    duration = exp.duration_sec
    out: Dict[str, List[LoadPhase]] = {}
    for api in exp.apis:
        if exp.load_mode:
            spec = exp.api_loads[api]
            base = spec.base_rate if spec.base_rate is not None else exp.base_rate
        else:
            base = exp.base_rate
        out[api] = sweep_phases(
            warmup_rate=base,
            warmup_duration_sec=warmup,
            steady_rate=steady_rates[api],
            steady_duration_sec=duration,
        )
    return out


def resolve_phases_mode(exp: ExperimentConfig) -> Dict[str, List[LoadPhase]]:
    validate_api_loads(exp)
    return {api: list(exp.api_loads[api].phases) for api in exp.apis}


def legacy_sweep_steady_rates(exp: ExperimentConfig) -> List[Dict[str, int]]:
    if exp.loads is None and exp.base_rate == 0:
        return [{api: 0 for api in exp.apis}] if exp.apis else [{}]
    start = exp.loads.start if exp.loads else exp.base_rate
    end = exp.loads.end + 1 if exp.loads else (exp.base_rate + 1)
    step = exp.loads.step if exp.loads else 1
    rates = list(range(start, end, step))
    return [{api: r for api in exp.apis} for r in rates]


def metadata_from_phases(api_phases: Dict[str, List[LoadPhase]]) -> tuple[int, int, int]:
    """Derive base, rate (max last-phase), duration (last steady duration) for logging."""
    if not api_phases:
        return 0, 0, 0
    first_api = next(iter(api_phases))
    phases = api_phases[first_api]
    if len(phases) >= 2:
        base = phases[0].rate
        duration = phases[-1].duration_sec
    elif phases:
        base = phases[0].rate
        duration = phases[0].duration_sec
    else:
        base, duration = 0, 0
    max_steady = max(p.rate for ps in api_phases.values() for p in ps[-1:])
    return base, max_steady, duration
