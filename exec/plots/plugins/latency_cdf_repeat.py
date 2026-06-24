"""Per-repeat E2E latency CDF from output/out-{api}.csv (all experiment types)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    from ..data_loader import load_out_latencies_ms
    from ..plotting_primitives import (
        ACM_COMPACT_HALF, ACM_QUARTER, SubplotGrid, plot_cdf,
    )
    from .latency_rate_vs_time_repeat import SLO_LINE_COLOR, _lookup_slo, _load_global_slos
except ImportError:
    from exec.plots.data_loader import load_out_latencies_ms  # type: ignore
    from exec.plots.plotting_primitives import (  # type: ignore
        ACM_COMPACT_HALF, ACM_QUARTER, SubplotGrid, plot_cdf,
    )
    from exec.plots.plugins.latency_rate_vs_time_repeat import (  # type: ignore
        SLO_LINE_COLOR, _lookup_slo, _load_global_slos,
    )


def _warmup_cooldown(ctx: Dict) -> Tuple[int, int]:
    cfg = ctx.get('record', {}).get('config') or {}
    warmup = int(cfg.get('warmup', 0) or 0)
    cooldown = int(cfg.get('cooldown', 0) or 0)
    return warmup, cooldown


def _latency_log_xlim(latencies_list: List[np.ndarray], slo_ms: float) -> Tuple[float, float]:
    vals: List[float] = []
    if slo_ms > 0:
        vals.append(float(slo_ms))
    for lat in latencies_list:
        pos = lat[np.isfinite(lat) & (lat > 0)]
        if pos.size:
            vals.append(float(np.max(pos)))
    top = max(vals) if vals else 1.0
    if top <= 0:
        top = 1.0
    return (1.0, max(1.2 * top, 1.0))


def _plot_single_api_cdf(api_name: str, latencies_ms: np.ndarray, slo_ms: float,
                         out_path: Path, style) -> None:
    grid = SubplotGrid(style, layout="1x1")
    ax = grid.get_ax(0, 0)
    plot_cdf(ax, values=latencies_ms, style=style, color_idx=0)
    ax.axvline(
        x=float(slo_ms), color=SLO_LINE_COLOR, linestyle='--', label='SLO',
        linewidth=style.line_width, zorder=4,
    )
    x_lo, x_hi = _latency_log_xlim([latencies_ms], slo_ms)
    grid.configure_ax(
        ax, xlabel='E2E latency (ms)', ylabel='CDF', grid=True, log_x=True,
        ylim=(0.0, 1.0), y_step=0.2, xlim=(x_lo, x_hi),
    )
    grid.add_shared_legend(position="top-left")
    grid.save(out_path)


def _plot_multi_api_cdf(api_latencies: Dict[str, np.ndarray], slos: Optional[Dict[str, float]],
                        out_path: Path, style) -> None:
    n_apis = len(api_latencies)
    layout = f"row-{n_apis}"
    style_grid = replace(style, aspect_ratio=0.8)
    grid = SubplotGrid(style_grid, layout=layout)

    slo_vals = [_lookup_slo(slos, api) for api in api_latencies]
    slo_for_xlim = max(slo_vals) if slo_vals else 60.0
    x_lo, x_hi = _latency_log_xlim(list(api_latencies.values()), float(slo_for_xlim))

    for idx, (api_name, latencies_ms) in enumerate(sorted(api_latencies.items())):
        ax = grid.get_ax(0, idx)
        plot_cdf(ax, values=latencies_ms, style=style, color_idx=0)
        slo_ms = _lookup_slo(slos, api_name)
        ax.axvline(
            x=float(slo_ms), color=SLO_LINE_COLOR, linestyle='--', label='SLO',
            linewidth=style.line_width, zorder=4,
        )
        display_api = api_name.replace('_all', '') if api_name.endswith('_all') else api_name
        grid.configure_ax(
            ax,
            title=display_api,
            show_title=True,
            xlabel='E2E latency (ms)',
            ylabel='CDF',
            show_xlabel=True,
            show_xticklabels=True,
            show_ylabel=(idx == 0),
            show_yticklabels=(idx == 0),
            grid=True,
            log_x=True,
            ylim=(0.0, 1.0),
            y_step=0.2,
            xlim=(x_lo, x_hi),
        )

    grid.add_shared_legend(position="top")
    grid.save(out_path)


def generate_repeat_plots(ctx: Dict) -> List[Path]:
    artifact_dir = Path(ctx['artifact_dir'])
    out_dir = Path(ctx['output_dir'])
    repeat_index = int(ctx['repeat_index'])
    output_data = artifact_dir / 'output'
    if not output_data.is_dir():
        return []

    warmup, cooldown = _warmup_cooldown(ctx)
    slos = ctx.get('slos') if isinstance(ctx.get('slos'), dict) else None
    if slos is None:
        slos = _load_global_slos()

    api_latencies: Dict[str, np.ndarray] = {}
    for csv_path in sorted(output_data.glob('out-*.csv')):
        api = csv_path.stem.removeprefix('out-')
        latencies = load_out_latencies_ms(
            csv_path, version=1, warmup=warmup, cooldown=cooldown,
        )
        if latencies.size > 0:
            api_latencies[api] = latencies

    if not api_latencies:
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'latency_cdf_repeat_{repeat_index:03d}.pdf'

    if len(api_latencies) == 1:
        api_name, latencies = next(iter(api_latencies.items()))
        style = ACM_QUARTER
        style = replace(style, legend_size=7)
        slo_ms = _lookup_slo(slos, api_name)
        _plot_single_api_cdf(api_name, latencies, slo_ms, out_path, style)
    else:
        _plot_multi_api_cdf(api_latencies, slos, out_path, ACM_COMPACT_HALF)

    return [out_path]
