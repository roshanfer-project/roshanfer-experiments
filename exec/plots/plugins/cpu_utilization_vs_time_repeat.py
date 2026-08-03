"""Per-repeat CPU utilization vs time from raw/cpu_metrics.csv (all experiment types)."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from exec.plots.plotting_primitives import ACM_COMPACT_FULL

try:
    from ..plotting_primitives import ACM_COMPACT_HALF, SubplotGrid, plot_line
except ImportError:
    from exec.plots.plotting_primitives import ACM_COMPACT_HALF, SubplotGrid, plot_line  # type: ignore

INFRA_POD_SUBSTRINGS = ("prometheus", "pushgateway")
APP_CONTAINER = "app"
SIDECAR_CONTAINERS = frozenset({"sidecar", "envoy"})
# deployment-rsHash-podHash; rs hash length varies (often 6–10)
_DEPLOYMENT_POD_RE = re.compile(r"^(.+)-[a-z0-9]{5,10}-[a-z0-9]{5}$")


def _is_infra_pod(pod: str) -> bool:
    pod_lower = pod.lower()
    return any(s in pod_lower for s in INFRA_POD_SUBSTRINGS)


def _microservice_from_pod(pod: str) -> str:
    m = _DEPLOYMENT_POD_RE.match(pod)
    return m.group(1) if m else pod


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


def _aggregate_by_microservice(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    out["microservice"] = out["pod"].map(_microservice_from_pod)
    return (
        out.groupby(["microservice", "relative_time_s"], as_index=False)
        .agg(normalized_pct=("normalized_pct", "mean"))
    )


def _markevery(n: int, target: int = 10) -> int:
    """Subsample markers so dense series stay readable."""
    return max(1, n // target)


def _plot_microservices(
    ax,
    ms_df: pd.DataFrame,
    microservices: List[str],
    style,
    color_map: Dict[str, int],
) -> Tuple[List, List]:
    handles, labels = [], []
    for ms in microservices:
        grp = ms_df[ms_df["microservice"] == ms].sort_values("relative_time_s")
        if grp.empty:
            continue
        x = grp["relative_time_s"].values
        plot_line(
            ax,
            x,
            grp["normalized_pct"].values,
            label=ms,
            style=style,
            color_idx=color_map[ms],
            show_markers=True,
            markevery=_markevery(len(x)),
        )
        handles.append(ax.lines[-1])
        labels.append(ms)
    return handles, labels


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

    app_ms_df = _aggregate_by_microservice(app_df)
    sidecar_ms_df = _aggregate_by_microservice(sidecar_df)
    app_ms = sorted(app_ms_df["microservice"].unique()) if not app_ms_df.empty else []
    sidecar_ms = (
        sorted(sidecar_ms_df["microservice"].unique())
        if not sidecar_ms_df.empty else []
    )
    # Sidecar-only names (e.g. ingress) have no App curve but still need a legend entry.
    all_ms = sorted(set(app_ms) | set(sidecar_ms))
    color_map = {ms: idx for idx, ms in enumerate(all_ms)}
    ylim = _y_limits(df)

    out_dir.mkdir(parents=True, exist_ok=True)
    style = replace(ACM_COMPACT_HALF, aspect_ratio=1)
    grid = SubplotGrid(style, layout="1x2")

    ax_app = grid.get_ax(0, 0)
    legend_handles, legend_labels = _plot_microservices(
        ax_app, app_ms_df, app_ms, style, color_map)
    grid.configure_ax(
        ax_app,
        title="Apps",
        show_title=True,
        xlabel="Time (s)",
        ylabel="Norm. CPU Util.",
        show_ylabel=True,
        grid=True,
        x_data=df["relative_time_s"].values,
        y_data=app_df["normalized_pct"].values if not app_df.empty else [
            0, ylim[1]],
        ylim=ylim,
        y_type="int",
        x_type="int"
    )

    ax_sidecar = grid.get_ax(0, 1)
    sc_handles, sc_labels = _plot_microservices(
        ax_sidecar, sidecar_ms_df, sidecar_ms, style, color_map)
    for h, lab in zip(sc_handles, sc_labels):
        if lab not in legend_labels:
            legend_handles.append(h)
            legend_labels.append(lab)
    grid.configure_ax(
        ax_sidecar,
        title="Sidecars",
        show_title=True,
        xlabel="Time (s)",
        show_ylabel=False,
        show_yticklabels=False,
        grid=True,
        x_data=df["relative_time_s"].values,
        y_data=sidecar_df["normalized_pct"].values if not sidecar_df.empty else [
            0, ylim[1]],
        ylim=ylim,
        y_step=20,
        y_type="int",
        x_type="int"
    )

    grid.add_shared_legend(
        position="top-right",
        handles=legend_handles,
        labels=legend_labels,
    )
    out_path = out_dir / \
        f"cpu_utilization_vs_time_repeat_{repeat_index:03d}.pdf"
    grid.save(out_path)
    return [out_path]
