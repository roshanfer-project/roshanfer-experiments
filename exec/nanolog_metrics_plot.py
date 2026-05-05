"""Decompressed NanoLog metric lines (M#) -> quantile time-series PDF."""

from __future__ import annotations

import argparse
import datetime
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
import matplotlib.pyplot as plt

from exec.plots import plotting_primitives as pp


def _ymax_quantiles(results: Dict[int, List[float]]) -> float:
    m = -np.inf
    for k in (50, 95, 99):
        for x in results[k]:
            if not np.isnan(x):
                m = max(m, float(x))
    return float(m) if np.isfinite(m) else 1.0


def parse_logs(files: List[Path]) -> Dict[str, List[Tuple[float, float]]]:
    metrics: Dict[str, List[Tuple[float, float]]] = defaultdict(list)

    for filepath in files:
        try:
            with open(filepath, "r", errors="replace") as f:
                for line in f:
                    parts = line.strip().split(" ")
                    if len(parts) < 6:
                        continue
                    try:
                        m_idx = parts.index("M#")
                    except ValueError:
                        continue
                    if m_idx + 5 >= len(parts):
                        continue
                    date_str = parts[0]
                    time_str = parts[1]
                    if "." in time_str:
                        hms, frac = time_str.split(".", 1)
                        frac = frac[:6]
                        time_str = f"{hms}.{frac}"
                    dt_str = f"{date_str} {time_str}"
                    try:
                        dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S.%f")
                        timestamp = dt.timestamp()
                    except ValueError:
                        continue
                    metric_name = parts[m_idx + 2]
                    conn_type = parts[m_idx + 3]
                    rpc_path = parts[m_idx + 4]
                    try:
                        value = float(parts[m_idx + 5])
                    except ValueError:
                        continue
                    # Join first three tokens after sidecar name — patterns like Measured QS-* live in conn_type.
                    full_metric_name = f"{metric_name} {conn_type} {rpc_path}"
                    metrics[full_metric_name].append((timestamp, value))
        except OSError as e:
            logging.warning("nanolog plot: read %s: %s", filepath, e)

    return metrics


def calculate_quantiles(
    data: List[Tuple[float, float]],
    resolution: float,
    count_only: bool = False,
    global_start: Optional[float] = None,
    global_end: Optional[float] = None,
) -> Tuple[Optional[List[float]], Optional[Dict[int, List[float]]]]:
    if not data and (global_start is None or global_end is None):
        return None, None

    if data:
        data = sorted(data, key=lambda x: x[0])
        first_time = data[0][0]
        last_time = data[-1][0]
    else:
        first_time = global_start
        last_time = global_end

    start_time = global_start if global_start is not None else first_time
    end_time = global_end if global_end is not None else last_time

    timestamps: List[float] = []
    quantiles: Dict[int, List[float]] = {50: [], 95: [], 99: []}

    current_bin_start = start_time
    current_bin_values: List[float] = []
    data_idx = 0
    n = len(data)

    while data_idx < n and data[data_idx][0] < current_bin_start:
        data_idx += 1

    while current_bin_start <= end_time + resolution:
        bin_end = current_bin_start + resolution
        while data_idx < n and data[data_idx][0] < bin_end:
            current_bin_values.append(data[data_idx][1])
            data_idx += 1

        if current_bin_values:
            timestamps.append(current_bin_start - start_time)
            if count_only:
                count_val = float(len(current_bin_values))
                quantiles[50].append(count_val)
                quantiles[95].append(count_val)
                quantiles[99].append(count_val)
            else:
                quantiles[50].append(float(np.percentile(current_bin_values, 50)))
                quantiles[95].append(float(np.percentile(current_bin_values, 95)))
                quantiles[99].append(float(np.percentile(current_bin_values, 99)))
        else:
            timestamps.append(current_bin_start - start_time)
            if count_only:
                quantiles[50].append(0.0)
                quantiles[95].append(0.0)
                quantiles[99].append(0.0)
            else:
                quantiles[50].append(float("nan"))
                quantiles[95].append(float("nan"))
                quantiles[99].append(float("nan"))

        current_bin_values = []
        current_bin_start += resolution

    return timestamps, quantiles


