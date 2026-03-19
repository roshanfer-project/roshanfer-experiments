"""Experiment-level latency vs throughput line plot with 95% CI.

REWRITTEN to use new RWG data loading and plotting architecture.

Produces one line plot per experiment:
  * latency_vs_throughput.pdf (P99 latency vs throughput with 95% CI error bars)

Supports single and multiple APIs. X-axis = throughput (RPS). Y-axis = p99_latency (ms).
All APIs are plotted on a single subplot with distinct lines.
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
        SubplotGrid, ACM_QUARTER, plot_line
    )
except ImportError:
    try:
        from exec.plots.data_loader import load_experiment_data  # type: ignore
        from exec.plots.aggregation import aggregate_overall_metric  # type: ignore
        from exec.plots.plotting_primitives import (  # type: ignore
            SubplotGrid, ACM_QUARTER, plot_line
        )
    except ImportError:
        from data_loader import load_experiment_data  # type: ignore
        from aggregation import aggregate_overall_metric  # type: ignore
        from plotting_primitives import (  # type: ignore
            SubplotGrid, ACM_QUARTER, plot_line
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
    
    if not apis:
        if os.environ.get('PLOT_DEBUG'):
            print("[latency_vs_throughput_experiment] Error: No APIs found")
        return produced
    
    # Sort units by load_value for proper line plot ordering
    unit_entries = [u for u in unit_entries if u.get('load_value') is not None]
    unit_entries.sort(key=lambda u: u['load_value'])
    
    if not unit_entries:
        if os.environ.get('PLOT_DEBUG'):
            print("[latency_vs_throughput_experiment] No units with valid load values")
        return produced
    
    # Collect data per API: api -> {throughput_means, latency_means, latency_cis}
    api_data: Dict[str, Dict] = {api: {'tp': [], 'lat': [], 'lat_ci': []} for api in apis}
    
    for unit_entry in unit_entries:
        load_value = unit_entry['load_value']
        artifact_dirs = unit_entry.get('artifact_dirs', [])
        
        if os.environ.get('PLOT_DEBUG'):
            print(f"[latency_vs_throughput_experiment] Processing load {load_value} with {len(artifact_dirs)} repeats")
        
        try:
            for api in apis:
                unit_throughputs = []
                unit_latencies = []
                for artifact_dir in artifact_dirs:
                    try:
                        from ..data_loader import load_repeat_data
                        repeat_data = load_repeat_data(artifact_dir)
                    except ImportError:
                        from exec.plots.data_loader import load_repeat_data  # type: ignore
                        repeat_data = load_repeat_data(artifact_dir)
                    if repeat_data and api in repeat_data:
                        vals = repeat_data[api]
                        if len(vals) == 3:
                            _, realtime, _ = vals
                        else:
                            _, realtime = vals
                        if realtime is not None:
                            if 'throughput_rate' in realtime.df.columns and 'p99_latency' in realtime.df.columns:
                                unit_throughputs.extend(realtime.df['throughput_rate'].tolist())
                                unit_latencies.extend(realtime.df['p99_latency'].tolist())
                if unit_throughputs and unit_latencies:
                    tp_mean, tp_std, tp_ci = aggregate_overall_metric(unit_throughputs)
                    lat_mean, lat_std, lat_ci = aggregate_overall_metric(unit_latencies)
                    if tp_mean is not None and lat_mean is not None:
                        api_data[api]['tp'].append(tp_mean)
                        api_data[api]['lat'].append(lat_mean)
                        api_data[api]['lat_ci'].append(lat_ci if lat_ci is not None else 0.0)
        except Exception as e:
            if os.environ.get('PLOT_DEBUG'):
                print(f"[latency_vs_throughput_experiment] Error processing load {load_value}: {e}")
                import traceback
                traceback.print_exc()
            continue
    
    # Check we have data for at least one API
    has_data = any(len(api_data[api]['tp']) > 0 for api in apis)
    if not has_data:
        if os.environ.get('PLOT_DEBUG'):
            print("[latency_vs_throughput_experiment] No valid data points found")
        return produced
    
    # Single subplot for all APIs
    grid = SubplotGrid(ACM_QUARTER, layout="1x1")
    ax = grid.get_ax(0, 0)

    all_tp, all_lat = [], []
    n_apis = sum(1 for api in apis if api_data[api]['tp'] and api_data[api]['lat'])
    for idx, api in enumerate(apis):
        tp = api_data[api]['tp']
        lat = api_data[api]['lat']
        lat_ci = api_data[api]['lat_ci']
        if not tp or not lat:
            continue
        display_api = api.replace('_all', '') if api.endswith('_all') else api
        plot_line(
            ax, tp, lat,
            yerr=lat_ci,
            label=display_api,
            style=ACM_QUARTER,
            color_idx=idx,
            style_idx=idx if n_apis > 1 else 0,
            show_markers=True
        )
        all_tp.extend(tp)
        all_lat.extend(lat)

    ax.legend()
    grid.configure_ax(
        ax,
        xlabel="Throughput (RPS)",
        ylabel="P99 Latency (ms)",
        x_data=all_tp,
        y_data=all_lat,
        y_type="int",
        grid=True
    )
    
    line_path = out_dir / 'latency_vs_throughput.pdf'
    grid.save(line_path)
    produced.append(line_path)
    
    if os.environ.get('PLOT_DEBUG'):
        print(f"[latency_vs_throughput_experiment] Generated line plot: {line_path.name}")
    
    return produced
