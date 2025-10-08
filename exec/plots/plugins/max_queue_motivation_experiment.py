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

try:
    from ..common import extract_series
except Exception:  # pragma: no cover
    from experiments.exec.plots.common import extract_series  # type: ignore

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
    
    # Prepare plotting arrays (grouped bars per service, one group per load)
    try:
        from experiments.canvas import canvas  # type: ignore
    except Exception:
        from canvas import canvas  # type: ignore
    import matplotlib.pyplot as plt  # type: ignore
    
    fig, ax = canvas.create_canvas(width_in_inches=max(3.33, 0.5 * len(services)), aspect_ratio=0.6,
                                   font_size=14, legend_size=12, line_width=1.5, marker_size=4)
    
    # Grouped bar plotting
    n_services = len(services)
    n_loads = len(loads)
    x_indices = list(range(n_services))
    total_group_width = 0.8
    if n_loads > 0:
        bar_width = total_group_width / n_loads
    else:
        bar_width = total_group_width
    
    try:
        import matplotlib.pyplot as plt  # type: ignore
        colors = [c['color'] for c in plt.rcParams['axes.prop_cycle']]
    except Exception:
        colors = []
    
    load_colors = {load: colors[i % len(colors)] if colors else None for i, load in enumerate(loads)}
    
    max_height = 0.0
    max_error = 0.0
    
    for load_idx, load in enumerate(loads):
        offsets = [x - total_group_width/2 + load_idx * bar_width + bar_width/2 for x in x_indices]
        means = []
        stds = []
        
        for svc in services:
            m, s = _mean_std(data[svc][load])
            if m is None:
                m = 0.0
                s = 0.0
            means.append(m)
            stds.append(0.0001 if (s is None or s == 0) else s)
            if m > max_height:
                max_height = m
            if m + (s if s is not None else 0) > max_error:
                max_error = m + (s if s is not None else 0)
        
        ax.bar(offsets, means, yerr=stds, width=bar_width*0.9, label=f'{round(load/100)} KRPS',
               color=load_colors.get(load), edgecolor='black', linewidth=0.6,
               error_kw=dict(capsize=3, elinewidth=1.0, capthick=0.8))
        
        # Add value labels on bars
        """ for ox, m in zip(offsets, means):
            ax.text(ox, m + (0.02 * (max_error if max_error > 0 else 1.0)), f"{m:.0f}", 
                   ha='center', va='bottom', fontsize=9) """
    
    ax.set_xticks(x_indices)
    ax.set_xticklabels(services, rotation=30, ha='right')
    ylab = ax.set_ylabel('Max Queueing (req)', labelpad=20)
    # Move the y-label a little lower (default is y=0.5, try y=0.42)
    ylab.set_position((ylab.get_position()[0], 0.42))
    #ax.set_xlabel('Service')
    ax.yaxis.grid(True, alpha=0.3)
    
    # Set y-limit to 1.2x (max value + error bar), or 1 if all zeros
    ylim_max = 1.2 * max_error if max_error > 0 else 1.0
    ax.set_ylim(0, ylim_max)
    
    # Set y ticks every 100 requests, ensuring the highest tick is above the maximum value
    import numpy as np
    if max_error > 0:
        tick_spacing = 100
        # Find the highest tick that exceeds max_error
        max_tick = tick_spacing * np.ceil(max_error / tick_spacing)
        if max_tick <= max_error:
            max_tick += tick_spacing
        
        ticks = np.arange(0, max_tick + tick_spacing, tick_spacing)
        ax.set_yticks(ticks)
        ax.set_ylim(0, max_tick + 10)  # Small padding above highest tick
    
    # Always show legend for loads (since we have exactly 2 loads)
    try:
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5, 1.04),
                       ncol=max(1, len(labels)), frameon=True, fancybox=True,
                       framealpha=0.85, edgecolor='#bbbbbb')
            fig.subplots_adjust(top=0.84)
    except Exception:
        pass
    
    fig_path = out_dir / 'max_queue_motivation_bar.pdf'
    fig.savefig(fig_path, bbox_inches='tight')
    plt.close(fig)
    
    return [fig_path]
