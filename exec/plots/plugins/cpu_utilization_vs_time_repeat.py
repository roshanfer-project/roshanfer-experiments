"""Per-repeat CPU utilization vs time from raw/cpu_metrics.csv (all experiment types)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Dict, List

import pandas as pd

try:
    from ..plotting_primitives import ACM_COMPACT_HALF, SubplotGrid, plot_line
except ImportError:
    from exec.plots.plotting_primitives import ACM_COMPACT_HALF, SubplotGrid, plot_line  # type: ignore

INFRA_POD_SUBSTRINGS = ("prometheus", "pushgateway")
APP_CONTAINER = "app"
SIDECAR_CONTAINERS = frozenset({"sidecar", "envoy"})


def _is_infra_pod(pod: str) -> bool:
    pod_lower = pod.lower()
    return any(s in pod_lower for s in INFRA_POD_SUBSTRINGS)


def _load_cpu_metrics(csv_path: Path) -> pd.DataFrame | None:
    if not csv_path.is_file():
        return None
    try:
        df = pd.read_csv(csv_path)
    except Exception:
        return None
    required = {"timestamp", "pod", "container", "utilization", "limit"}
    if not required.issubset(df.columns):
        return None
    df = df[~df["pod"].apply(_is_infra_pod)].copy()
    df["limit"] = pd.to_numeric(df["limit"], errors="coerce")
    df["utilization"] = pd.to_numeric(df["utilization"], errors="coerce")
    df = df[df["limit"] > 0].dropna(subset=["utilization", "limit"])
    if df.empty:
        return None
    df["normalized_pct"] = df["utilization"] / df["limit"]
    ts = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.assign(_ts=ts).dropna(subset=["_ts"])
    if df.empty:
        return None
    t0 = df["_ts"].min()
    df["relative_time_s"] = (df["_ts"] - t0).dt.total_seconds()
    return df


def _split_app_sidecar(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    app_df = df[df["container"] == APP_CONTAINER].copy()
    sidecar_df = df[df["container"].isin(SIDECAR_CONTAINERS)].copy()
    return app_df, sidecar_df


def _plot_pods(ax, pod_df: pd.DataFrame, pods: List[str], pod_colors: Dict[str, int], style) -> None:
    for pod in pods:
        grp = pod_df[pod_df["pod"] == pod].sort_values("relative_time_s")
        if grp.empty:
            continue
        plot_line(
            ax,
            grp["relative_time_s"].values,
            grp["normalized_pct"].values,
            label=pod,
            style=style,
            color_idx=pod_colors[pod],
        )


def _y_limits(df: pd.DataFrame) -> tuple[float, float]:
    top = float(df["normalized_pct"].max()) if not df.empty else 100.0
    top = min(100.0, max(10.0, top * 1.05))
    return 0.0, top


def generate_repeat_plots(ctx: Dict) -> List[Path]:
    artifact_dir = Path(ctx["artifact_dir"])
    out_dir = Path(ctx["output_dir"])
    repeat_index = int(ctx["repeat_index"])

    df = _load_cpu_metrics(artifact_dir / "raw" / "cpu_metrics.csv")
    if df is None:
        return []

    app_df, sidecar_df = _split_app_sidecar(df)
    if app_df.empty and sidecar_df.empty:
        return []

    all_pods = sorted(set(app_df["pod"].unique()) | set(sidecar_df["pod"].unique()))
    pod_colors = {pod: idx for idx, pod in enumerate(all_pods)}
    ylim = _y_limits(df)

    out_dir.mkdir(parents=True, exist_ok=True)
    style = replace(ACM_COMPACT_HALF, aspect_ratio=1)
    grid = SubplotGrid(style, layout="1x2")

    ax_app = grid.get_ax(0, 0)
    _plot_pods(ax_app, app_df, all_pods, pod_colors, style)
    grid.configure_ax(
        ax_app,
        title="Apps",
        show_title=True,
        xlabel="Time (s)",
        ylabel="CPU utilization (% of limit)",
        show_ylabel=True,
        grid=True,
        x_data=df["relative_time_s"].values,
        y_data=app_df["normalized_pct"].values if not app_df.empty else [0, ylim[1]],
        ylim=ylim,
        y_step=20,
        y_type="int",
        x_type="int",
        x_step=3,
    )

    ax_sidecar = grid.get_ax(0, 1)
    _plot_pods(ax_sidecar, sidecar_df, all_pods, pod_colors, style)
    grid.configure_ax(
        ax_sidecar,
        title="Sidecars",
        show_title=True,
        xlabel="Time (s)",
        show_ylabel=False,
        show_yticklabels=False,
        grid=True,
        x_data=df["relative_time_s"].values,
        y_data=sidecar_df["normalized_pct"].values if not sidecar_df.empty else [0, ylim[1]],
        ylim=ylim,
        y_step=20,
        y_type="int",
        x_type="int",
        x_step=3,
    )

    grid.add_shared_legend(position="top-right")
    out_path = out_dir / f"cpu_utilization_vs_time_repeat_{repeat_index:03d}.pdf"
    grid.save(out_path)
    return [out_path]
