"""Unit-level aggregation plugin for latency-and-goodput-vs-load experiments.

Generates per run-unit (load point) PDF figures combining repeats:
  * latency_vs_load_unit.pdf : p50 & p95 (per API) with error bars across repeats
  * goodput_vs_load_unit.pdf : goodput (per API) with error bars across repeats

Assumptions:
  * plot_runner extended to call aggregate plugins after per-repeat processing.
  * Context provided via aggregate key: {
        'type': str,
        'experiment_name': str,
        'run_unit_name': str,
        'group_name': str,
        'artifact_dirs': [Path per repeat],
        'repeat_metric_files': [ { stem->json } per repeat ],
        'output_dir': Path (unit level),
        'loads': int load value (base load),
        'slos': { api: ms }
    }

Supported up to 3 apis (each subplot). X-axis is load*10 (derivation delegated to caller; here we just use provided x value).
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List
import math
import statistics

try:
    from ..common import LATENCY_KEYS_ORDER, extract_series
except Exception:  # pragma: no cover
    from experiments.exec.plots.common import LATENCY_KEYS_ORDER, extract_series  # type: ignore

SUPPORTED_TYPES = ['latency-and-goodput-vs-load']

# Public hook name for discovery (aggregate phase will look for this attribute)
def generate_unit_plots(ctx: Dict) -> List[Path]:  # type: ignore
    apis: List[str] = ctx.get('apis') or []
    system_name: str = ctx.get('system') or ctx.get('system_name') or 'system'
    repeat_metric_files: List[Dict[str, dict]] = ctx['repeat_metric_files']
    slos: Dict[str, float] | None = ctx.get('slos')
    if slos is None:
        print(f"[latency_goodput_vs_load_unit] No SLOs defined for context")
    load_val = ctx.get('load_value')
    x_value = load_val * 10 if load_val is not None else None
    out_dir: Path = ctx['output_dir']
    out_dir.mkdir(parents=True, exist_ok=True)
    produced: List[Path] = []
    if x_value is None or not apis:
        return produced
    # Data structures: per api -> metric -> list of (value at representative timestamp) aggregated (mean over last second of each repeat)
    latency_summary = {api: {k: [] for k in ('latency_p50','latency_p95')} for api in apis}
    goodput_summary = {api: [] for api in apis}
    for repeat_files in repeat_metric_files:
        for api in apis:
            for metric in ('latency_p50','latency_p95'):
                stem = f"{metric}_{api}"
                data = repeat_files.get(stem) or repeat_files.get(metric)  # single api fallback
                if not data:
                    continue
                ts, vals = extract_series(data)
                if not vals:
                    continue
                latency_summary[api][metric].append(sum(vals)/len(vals))  # simple mean over window
            stem_g = f"goodput_{api}"
            data_g = repeat_files.get(stem_g) or repeat_files.get('goodput')
            if data_g:
                ts, vals = extract_series(data_g)
                if vals:
                    goodput_summary[api].append(sum(vals)/len(vals))
    # Helper to calc mean and std (std=0 if <2 samples)
    def mean_std(values: List[float]):
        if not values:
            return None, None
        m = sum(values)/len(values)
        if len(values) < 2:
            return m, 0.0
        return m, statistics.pstdev(values)
    # Plot latency figure (subplots per api)
    try:
        from experiments.canvas import canvas  # type: ignore
    except Exception:
        from canvas import canvas  # type: ignore
    import matplotlib.pyplot as plt  # type: ignore
    fig_l, axes_l = canvas.create_canvas(nrows=1, ncols=len(apis), width_in_inches=3.33*len(apis), aspect_ratio=0.66, line_width=3.2)
    if len(apis) == 1:
        axes_l = [axes_l]
    for ax, api in zip(axes_l, apis):
        p50m, p50s = mean_std(latency_summary[api]['latency_p50'])
        p95m, p95s = mean_std(latency_summary[api]['latency_p95'])
        xs = [x_value]
        if p50m is not None:
            ax.errorbar(xs, [p50m], yerr=[p50s], fmt='o-', label='p50', linewidth=3.2)
        if p95m is not None:
            # Use system name in legend instead of generic p95 label
            ax.errorbar(xs, [p95m], yerr=[p95s], fmt='s--', label=system_name, linewidth=3.2)
        slo_val = slos.get(api) if slos else None
        if slo_val:
            ax.axhline(y=slo_val, color='r', linestyle='--', label='SLO')
        else:
            print(f"[latency_goodput_vs_load_unit] No SLO defined for API '{api}', slos: {slos}")
        ax.set_xlabel('Offered Load (RPS)')
        if ax == axes_l[0]:
            ax.set_ylabel('P95 Latency (ms)')
        ax.set_xticks([x_value])
        ax.set_title(api)
        ax.set_yscale('log')
        ax.set_ylim(1, 500)
    # legend deferred to figure level
        ax.grid(True, alpha=0.3)
    # figure-level legend (latency)
    try:
        handles, labels = [], []
        for ax in axes_l:
            h,l = ax.get_legend_handles_labels()
            for hh,ll in zip(h,l):
                if ll not in labels:
                    handles.append(hh); labels.append(ll)
        if handles:
            if len(apis) > 1:
                # Multi-API: lift legend above titles
                fig_l.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5,1.12),
                              ncol=max(1,len(labels)), frameon=True, fancybox=True,
                              framealpha=0.85, edgecolor='#bbbbbb')
                fig_l.subplots_adjust(top=0.80)
            else:
                # Single API: raise legend further to avoid title overlap
                fig_l.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5,1.12),
                              ncol=max(1,len(labels)), frameon=True, fancybox=True,
                              framealpha=0.85, edgecolor='#bbbbbb')
                fig_l.subplots_adjust(top=0.80)
    except Exception:
        pass
    lat_path = out_dir / 'latency_vs_load_unit.pdf'
    fig_l.savefig(lat_path, bbox_inches='tight')
    plt.close(fig_l)
    produced.append(lat_path)
    # Goodput figure
    fig_g, axes_g = canvas.create_canvas(nrows=1, ncols=len(apis), width_in_inches=3.33*len(apis), aspect_ratio=0.66, line_width=3.2)
    if len(apis) == 1:
        axes_g = [axes_g]
    for ax, api in zip(axes_g, apis):
        gm, gs = mean_std(goodput_summary[api])
        if gm is None:
            continue
        xs = [x_value]
        ax.errorbar(xs, [gm/1000.0], yerr=[(gs or 0)/1000.0], fmt='o-', label=system_name, linewidth=3.2)
        ax.set_xlabel('Offered Load (RPS)')
        if ax == axes_g[0]:
            ax.set_ylabel('Goodput (KRPS)')
        ax.set_xticks([x_value])
        ax.set_title(api)
    # legend deferred to figure level
        ax.grid(True, alpha=0.3)
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
    goodput_path = out_dir / 'goodput_vs_load_unit.pdf'
    fig_g.savefig(goodput_path, bbox_inches='tight')
    plt.close(fig_g)
    produced.append(goodput_path)
    return produced
