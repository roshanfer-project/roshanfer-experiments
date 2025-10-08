"""Generate latency & rate vs time figures for single‑API latency-and-rate-vs-time experiments.

Usage (example):
  python -m experiments.exec.plots.latency_rate_vs_time --experiment-index 001 \
      --experiment-name latency-and-rate-vs-time-hotel-1-sidecar \
      --output-dir generated_plots

Assumptions:
  - Executor persisted runs under <output_base_dir>/exp-<experiment_index>/<experiment_name>/<unit_name>/repeat_xxx
  - Each repeat raw dir contains run_details.json and optionally metrics JSON results under metrics/.
  - start_timestamp / end_timestamp captured in RunResult were written to run_summary.jsonl (future enhancement: read those).
  - For simplicity we derive times from metrics query results when available.

Current implementation:
  - Collects all metrics JSON files for the chosen experiment units (matching name prefix with load variants) and builds
    a combined time series for goodput, slo_violation, dropped, dropped_in (if present).
  - Plots stacked area (KRPS) and latency percentiles (if latency_p50 / latency_p95 present) vs relative seconds.

Future enhancements:
  - Aggregate across repeats (mean/CI) instead of plotting each repeat.
  - Support multi-API experiments.
  - Integrate SLO threshold lines configurable from config/report settings.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple
import pandas as pd

from experiments.exec.plots.common import (
    RATE_KEYS_ORDER as METRIC_RATE_KEYS,
    LATENCY_KEYS_ORDER as LATENCY_KEYS,
    extract_series,
    plot_rate_stack,
    plot_latency_lines,
)


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
            # Expect structure {query: ..., result: [...]}
            res = data.get("result")
            if isinstance(res, list):
                out[fp.stem] = data
        except Exception:
            pass
    return out


def _extract_series(result_entry: dict) -> Tuple[List[float], List[float]]:  # backward compat shim
    return extract_series(result_entry)


def _aggregate_units(all_units: List[Path]) -> Dict[str, pd.DataFrame]:
    rate_frames: List[pd.DataFrame] = []
    latency_frames: List[pd.DataFrame] = []
    for unit_dir in all_units:
        metric_files = _load_metric_files(unit_dir)
        for key in METRIC_RATE_KEYS + LATENCY_KEYS:
            # Matching by prefix (query names may include _api suffix) e.g., goodput_search-hotel
            matching = [name for name in metric_files.keys() if name.startswith(key)]
            for name in matching:
                times, vals = _extract_series(metric_files[name])
                if not times:
                    continue
                rel0 = times[0]
                rel_times = [t - rel0 for t in times]
                df = pd.DataFrame({"t": rel_times, "value": vals, "metric": key, "unit_dir": str(unit_dir)})
                if key in METRIC_RATE_KEYS:
                    rate_frames.append(df)
                else:
                    latency_frames.append(df)
    result: Dict[str, pd.DataFrame] = {}
    if rate_frames:
        rf = pd.concat(rate_frames)
        # group by metric and time (rounded) averaging across repeats
        rf['t_round'] = rf['t'].round(3)
        rate_agg = rf.groupby(['metric','t_round']).value.mean().reset_index().rename(columns={'t_round':'t'})
        result['rate'] = rate_agg
    if latency_frames:
        lf = pd.concat(latency_frames)
        lf['t_round'] = lf['t'].round(3)
        lat_agg = lf.groupby(['metric','t_round']).value.mean().reset_index().rename(columns={'t_round':'t'})
        result['latency'] = lat_agg
    return result


def generate(experiment_index: str, experiments_root: Path, experiment_name: str, output_dir: Path) -> Path:
    base_dir = experiments_root / f"exp-{experiment_index}" / experiment_name
    if not base_dir.exists():
        raise FileNotFoundError(f"Experiment directory not found: {base_dir}")
    # Find unit variant directories (directories directly under experiment_name)
    unit_dirs: List[Path] = [p for p in base_dir.iterdir() if p.is_dir()]
    # Each unit dir contains repeat_XXX folders; we gather all of them.
    all_repeat_dirs: List[Path] = []
    for ud in unit_dirs:
        all_repeat_dirs.extend([p for p in ud.glob('repeat_*') if p.is_dir()])
    agg = _aggregate_units(all_repeat_dirs)
    # Print maximum value of every time series for debugging
    for key, df in agg.items():
        print(f"Max values for {key}:")
        for metric in df['metric'].unique():
            max_val = df[df['metric'] == metric]['value'].max()
            print(f"  {metric}: {max_val}")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Plot rate stack (convert aggregate pivot style to long-form expected by plot_rate_stack)
    if 'rate' in agg and not agg['rate'].empty:
        rate_df = agg['rate'].copy()
        # rate_df columns: metric, t, value (RPS). Need KRPS & relative time (already relative)
        rate_df['t_rel'] = rate_df['t']
        rate_df['value_krps'] = rate_df['value'] / 1000.0
        plot_rate_stack(rate_df.rename(columns={'t': 't_rel'}), output_dir / 'rate_vs_time.pdf', time_col='t_rel', value_col='value_krps')
    # Plot latency lines
    if 'latency' in agg and not agg['latency'].empty:
        lat_df = agg['latency'].copy()
        lat_df['t_rel'] = lat_df['t']
        plot_latency_lines(lat_df.rename(columns={'t': 't_rel'}), output_dir / 'latency_vs_time.pdf', time_col='t_rel', value_col='value')
    return output_dir


def parse_args(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--experiment-index', required=True)
    p.add_argument('--experiment-name', required=True)
    p.add_argument('--output-dir', default='generated_plots')
    p.add_argument('--experiments-root', default='experiment_runs')
    return p.parse_args(argv)


def main(argv=None):
    ns = parse_args(argv)
    out = Path(ns.output_dir) / ns.experiment_name
    generate(ns.experiment_index, Path(ns.experiments_root), ns.experiment_name, out)
    print(f"Plots written to {out}")

if __name__ == '__main__':  # pragma: no cover
    main()
