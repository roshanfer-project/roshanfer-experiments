"""Experiment-level aggregate bar plot for max-queue-motivation experiments.

Figure spec (grouped bars with loads):
    * One figure per experiment (spanning multiple loads).
    * X-axis: services.
    * For each service: one bar per load (grouped). Bar height = mean(max queue length over time) across repeats for that (service, load).
        - For each repeat, we take the time-series max from the corresponding metric file.
        - We then compute mean and std deviation across repeats; show error bars.
    * Only supports ONE API (enforced).
    * Assumes exactly TWO loads (error if not).
    * Y-label: "Max Queueing (req)"
    * Legend shows the different loads.
    * If a metric file is missing or empty for (service, load) in a repeat, that repeat contributes 0 for that pair.
    * Metric stems: preferred ordering queue_length_<api>_<service>; accept queue_length_<service>_<api> and queue_length_<service>.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
import math
import statistics

SUPPORTED_TYPES = ['max-queue-motivation']

# Reuse utility functions from max_queue_unit
try:
    from .max_queue_unit import (
        _normalize_service_name,
        _mean_std,
        _infer_services_and_apis,
        _read_max_for_repeat,
    )
except Exception:  # pragma: no cover
    from experiments.exec.plots.plugins.max_queue_unit import (  # type: ignore
        _normalize_service_name,
        _mean_std,
        _infer_services_and_apis,
        _read_max_for_repeat,
    )


def generate_experiment_plots(ctx: Dict) -> List[Path]:  # type: ignore
    if ctx.get('type') not in SUPPORTED_TYPES:
        return []
    
    unit_entries = ctx['unit_entries']  # list of {run_unit_name, repeat_metric_files, load_value, ...}
    out_dir: Path = ctx['output_dir']
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Extract APIs from first unit entry
    if not unit_entries:
        return []
    
    # Get APIs from context or infer from first unit
    apis = ctx.get('apis', [])
    if not apis:
        # Try to infer from first unit's metric files
        first_unit = unit_entries[0]
        repeat_metric_files = first_unit.get('repeat_metric_files', [])
        if repeat_metric_files:
            _, apis = _infer_services_and_apis(repeat_metric_files, [], [])
    
    # Enforce: only one API supported
    if len(apis) != 1:
        raise ValueError(f"max-queue-motivation only supports exactly one API, got {len(apis)}: {apis}")
    
    api = apis[0]
    
    # Extract loads
    loads = []
    load_to_unit = {}
    for unit_entry in unit_entries:
        load_value = unit_entry.get('load_value')
        if load_value is not None:
            loads.append(load_value)
            load_to_unit[load_value] = unit_entry
    
    # Remove duplicates and sort
    loads = sorted(list(set(loads)))
    
    # Enforce: exactly two loads
    if len(loads) != 2:
        raise ValueError(f"max-queue-motivation assumes exactly 2 loads, got {len(loads)}: {loads}")
    
    # Get services from first unit entry
    first_unit = unit_entries[0]
    repeat_metric_files = first_unit.get('repeat_metric_files', [])
    
    # Try to get services from context if available
    fallback_services = []
    fallback_apis = [api]
    
    # Look for services in the first unit entry's context if available
    # This is a heuristic - try to get from first record or infer
    services, _ = _infer_services_and_apis(repeat_metric_files, fallback_services, fallback_apis)
    services = [_normalize_service_name(svc) for svc in services]
    
    import os
    if os.environ.get('PLOT_DEBUG'):
        print(f"[max-queue-motivation] services={services} api={api} loads={loads}")
    
    data_max: Dict[str, Dict[int, List[float]]] = {svc: {load: [] for load in loads} for svc in services}

    for load in loads:
        unit_entry = load_to_unit[load]
        repeat_metric_files = unit_entry.get('repeat_metric_files', [])

        for repeat_idx, mf in enumerate(repeat_metric_files):
            if os.environ.get('PLOT_DEBUG'):
                print(f"[max-queue-motivation][load {load}][repeat {repeat_idx}] scan start")

            for svc in services:
                max_v = _read_max_for_repeat(mf, svc, api)
                data_max[svc][load].append(max_v)
                if os.environ.get('PLOT_DEBUG'):
                    print(
                        f"[max-queue-motivation][load {load}][repeat {repeat_idx}] "
                        f"svc={svc} api={api} max={max_v}"
                    )

    if os.environ.get('PLOT_DEBUG'):
        counts = {
            svc: {load: len(lst) for load, lst in loads_dict.items()}
            for svc, loads_dict in data_max.items()
        }
        print(f"[max-queue-motivation][aggregate] repeat_counts={counts}")

    non_zero_services = []
    for svc in services:
        for load in loads:
            if any(v > 0 for v in data_max[svc][load]):
                non_zero_services.append(svc)
                break

    if os.environ.get('PLOT_DEBUG'):
        print(
            f"[max-queue-motivation] Filtering services: original={len(services)} "
            f"kept={len(non_zero_services)} dropped={set(services)-set(non_zero_services)}"
        )
    services = non_zero_services

    if not services:
        print("[max-queue-motivation] All services have zero max queue; skipping plots.")
        return []

    try:
        from ..plotting_primitives import (
            SubplotGrid, ACM_COMPACT_HALF, ACM_QUARTER, plot_grouped_bars
        )
    except ImportError:
        try:
            from exec.plots.plotting_primitives import (  # type: ignore
                SubplotGrid, ACM_COMPACT_HALF, ACM_QUARTER, plot_grouped_bars
            )
        except ImportError:
            from plotting_primitives import (  # type: ignore
                SubplotGrid, ACM_COMPACT_HALF, ACM_QUARTER, plot_grouped_bars
            )

    style = ACM_QUARTER
    paths: List[Path] = []

    def _save_motivation(data: Dict[str, Dict[int, List[float]]], rel_name: str, ylabel: str, log_y: bool) -> Path:
        grid = SubplotGrid(style, layout="1x1")
        ax = grid.get_ax(0, 0)
        bar_groups = []
        for load in loads:
            means = []
            stds = []
            for svc in services:
                m, s = _mean_std(data[svc][load])
                if m is None:
                    m = 0.0
                if s is None:
                    s = 0.0
                means.append(m)
                stds.append(s)
            label = f'{round(load/1000)} KRPS'
            bar_groups.append((label, means, stds))

        plot_grouped_bars(ax, list(range(len(services))), bar_groups, style=style)
        ax.set_xticks(list(range(len(services))))
        ax.set_xticklabels(services, rotation=30, ha='right')

        max_val = 0.0
        for _, means, stds in bar_groups:
            for m, s in zip(means, stds):
                top = m + (s if s else 0)
                if top > max_val:
                    max_val = top

        if log_y:
            ylim_max = 1.2 * max_val if max_val > 0 else 10.0
            ylim_min = 0.9
            grid.configure_ax(ax, ylabel=ylabel, ylim=(ylim_min, ylim_max), log_y=True)
        else:
            ylim_max = 1.2 * max_val if max_val > 0 else 1.0
            ylim_min = 0.0
            grid.configure_ax(ax, ylabel=ylabel, ylim=(ylim_min, ylim_max), log_y=False)

        grid.add_shared_legend(position="top")
        fig_path = out_dir / rel_name
        grid.save(fig_path)
        return fig_path

    paths.append(_save_motivation(data_max, 'max_queue_motivation_bar.pdf', 'Max Queueing (req)', True))

    return paths
