"""Experiment-level aggregate plots for latency-and-goodput-vs-load.

Produces exactly two figures per experiment (spanning all loads):
  * latency_vs_load.pdf  (p50 & p95 per API with error bars across repeats)
  * goodput_vs_load.pdf  (goodput per API with error bars across repeats)

Supports up to 3 APIs. X-axis = load * 10 (per instructions).
Error bars: standard deviation across repeats (0 if single repeat).
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Optional
import json
from pathlib import Path as _Path
import statistics
import math

SUPPORTED_TYPES = ['latency-and-goodput-vs-load']

try:
    from ..common import extract_series
except Exception:  # pragma: no cover
    from experiments.exec.plots.common import extract_series  # type: ignore

LAT_KEYS = ('latency_p50','latency_p95')


def _mean_std(values: List[float]):
    """Return (mean, std) ignoring NaN/None.

    Outcomes:
      * No valid values -> (None, None)
      * One valid value -> (mean, 0.0)
      * >=2 valid values -> (mean, population std)
    """
    if not values:
        return None, None
    filtered = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not filtered:
        return None, None
    m = sum(filtered) / len(filtered)
    if len(filtered) < 2:
        return m, 0.0
    try:
        return m, statistics.pstdev(filtered)
    except Exception:
        return m, 0.0


def _windowed_mean(ts: List[float], vals: List[float], ignore_first: float = 5.0, last_window: float = 10.0) -> float | None:
    """Compute mean over filtered window: drop first ignore_first seconds (relative) then keep only
    samples in the last last_window seconds of the series.

    ts may be absolute epoch timestamps; we compute relative by subtracting min(ts).
    If no samples remain after filtering return None.
    """
    if not ts or not vals:
        return None
    if len(ts) != len(vals):
        return None
    t0 = min(ts)
    rel = [t - t0 for t in ts]
    max_rel = max(rel)
    # Determine lower bound after applying both filters
    lower_bound = max(ignore_first, max_rel - last_window)
    filtered = [v for tr, v in zip(rel, vals) if tr >= lower_bound]
    if not filtered:
        return None
    return sum(filtered) / len(filtered)


def generate_experiment_plots(ctx: Dict) -> List[Path]:  # type: ignore
    if ctx.get('type') not in SUPPORTED_TYPES:
        return []
    apis: List[str] = ctx.get('apis') or []
    unit_entries = ctx['unit_entries']  # list of {run_unit_name, repeat_metric_files, load_value, ...}
    out_dir: Path = ctx['output_dir']
    out_dir.mkdir(parents=True, exist_ok=True)
    slos: Dict[str, float] | None = ctx.get('slos')
    system_name: str = ctx.get('system') or ctx.get('system_name') or 'system'
    if slos is None:
        print(f"[generate_experiment_plots] No SLOs defined for context")
        # Fallback: search for config files upward similar to repeat plugin
        def _load_global_slos() -> Optional[Dict[str, float]]:
            candidates_rel = [
                'experiments/exec/config.json',
                'experiments/exec/config.sample.json',
                'config.json',
                'config.sample.json',
            ]
            this_dir = _Path(__file__).resolve().parent
            search_roots = [this_dir] + list(this_dir.parents)
            tried = []
            import os
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
                            print(f"[plot-debug] aggregate loaded SLOs from {cpath}: {list(data['slos'].keys())}")
                        return data['slos']  # type: ignore
            if os.environ.get('PLOT_DEBUG'):
                print('[plot-debug] aggregate no SLO config found')
            return None
        slos = _load_global_slos()
    produced: List[Path] = []
    if not unit_entries:
        return produced
    # Sort units by load_value
    unit_entries = [u for u in unit_entries if u.get('load_value') is not None]
    unit_entries.sort(key=lambda u: u['load_value'])
    if not unit_entries:
        return produced
    # Build per API series: load_x -> values list per repeat (mean over time range of each repeat)
    latency_data = {api: {k: [] for k in LAT_KEYS} for api in apis}
    raw_latency_values: List[float] = []  # collect all windowed latency samples for dynamic y-limit
    goodput_data = {api: [] for api in apis}
    loads = []  # in KRPS
    for u in unit_entries:
        load = u['load_value']
        # Convert offered load: original RPS = load * 10; represent in KRPS
        x_val = (load * 10) / 1000.0
        loads.append(x_val)
        repeat_metric_files = u['repeat_metric_files']
        # For each api collect repeat stats
        for api in apis:
            # Goodput
            gp_repeat_vals = []
            for rf in repeat_metric_files:
                gp_json = rf.get(f'goodput_{api}_all') or rf.get(f'goodput_{api}')
                if gp_json:
                    ts, vals = extract_series(gp_json)
                    wm = _windowed_mean(ts, vals)
                    if wm is not None:
                        gp_repeat_vals.append(wm)
            goodput_data[api].append(gp_repeat_vals)
            # Latencies
            for lk in LAT_KEYS:
                lat_repeat_vals = []
                for rf in repeat_metric_files:
                    lat_json = rf.get(f'{lk}_{api}_all') or rf.get(f'{lk}_{api}')
                    if lat_json:
                        ts, vals = extract_series(lat_json)
                        wm = _windowed_mean(ts, vals)
                        if wm is not None:
                            lat_repeat_vals.append(wm)
                            if lk == 'latency_p95':  # only use p95 samples for scaling
                                raw_latency_values.append(wm)
                latency_data[api][lk].append(lat_repeat_vals)
    # Now compute mean/std arrays aligned with loads
    try:
        from experiments.canvas import canvas  # type: ignore
    except Exception:
        from canvas import canvas  # type: ignore
    import matplotlib.pyplot as plt  # type: ignore
    # Latency figure
    # Predefine distinct colors for latency percentiles (consistent across APIs)
    try:
        lat_colors = {
            'latency_p50': canvas.color_list[0],
            'latency_p95': canvas.color_list[1],
        }
    except Exception:
        lat_colors = {
            'latency_p50': '#1f77b4',
            'latency_p95': '#ff7f0e',
        }
    fig_l, axes_l = canvas.create_canvas(
        nrows=1, ncols=len(apis), width_in_inches=3.33, aspect_ratio=0.66,
        line_width=2, font_size=16, legend_size=14, marker_size=5
    )
    # Normalize axes_l to a simple list of Axes
    try:
        from matplotlib.axes import Axes as _Axes  # type: ignore
    except Exception:
        _Axes = object  # fallback
    if isinstance(axes_l, _Axes):
        axes_l = [axes_l]
    else:
        try:
            # axes_l might be a numpy array; flatten and convert
            axes_l = list(getattr(axes_l, 'ravel')().tolist())  # type: ignore
        except Exception:
            axes_l = list(axes_l) if not isinstance(axes_l, list) else axes_l
    from matplotlib.ticker import MultipleLocator  # type: ignore
    global_latency_values = []  # collect all latency means across APIs for dynamic y-limit
    api_latency_means_map = {}  # store means per api to re-apply after y-max decision if needed
    # Flexible SLO lookup helper (local)
    def _lookup_slo(smap: Optional[Dict[str, float]], name: str) -> Optional[float]:
        if not smap:
            return None
        base = name[:-4] if name.endswith('_all') else name
        cand = [base, base.replace('-', '_'), base.replace('_','-')]
        cand.extend([c.lower() for c in cand])
        seen = []
        ordered = []
        for c in cand:
            if c not in seen:
                seen.append(c)
                ordered.append(c)
        for key in ordered:
            if key in smap:
                return smap[key]
        return None
    for ax, api in zip(axes_l, apis):
        display_api = api[:-4] if api.endswith('_all') else api
        # Only plot latency_p95 per request (p50 removed)
        for lk, marker, style in (('latency_p95','^','--'),):
            means = []
            stds = []
            for rep_lists in latency_data[api][lk]:
                m, s = _mean_std(rep_lists)
                if m is None:
                    means.append(float('nan'))
                    stds.append(0.0)
                else:
                    means.append(m)
                    stds.append(s)
            # plot only if at least one finite mean
            if any(v is not None and not (isinstance(v,float) and math.isnan(v)) for v in means):
                color = lat_colors.get(lk)
                # Use explicit marker styling & caps on error bars
                # Use system name instead of percentile label for p95 line
                ax.errorbar(loads, means, yerr=stds, fmt=style, marker=marker, label=system_name,
                            linewidth=2.8, color=color, markersize=6.5, markerfacecolor=color,
                            markeredgecolor='black', markeredgewidth=0.6, capsize=4, elinewidth=1.4)
            # accumulate finite means
            for v in means:
                if isinstance(v, float) and not math.isnan(v):
                    global_latency_values.append(v)
            api_latency_means_map[api] = means
        slo_val = _lookup_slo(slos, display_api)
        if slo_val is not None:
            ax.axhline(y=slo_val, color='r', linestyle='--', label='SLO')
        ax.set_xlabel('Offered Load (KRPS)')
        if ax == axes_l[0]:
            ax.set_ylabel('P95 Latency (ms)')
        ax.set_yscale('log')
        ax.set_title(display_api)
        # X-axis grid every 2 KRPS
        ax.xaxis.set_major_locator(MultipleLocator(2))
        # Guarantee first and last load appear as ticks if locator missed last
        try:
            cur_ticks = list(ax.get_xticks())
            if loads:
                first_x, last_x = loads[0], loads[-1]
                changed = False
                if first_x not in cur_ticks:
                    cur_ticks.insert(0, first_x)
                    changed = True
                if last_x not in cur_ticks:
                    cur_ticks.append(last_x)
                    changed = True
                if changed:
                    cur_ticks = sorted(set(cur_ticks))
                    ax.set_xticks(cur_ticks)
        except Exception:
            pass
        ax.grid(True, which='major', axis='both', alpha=0.3)
    # legend merged at figure level
        # Add guard space
        if loads:
            span = loads[-1] - loads[0]
            pad = 0.03 * span if span > 0 else 0.05
            ax.set_xlim(loads[0] - pad, loads[-1] + pad)
    # Dynamic latency y-max: 5x global max (fallback 500 if no data)
    # Prefer raw_latency_values (windowed p95 samples) if means were NaN
    if not global_latency_values and raw_latency_values:
        global_latency_values = raw_latency_values
    if global_latency_values:
        dyn_y_max = max(global_latency_values) * 5.0
        # Ensure at least slightly above max to avoid clipping
        dyn_y_max *= 1.05
        # Clamp lower bound of upper limit
        if dyn_y_max < 10:
            dyn_y_max = 10
    else:
        dyn_y_max = 500
    for ax in axes_l:
        try:
            ax.set_ylim(1, dyn_y_max)
        except Exception:
            continue
    try:
        handles, labels = [], []
        for ax in axes_l:
            h,l = ax.get_legend_handles_labels()
            for hh,ll in zip(h,l):
                if ll not in labels:
                    handles.append(hh); labels.append(ll)
        if handles:
            if len(apis) > 1:
                fig_l.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5,1.12),
                              ncol=max(1,len(labels)), frameon=True, fancybox=True,
                              framealpha=0.85, edgecolor='#bbbbbb')
                fig_l.subplots_adjust(top=0.80)
            else:
                fig_l.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5,1.12),
                              ncol=max(1,len(labels)), frameon=True, fancybox=True,
                              framealpha=0.85, edgecolor='#bbbbbb')
                fig_l.subplots_adjust(top=0.80)
    except Exception:
        pass
    lat_path = out_dir / 'latency_vs_load.pdf'
    fig_l.savefig(lat_path, bbox_inches='tight')
    plt.close(fig_l)
    produced.append(lat_path)
    # Goodput figure
    fig_g, axes_g = canvas.create_canvas(
        nrows=1, ncols=len(apis), width_in_inches=3.33, aspect_ratio=0.66,
        line_width=2, font_size=16, legend_size=14, marker_size=5
    )
    # Normalize axes_g similarly
    if isinstance(axes_g, _Axes):
        axes_g = [axes_g]
    else:
        try:
            axes_g = list(getattr(axes_g, 'ravel')().tolist())  # type: ignore
        except Exception:
            axes_g = list(axes_g) if not isinstance(axes_g, list) else axes_g
    for ax, api in zip(axes_g, apis):
        display_api = api[:-4] if api.endswith('_all') else api
        means = []
        stds = []
        for rep_lists in goodput_data[api]:
            m, s = _mean_std(rep_lists)
            if m is None:
                means.append(float('nan'))
                stds.append(0.0)
            else:
                means.append(m/1000.0)  # KRPS
                stds.append((s or 0)/1000.0)
        if any(v is not None and not (isinstance(v,float) and math.isnan(v)) for v in means):
            try:
                gp_color = canvas.color_list[0]
            except Exception:
                gp_color = '#1f77b4'
            # Use system name instead of generic 'goodput'
            ax.errorbar(
                loads, means, yerr=stds, fmt='-o', color=gp_color, label=system_name, linewidth=2.4,
                markersize=6.0, markerfacecolor=gp_color, markeredgecolor='black', markeredgewidth=0.6,
                capsize=4, elinewidth=1.3
            )
        ax.set_xlabel('Offered Load (KRPS)')
        if ax == axes_g[0]:
            ax.set_ylabel('Goodput (KRPS)')
        ax.set_title(display_api)
        ax.xaxis.set_major_locator(MultipleLocator(2))
        try:
            cur_ticks = list(ax.get_xticks())
            if loads:
                first_x, last_x = loads[0], loads[-1]
                changed = False
                if first_x not in cur_ticks:
                    cur_ticks.insert(0, first_x)
                    changed = True
                if last_x not in cur_ticks:
                    cur_ticks.append(last_x)
                    changed = True
                if changed:
                    cur_ticks = sorted(set(cur_ticks))
                    ax.set_xticks(cur_ticks)
        except Exception:
            pass
        # Y grid every 1 KRPS
        finite_means = [v for v in means if isinstance(v, float) and not math.isnan(v)]
        if finite_means:
            max_val = max(finite_means)
            step = 1.0
            import math as _m
            n = int(_m.ceil(max_val / step))
            yticks = [i * step for i in range(n + 1)]
            if yticks:
                ax.set_yticks(yticks)
        ax.grid(True, which='major', axis='both', alpha=0.3)
    # legend merged at figure level
        if loads:
            span = loads[-1] - loads[0]
            pad = 0.03 * span if span > 0 else 0.05
            ax.set_xlim(loads[0] - pad, loads[-1] + pad)
    try:
        handles, labels = [], []
        for ax in axes_g:
            h,l = ax.get_legend_handles_labels()
            for hh,ll in zip(h,l):
                if ll not in labels:
                    handles.append(hh); labels.append(ll)
        if handles:
            if len(apis) > 1:
                fig_g.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5,1.12),
                              ncol=max(1,len(labels)), frameon=True, fancybox=True,
                              framealpha=0.85, edgecolor='#bbbbbb')
                fig_g.subplots_adjust(top=0.80)
            else:
                fig_g.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5,1.12),
                              ncol=max(1,len(labels)), frameon=True, fancybox=True,
                              framealpha=0.85, edgecolor='#bbbbbb')
                fig_g.subplots_adjust(top=0.80)
    except Exception:
        pass
    goodput_path = out_dir / 'goodput_vs_load.pdf'
    fig_g.savefig(goodput_path, bbox_inches='tight')
    plt.close(fig_g)
    produced.append(goodput_path)
    # Prune any per-load subdirectories (created by earlier steps) under experiment directory
    try:
        import shutil
        for child in out_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
    except Exception:
        pass
    return produced
