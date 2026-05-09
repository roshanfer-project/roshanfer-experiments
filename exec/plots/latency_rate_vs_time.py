"""Latency & rate vs time for latency-and-rate-vs-time style experiments."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

from exec.plots.data_loader import extract_series
from exec.plots.plugins.latency_rate_vs_time_repeat import SLO_LINE_COLOR
from exec.plots.plotting_primitives import ACM_COMPACT_HALF, SubplotGrid, plot_line, plot_stacked_area

RATE_KEYS_ORDER = ["goodput", "slo_violation", "dropped_in", "dropped"]
LATENCY_KEYS_ORDER = ["latency_p50", "latency_p99", "latency_p99"]


def _label_fixer(label: str) -> str:
    if label == "dropped_in":
        return "dropped in"
    if label == "slo_violation":
        return "SLO violation"
    if label == "latency_p50":
        return "P50"
    if label == "latency_p99":
        return "P99"
    return label


def _load_metric_files(unit_dir: Path) -> Dict[str, dict]:
    metrics_dir = unit_dir / "metrics"
    if not metrics_dir.exists():
        return {}
    out: Dict[str, dict] = {}
    for fp in metrics_dir.glob("*.json"):
        if fp.name == "_index.json":
            continue
        try:
            data = json.loads(fp.read_text())
            res = data.get("result")
            if isinstance(res, list):
                out[fp.stem] = data
        except Exception:
            pass
    return out


def _aggregate_units(all_units: List[Path]) -> Dict[str, pd.DataFrame]:
    rate_frames: List[pd.DataFrame] = []
    latency_frames: List[pd.DataFrame] = []
    for unit_dir in all_units:
        metric_files = _load_metric_files(unit_dir)
        for key in RATE_KEYS_ORDER + LATENCY_KEYS_ORDER:
            matching = [name for name in metric_files.keys() if name.startswith(key)]
            for name in matching:
                times, vals = extract_series(metric_files[name])
                if not times:
                    continue
                rel0 = times[0]
                rel_times = [t - rel0 for t in times]
                df = pd.DataFrame(
                    {"t": rel_times, "value": vals, "metric": key, "unit_dir": str(unit_dir)}
                )
                if key in RATE_KEYS_ORDER:
                    rate_frames.append(df)
                else:
                    latency_frames.append(df)
    result: Dict[str, pd.DataFrame] = {}
    if rate_frames:
        rf = pd.concat(rate_frames)
        rf["t_round"] = rf["t"].round(3)
        rate_agg = rf.groupby(["metric", "t_round"]).value.mean().reset_index().rename(columns={"t_round": "t"})
        result["rate"] = rate_agg
    if latency_frames:
        lf = pd.concat(latency_frames)
        lf["t_round"] = lf["t"].round(3)
        lat_agg = lf.groupby(["metric", "t_round"]).value.mean().reset_index().rename(columns={"t_round": "t"})
        result["latency"] = lat_agg
    return result


def _save_rate_stack(rate_df: pd.DataFrame, out_path: Path, time_col: str, value_col: str) -> None:
    df = rate_df.copy()
    if time_col in df.columns:
        df = df[df[time_col] <= 15.0].copy()
    if df.empty:
        return
    available = df.metric.unique()
    if "dropped" in available and "dropped_in" in available:
        pivot_df = df.pivot_table(index=time_col, columns="metric", values=value_col, fill_value=0)
        if "dropped" in pivot_df.columns and "dropped_in" in pivot_df.columns:
            pivot_df = pivot_df.copy()
            pivot_df["dropped"] = (pivot_df["dropped"] - pivot_df["dropped_in"]).clip(lower=0)
            df = pivot_df.reset_index().melt(id_vars=[time_col], var_name="metric", value_name=value_col)
    order = [k for k in RATE_KEYS_ORDER if k in df["metric"].unique()]
    if not order:
        return
    pivot = df.pivot_table(index=time_col, columns="metric", values=value_col, fill_value=0)
    pivot = pivot.sort_index()
    x = pivot.index.to_numpy(dtype=float)
    y_series = {_label_fixer(k): pivot[k].to_numpy(dtype=float) for k in order if k in pivot.columns}
    if not y_series:
        return
    style = ACM_COMPACT_HALF
    grid = SubplotGrid(style, layout="1x1")
    ax = grid.get_ax(0, 0)
    plot_stacked_area(ax, x, y_series, style=style)
    y_stack = np.sum(list(y_series.values()), axis=0)
    grid.configure_ax(
        ax,
        xlabel="Time (s)",
        ylabel="Rate (KRPS)",
        x_data=x,
        y_data=y_stack,
        grid=True,
    )
    ncol = len(y_series) if len(y_series) <= 2 else 2
    grid.add_shared_legend(position="top", ncol=ncol, two_rows=len(y_series) > 2)
    grid.save(out_path)


def _save_latency_lines(
    lat_df: pd.DataFrame,
    out_path: Path,
    time_col: str,
    value_col: str,
    add_slo: bool = True,
    slo_ms: float = 60.0,
    y_min: float = 1.0,
    y_max: float = 500.0,
) -> None:
    df = lat_df.copy()
    if time_col in df.columns:
        df = df[df[time_col] <= 15.0].copy()
    if df.empty:
        return
    seen: set[str] = set()
    order: List[str] = []
    for k in LATENCY_KEYS_ORDER:
        if k in df["metric"].unique() and k not in seen:
            seen.add(k)
            order.append(k)
    if not order:
        return
    style = ACM_COMPACT_HALF
    grid = SubplotGrid(style, layout="1x1")
    ax = grid.get_ax(0, 0)
    xs = []
    for i, k in enumerate(order):
        sub = df[df.metric == k].sort_values(time_col)
        x = sub[time_col].to_numpy(dtype=float)
        y = sub[value_col].to_numpy(dtype=float)
        if len(x):
            xs.append(x)
        plot_line(ax, x, y, label=_label_fixer(k), style=style, color_idx=i)
    if add_slo:
        ax.axhline(y=slo_ms, color=SLO_LINE_COLOR, linestyle="--", label="SLO")
    x_data = np.concatenate(xs) if xs else None
    grid.configure_ax(
        ax,
        xlabel="Time (s)",
        ylabel="Latency (ms)",
        x_data=x_data,
        log_y=True,
        ylim=(y_min, y_max),
        grid=True,
    )
    grid.add_shared_legend(position="top")
    grid.save(out_path)


def generate(experiment_index: str, experiments_root: Path, experiment_name: str, output_dir: Path) -> Path:
    base_dir = experiments_root / f"exp-{experiment_index}" / experiment_name
    if not base_dir.exists():
        raise FileNotFoundError(f"Experiment directory not found: {base_dir}")
    unit_dirs: List[Path] = [p for p in base_dir.iterdir() if p.is_dir()]
    all_repeat_dirs: List[Path] = []
    for ud in unit_dirs:
        all_repeat_dirs.extend([p for p in ud.glob("repeat_*") if p.is_dir()])
    agg = _aggregate_units(all_repeat_dirs)
    for key, df in agg.items():
        print(f"Max values for {key}:")
        for metric in df["metric"].unique():
            max_val = df[df["metric"] == metric]["value"].max()
            print(f"  {metric}: {max_val}")
    output_dir.mkdir(parents=True, exist_ok=True)
    if "rate" in agg and not agg["rate"].empty:
        rate_df = agg["rate"].copy()
        rate_df["t_rel"] = rate_df["t"]
        rate_df["value_krps"] = rate_df["value"] / 1000.0
        _save_rate_stack(rate_df, output_dir / "rate_vs_time.pdf", time_col="t_rel", value_col="value_krps")
    if "latency" in agg and not agg["latency"].empty:
        lat_df = agg["latency"].copy()
        lat_df["t_rel"] = lat_df["t"]
        _save_latency_lines(lat_df, output_dir / "latency_vs_time.pdf", time_col="t_rel", value_col="value")
    return output_dir


def parse_args(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--experiment-index", required=True)
    p.add_argument("--experiment-name", required=True)
    p.add_argument("--output-dir", default="generated_plots")
    p.add_argument("--experiments-root", default="experiment_runs")
    return p.parse_args(argv)


def main(argv=None):
    ns = parse_args(argv)
    out = Path(ns.output_dir) / ns.experiment_name
    generate(ns.experiment_index, Path(ns.experiments_root), ns.experiment_name, out)
    print(f"Plots written to {out}")


if __name__ == "__main__":
    main()
