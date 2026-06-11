"""Repeat-level plotting plugin for latency/rate vs time experiments.

REWRITTEN to use new plotting architecture (RWG data, plotting_primitives).

Exports a single function:
  generate_repeat_plots(repeat_ctx) -> List[Path]

repeat_ctx fields (dict):
  {
    'type': experiment type string,
    'experiment_name': str,
    'group_name': str,
    'repeat_index': int,
    'artifact_dir': Path (repeat directory),
    'metrics_dir': Path (repeat metrics directory - NOT USED, we read from output/),
    'output_dir': Path (destination for plots),
    'metric_files': Dict[str, dict] - NOT USED (legacy Prometheus)
    'slos': Optional[Dict[str, float]] - SLO mapping per API
  }

This module loads realtime-{api}.csv files from the repeat's output/ directory
and generates stacked rate plots and latency line plots using plotting_primitives.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
import pandas as pd
import numpy as np
import json
import os

# Import new plotting infrastructure
try:
    from ..data_loader import load_repeat_data, RealtimeData
    from ..plotting_primitives import (
        SubplotGrid, ACM_COMPACT_HALF, ACM_QUARTER,
        PlotStyle, plot_line, plot_stacked_area
    )
except ImportError:
    try:
        from exec.plots.data_loader import load_repeat_data, RealtimeData  # type: ignore
        from exec.plots.plotting_primitives import (  # type: ignore
            SubplotGrid, ACM_COMPACT_HALF, ACM_QUARTER,
            PlotStyle, plot_line, plot_stacked_area
        )
    except ImportError:
        from data_loader import load_repeat_data, RealtimeData  # type: ignore
        from plotting_primitives import (  # type: ignore
            SubplotGrid, ACM_COMPACT_HALF, ACM_QUARTER,
            PlotStyle, plot_line, plot_stacked_area
        )


# Explicit experiment types this plugin supports
SUPPORTED_TYPES = [
    'latency-and-rate-vs-time',  # per-repeat only; no cross-repeat aggregation
]

# Custom color map for rate plots
RATE_COLOR_MAP = {
    'goodput': '#4daf4a',       # Green
    'SLO violation': '#e41a1c', # Red
    'dropped': '#ff7f00',       # Orange
    'errors': '#999999',        # Gray
}

# Distinct from P50/P99 line colors (first two palette entries are red/blue)
SLO_LINE_COLOR = 'black'


def _load_global_slos() -> Optional[Dict[str, float]]:
    """Search for config.json / config.sample.json containing 'slos' starting from this file upward.
    
    Returns the first discovered slos mapping or None.
    """
    candidates_rel = [
        'experiments/exec/config.json',
        'experiments/exec/config.sample.json',
        'config.json',
        'config.sample.json',
    ]
    tried = []
    this_dir = Path(__file__).resolve().parent
    search_roots = [this_dir] + list(this_dir.parents)
    for root in search_roots:
        for rel in candidates_rel:
            cpath = (root / rel).resolve()
            if cpath in tried:
                continue
            tried.append(cpath)
            if not cpath.exists():
                continue
            try:
                data = json.loads(cpath.read_text())
            except Exception:
                continue
            if isinstance(data, dict) and isinstance(data.get('slos'), dict):
                if os.environ.get('PLOT_DEBUG'):
                    print(f"[plot-debug] loaded SLOs from {cpath}: {list(data['slos'].keys())}")
                return data['slos']  # type: ignore
    if os.environ.get('PLOT_DEBUG'):
        print('[plot-debug] no SLO config found; tried:', ', '.join(str(p) for p in tried))
    return None


def _lookup_slo(slos: Optional[Dict[str, float]], api: str) -> float:
    """Return SLO ms for api using flexible key matching.
    
    Tries variants: exact, hyphen/underscore swapped, lowercase, stripped _all.
    Defaults to 60.0ms if not found.
    """
    if not slos or not api:
        return 60.0
    
    candidates = [api]
    if api.endswith('_all'):
        candidates.append(api[:-4])
    if '-' in api:
        candidates.append(api.replace('-', '_'))
    if '_' in api:
        candidates.append(api.replace('_', '-'))
    candidates.extend({c.lower() for c in candidates})
    
    seen = []
    ordered = []
    for c in candidates:
        if c not in seen:
            seen.append(c)
            ordered.append(c)
    
    for key in ordered:
        if key in slos:
            return float(slos[key])
    
    return 60.0  # Default SLO


def _latency_log_ylim_many(dfs: Sequence[pd.DataFrame], slo_ms: float) -> Tuple[float, float]:
    """ymax = 1.2 * max(plotted percentiles, SLO) over all frames; ymin 1 ms for log scale."""
    vals: List[float] = []
    if slo_ms > 0:
        vals.append(float(slo_ms))
    for df in dfs:
        for col in ('p50_latency', 'p99_latency', 'p95_latency'):
            if col not in df.columns:
                continue
            s = pd.to_numeric(df[col], errors='coerce').dropna()
            if s.empty:
                continue
            m = float(s.max())
            if m > 0 and np.isfinite(m):
                vals.append(m)
    top = max(vals) if vals else 1.0
    if top <= 0:
        top = 1.0
    ymax = max(1.2 * top, 1.0)
    return (1.0, ymax)


def _latency_log_ylim(df: pd.DataFrame, slo_ms: float) -> Tuple[float, float]:
    """ymax = 1.2 * max(plotted percentiles, SLO); ymin fixed at 1 ms for log scale."""
    return _latency_log_ylim_many([df], slo_ms)


def _rate_ylim_krps(y_series: Dict[str, np.ndarray]) -> Tuple[float, float]:
    """Linear y for stacked rates: ymin=0, ymax 20% above max total stack height (KRPS)."""
    if not y_series:
        return (0.0, 1.0)
    first = next(iter(y_series.values()))
    total = np.zeros_like(np.asarray(first, dtype=float), dtype=float)
    for arr in y_series.values():
        total = total + np.asarray(arr, dtype=float)
    mx = float(np.max(total)) if total.size else 0.0
    if mx <= 0:
        return (0.0, 1.0)
    return (0.0, max(1.2 * mx, 1e-6))


def _prepare_rate_data_for_stack(realtime: RealtimeData) -> Dict[str, np.ndarray]:
    """Prepare rate data for stacked area plot.
    
    Returns dictionary mapping metric name to rate values (KRPS).
    Metrics are ordered for stacking: goodput (bottom) -> violations -> dropped -> errors (top)
    """
    df = realtime.df
    
    if df.empty:
        return {}
    
    # Convert to KRPS (thousands of requests per second)
    y_series = {}
    
    # Stack order (bottom to top):
    # 1. Goodput (base layer)
    if 'goodput' in df.columns:
        y_series['goodput'] = (df['goodput'] / 1000.0).values
    
    # 2. SLO violations
    if 'slo_violations' in df.columns:
        y_series['SLO violation'] = (df['slo_violations'] / 1000.0).values
    
    # 3. Dropped requests
    if 'dropped_requests' in df.columns:
        y_series['dropped'] = (df['dropped_requests'] / 1000.0).values
    

    
    return y_series


def _plot_single_api_rate(realtime: RealtimeData, out_path: Path, style: PlotStyle):
    """Plot stacked rate chart for a single API."""
    grid = SubplotGrid(style, layout="1x1")
    ax = grid.get_ax(0, 0)
    
    y_series = _prepare_rate_data_for_stack(realtime)
    if not y_series:
        grid.save(out_path)
        return
    
    df = realtime.df
    x = df['relative_time'].values
    
    # Plot stacked area
    # Plot stacked area
    plot_stacked_area(ax, x, y_series, style=style, color_map=RATE_COLOR_MAP)
    
    y_lo, y_hi = _rate_ylim_krps(y_series)
    grid.configure_ax(ax, xlabel='Time (s)', ylabel='Rate (KRPS)', grid=True,
    x_data=x, x_type='int', y_type='int', y_step=2, ylim=(y_lo, y_hi))
    
    # Add legend
    grid.add_shared_legend(position="top-left", two_rows=True)
    
    # Save
    grid.save(out_path)


def _plot_multi_api_rate(api_realtime: Dict[str, RealtimeData], out_path: Path, style: PlotStyle):
    """Plot stacked rate charts for multiple APIs in subplots."""
    n_apis = len(api_realtime)
    layout = f"row-{n_apis}"
    
    style_grid = replace(style, aspect_ratio=0.9)
    grid = SubplotGrid(style_grid, layout=layout)
    nrows = 1
    
    # 1. Pre-calculate data and global Y max for consistent scaling
    prepared_data = {}
    global_max_y = 0.0
    
    for api_name, realtime in api_realtime.items():
        y_series = _prepare_rate_data_for_stack(realtime)
        if not y_series:
            continue
        prepared_data[api_name] = y_series
        
        # Calculate max height of stack
        # y_series values are numpy arrays of same length
        first_arr = next(iter(y_series.values()))
        total = np.zeros_like(first_arr)
        for arr in y_series.values():
            total += arr
        
        # Determine max rate for this API
        current_max = np.max(total) if len(total) > 0 else 0.0
        if current_max > global_max_y:
            global_max_y = current_max
            
    if global_max_y <= 0:
        ylim_max = 1.0
    else:
        ylim_max = max(1.2 * global_max_y, 1e-6)

    for idx, (api_name, realtime) in enumerate(sorted(api_realtime.items())):
        ax = grid.get_ax(0, idx)
        
        y_series = prepared_data.get(api_name)
        if not y_series:
            continue
        
        df = realtime.df
        x = df['relative_time'].values
        
        # Plot stacked area
        # Plot stacked area
        plot_stacked_area(ax, x, y_series, style=style, color_map=RATE_COLOR_MAP)

        row, col = 0, idx
        bottom = row == nrows - 1
        grid.configure_ax(
            ax,
            xlabel="Time (s)",
            ylabel="Rate (KRPS)",
            title="",
            show_title=False,
            show_xlabel=bottom,
            show_ylabel=(col == 0),
            show_xticklabels=bottom,
            show_yticklabels=(col == 0),
            grid=True,
            x_data=x,
            x_type="int",
            y_type="int",
            y_step=2,
            ylim=(0.0, ylim_max),
        )
    
    # Add shared legend
    grid.add_shared_legend(position="top")
    
    # Save
    grid.save(out_path)


def _plot_single_api_latency(realtime: RealtimeData, out_path: Path, style: PlotStyle, slo_ms: float):
    """Plot latency percentiles for a single API."""
    grid = SubplotGrid(style, layout="1x1")
    ax = grid.get_ax(0, 0)
    
    df = realtime.df
    
    if df.empty:
        grid.save(out_path)
        return
    
    x = df['relative_time'].values
    
    # Plot P50
    if 'p50_latency' in df.columns:
        plot_line(ax, x, df['p50_latency'].values, label='P50', style=style, color_idx=0)
    
    # Plot P99
    if 'p99_latency' in df.columns:
        plot_line(ax, x, df['p99_latency'].values, label='P99', style=style, color_idx=1)
    
    """ # Plot P99
    if 'p99_latency' in df.columns:
        plot_line(ax, x, df['p99_latency'].values, label='P99', style=style, color_idx=2) """
    
    # Add SLO line (not red — P50 uses palette red and would hide a red threshold)
    ax.axhline(
        y=float(slo_ms), color=SLO_LINE_COLOR, linestyle='--', label='SLO',
        linewidth=style.line_width, zorder=4,
    )
    
    # Configure axis (log scale for latency)
    grid.configure_ax(ax, xlabel='Time (s)', ylabel='Latency (ms)', grid=True, log_y=True,
    x_data=x, x_type='int')
    y_lo, y_hi = _latency_log_ylim(df, float(slo_ms))
    ax.set_ylim(y_lo, y_hi)
    
    # Add legend
    grid.add_shared_legend(position="top-left")
    
    # Save
    grid.save(out_path)


def _plot_multi_api_latency(api_realtime: Dict[str, RealtimeData], out_path: Path, 
                            style: PlotStyle, slos: Optional[Dict[str, float]]):
    """Plot latency percentiles for multiple APIs in subplots."""
    n_apis = len(api_realtime)
    layout = f"row-{n_apis}"
    
    style_grid = replace(style, aspect_ratio=0.8)
    grid = SubplotGrid(style_grid, layout=layout)

    lat_dfs: List[pd.DataFrame] = []
    slo_vals: List[float] = []
    for api_name, realtime in sorted(api_realtime.items()):
        df = realtime.df
        if df.empty:
            continue
        lat_dfs.append(df)
        slo_vals.append(_lookup_slo(slos, api_name))
    slo_for_ylim = max(slo_vals) if slo_vals else 60.0
    y_lo, y_hi = _latency_log_ylim_many(lat_dfs, float(slo_for_ylim))

    for idx, (api_name, realtime) in enumerate(sorted(api_realtime.items())):
        ax = grid.get_ax(0, idx)
        
        df = realtime.df
        
        if df.empty:
            continue
        
        x = df['relative_time'].values
        
        # Plot P50
        if 'p50_latency' in df.columns:
            plot_line(ax, x, df['p50_latency'].values, label='P50', style=style, color_idx=0)

        # Plot P99
        if 'p99_latency' in df.columns:
            plot_line(ax, x, df['p99_latency'].values, label='P99', style=style, color_idx=1)
        
        """ # Plot P99
        if 'p99_latency' in df.columns:
            plot_line(ax, x, df['p99_latency'].values, label='P99', style=style, color_idx=2) """
        
        # Add SLO line (not red — P50 uses palette red)
        slo_ms = _lookup_slo(slos, api_name)
        ax.axhline(
            y=float(slo_ms), color=SLO_LINE_COLOR, linestyle='--', label='SLO',
            linewidth=style.line_width, zorder=4,
        )

        display_api = api_name.replace('_all', '') if api_name.endswith('_all') else api_name
        col = idx
        grid.configure_ax(
            ax,
            title=display_api,
            show_title=True,
            xlabel="Time (s)",
            ylabel="Latency (ms)",
            show_xlabel=False,
            show_xticklabels=False,
            show_ylabel=(col == 0),
            show_yticklabels=(col == 0),
            grid=True,
            log_y=True,
            x_data=x,
            x_type="int",
            ylim=(y_lo, y_hi),
        )
    
    # Add shared legend
    grid.add_shared_legend(position="top")
    
    # Save
    grid.save(out_path)


def generate_repeat_plots(ctx: Dict) -> List[Path]:
    """Generate rate and latency plots for a single repeat.
    
    Uses realtime-{api}.csv data from RWG output.
    
    Args:
        ctx: Context dictionary with repeat information
        
    Returns:
        List of paths to generated plot files
    """
    artifact_dir: Path = ctx['artifact_dir']
    out_dir: Path = ctx['output_dir']
    repeat_index: int = ctx['repeat_index']
    
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Load SLOs
    slos = ctx.get('slos') if isinstance(ctx.get('slos'), dict) else None
    if slos is None:
        slos = _load_global_slos()
    
    if os.environ.get('PLOT_DEBUG'):
        print(f"[plot-debug] repeat-level SLO keys: {list(slos.keys()) if slos else None}")
    
    # Load realtime data for all APIs
    try:
        repeat_data = load_repeat_data(artifact_dir)
    except Exception as e:
        if os.environ.get('PLOT_DEBUG'):
            print(f"[plot-debug] Failed to load repeat data: {e}")
        return []
    
    # Extract realtime data (ignore overall data for this plot type)
    api_realtime: Dict[str, RealtimeData] = {}
    for api_name, vals in repeat_data.items():
        if len(vals) == 3:
             overall, realtime, _ = vals
        else:
             overall, realtime = vals
        if realtime is not None:
            api_realtime[api_name] = realtime
    
    if not api_realtime:
        if os.environ.get('PLOT_DEBUG'):
            print("[plot-debug] No realtime data found for any API")
        return []
    
    # Use ACM compact half-column style
    # Use ACM quarter column style
    style = ACM_COMPACT_HALF
    style_one_api = ACM_QUARTER
    
    produced: List[Path] = []
    
    # Generate rate plot
    rate_path = out_dir / f"rate_vs_time_repeat_{repeat_index:03d}.pdf"
    if len(api_realtime) == 1:
        api_name, realtime = next(iter(api_realtime.items()))
        style_one_api.legend_size = 7
        _plot_single_api_rate(realtime, rate_path, style_one_api)
    else:
        _plot_multi_api_rate(api_realtime, rate_path, style)
    produced.append(rate_path)
    
    # Generate latency plot
    lat_path = out_dir / f"latency_vs_time_repeat_{repeat_index:03d}.pdf"
    if len(api_realtime) == 1:
        api_name, realtime = next(iter(api_realtime.items()))
        slo_ms = _lookup_slo(slos, api_name)
        style_one_api.legend_size = 7
        _plot_single_api_latency(realtime, lat_path, style_one_api, slo_ms)
    else:
        _plot_multi_api_latency(api_realtime, lat_path, style, slos)
    produced.append(lat_path)
    
    if os.environ.get('PLOT_DEBUG'):
        print(f"[plot-debug] Generated {len(produced)} plots: {[p.name for p in produced]}")
    
    return produced
