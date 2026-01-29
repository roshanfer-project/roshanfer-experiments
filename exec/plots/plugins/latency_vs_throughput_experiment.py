"""Experiment-level latency vs throughput line plot with 95% CI.

REWRITTEN to use new RWG data loading and plotting architecture.

Produces one line plot per experiment:
  * latency_vs_throughput.pdf (P99 latency vs throughput with 95% CI error bars)

Supports single API only. X-axis = success field (throughput in RPS).
Y-axis = p99_latency field (milliseconds). Data aggregated by load level with 95% CI.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional
import os

# Import new RWG data loading and plotting
try:
    from ..data_loader import load_experiment_data
    from ..aggregation import aggregate_overall_metric
    from ..plotting_primitives import (
        SubplotGrid, ACM_COMPACT_HALF, plot_line
    )
except ImportError:
    try:
        from exec.plots.data_loader import load_experiment_data  # type: ignore
        from exec.plots.aggregation import aggregate_overall_metric  # type: ignore
        from exec.plots.plotting_primitives import (  # type: ignore
            SubplotGrid, ACM_COMPACT_HALF, plot_line
        )
    except ImportError:
        from data_loader import load_experiment_data  # type: ignore
        from aggregation import aggregate_overall_metric  # type: ignore
        from plotting_primitives import (  # type: ignore
            SubplotGrid, ACM_COMPACT_HALF, plot_line
        )

SUPPORTED_TYPES = ['latency-vs-throughput']


def generate_experiment_plots(ctx: Dict) -> List[Path]:
    """Generate experiment-level latency vs throughput line plot with 95% CI.
    
    Loads overall-{api}.json from all units and repeats, aggregates by load level,
    and plots p99_latency vs success (throughput) with 95% confidence intervals.
    """
    if os.environ.get('PLOT_DEBUG'):
        print(f"[latency_vs_throughput_experiment] Called with type: {ctx.get('type')}")
    
    if ctx.get('type') not in SUPPORTED_TYPES:
        if os.environ.get('PLOT_DEBUG'):
            print(f"[latency_vs_throughput_experiment] Type {ctx.get('type')} not in supported types: {SUPPORTED_TYPES}")
        return []
    
    apis: List[str] = ctx.get('apis') or []
    unit_entries = ctx['unit_entries']  # list of {run_unit_name, artifact_dirs, load_value, ...}
    out_dir: Path = ctx['output_dir']
    
    out_dir.mkdir(parents=True, exist_ok=True)
    produced: List[Path] = []
    
    if not unit_entries:
        return produced
    
    # Check for single API support
    if len(apis) > 1:
        if os.environ.get('PLOT_DEBUG'):
            print(f"[latency_vs_throughput_experiment] Error: Multiple APIs not supported. Found: {apis}")
        raise ValueError(f"latency-vs-throughput plot type supports single API only. Found {len(apis)} APIs: {apis}")
    
    if not apis:
        if os.environ.get('PLOT_DEBUG'):
            print("[latency_vs_throughput_experiment] Error: No APIs found")
        return produced
    
    api = apis[0]  # Single API
    
    # Sort units by load_value for proper line plot ordering
    unit_entries = [u for u in unit_entries if u.get('load_value') is not None]
    unit_entries.sort(key=lambda u: u['load_value'])
    
    if not unit_entries:
        if os.environ.get('PLOT_DEBUG'):
            print("[latency_vs_throughput_experiment] No units with valid load values")
        return produced
    
    # Collect data by load level for aggregation
    load_levels = []  # X-axis values (load levels)
    throughput_means = []  # Mean throughput per load level
    throughput_cis = []   # 95% CI for throughput
    latency_means = []    # Mean P99 latency per load level  
    latency_cis = []      # 95% CI for P99 latency
    
    for unit_entry in unit_entries:
        load_value = unit_entry['load_value']
        artifact_dirs = unit_entry.get('artifact_dirs', [])
        
        if os.environ.get('PLOT_DEBUG'):
            print(f"[latency_vs_throughput_experiment] Processing load {load_value} with {len(artifact_dirs)} repeats")
        
        # Collect data from all repeats for this load level
        unit_throughputs = []
        unit_latencies = []
        
        try:
            for artifact_dir in artifact_dirs:
                # Load this specific repeat
                from ..data_loader import load_repeat_data
                repeat_data = load_repeat_data(artifact_dir)
                
                if repeat_data and api in repeat_data:
                    vals = repeat_data[api]
                    if len(vals) == 3:
                         _, realtime, _ = vals
                    else:
                         _, realtime = vals
                    
                    if realtime is not None:
                        # Extract throughput (throughput_rate) and p99 latency from realtime data
                        # We use all samples from the realtime report
                        if 'throughput_rate' in realtime.df.columns and 'p99_latency' in realtime.df.columns:
                            unit_throughputs.extend(realtime.df['throughput_rate'].tolist())
                            unit_latencies.extend(realtime.df['p99_latency'].tolist())
            
            # Aggregate across repeats for this load level
            if unit_throughputs and unit_latencies:
                # Calculate mean and 95% CI for throughput
                tp_mean, tp_std, tp_ci = aggregate_overall_metric(unit_throughputs)
                # Calculate mean and 95% CI for latency
                lat_mean, lat_std, lat_ci = aggregate_overall_metric(unit_latencies)
                
                if tp_mean is not None and lat_mean is not None:
                    load_levels.append(load_value)
                    throughput_means.append(tp_mean)
                    throughput_cis.append(tp_ci if tp_ci is not None else 0.0)
                    latency_means.append(lat_mean)
                    latency_cis.append(lat_ci if lat_ci is not None else 0.0)
                    
                    if os.environ.get('PLOT_DEBUG'):
                        print(f"[latency_vs_throughput_experiment] Load {load_value}: throughput={tp_mean:.1f}±{tp_ci:.1f}, latency={lat_mean:.3f}±{lat_ci:.3f}")
        
        except Exception as e:
            if os.environ.get('PLOT_DEBUG'):
                print(f"[latency_vs_throughput_experiment] Error processing load {load_value}: {e}")
                import traceback
                traceback.print_exc()
            continue
    
    if not load_levels:
        if os.environ.get('PLOT_DEBUG'):
            print("[latency_vs_throughput_experiment] No valid data points found")
        return produced
    
    # Use ACM compact style
    style = ACM_COMPACT_HALF
    
    # Create single subplot
    grid = SubplotGrid(style, layout="1x1")
    ax = grid.get_ax(0, 0)
    
    # Create line plot with 95% confidence intervals
    plot_line(
        ax, throughput_means, latency_means,
        yerr=latency_cis,
        label=f'{api} (P99 Latency)',
        style=style,
        color_idx=0,  # Use default color
        show_markers=True  # Show markers on line plot
    )
    
    # Configure axis
    display_api = api.replace('_all', '') if api.endswith('_all') else api
    
    grid.configure_ax(
        ax,
        xlabel="Success Throughput (RPS)",
        ylabel="P99 Latency (ms)",
        title=f"Latency vs Throughput - {display_api}",
        x_data=throughput_means,
        y_data=latency_means,
        y_step=2,
        y_type="int",
        x_step=2000,
        grid=True
    )
    
    # Add legend
    grid.add_shared_legend(position="top")
    
    # Save
    line_path = out_dir / 'latency_vs_throughput.pdf'
    grid.save(line_path)
    produced.append(line_path)
    
    if os.environ.get('PLOT_DEBUG'):
        print(f"[latency_vs_throughput_experiment] Generated line plot with {len(load_levels)} load levels: {line_path.name}")
    
    return produced