def generate_nanolog_pdf(log_files: List[Path], output_pdf: Path, resolution: float = 0.2) -> None:
    paths = [Path(p).resolve() for p in log_files]
    metrics_data = parse_logs(paths)
    if not metrics_data:
        logging.warning("nanolog plot: no M# metrics in inputs; skipping %s", output_pdf)
        return

    global_start = float("inf")
    global_end = float("-inf")
    has_data = False
    for _, data in metrics_data.items():
        if data:
            ts = [x[0] for x in data]
            global_start = min(global_start, min(ts))
            global_end = max(global_end, max(ts))
            has_data = True
    if not has_data:
        return

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(output_pdf) as pdf:
        # Tight subplot gaps (0.5 hspace blows up vertical whitespace vs defaults ~0.04).
        style = pp.PlotStyle(width_points=240, hspace=0.06, h_pad=0.02, wspace=0.06, w_pad=0.02)
        sorted_keys = sorted(metrics_data.keys())
        rows, cols = 3, 2
        plots_per_page = rows * cols

        for i in range(0, len(sorted_keys), plots_per_page):
            chunk = sorted_keys[i : i + plots_per_page]
            grid = pp.SubplotGrid(style, layout=f"{rows}x{cols}")

            for j, full_metric_name in enumerate(chunk):
                is_limit = full_metric_name.startswith("LIMIT")
                # AIMD relative error / queue metrics — not μs latency (even when logged with T:T).
                is_err = (
                    "Measured ERR" in full_metric_name
                    or " ERR-" in full_metric_name
                    or full_metric_name.startswith("ERR ")
                )
                is_dimless = (
                    "QS" in full_metric_name
                    or "DROP" in full_metric_name
                    or "DSC" in full_metric_name
                    or "TwAvg" in full_metric_name
                    or "TimeMean" in full_metric_name
                    or is_err
                    or is_limit
                )
                is_drop = "DROP" in full_metric_name
                row_idx = j // cols
                col_idx = j % cols
                ax = grid.get_ax(row_idx, col_idx)
                data = metrics_data[full_metric_name]
                timestamps, results = calculate_quantiles(
                    data,
                    resolution,
                    count_only=is_drop,
                    global_start=global_start,
                    global_end=global_end,
                )
                if not timestamps or results is None:
                    continue

                apply_us_to_ms = not is_dimless and "Prob" not in full_metric_name
                if apply_us_to_ms:
                    results[50] = [x * 0.001 if not np.isnan(x) else x for x in results[50]]
                    results[95] = [x * 0.001 if not np.isnan(x) else x for x in results[95]]
                    results[99] = [x * 0.001 if not np.isnan(x) else x for x in results[99]]

                max_val = _ymax_quantiles(results)
                if max_val <= 0:
                    max_val = 1.0
                y_top = max_val * 1.2

                if is_err:
                    mins = []
                    for k in (50, 95, 99):
                        for x in results[k]:
                            if not np.isnan(x):
                                mins.append(float(x))
                    y_bottom = min(mins) * 1.2 if mins and min(mins) < 0 else 0.0
                else:
                    y_bottom = 0.0

                if (
                    "EMA" in full_metric_name
                    or full_metric_name.startswith("MA ")
                    or full_metric_name.startswith("TD-")
                    or "HIST" in full_metric_name
                    or is_limit
                    or "Local-RT" in full_metric_name
                    or "TwAvg" in full_metric_name
                    or "TimeMean" in full_metric_name
                    or is_err
                ):
                    pp.plot_scatter(ax, timestamps, results[50], label="P50", style=style, color_idx=1)
                    pp.plot_scatter(ax, timestamps, results[95], label="P95", style=style, color_idx=4)
                    pp.plot_scatter(ax, timestamps, results[99], label="P99", style=style, color_idx=0)
                else:
                    pp.plot_line(ax, timestamps, results[50], label="P50", style=style, color_idx=1)
                    pp.plot_line(ax, timestamps, results[95], label="P95", style=style, color_idx=4)
                    pp.plot_line(ax, timestamps, results[99], label="P99", style=style, color_idx=0)

                grid.configure_ax(
                    ax,
                    xlabel="Time (s)" if row_idx == rows - 1 else "",
                    ylabel=(
                        ("Err" if is_err else "Count")
                        if is_dimless
                        else "Lat (ms)"
                    )
                    if col_idx == 0
                    else "",
                    title=full_metric_name,
                    ylim=(y_bottom, y_top),
                )

            for k in range(len(chunk), plots_per_page):
                row_idx = k // cols
                col_idx = k % cols
                grid.get_ax(row_idx, col_idx).set_visible(False)

            grid.add_shared_legend(position="bottom")
            grid.fig.savefig(pdf, format="pdf", bbox_inches="tight")
            plt.close(grid.fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Plot NanoLog M# metric quantiles over time.")
    parser.add_argument("--files", nargs="+", type=Path, required=True)
    parser.add_argument("--resolution", type=float, default=0.2)
    parser.add_argument("--output", type=Path, default=Path("metrics.pdf"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    generate_nanolog_pdf(args.files, args.output, args.resolution)
    return 0


if __name__ == "__main__":
    sys.exit(main())
