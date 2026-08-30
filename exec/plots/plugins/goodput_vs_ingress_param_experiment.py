"""Experiment-level goodput vs ingress AIMD parameter (bar plot).

X-axis = swept parameter value. Y-axis = goodput (KRPS), 95% CI across repeats.
Other AIMD knobs stay at their defaults. Load is fixed per experiment.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple
import math
import os
import re

try:
    from ..aggregation import aggregate_by_api
    from ..plotting_primitives import (
        SubplotGrid, ACM_QUARTER, plot_grouped_bars
    )
except ImportError:
    try:
        from exec.plots.aggregation import aggregate_by_api  # type: ignore
        from exec.plots.plotting_primitives import (  # type: ignore
            SubplotGrid, ACM_QUARTER, plot_grouped_bars
        )
    except ImportError:
        from aggregation import aggregate_by_api  # type: ignore
        from plotting_primitives import (  # type: ignore
            SubplotGrid, ACM_QUARTER, plot_grouped_bars
        )

try:
    from ..data_loader import load_repeat_data
except ImportError:
    try:
        from exec.plots.data_loader import load_repeat_data  # type: ignore
    except ImportError:
        from data_loader import load_repeat_data  # type: ignore

SUPPORTED_TYPES = ['ingress-param-sensitivity']

_PARAM_RE = re.compile(
    r'-(aimd_err_d|aimd_err_i|aimd_adj_d|aimd_adj_i|safe_multiply)'
    r'-v(-?[\d.]+(?:[eE][+-]?\d+)?)-rate-'
)


def _parse_unit(unit_name: str) -> Optional[Tuple[str, float]]:
    m = _PARAM_RE.search(unit_name or '')
    if not m:
        return None
    return m.group(1), float(m.group(2))


_PARAM_LABELS = {
    "aimd_adj_d": r"$adj_{d}$",
    "aimd_adj_i": r"$adj_{i}$",
    "aimd_err_d": r"$err_{d}$",
    "aimd_err_i": r"$err_{i}$",
}


def _fmt_tick(v: float) -> str:
    return f"{v:g}"


def _param_xlabel(parameter: str) -> str:
    return _PARAM_LABELS.get(parameter, parameter)


def generate_experiment_plots(ctx: Dict) -> List[Path]:
    if ctx.get('type') not in SUPPORTED_TYPES:
        return []

    apis: List[str] = ctx.get('apis') or []
    unit_entries = ctx['unit_entries']
    out_dir: Path = ctx['output_dir']
    out_dir.mkdir(parents=True, exist_ok=True)
    produced: List[Path] = []

    if not unit_entries or not apis:
        return produced

    parsed = []
    parameter = None
    for unit_entry in unit_entries:
        parsed_unit = _parse_unit(unit_entry.get('run_unit_name', ''))
        if parsed_unit is None:
            continue
        param, value = parsed_unit
        if parameter is None:
            parameter = param
        parsed.append((value, unit_entry))

    if not parsed or parameter is None:
        if os.environ.get('PLOT_DEBUG'):
            print("[goodput_vs_ingress_param] no units with param-vN-rate in name")
        return produced

    parsed.sort(key=lambda x: x[0])
    xs = [v for v, _ in parsed]
    goodput_series = {api: [] for api in apis}

    for _value, unit_entry in parsed:
        artifact_dirs = unit_entry.get('artifact_dirs', [])
        try:
            all_repeats = []
            for artifact_dir in artifact_dirs:
                repeat_data = load_repeat_data(artifact_dir)
                if repeat_data:
                    all_repeats.append(repeat_data)
            if not all_repeats:
                for api in apis:
                    goodput_series[api].append((None, None, None))
                continue
            aggregated = aggregate_by_api(all_repeats)
            for api in apis:
                if api in aggregated:
                    goodput_series[api].append(
                        aggregated[api].get('goodput', (None, None, None))
                    )
                else:
                    goodput_series[api].append((None, None, None))
        except Exception as e:
            if os.environ.get('PLOT_DEBUG'):
                print(f"[goodput_vs_ingress_param] unit error: {e}")
            for api in apis:
                goodput_series[api].append((None, None, None))

    bar_groups = []
    all_gp = []
    for api in apis:
        heights = []
        errors = []
        has_data = False
        for item in goodput_series[api]:
            mean = item[0]
            ci = item[2]
            if mean is not None and not (isinstance(mean, float) and math.isnan(mean)):
                h = mean / 1000.0
                heights.append(h)
                errors.append((ci / 1000.0) if ci is not None else 0.0)
                all_gp.append(h)
                has_data = True
            else:
                heights.append(0.0)
                errors.append(0.0)
        if has_data:
            display_api = api.replace('_all', '') if api.endswith('_all') else api
            bar_groups.append((display_api, heights, errors))

    if not bar_groups:
        if os.environ.get('PLOT_DEBUG'):
            print("[goodput_vs_ingress_param] no valid goodput data")
        return produced

    y_max = max(all_gp) * 1.1 if all_gp else 10.0
    y_max = max(y_max, 0.1)

    style = ACM_QUARTER
    grid = SubplotGrid(style, layout="1x1")
    ax = grid.get_ax(0, 0)
    x_positions = list(range(len(xs)))
    labels = [_fmt_tick(v) for v in xs]

    plot_grouped_bars(ax, x_positions, bar_groups, style=style)
    grid.configure_ax(
        ax,
        xlabel=_param_xlabel(parameter),
        ylabel="Goodput (KRPS)",
        show_xticklabels=True,
        y_guard=0.05,
        ylim=(0, y_max),
        y_type='float',
    )
    ax.set_xticks(x_positions)
    ax.set_xticklabels(
        labels,
        rotation=0 if len(labels) < 6 else 30,
        ha='center',
        fontsize=style.font_size - 1,
    )
    if len(bar_groups) > 1:
        grid.add_shared_legend(position="top")

    out_path = out_dir / 'goodput_vs_ingress_param.pdf'
    grid.save(out_path)
    produced.append(out_path)

    if os.environ.get('PLOT_DEBUG'):
        print(f"[goodput_vs_ingress_param] Generated {out_path.name}")

    return produced
