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

from ..data_loader import extract_series

# Reuse utility functions from max_queue_unit
try:
    from .max_queue_unit import _normalize_service_name, _mean_std, _infer_services_and_apis
except Exception:  # pragma: no cover
    from experiments.exec.plots.plugins.max_queue_unit import _normalize_service_name, _mean_std, _infer_services_and_apis  # type: ignore


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
    
    # Build nested data structure: data[service][load] -> list of per-repeat maxima
    data: Dict[str, Dict[int, List[float]]] = {svc: {load: [] for load in loads} for svc in services}
    
    # Collect data for each load
    for load in loads:
        unit_entry = load_to_unit[load]
        repeat_metric_files = unit_entry.get('repeat_metric_files', [])
        
        for repeat_idx, mf in enumerate(repeat_metric_files):
            if os.environ.get('PLOT_DEBUG'):
                print(f"[max-queue-motivation][load {load}][repeat {repeat_idx}] scan start")
            
            for svc in services:
                # Check for hierarchical data first (prometheus -> api -> service -> max_queue)
                hierarchical_val = None
                
                if 'prometheus' in mf:
                    prom_data = mf['prometheus']
                    if api in prom_data:
                        api_data = prom_data[api]
                        # Look for service match (handling normalization)
                        found_svc_stats = None
                         # Try direct match first
                        if svc in api_data:
                             found_svc_stats = api_data[svc]
                        
                        # Try iterating to match normalized names if direct match failed
                        if not found_svc_stats:
                            for raw_svc, stats in api_data.items():
                                if _normalize_service_name(raw_svc) == svc:
                                    found_svc_stats = stats
                                    break
                                    
                        if found_svc_stats and isinstance(found_svc_stats, dict) and 'max_queue' in found_svc_stats:
                            hierarchical_val = float(found_svc_stats['max_queue'])
                
                if hierarchical_val is not None:
                    data[svc][load].append(hierarchical_val)
                    if os.environ.get('PLOT_DEBUG'):
                         print(f"[max-queue-motivation][load {load}][repeat {repeat_idx}] found hier val={hierarchical_val} for svc={svc} api={api}")
                    continue

                # Candidate stems in priority order - include both normalized and original service names
                original_service_variants = [svc]
                # If normalized service is 'frontend' or 'nginx', also try their -grpc forms
                if svc == 'frontend':
                    original_service_variants.append('frontend-grpc')
                elif svc == 'nginx':
                    original_service_variants.append('nginx-grpc')
                
                stem_candidates = []
                for service_variant in original_service_variants:
                    stem_candidates.extend([
                        f'queue_length_{api}_{service_variant}',
                        f'queue_length_{service_variant}_{api}',
                        f'queue_length_{service_variant}'
                    ])
                
                chosen = None
                for st in stem_candidates:
                    if st in mf:
                        chosen = st
                        break
                
                if not chosen:
                    # No metric -> treat as zero for this repeat
                    data[svc][load].append(0.0)
                    if os.environ.get('PLOT_DEBUG'):
                        print(f"[max-queue-motivation][load {load}][repeat {repeat_idx}] missing svc={svc} api={api}")
                    continue
                
                ts, vals = extract_series(mf[chosen])
                if not vals:
                    data[svc][load].append(0.0)
                    if os.environ.get('PLOT_DEBUG'):
                        print(f"[max-queue-motivation][load {load}][repeat {repeat_idx}] empty svc={svc} api={api} stem={chosen}")
                else:
                    vmax = max(vals)
                    data[svc][load].append(float(vmax))
                    if os.environ.get('PLOT_DEBUG'):
                        print(f"[max-queue-motivation][load {load}][repeat {repeat_idx}] svc={svc} api={api} stem={chosen} max={vmax}")
    
    if os.environ.get('PLOT_DEBUG'):
        counts = {svc: {load: len(lst) for load, lst in loads_dict.items()} for svc, loads_dict in data.items()}
        print(f"[max-queue-motivation][aggregate] repeat_counts={counts}")
    
    # FILTER: Remove services with all-zero values across all loads
    non_zero_services = []
    for svc in services:
        has_nonzero = False
        for load in loads:
            vals = data[svc][load]
            if any(v > 0 for v in vals):
                has_nonzero = True
                break
        if has_nonzero:
            non_zero_services.append(svc)
            
    if os.environ.get('PLOT_DEBUG'):
        print(f"[max-queue-motivation] Filtering services: original={len(services)} kept={len(non_zero_services)} dropped={set(services)-set(non_zero_services)}")
    services = non_zero_services
    
    if not services:
        print("[max-queue-motivation] All services have zero max queue length; skipping plot.")
        return []

    # Prepare plotting arrays (grouped bars per service, one group per load)
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
            
    # Strict width: 120pt (1.665 inches)
    style = ACM_QUARTER
    
    grid = SubplotGrid(style, layout="1x1")
    ax = grid.get_ax(0, 0)
    
    # Prepare data for plot_grouped_bars
    # bar_groups: List of (label, heights, errors)
    bar_groups = []
    
    for load in loads:
        means = []
        stds = []
        for svc in services:
            m, s = _mean_std(data[svc][load])
            if m is None: m = 0.0
            if s is None: s = 0.0
            
            means.append(m)
            stds.append(s)
            
        # Label with KRPS
        label = f'{round(load/1000)} KRPS'
        bar_groups.append((label, means, stds))
        
    # Plot grouped bars
    plot_grouped_bars(ax, list(range(len(services))), bar_groups, style=style)
    
    # Configure Axes
    ax.set_xticks(list(range(len(services))))
    ax.set_xticklabels(services, rotation=30, ha='right')
    
    # Determine Y-limit
    max_val = 0.0
    for _, means, stds in bar_groups:
        for m, s in zip(means, stds):
            top = m + (s if s else 0)
            if top > max_val:
                max_val = top
    
    ylim_max = 1.2 * max_val if max_val > 0 else 10.0
    # For log scale, start at something small but positive, e.g. 0.9 or 1
    ylim_min = 0.9
    
    # Configure common axis properties
    grid.configure_ax(ax, ylabel='Max Queueing (req)', ylim=(ylim_min, ylim_max), log_y=True)
    
    # Add legend
    grid.add_shared_legend(position="top")
    
    fig_path = out_dir / 'max_queue_motivation_bar.pdf'
    grid.save(fig_path)
    
    return [fig_path]
