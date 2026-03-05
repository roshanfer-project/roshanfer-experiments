#!/usr/bin/env python3
"""Find top pods by normalized CPU utilization (utilization/limit) from cpu_metrics.csv."""

import argparse
import csv
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Top pods by normalized CPU utilization")
    parser.add_argument("csv_path", type=Path, help="Path to cpu_metrics.csv")
    parser.add_argument("-n", "--top", type=int, default=20, help="Number of top pods to show")
    parser.add_argument("--metric", choices=["max", "avg"], default="max",
                       help="Use max or avg normalized utilization across samples")
    args = parser.parse_args()

    if not args.csv_path.exists():
        print(f"Error: {args.csv_path} not found", file=sys.stderr)
        sys.exit(1)

    # pod_key -> (util_vals, limit) - limit is same per pod
    pod_data: dict[str, tuple[list[float], float]] = {}

    with args.csv_path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                util = float(row["utilization"])
                limit = float(row["limit"])
            except (KeyError, ValueError):
                continue
            if limit <= 0:
                continue
            key = f"{row['namespace']}/{row['pod']}"
            if key not in pod_data:
                pod_data[key] = ([], limit)
            pod_data[key][0].append(util)

    if not pod_data:
        print("No valid data (need utilization, limit; skip limit<=0)", file=sys.stderr)
        sys.exit(1)

    if args.metric == "max":
        scores = [(k, max(v), lim) for k, (v, lim) in pod_data.items()]
    else:
        scores = [(k, sum(v) / len(v), lim) for k, (v, lim) in pod_data.items()]

    # sort by normalized util (util/limit)
    scores.sort(key=lambda x: -(x[1] / x[2]))
    top = scores[: args.top]

    print(f"Top {len(top)} pods by {args.metric} normalized CPU (util/limit):")
    print(f"{'pod':<55} {'util':>8} {'limit':>8} {'norm':>8}")
    print("-" * 82)
    for pod, util, limit in top:
        norm = util / limit
        print(f"{pod:<55} {util:>8.2f} {limit:>8.2f} {norm:>8.2f}")


if __name__ == "__main__":
    main()
