"""Experiment-level SLO violation vs throughput line plot with 95% CI.

Produces one line plot per experiment:
  * latency_vs_throughput.pdf (SLO Violation (%) vs throughput with 95% CI error bars)

Supports single and multiple APIs. X-axis = throughput (RPS). Y-axis = SLO Violation (%).
All APIs are plotted on a single subplot with distinct lines.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List
import os

try:
    from ..data_loader import load_repeat_data
    from ..aggregation import aggregate_overall_metric
    from ..plotting_primitives import (
        SubplotGrid, ACM_QUARTER, plot_line
    )
except ImportError:
    try:
        from exec.plots.data_loader import load_repeat_data  # type: ignore
        from exec.plots.aggregation import aggregate_overall_metric  # type: ignore
        from exec.plots.plotting_primitives import (  # type: ignore
            SubplotGrid, ACM_QUARTER, plot_line
        )
    except ImportError:
        from data_loader import load_repeat_data  # type: ignore
        from aggregation import aggregate_overall_metric  # type: ignore
        from plotting_primitives import (  # type: ignore
            SubplotGrid, ACM_QUARTER, plot_line
        )

SUPPORTED_TYPES = ['latency-vs-throughput']


def generate_experiment_plots(ctx: Dict) -> List[Path]:
    """Generate experiment-level SLO violation vs throughput line plot with 95% CI."""
    if os.environ.get('PLOT_DEBUG'):
        print(f"[latency_vs_throughput_experiment] Called with type: {ctx.get('type')}")

    if ctx.get('type') not in SUPPORTED_TYPES:
        if os.environ.get('PLOT_DEBUG'):
            print(f"[latency_vs_throughput_experiment] Type {ctx.get('type')} not in supported types: {SUPPORTED_TYPES}")
        return []

    apis: List[str] = ctx.get('apis') or []
    unit_entries = ctx['unit_entries']
    out_dir: Path = ctx['output_dir']

    out_dir.mkdir(parents=True, exist_ok=True)
    produced: List[Path] = []

    if not unit_entries:
        return produced

    if not apis:
        if os.environ.get('PLOT_DEBUG'):
            print("[latency_vs_throughput_experiment] Error: No APIs found")
        return produced

    unit_entries = [u for u in unit_entries if u.get('load_value') is not None]
    unit_entries.sort(key=lambda u: u['load_value'])

    if not unit_entries:
        if os.environ.get('PLOT_DEBUG'):
            print("[latency_vs_throughput_experiment] No units with valid load values")
        return produced

    api_data: Dict[str, Dict] = {api: {'tp': [], 'slo': [], 'slo_ci': []} for api in apis}

    for unit_entry in unit_entries:
        load_value = unit_entry['load_value']
        artifact_dirs = unit_entry.get('artifact_dirs', [])

        if os.environ.get('PLOT_DEBUG'):
            print(f"[latency_vs_throughput_experiment] Processing load {load_value} with {len(artifact_dirs)} repeats")

        try:
            for api in apis:
                unit_throughputs = []
                unit_slo_pcts = []
                for artifact_dir in artifact_dirs:
                    repeat_data = load_repeat_data(artifact_dir)
                    if repeat_data and api in repeat_data:
                        vals = repeat_data[api]
                        if len(vals) == 3:
                            overall, _, _ = vals
                        else:
                            overall, _ = vals
                        if overall is not None and overall.num_throughput > 0:
                            unit_throughputs.append(float(overall.throughput))
                            pct = (float(overall.num_slo_violations) / float(overall.num_throughput)) * 100.0
                            unit_slo_pcts.append(pct)
                if unit_throughputs and unit_slo_pcts:
                    tp_mean, _, _ = aggregate_overall_metric(unit_throughputs)
                    slo_mean, _, slo_ci = aggregate_overall_metric(unit_slo_pcts)
                    if tp_mean is not None and slo_mean is not None:
                        api_data[api]['tp'].append(tp_mean)
                        api_data[api]['slo'].append(slo_mean)
                        api_data[api]['slo_ci'].append(slo_ci if slo_ci is not None else 0.0)
        except Exception as e:
            if os.environ.get('PLOT_DEBUG'):
                print(f"[latency_vs_throughput_experiment] Error processing load {load_value}: {e}")
                import traceback
                traceback.print_exc()
            continue

    has_data = any(len(api_data[api]['tp']) > 0 for api in apis)
    if not has_data:
        if os.environ.get('PLOT_DEBUG'):
            print("[latency_vs_throughput_experiment] No valid data points found")
        return produced

    grid = SubplotGrid(ACM_QUARTER, layout="1x1")
    ax = grid.get_ax(0, 0)

    all_tp, all_slo = [], []
    n_apis = sum(1 for api in apis if api_data[api]['tp'] and api_data[api]['slo'])
    for idx, api in enumerate(apis):
        tp = api_data[api]['tp']
        slo = api_data[api]['slo']
        slo_ci = api_data[api]['slo_ci']
        if not tp or not slo:
            continue
        display_api = api.replace('_all', '') if api.endswith('_all') else api
        plot_line(
            ax, tp, slo,
            yerr=slo_ci,
            label=display_api,
            style=ACM_QUARTER,
            color_idx=idx,
            style_idx=idx if n_apis > 1 else 0,
            show_markers=True
        )
        all_tp.extend(tp)
        all_slo.extend(slo)

    ax.legend()
    grid.configure_ax(
        ax,
        xlabel="Throughput (RPS)",
        ylabel="SLO Violation (%)",
        x_data=all_tp,
        y_data=all_slo,
        y_type="float",
        grid=True
    )

    line_path = out_dir / 'latency_vs_throughput.pdf'
    grid.save(line_path)
    produced.append(line_path)

    if os.environ.get('PLOT_DEBUG'):
        print(f"[latency_vs_throughput_experiment] Generated line plot: {line_path.name}")

    return produced
