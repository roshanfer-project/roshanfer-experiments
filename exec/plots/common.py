"""Common plotting utilities - LEGACY.

This module contains legacy plotting helpers that will be deprecated.
New code should use:
  - plotting_primitives.py: Generic plotting functions
  - data_loader.py: RWG data loading
  - aggregation.py: Metric aggregation

Legacy functions kept for backward compatibility during migration.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple
import math

# Enforce mandatory canvas usage (no matplotlib fallback allowed anymore) with dual import path support.
try:
    from experiments.canvas import canvas  # type: ignore
except Exception:  # running with top-level package 'exec'
    try:
        from canvas import canvas  # type: ignore
    except Exception as e:  # pragma: no cover
        raise ImportError("Unable to import canvas. Run from repo root or add project root to PYTHONPATH.") from e

# Legacy constants - kept for backward compatibility
RATE_KEYS_ORDER = ["goodput", "slo_violation", "dropped_in", "dropped"]
LATENCY_KEYS_ORDER = ["latency_p50", "latency_p99", "latency_p99"]

def _label_fixer(label: str) -> str:
    if label == 'dropped_in':
        return 'dropped in'
    elif label == 'slo_violation':
        return 'SLO violation'
    elif label == "latency_p50":
        return "P50"
    elif label == "latency_p99":
        return "P99"
    return label


def plot_rate_stack(df, out_path: Path, time_col: str = 't_rel', value_col: str = 'value_krps') -> Path:
    """DEPRECATED: Plot stacked area for rate metrics using canvas only (KRPS expected).
    
    Use plotting_primitives.plot_stacked_area() for new code.

    DataFrame columns required: time_col, value_col, 'metric'.
    """
    if df is None or df.empty:
        return out_path
    # Limit to first 15s of relative time
    if time_col in df.columns:
        df = df[df[time_col] <= 15.0].copy()
        if df.empty:
            return out_path
    
    # Subtract "dropped_in" from "dropped" if both are present to avoid double counting
    available_metrics = df.metric.unique()
    if 'dropped' in available_metrics and 'dropped_in' in available_metrics:
        # Create pivot table to align time points
        pivot_df = df.pivot_table(index=time_col, columns='metric', values=value_col, fill_value=0)
        if 'dropped' in pivot_df.columns and 'dropped_in' in pivot_df.columns:
            # Subtract dropped_in from dropped (ensure non-negative result)
            pivot_df['dropped'] = (pivot_df['dropped'] - pivot_df['dropped_in']).clip(lower=0)
            # Convert back to long format
            df = pivot_df.reset_index().melt(id_vars=[time_col], var_name='metric', value_name=value_col)
    
    order = [k for k in RATE_KEYS_ORDER if k in df.metric.unique()]
    if not order:
        return out_path
    fig, ax = canvas.create_canvas(width_in_inches=3.33, marker_size=1, line_width=0.5, font_size=12, legend_size=12)  # type: ignore
    bottom = None
    # Build a deterministic color mapping from current prop cycle (no custom palette defined here)
    try:
        import matplotlib.pyplot as _plt  # type: ignore
        cycle_colors = [c['color'] for c in _plt.rcParams['axes.prop_cycle']]
    except Exception:
        cycle_colors = []
    color_map = {metric: cycle_colors[i % len(cycle_colors)] if cycle_colors else None for i, metric in enumerate(order)}
    for k in order:
        sub = df[df.metric == k]
        x = sub[time_col].values
        y = sub[value_col].values
        color = color_map.get(k)
        if bottom is None:
            ax.fill_between(x, 0, y, label=_label_fixer(k), alpha=0.6, color=color)
            bottom = y
        else:
            top = bottom + y
            ax.fill_between(x, bottom, top, label=_label_fixer(k), alpha=0.6, color=color)
            bottom = top
    if len(x):
        max_t = float(x.max())
        min_t = float(x.min()) if len(x) else 0.0
        ticks = [t for t in range(0, int(math.floor(max_t)) + 1, 3)]
        if not ticks:
            ticks = [0]
        ceil_max = int(math.ceil(max_t))
        if ceil_max - ticks[-1] > 0:
            ticks.append(ceil_max)
        ticks = sorted(set(ticks))
        ax.set_xticks(ticks)
        ax.set_xticklabels([str(int(t)) if abs(t - round(t)) < 1e-9 else f"{t:g}" for t in ticks])
        span = max_t - min_t
        pad = 0.03 * span if span > 0 else 0.2
        ax.set_xlim(min_t - pad if min_t > 0 else 0 - pad, max_t + pad)
        ax.xaxis.grid(True, which='major', alpha=0.3)
    # Y grid every 2k RPS (2 KRPS)
    if bottom is not None and len(bottom):
        step = 2.0  # KRPS per grid line
        max_val = float(max(bottom))
        n = int(math.ceil(max_val / step))
        yticks = [i * step for i in range(n + 1)]
        ax.set_yticks(yticks)
        ax.yaxis.grid(True, which='major', alpha=0.3)
    ax.set_xlabel('Time (s)')
    ax.set_ylabel('Rate (KRPS)')
    # Figure-level legend at top
    try:
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            # Put all legends in one line if 2 or fewer items, otherwise use 2 columns
            ncol = len(labels) if len(labels) <= 2 else 2
            # Adjust positioning based on whether legend is in one line or two
            if len(labels) <= 2:
                bbox_anchor = (0.5, 1.08)  # Closer to plot for single line
                top_adjust = 0.90  # Less top margin needed
            else:
                bbox_anchor = (0.5, 1.15)  # Original positioning for two lines
                top_adjust = 0.85  # Original top margin
            fig.legend(
                handles, labels,
                loc='upper center', bbox_to_anchor=bbox_anchor,
                ncol=ncol, frameon=True,
                fancybox=True, framealpha=0.85, edgecolor='#bbbbbb'
            )
            fig.subplots_adjust(top=top_adjust)
    except Exception:
        pass
    fig.savefig(out_path, bbox_inches='tight')
    try:  # Close if matplotlib present; canvas likely returns matplotlib objects.
        import matplotlib.pyplot as _plt  # type: ignore
        _plt.close(fig)
    except Exception:  # pragma: no cover
        pass
    return out_path


def plot_latency_lines(df, out_path: Path, time_col: str = 't_rel', value_col: str = 'value', add_slo: bool = True,
                       slo_ms: float = 60.0, log_scale: bool = True, y_min: float = 1.0, y_max: float = 500.0) -> Path:
    """DEPRECATED: Plot latency percentile lines using canvas only.
    
    Use plotting_primitives.plot_line() for new code."""
    if df is None or df.empty:
        return out_path
    # Limit to first 15s of relative time
    if time_col in df.columns:
        df = df[df[time_col] <= 15.0].copy()
        if df.empty:
            return out_path
    order = [k for k in LATENCY_KEYS_ORDER if k in df.metric.unique()]
    if not order:
        return out_path
    fig, ax = canvas.create_canvas(width_in_inches=3.33, marker_size=1, line_width=2, font_size=12, legend_size=12)  # type: ignore
    try:
        import matplotlib.pyplot as _plt  # type: ignore
        cycle_colors = [c['color'] for c in _plt.rcParams['axes.prop_cycle']]
    except Exception:
        cycle_colors = []
    color_map = {metric: cycle_colors[i % len(cycle_colors)] if cycle_colors else None for i, metric in enumerate(order)}
    for k in order:
        sub = df[df.metric == k]
        color = color_map.get(k)
        if color:
            ax.plot(sub[time_col], sub[value_col], label=_label_fixer(k), color=color)
        else:
            ax.plot(sub[time_col], sub[value_col], label=_label_fixer(k))
    if len(df[time_col]):
        max_t = float(df[time_col].max())
        min_t = float(df[time_col].min())
        ticks = [t for t in range(0, int(math.floor(max_t)) + 1, 3)]
        if not ticks:
            ticks = [0]
        ceil_max = int(math.ceil(max_t))
        if ceil_max - ticks[-1] > 0:
            ticks.append(ceil_max)
        ticks = sorted(set(ticks))
        ax.set_xticks(ticks)
        ax.set_xticklabels([str(int(t)) if abs(t - round(t)) < 1e-9 else f"{t:g}" for t in ticks])
        span = max_t - min_t
        pad = 0.03 * span if span > 0 else 0.2
        ax.set_xlim(min_t - pad if min_t > 0 else 0 - pad, max_t + pad)
        ax.xaxis.grid(True, which='major', alpha=0.3)
    if add_slo:
        ax.axhline(y=slo_ms, color='r', linestyle='--', label='SLO')
    if log_scale:
        ax.set_yscale('log')
        ax.set_ylim(y_min, y_max)
    ax.set_xlabel('Time (s)')
    ax.set_ylabel('Latency (ms)')
    ax.grid(True, alpha=0.3)
    # Figure-level legend at top
    try:
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            fig.legend(
                handles, labels,
                loc='upper center', bbox_to_anchor=(0.5,1.02),
                ncol=max(1, len(labels)), frameon=True,
                fancybox=True, framealpha=0.85, edgecolor='#bbbbbb'
            )
            fig.subplots_adjust(top=0.85)
    except Exception:
        pass
    fig.savefig(out_path, bbox_inches='tight')
    try:
        import matplotlib.pyplot as _plt  # type: ignore
        _plt.close(fig)
    except Exception:  # pragma: no cover
        pass
    return out_path
