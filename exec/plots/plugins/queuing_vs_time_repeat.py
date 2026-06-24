"""Per-repeat p99 queuing delay vs time from output/realtime-queuing-{api}.csv."""

from __future__ import annotations

import math
import re
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    from ..plotting_primitives import ACM_COMPACT_HALF, SubplotGrid, plot_line
except ImportError:
    from exec.plots.plotting_primitives import ACM_COMPACT_HALF, SubplotGrid, plot_line  # type: ignore

SUPPORTED_TYPES = ["latency-and-rate-vs-time"]
MAX_COLS = 3
_DEPLOYMENT_POD_RE = re.compile(r"^(.+)-[a-z0-9]{8,10}-[a-z0-9]{5}$")


def _microservice_from_pod(pod: str) -> str:
    m = _DEPLOYMENT_POD_RE.match(pod)
    return m.group(1) if m else pod


def _load_queuing_csv(path: Path) -> Optional[pd.DataFrame]:
    if not path.is_file():
        return None
    try:
        df = pd.read_csv(path)
    except Exception:
        return None
    required = {"relative_time", "service", "p99_queuing_ms"}
    if not required.issubset(df.columns):
        return None
    df = df.copy()
    df["relative_time"] = pd.to_numeric(df["relative_time"], errors="coerce")
    df["p99_queuing_ms"] = pd.to_numeric(df["p99_queuing_ms"], errors="coerce")
    df = df.dropna(subset=["relative_time", "p99_queuing_ms", "service"])
    if df.empty:
        return None
    df["microservice"] = df["service"].map(_microservice_from_pod)
    return df


def _grid_shape(n: int) -> Tuple[int, int]:
    ncols = min(n, MAX_COLS)
    nrows = math.ceil(n / ncols)
    return nrows, ncols


def _linear_ylim(values: List[float]) -> Tuple[float, float]:
    pos = [float(v)
           for v in values if v is not None and np.isfinite(v) and v >= 0]
    if not pos:
        return (0.0, 1.0)
    top = max(pos)
    return (0.0, max(1.2 * top, 1e-3))


def _plot_api_queuing(df: pd.DataFrame, api: str, out_path: Path, style) -> None:
    microservices = sorted(df["microservice"].unique())
    n_ms = len(microservices)
    nrows, ncols = _grid_shape(n_ms)

    grid = SubplotGrid(style, layout=f"{nrows}x{ncols}")

    replicas = sorted(df["service"].unique())
    replica_colors = {pod: idx for idx, pod in enumerate(replicas)}
    ylim = _linear_ylim(df["p99_queuing_ms"].tolist())
    x_data = df["relative_time"].values

    for idx, ms in enumerate(microservices):
        row, col = divmod(idx, ncols)
        ax = grid.get_ax(row, col)
        ms_df = df[df["microservice"] == ms]
        pods = sorted(ms_df["service"].unique())
        for pod in pods:
            grp = ms_df[ms_df["service"] == pod].sort_values("relative_time")
            if grp.empty:
                continue
            plot_line(
                ax,
                grp["relative_time"].values,
                grp["p99_queuing_ms"].values,
                label=None,
                style=style,
                color_idx=replica_colors[pod],
            )

        bottom = row == nrows - 1
        left = col == 0
        grid.configure_ax(
            ax,
            title=ms,
            show_title=True,
            xlabel="Time (s)",
            ylabel="P99 queuing (ms)",
            show_xlabel=bottom,
            show_xticklabels=bottom,
            show_ylabel=left,
            show_yticklabels=left,
            grid=True,
            x_data=x_data,
            x_type="int",
            y_data=df["p99_queuing_ms"].values,
            ylim=ylim,
        )

    for idx in range(n_ms, nrows * ncols):
        row, col = divmod(idx, ncols)
        grid.get_ax(row, col).set_visible(False)

    grid.save(out_path)


def generate_repeat_plots(ctx: Dict) -> List[Path]:
    artifact_dir = Path(ctx["artifact_dir"])
    out_dir = Path(ctx["output_dir"])
    repeat_index = int(ctx["repeat_index"])
    output_data = artifact_dir / "output"
    if not output_data.is_dir():
        return []

    produced: List[Path] = []
    style = ACM_COMPACT_HALF
    style = replace(style, aspect_ratio=0.9)
    out_dir.mkdir(parents=True, exist_ok=True)

    for csv_path in sorted(output_data.glob("realtime-queuing-*.csv")):
        api = csv_path.stem.removeprefix("realtime-queuing-")
        df = _load_queuing_csv(csv_path)
        if df is None:
            continue
        out_path = out_dir / \
            f"queuing_vs_time_{api}_repeat_{repeat_index:03d}.pdf"
        _plot_api_queuing(df, api, out_path, style)
        produced.append(out_path)

    return produced
