"""Per-repeat plots for Envoy req−resp gap from absolute counters (metrics/envoy/*.csv)."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

try:
    from ..plotting_primitives import ACM_COMPACT_HALF, PlotStyle, SubplotGrid, plot_line
except ImportError:
    from exec.plots.plotting_primitives import ACM_COMPACT_HALF, PlotStyle, SubplotGrid, plot_line  # type: ignore

SUPPORTED_TYPES = ["latency-and-rate-vs-time"]

_EMA_ALPHA = 0.3


def _ema(values, alpha: float = _EMA_ALPHA):
    return pd.Series(values).ewm(alpha=alpha, adjust=False).mean().to_numpy()


def _load_envoy_csvs(metrics_dir: Path) -> Dict[str, pd.DataFrame]:
    envoy_dir = metrics_dir / "envoy"
    if not envoy_dir.is_dir():
        return {}
    out: Dict[str, pd.DataFrame] = {}
    for fp in sorted(envoy_dir.glob("*.csv")):
        try:
            df = pd.read_csv(fp)
            if "relative_time_s" not in df.columns:
                continue
            out[fp.stem] = df
        except Exception:
            continue
    return out


def _listener_pairs(df: pd.DataFrame) -> List[Tuple[str, str, str]]:
    pairs: List[Tuple[str, str, str]] = []
    for col in df.columns:
        if not col.endswith("-Req"):
            continue
        lid = col[:-4]
        resp_col = f"{lid}-Resp"
        if resp_col in df.columns:
            pairs.append((lid, col, resp_col))
    return sorted(pairs, key=lambda t: t[0])


def _gap_series(df: pd.DataFrame, req_col: str, resp_col: str):
    return (df[req_col] - df[resp_col]).values


def _plot_sidecar(ax, df: pd.DataFrame, style: PlotStyle) -> None:
    x = df["relative_time_s"].values
    pairs = _listener_pairs(df)
    if pairs:
        for idx, (lid, req_col, resp_col) in enumerate(pairs):
            y = _gap_series(df, req_col, resp_col)
            plot_line(
                ax,
                x,
                _ema(y),
                label=f"{lid} EMA (α=0.3)",
                style=style,
                color_idx=idx,
                style_idx=idx % 2,
            )
        return
    if "Ingress-Req" not in df.columns or "Ingress-Resp" not in df.columns:
        return
    y = _gap_series(df, "Ingress-Req", "Ingress-Resp")
    plot_line(
        ax, x, _ema(y), label="EMA (α=0.3)", style=style, color_idx=1, style_idx=1
    )


def _max_ema_gap(df: pd.DataFrame) -> float:
    y_max = 0.0
    for _, req_col, resp_col in _listener_pairs(df):
        y_max = max(y_max, float(
            _ema(_gap_series(df, req_col, resp_col)).max()))
    if "Ingress-Req" in df.columns and "Ingress-Resp" in df.columns:
        y_max = max(
            y_max,
            float(_ema(_gap_series(df, "Ingress-Req", "Ingress-Resp")).max()),
        )
    return y_max


def _shared_ylim(sidecars: Dict[str, pd.DataFrame]) -> tuple[float, float]:
    global_max = 0.0
    for df in sidecars.values():
        y_max = _max_ema_gap(df)
        if y_max > global_max:
            global_max = y_max
    if global_max <= 0:
        return 0.0, 1.0
    return 0.0, max(1.0, global_max)


def _grid_layout(n: int) -> Tuple[str, int, int]:
    if n <= 1:
        return "1x1", 1, 1
    if n <= 3:
        return f"row-{n}", 1, n
    ncols = 3
    nrows = math.ceil(n / ncols)
    return f"{nrows}x{ncols}", nrows, ncols


def generate_repeat_plots(ctx: Dict) -> List[Path]:
    metrics_dir: Path = Path(
        ctx.get("metrics_dir", ctx["artifact_dir"] / "metrics"))
    out_dir: Path = Path(ctx["output_dir"])
    repeat_index: int = int(ctx["repeat_index"])

    sidecars = _load_envoy_csvs(metrics_dir)
    if not sidecars:
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    style = ACM_COMPACT_HALF
    style = replace(style, aspect_ratio=1)
    n = len(sidecars)
    layout, nrows, ncols = _grid_layout(n)
    grid = SubplotGrid(style, layout=layout)
    ylim = _shared_ylim(sidecars)

    for idx, (name, df) in enumerate(sorted(sidecars.items())):
        row, col = (0, 0) if n == 1 else divmod(idx, ncols)
        ax = grid.get_ax(row, col)
        _plot_sidecar(ax, df, style)
        x = df["relative_time_s"].values
        grid.configure_ax(
            ax,
            title=name,
            show_title=True,
            xlabel="Time (s)",
            ylabel="Concurrency",
            show_xlabel=(row == nrows - 1),
            show_xticklabels=(row == nrows - 1),
            show_ylabel=(col == 0),
            show_yticklabels=(col == 0),
            grid=True,
            x_data=x,
            x_type="int",
            y_data=[0.0, ylim[1]],
            y_type="int",
            ylim=ylim
        )

    for idx in range(n, nrows * ncols):
        row, col = divmod(idx, ncols)
        grid.get_ax(row, col).set_visible(False)

    grid.add_shared_legend(position="top-right", two_rows=True)
    out_path = out_dir / f"envoy_ingress_rate_repeat_{repeat_index:03d}.pdf"
    grid.save(out_path)
    return [out_path]
