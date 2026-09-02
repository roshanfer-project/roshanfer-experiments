"""Experiment-level throughput vs overcommitment grouped bar plot.

Produces one bar plot per experiment:
  * throughput_vs_overcommitment.pdf (throughput per API vs OC, 95% CI)

X-axis = overcommitment (%). Y-axis = throughput (RPS). Bars grouped by API.
If an OC has multiple loads, the highest offered load is used.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional
import os
import re

try:
    from ..data_loader import load_repeat_data
    from ..aggregation import aggregate_overall_metric
    from ..plotting_primitives import (
        SubplotGrid, ACM_QUARTER, plot_grouped_bars
    )
except ImportError:
    try:
        from exec.plots.data_loader import load_repeat_data  # type: ignore
        from exec.plots.aggregation import aggregate_overall_metric  # type: ignore
        from exec.plots.plotting_primitives import (  # type: ignore
            SubplotGrid, ACM_QUARTER, plot_grouped_bars
        )
    except ImportError:
        from data_loader import load_repeat_data  # type: ignore
        from aggregation import aggregate_overall_metric  # type: ignore
        from plotting_primitives import (  # type: ignore
            SubplotGrid, ACM_QUARTER, plot_grouped_bars
        )

SUPPORTED_TYPES = ['throughput-vs-overcommitment']

_OC_RE = re.compile(r'oc-(\d+)')


def _oc_percent(unit_name: str) -> Optional[int]:
    m = _OC_RE.search(unit_name or '')
    return int(m.group(1)) if m else None


def generate_experiment_plots(ctx: Dict) -> List[Path]:
    if os.environ.get('PLOT_DEBUG'):
        print(f"[throughput_vs_overcommitment] Called with type: {ctx.get('type')}")

    if ctx.get('type') not in SUPPORTED_TYPES:
        return []

    apis: List[str] = ctx.get('apis') or []
    unit_entries = ctx['unit_entries']
    out_dir: Path = ctx['output_dir']
    out_dir.mkdir(parents=True, exist_ok=True)
    produced: List[Path] = []

    if not unit_entries or not apis:
        return produced

    # oc_pct -> best unit (highest load)
    by_oc: Dict[int, dict] = {}
    for unit_entry in unit_entries:
        oc = _oc_percent(unit_entry.get('run_unit_name', ''))
        if oc is None:
            continue
        load = unit_entry.get('load_value')
        load = load if load is not None else -1
        prev = by_oc.get(oc)
        if prev is None or load >= (prev.get('load_value') if prev.get('load_value') is not None else -1):
            by_oc[oc] = unit_entry

    if not by_oc:
        if os.environ.get('PLOT_DEBUG'):
            print("[throughput_vs_overcommitment] No units with oc-N in name")
        return produced

    oc_values = sorted(by_oc.keys())
    api_heights: Dict[str, List[float]] = {api: [] for api in apis}
    api_errors: Dict[str, List[float]] = {api: [] for api in apis}
    has_data = {api: False for api in apis}

    for oc in oc_values:
        unit_entry = by_oc[oc]
        artifact_dirs = unit_entry.get('artifact_dirs', [])
        for api in apis:
            throughputs = []
            for artifact_dir in artifact_dirs:
                try:
                    repeat_data = load_repeat_data(artifact_dir)
                except Exception:
                    repeat_data = None
                if not repeat_data or api not in repeat_data:
                    continue
                vals = repeat_data[api]
                overall = vals[0] if vals else None
                if overall is not None and getattr(overall, 'throughput', None) is not None:
                    throughputs.append(float(overall.throughput))
            mean, _std, ci = aggregate_overall_metric(throughputs)
            if mean is not None:
                api_heights[api].append(mean)
                api_errors[api].append(ci if ci is not None else 0.0)
                has_data[api] = True
            else:
                api_heights[api].append(0.0)
                api_errors[api].append(0.0)

    bar_groups = []
    max_tp = 0.0
    for api in apis:
        if not has_data[api]:
            continue
        heights = api_heights[api]
        errors = api_errors[api]
        bar_groups.append((api, heights, errors))
        if heights:
            max_tp = max(max_tp, max(heights))

    if not bar_groups:
        if os.environ.get('PLOT_DEBUG'):
            print("[throughput_vs_overcommitment] No valid throughput data")
        return produced

    bar_style = ACM_QUARTER
    grid = SubplotGrid(bar_style, layout="1x1")
    ax = grid.get_ax(0, 0)
    x_positions = list(range(len(oc_values)))
    labels = [f"{oc}%" for oc in oc_values]

    plot_grouped_bars(ax, x_positions, bar_groups, style=bar_style)
    grid.configure_ax(
        ax,
        xlabel="",
        ylabel="Throughput (RPS)",
        show_xticklabels=True,
        y_guard=0.05,
        ylim=(0, max_tp * 1.1 if max_tp > 0 else 1),
        y_step=100,
        y_type='int',
    )
    ax.set_xticks(x_positions)
    ax.set_xticklabels(
        labels,
        rotation=0 if len(labels) < 4 else 30,
        ha='center',
        fontsize=bar_style.font_size - 1,
    )
    grid.add_shared_legend(position="top")

    bar_path = out_dir / 'throughput_vs_overcommitment.pdf'
    grid.save(bar_path)
    produced.append(bar_path)

    if os.environ.get('PLOT_DEBUG'):
        print(f"[throughput_vs_overcommitment] Generated {bar_path.name}")

    return produced
