"""Repeat-level plotting plugin for latency/ rate vs time experiments.

Exports a single function:
  generate_repeat_plots(repeat_ctx) -> List[Path]

repeat_ctx fields (dict):
  {
    'type': experiment type string,
    'experiment_name': str,
    'group_name': str,
    'repeat_index': int,
    'artifact_dir': Path (repeat directory),
    'metrics_dir': Path (repeat metrics directory),
    'output_dir': Path (destination for plots),
    'metric_files': Dict[str, dict] loaded JSON objects (stem -> JSON content)
  }

This module purposefully keeps domain-specific plotting code isolated from the
plot_runner orchestrator so adding new plot types only requires creating a new
plugin file and (optionally) registering it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple, Optional
import pandas as pd
import json
import os

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
    # Build a set to avoid duplicate absolute paths
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

try:
    # Preferred: relative import (works under both experiments.exec and exec package roots)
    from ..common import (
        RATE_KEYS_ORDER,
        LATENCY_KEYS_ORDER,
        extract_series,
        plot_rate_stack,
        plot_latency_lines,
    )
except Exception:  # pragma: no cover
    # Fallback absolute variants
    try:
        from experiments.exec.plots.common import (
            RATE_KEYS_ORDER,
            LATENCY_KEYS_ORDER,
            extract_series,
            plot_rate_stack,
            plot_latency_lines,
        )
    except Exception:
        from exec.plots.common import (  # type: ignore
            RATE_KEYS_ORDER,
            LATENCY_KEYS_ORDER,
            extract_series,
            plot_rate_stack,
            plot_latency_lines,
        )

# Explicit experiment types this plugin supports (ensure discovery attaches it when filtering by type)
SUPPORTED_TYPES = [
    'latency-and-rate-vs-time',  # per-repeat only; no cross-repeat aggregation
]

def _label_fixer(label: str) -> str:
    if label == 'dropped_in':
        return 'dropped in'
    elif label == 'slo_violation':
        return 'SLO violation'
    elif label == "latency_p50":
        return "P50"
    elif label == "latency_p95":
        return "P95"
    return label

def _prepare_rate(metric_files: Dict[str, dict]) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    ref = None
    for metric_key in RATE_KEYS_ORDER:
        # Only consider exact match or metric_key_ prefix to avoid 'dropped' capturing 'dropped_in'
        matches = [k for k in metric_files if (k == metric_key) or k.startswith(metric_key + '_')]
        if not matches:
            continue
        # Prefer a match that is NOT an aggregate (_all) if multiple are present
        non_all = [m for m in matches if not m.endswith('_all')]
        if non_all:
            preferred = sorted(non_all, key=len)[0]
        else:
            # All available matches end with _all; pick the shortest stem to avoid selecting a longer metric like dropped_in when searching for dropped
            preferred = sorted(matches, key=len)[0]
        ts, vals = extract_series(metric_files[preferred])
        if not ts:
            continue
        if metric_key == 'goodput':
            ref = ts
        frames.append(pd.DataFrame({'t': ts, 'value': vals, 'metric': metric_key}))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames)
    if ref:
        aligned: List[pd.DataFrame] = []
        for metric_key in RATE_KEYS_ORDER:
            sub = df[df.metric == metric_key]
            if sub.empty:
                continue
            mapping = dict(zip(sub.t, sub.value))
            vals = [mapping.get(t, 0.0 if metric_key != 'goodput' else 0.0) for t in ref]
            aligned.append(pd.DataFrame({'t': ref, 'value': vals, 'metric': metric_key}))
        if aligned:
            df = pd.concat(aligned)
    
    # Subtract "dropped_in" from "dropped" if both are present to avoid double counting
    available_metrics = df.metric.unique()
    if 'dropped' in available_metrics and 'dropped_in' in available_metrics:
        # Create pivot table to align time points
        pivot_df = df.pivot_table(index='t', columns='metric', values='value', fill_value=0)
        if 'dropped' in pivot_df.columns and 'dropped_in' in pivot_df.columns:
            # Subtract dropped_in from dropped (ensure non-negative result)
            pivot_df['dropped'] = (pivot_df['dropped'] - pivot_df['dropped_in']).clip(lower=0)
            # Convert back to long format
            df = pivot_df.reset_index().melt(id_vars=['t'], var_name='metric', value_name='value')
    
    t0 = df.t.min()
    df['t_rel'] = df.t - t0
    df['value_krps'] = df.value / 1000.0
    return df


def _prepare_latency(metric_files: Dict[str, dict]) -> pd.DataFrame:
    frames: List[pd.DataFrame] = []
    for metric_key in LATENCY_KEYS_ORDER:
        matches = [k for k in metric_files if (k == metric_key) or k.startswith(metric_key + '_')]
        if not matches:
            continue
        ts, vals = extract_series(metric_files[matches[0]])
        if not ts:
            continue
        frames.append(pd.DataFrame({'t': ts, 'value': vals, 'metric': metric_key}))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames)
    t0 = df.t.min()
    df['t_rel'] = df.t - t0
    return df


def _plot_rate(df: pd.DataFrame, out: Path) -> Path:
    return plot_rate_stack(df.rename(columns={'t_rel': 't_rel', 'value_krps': 'value_krps'}), out)

def _lookup_slo(slos: Optional[Dict[str, float]], api: str) -> Optional[float]:
    """Return SLO ms for api using flexible key matching.

    Tries variants: exact, hyphen/underscore swapped, lowercase, stripped _all.
    """
    if not slos or not api:
        return None
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
            return slos[key]
    return None


def _plot_latency(df: pd.DataFrame, out: Path, slos: Optional[Dict[str, float]] = None, api: str = 'default') -> Path:
    slo_val = _lookup_slo(slos, api) or 60.0
    return plot_latency_lines(df, out, time_col='t_rel', value_col='value', slo_ms=slo_val)


def _split_metric_files_by_api(metric_files: Dict[str, dict]) -> Dict[str, Dict[str, dict]]:
    """Detect per-API metric JSONs by filename stem.

    Naming heuristics:
      <metric_prefix>            -> single API experiment (no split)
      <metric_prefix>_<api-name> -> multi-API; everything after first '_' is api identifier
    """
    per_api: Dict[str, Dict[str, dict]] = {}
    # First pass detect if any key contains underscore after known prefix
    multi = False
    for stem in metric_files.keys():
        for prefix in RATE_KEYS_ORDER + LATENCY_KEYS_ORDER:
            if stem == prefix:
                continue
            if stem.startswith(prefix + '_'):
                multi = True
                break
        if multi:
            break
    if not multi:
        # Treat as single implicit API named 'default'
        return {'default': metric_files}
    for stem, content in metric_files.items():
        matched = False
        for prefix in RATE_KEYS_ORDER + LATENCY_KEYS_ORDER:
            if stem.startswith(prefix + '_'):
                api = stem[len(prefix) + 1:]
                per_api.setdefault(api, {})[stem] = content
                matched = True
                break
        if not matched:
            # Unknown naming; include under 'default'
            per_api.setdefault('default', {})[stem] = content
    return per_api


def _prepare_per_api(metric_files: Dict[str, dict]) -> Dict[str, Tuple[pd.DataFrame, pd.DataFrame]]:
    out: Dict[str, Tuple[pd.DataFrame, pd.DataFrame]] = {}
    for api, files in _split_metric_files_by_api(metric_files).items():
        out[api] = (_prepare_rate(files), _prepare_latency(files))
    return out


def _plot_multi_api(per_api: Dict[str, Tuple[pd.DataFrame, pd.DataFrame]], out_dir: Path, repeat_index: int, slos: Dict[str, float] | None = None) -> List[Path]:
    # Robust import of canvas (mirror logic in common.py) so running from different CWDs works.
    try:
        from ..common import canvas  # type: ignore
    except Exception:  # pragma: no cover
        try:
            from experiments.canvas import canvas  # type: ignore
        except Exception:
            from canvas import canvas  # type: ignore
    apis = list(per_api.keys())
    produced: List[Path] = []
    # Rate figure
    fig_r, axes_r = canvas.create_canvas(
        nrows=1, ncols=len(apis), width_in_inches=3.33, aspect_ratio=0.66,
        font_size=16, legend_size=14, line_width=1.5, marker_size=4
    )
    if len(apis) == 1:
        axes_r = [axes_r]
    # Build global color map for rate metrics
    try:
        import matplotlib.pyplot as _plt  # type: ignore
        cycle_colors = [c['color'] for c in _plt.rcParams['axes.prop_cycle']]
    except Exception:
        cycle_colors = []
    global_rate_color_map = {m: cycle_colors[i % len(cycle_colors)] if cycle_colors else None for i, m in enumerate(RATE_KEYS_ORDER)}
    rate_handles_labels = []
    for idx, (ax, api) in enumerate(zip(axes_r, apis)):
        rate_df, _ = per_api[api]
        # Crop to 15s window
        if 't_rel' in rate_df.columns:
            rate_df = rate_df[rate_df.t_rel <= 15.0].copy()
        if rate_df is None or rate_df.empty:
            continue
        import os
        if os.environ.get('PLOT_DEBUG'):
            print(f"[plot-debug] API {api} rate metrics present: {sorted(rate_df.metric.unique())}")
        present_metrics = list(rate_df.metric.unique())
        order = [k for k in RATE_KEYS_ORDER if k in present_metrics]
        bottom = None
        for i, m in enumerate(order):
            sdf = rate_df[rate_df.metric == m]
            x = sdf.t_rel.values
            y = sdf.value_krps.values
            color = global_rate_color_map.get(m)
            print(f"[plot-debug] API {api} rate metric '{m}' y = {max(y)}")
            if bottom is None:
                h = ax.fill_between(x, 0, y, label=_label_fixer(m), alpha=0.6, color=color)
                bottom = y
            else:
                top = bottom + y
                h = ax.fill_between(x, bottom, top, label=_label_fixer(m), alpha=0.6, color=color)
                bottom = top
        h, l = ax.get_legend_handles_labels()
        rate_handles_labels.extend([(hh, ll) for hh, ll in zip(h, l) if ll not in [lbl for _, lbl in rate_handles_labels]])
        if bottom is not None and len(bottom):
            import math
            max_t = float(x.max()) if len(x) else 0.0
            ticks = [t for t in range(0, int(math.floor(max_t)) + 1, 3)]
            if not ticks:
                ticks = [0]
            ceil_max = int(math.ceil(max_t))
            if ceil_max - ticks[-1] > 0:
                ticks.append(ceil_max)
            ticks = sorted(set(ticks))
            ax.set_xticks(ticks)
            ax.set_xticklabels([str(int(t)) if abs(t - round(t)) < 1e-9 else f"{t:g}" for t in ticks])
            span = max_t - 0.0
            pad = 0.03 * span if span > 0 else 0.2
            ax.set_xlim(0 - pad, max_t + pad)
            step = 2.0
            max_val = float(max(bottom))
            n = int(math.ceil(max_val / step))
            yticks = [i * step for i in range(n + 1)]
            ax.set_yticks(yticks)
            ax.yaxis.grid(True, which='major', alpha=0.3)
            # Only show y-tick labels on the leftmost subplot
            if idx != 0:
                ax.set_yticklabels([])
        display_api = api[:-4] if api.endswith('_all') else api
        ax.set_title(display_api)
        ax.set_xlabel('Time (s)')
        if ax == axes_r[0]:
            ax.set_ylabel('Rate (KRPS)')
    # Figure-level legend for rate figure
    try:
        import os
        labels = [lbl for _, lbl in rate_handles_labels]
        handles = [hdl for hdl, _ in rate_handles_labels]
        if os.environ.get('PLOT_DEBUG'):
            print(f"[plot-debug] rate merged legend labels: {labels}")
        if handles:
            # Put all legends in one line if 2 or fewer items, otherwise use all items
            ncol = len(labels) if len(labels) <= 2 else max(1, len(labels))
            # Adjust positioning based on whether legend is in one line or multiple
            if len(labels) <= 2:
                bbox_anchor = (0.5, 1.05)  # Closer to plot for single line
                top_adjust = 0.85  # Less top margin needed
            else:
                bbox_anchor = (0.5, 1.12)  # Original positioning for multiple lines
                top_adjust = 0.80  # Original top margin
            leg = fig_r.legend(
                handles, labels,
                loc='upper center', bbox_to_anchor=bbox_anchor,
                ncol=ncol, frameon=True, fancybox=True,
                framealpha=0.9, edgecolor='#cccccc'
            )
            fig_r.subplots_adjust(top=top_adjust)
        else:
            if axes_r:
                axes_r[0].legend(loc='upper left', frameon=True, fancybox=True, framealpha=0.9)
    except Exception as e:
        if os.environ.get('PLOT_DEBUG'):
            print(f"[plot-debug] rate legend exception: {e}")
    rate_path = out_dir / f"rate_vs_time_repeat_{repeat_index:03d}.pdf"
    fig_r.savefig(rate_path, bbox_inches='tight')
    try:
        import matplotlib.pyplot as _plt  # type: ignore
        _plt.close(fig_r)
    except Exception:
        pass
    produced.append(rate_path)
    # Latency figure
    fig_l, axes_l = canvas.create_canvas(
        nrows=1, ncols=len(apis), width_in_inches=3.33, aspect_ratio=0.66,
        font_size=16, legend_size=14, line_width=2, marker_size=4
    )
    if len(apis) == 1:
        axes_l = [axes_l]
    # Build global color map for latency metrics
    global_latency_color_map = {m: cycle_colors[i % len(cycle_colors)] if cycle_colors else None for i, m in enumerate(LATENCY_KEYS_ORDER)}
    latency_handles_labels = []
    for idx, (ax, api) in enumerate(zip(axes_l, apis)):
        _, lat_df = per_api[api]
        if 't_rel' in lat_df.columns:
            lat_df = lat_df[lat_df.t_rel <= 15.0].copy()
        if lat_df is None or lat_df.empty:
            continue
        import os
        if os.environ.get('PLOT_DEBUG'):
            print(f"[plot-debug] API {api} latency metrics present: {sorted(lat_df.metric.unique())}")
        present_metrics = list(lat_df.metric.unique())
        order = [k for k in LATENCY_KEYS_ORDER if k in present_metrics]
        for m in order:
            sdf = lat_df[lat_df.metric == m]
            ax.plot(sdf.t_rel, sdf.value, label=_label_fixer(m), color=global_latency_color_map.get(m), linewidth=2)
        h, l = ax.get_legend_handles_labels()
        latency_handles_labels.extend([(hh, ll) for hh, ll in zip(h, l) if ll not in [lbl for _, lbl in latency_handles_labels]])
        if len(lat_df.t_rel):
            import math
            max_t = float(lat_df.t_rel.max())
            ticks = [t for t in range(0, int(math.floor(max_t)) + 1, 3)]
            if not ticks:
                ticks = [0]
            ceil_max = int(math.ceil(max_t))
            if ceil_max - ticks[-1] > 0:
                ticks.append(ceil_max)
            ticks = sorted(set(ticks))
            ax.set_xticks(ticks)
            ax.set_xticklabels([str(int(t)) if abs(t - round(t)) < 1e-9 else f"{t:g}" for t in ticks])
            span = max_t - 0.0
            pad = 0.03 * span if span > 0 else 0.2
            ax.set_xlim(0 - pad, max_t + pad)
            ax.xaxis.grid(True, which='major', alpha=0.3)
            # Only show y-tick labels on the leftmost subplot
            if idx != 0:
                ax.set_yticklabels([])
        display_api = api[:-4] if api.endswith('_all') else api
        base_api = display_api
        slo_val = _lookup_slo(slos, base_api) or 60.0
        if os.environ.get('PLOT_DEBUG'):
            print(f"[plot-debug] SLO lookup api='{api}' base='{base_api}' slo={slo_val} keys={list(slos.keys()) if slos else None}")
        ax.axhline(y=slo_val, color='r', linestyle='--', label='SLO')
        ax.set_yscale('log')
        ax.set_ylim(1, 500)
        ax.set_title(display_api)
        ax.set_xlabel('Time (s)')
        if idx != 0:
            ax.set_yticklabels([])
        if ax == axes_l[0]:
            ax.set_ylabel('Latency (ms)')
        ax.grid(True, alpha=0.3)
    # Figure-level legend for latency figure
    try:
        import os
        import matplotlib.lines as mlines
        labels = [lbl for _, lbl in latency_handles_labels]
        handles = [hdl for hdl, _ in latency_handles_labels]
        # Add SLO to legend if not present
        if 'SLO' not in labels:
            slo_handle = mlines.Line2D([], [], color='r', linestyle='--', label='SLO')
            handles.append(slo_handle)
            labels.append('SLO')
        if os.environ.get('PLOT_DEBUG'):
            print(f"[plot-debug] latency merged legend labels: {labels}")
        if handles:
            fig_l.legend(
                handles, labels,
                loc='upper center', bbox_to_anchor=(0.5,1.12),
                ncol=max(1,len(labels)), frameon=True, fancybox=True,
                framealpha=0.9, edgecolor='#cccccc'
            )
            fig_l.subplots_adjust(top=0.80)
        else:
            if axes_l:
                axes_l[0].legend(loc='upper left', frameon=True, fancybox=True, framealpha=0.9)
    except Exception as e:
        if os.environ.get('PLOT_DEBUG'):
            print(f"[plot-debug] latency legend exception: {e}")
    lat_path = out_dir / f"latency_vs_time_repeat_{repeat_index:03d}.pdf"
    fig_l.savefig(lat_path, bbox_inches='tight')
    try:
        import matplotlib.pyplot as _plt  # type: ignore
        _plt.close(fig_l)
    except Exception:
        pass
    produced.append(lat_path)
    return produced


def generate_repeat_plots(ctx: Dict) -> List[Path]:
    metric_files: Dict[str, dict] = ctx['metric_files']
    out_dir: Path = ctx['output_dir']
    out_dir.mkdir(parents=True, exist_ok=True)
    per_api = _prepare_per_api(metric_files)
    # SLOs can be supplied in context (executor attaches config extras) under 'slos'
    slos = ctx.get('slos') if isinstance(ctx.get('slos'), dict) else None
    if slos is None:
        slos = _load_global_slos()
    if os.environ.get('PLOT_DEBUG'):
        print(f"[plot-debug] repeat-level SLO keys: {list(slos.keys()) if slos else None}")
    # If single API (named 'default' or only one key) fall back to existing single-file generation with SLO
    if len(per_api) == 1:
        api_name, api_df = next(iter(per_api.items()))
        rate_df, lat_df = api_df
        produced: List[Path] = []
        produced.append(_plot_rate(rate_df, out_dir / f"rate_vs_time_repeat_{ctx['repeat_index']:03d}.pdf"))
        produced.append(_plot_latency(lat_df, out_dir / f"latency_vs_time_repeat_{ctx['repeat_index']:03d}.pdf", slos=slos, api=api_name))
        return produced
    # Multi-API (up to 3) combined multi-subplot figures
    return _plot_multi_api(per_api, out_dir, ctx['repeat_index'], slos)
