"""Experiment-level aggregate plots for latency-and-goodput-vs-load.

REWRITTEN to use new RWG data loading and plotting architecture.

Produces exactly two figures per experiment (spanning all loads):
  * latency_vs_load.pdf  (P95 per API with error bars across repeats)
  * goodput_vs_load.pdf  (goodput per API with error bars across repeats)

Supports up to 3 APIs. X-axis = load * 10 / 1000 (KRPS).
Error bars: 95% confidence interval across repeats.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional
import math
import os

# Import new RWG data loading and plotting
try:
    from ..data_loader import load_experiment_data
    from ..aggregation import aggregate_by_api
    from ..plotting_primitives import (
        SubplotGrid, ACM_COMPACT_HALF, plot_line
    )
except ImportError:
    try:
        from exec.plots.data_loader import load_experiment_data  # type: ignore
        from exec.plots.aggregation import aggregate_by_api  # type: ignore
        from exec.plots.plotting_primitives import (  # type: ignore
            SubplotGrid, ACM_COMPACT_HALF, plot_line
        )
    except ImportError:
        from data_loader import load_experiment_data  # type: ignore
        from aggregation import aggregate_by_api  # type: ignore
        from plotting_primitives import (  # type: ignore
            SubplotGrid, ACM_COMPACT_HALF, plot_line
        )

SUPPORTED_TYPES = ['latency-and-goodput-vs-load']


def _lookup_slo(slos: Optional[Dict[str, float]], api: str) -> Optional[float]:
    """Return SLO ms for api using flexible key matching."""
    if not slos or not api:
        return None
    
    base = api[:-4] if api.endswith('_all') else api
    candidates = [base, base.replace('-', '_'), base.replace('_', '-')]
    candidates.extend([c.lower() for c in candidates])
    
    seen = []
    ordered = []
    for c in candidates:
        if c not in seen:
            seen.append(c)
            ordered.append(c)
    
    for key in ordered:
        if key in slos:
            return slos[key]
    
    return None


def generate_experiment_plots(ctx: Dict) -> List[Path]:
    """Generate experiment-level plots aggregating across all load points.
    
    Loads overall-{api}.json from all units and repeats, aggregates metrics,
    and plots latency and goodput vs load with error bars.
    """
    if ctx.get('type') not in SUPPORTED_TYPES:
        return []
    
    apis: List[str] = ctx.get('apis') or []
    unit_entries = ctx['unit_entries']  # list of {run_unit_name, artifact_dirs, load_value, ...}
    out_dir: Path = ctx['output_dir']
    slos: Optional[Dict[str, float]] = ctx.get('slos')
    
    out_dir.mkdir(parents=True, exist_ok=True)
    produced: List[Path] = []
    
    if not unit_entries or not apis:
        return produced
    
    # Sort units by load_value
    unit_entries = [u for u in unit_entries if u.get('load_value') is not None]
    unit_entries.sort(key=lambda u: u['load_value'])
    
    if not unit_entries:
        return produced
    
    # Build per-API series: load -> (mean, std, ci) for each metric
    loads = []  # in KRPS
    latency_series = {api: {'p95': []} for api in apis}
    goodput_series = {api: [] for api in apis}
    
    for unit_entry in unit_entries:
        load = unit_entry['load_value']
        x_val = (load * 10) / 1000.0  # Convert to KRPS
        loads.append(x_val)
        
        artifact_dirs = unit_entry.get('artifact_dirs', [])
        
        # Load and aggregate data for this unit
        try:
            all_repeats = []
            for artifact_dir in artifact_dirs:
                # Load this specific repeat
                from ..data_loader import load_repeat_data
                repeat_data = load_repeat_data(artifact_dir)
                if repeat_data:
                    all_repeats.append(repeat_data)
            
            if not all_repeats:
                # No data for this load point - add None placeholders
                for api in apis:
                    latency_series[api]['p95'].append((None, None, None))
                    goodput_series[api].append((None, None, None))
                continue
            
            # Aggregate across repeats
            aggregated = aggregate_by_api(all_repeats)
            
            for api in apis:
                if api in aggregated:
                    # Latency P95
                    p95_mean, p95_std, p95_ci = aggregated[api].get('p95_latency', (None, None, None))
                    latency_series[api]['p95'].append((p95_mean, p95_std, p95_ci))
                    
                    # Goodput
                    gp_mean, gp_std, gp_ci = aggregated[api].get('goodput', (None, None, None))
                    goodput_series[api].append((gp_mean, gp_std, gp_ci))
                else:
                    latency_series[api]['p95'].append((None, None, None))
                    goodput_series[api].append((None, None, None))
        
        except Exception as e:
            if os.environ.get('PLOT_DEBUG'):
                print(f"[latency_goodput_vs_load_experiment] Error processing unit {load}: {e}")
            # Add None placeholders
            for api in apis:
                latency_series[api]['p95'].append((None, None, None))
                goodput_series[api].append((None, None, None))
    
    # Use ACM compact style
    style = ACM_COMPACT_HALF
    
    # === LATENCY FIGURE ===
    layout = f"row-{len(apis)}" if len(apis) > 1 else "1x1"
    grid_lat = SubplotGrid(style, layout=layout)
    
    # Track all latency values for dynamic Y-axis
    all_latency_values = []
    
    for idx, api in enumerate(apis):
        ax = grid_lat.get_ax(0, idx)
        
        # Extract data for this API
        p95_data = latency_series[api]['p95']
        means = [item[0] for item in p95_data]
        cis = [item[2] if item[2] is not None else 0.0 for item in p95_data]
        
        # Filter out None values
        valid_data = [(l, m, c) for l, m, c in zip(loads, means, cis)
                     if m is not None and not (isinstance(m, float) and math.isnan(m))]
        
        if valid_data:
            valid_loads, valid_means, valid_cis = zip(*valid_data)
            
            # Plot with error bars
            plot_line(
                ax, valid_loads, valid_means,
                yerr=valid_cis,
                label='P95',
                style=style,
                color_idx=1,
                show_markers=True  # Good for sparse load points
            )
            
            all_latency_values.extend(valid_means)
        
        # Add SLO line
        display_api = api.replace('_all', '') if api.endswith('_all') else api
        slo_val = _lookup_slo(slos, display_api)
        if slo_val is not None:
            ax.axhline(y=slo_val, color='r', linestyle='--',
                      label='SLO', linewidth=style.line_width)
            all_latency_values.append(slo_val)
        
        # Configure axis
        ax.set_title(display_api, fontsize=style.title_size)
        ax.set_yscale('log')
        ax.grid(True, alpha=0.3)
        
        # Set x-axis limits with padding
        if loads:
            span = loads[-1] - loads[0]
            pad = 0.03 * span if span > 0 else 0.05
            ax.set_xlim(loads[0] - pad, loads[-1] + pad)
    
    # Dynamic Y-axis: 5x max value
    if all_latency_values:
        dyn_y_max = max(all_latency_values) * 5.0 * 1.05
        dyn_y_max = max(dyn_y_max, 10)  # Minimum 10ms
    else:
        dyn_y_max = 500
    
    for idx in range(len(apis)):
        ax = grid_lat.get_ax(0, idx)
        ax.set_ylim(1, dyn_y_max)
    
    # Configure labels
    grid_lat.configure_labels(
        pattern="leftmost_y_bottom_x",
        xlabel="Offered Load (KRPS)",
        ylabel="P95 Latency (ms)"
    )
    
    # Add legend
    grid_lat.add_shared_legend(position="top")
    
    # Save
    lat_path = out_dir / 'latency_vs_load.pdf'
    grid_lat.save(lat_path)
    produced.append(lat_path)
    
    # === GOODPUT FIGURE ===
    grid_gp = SubplotGrid(style, layout=layout)
    
    for idx, api in enumerate(apis):
        ax = grid_gp.get_ax(0, idx)
        
        # Extract data for this API
        gp_data = goodput_series[api]
        means = [(item[0] / 1000.0) if item[0] is not None else None for item in gp_data]  # Convert to KRPS
        cis = [(item[2] / 1000.0) if item[2] is not None else 0.0 for item in gp_data]
        
        # Filter out None values
        valid_data = [(l, m, c) for l, m, c in zip(loads, means, cis)
                     if m is not None and not (isinstance(m, float) and math.isnan(m))]
        
        if valid_data:
            valid_loads, valid_means, valid_cis = zip(*valid_data)
            
            # Plot with error bars
            plot_line(
                ax, valid_loads, valid_means,
                yerr=valid_cis,
                label='Goodput',
                style=style,
                color_idx=0,
                show_markers=True  # Good for sparse load points
            )
        
        # Configure axis
        display_api = api.replace('_all', '') if api.endswith('_all') else api
        ax.set_title(display_api, fontsize=style.title_size)
        ax.grid(True, alpha=0.3)
        
        # Set x-axis limits with padding
        if loads:
            span = loads[-1] - loads[0]
            pad = 0.03 * span if span > 0 else 0.05
            ax.set_xlim(loads[0] - pad, loads[-1] + pad)
    
    # Configure labels
    grid_gp.configure_labels(
        pattern="leftmost_y_bottom_x",
        xlabel="Offered Load (KRPS)",
        ylabel="Goodput (KRPS)"
    )
    
    # Add legend
    grid_gp.add_shared_legend(position="top")
    
    # Save
    goodput_path = out_dir / 'goodput_vs_load.pdf'
    grid_gp.save(goodput_path)
    produced.append(goodput_path)
    
    if os.environ.get('PLOT_DEBUG'):
        print(f"[latency_goodput_vs_load_experiment] Generated {len(produced)} plots: {[p.name for p in produced]}")
    
    return produced
