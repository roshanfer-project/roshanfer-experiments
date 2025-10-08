"""High-level experiment executor.

Responsibilities:
1. Read high-level experiment selection file (JSON) provided by user.
2. Expand any composite experiments into concrete run units (delegated to _expand_experiment - placeholder for you).
3. For each run unit: invoke runner.run(), then collector.collect().
4. Persist raw artifacts + collected metrics under a timestamped root directory so prior runs are never overwritten.
5. Append (never overwrite) a machine-readable summary (CSV + JSON) and generate a human readable report.md.

You can instruct the executor via CLI:
	python -m experiments.exec.executor --experiments-file path/to/experiments.json [--config path/to/config.json]

The experiments file format (example):
{
  "experiments": [
	{
	  "name": "latency-vs-load basic",
	  "type": "latency-vs-load",
	  "script": "experiments/latency-vs-load/run.py",  # optional explicit script path
	  "params": {"loads": [50,100,150], "duration_sec": 60}
	}
  ]
}

Config file format is defined in config.py (see Config dataclass docstring).

NOTE: Many domain-specific details (starting services, seeding data, etc.) are left as TODO placeholders for you to implement.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime
from typing import Dict, Iterable, List, Sequence

from .config import load_config, Config
from .models import ExperimentConfig, RunUnit, RunResult, CollectorResult
from .runner import Runner
from .collector import Collector
import traceback as tb
from . import report as report_module  # Will create lazily if absent (placeholder generation below)

# Retain timestamp function (may still be useful for file naming inside repeats if desired)
def _timestamp() -> str:
	return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def _load_experiments_file(path: Path) -> List[ExperimentConfig]:
	with path.open() as f:
		data = json.load(f)
	exps_raw = data.get("experiments", [])
	exps: List[ExperimentConfig] = []
	for idx, raw in enumerate(exps_raw):
		try:
			exps.append(ExperimentConfig.from_dict(raw))
		except Exception as e:  # noqa: BLE001
			print(f"Skipping experiment index {idx} due to parse error: {e}", file=sys.stderr)
	return exps


def _expand_experiment(exp: ExperimentConfig) -> Iterable[RunUnit]:
	"""Turn a high-level experiment into concrete RunUnit(s).

	CURRENT IMPLEMENTATION: minimal pass-through producing a single RunUnit.
	PLACEHOLDER: For complex experiments (e.g., latency-vs-load with multiple load levels),
	you likely want to expand based on exp.params (e.g., for each load in params["loads"],
	yield a RunUnit with that single load). Implement that custom logic here.
	"""
	for load in range(exp.loads.start, exp.loads.end + 1, exp.loads.step):
		# Distinguish each load variant so repeats for a single load sit under its own folder.
		variant_name = f"{exp.name}-rate-{load}"
		yield RunUnit(
			name=variant_name,
			type=exp.type,
			script=exp.script,
			base=exp.base_rate,
			rate=load,
			duration=exp.duration,
			system=exp.system,
			apis=exp.apis,
			services=exp.services,
			metadata={},
			bench=exp.bench,
			collector_range=exp.collector_range,
			collector_step=exp.collector_step,
			repeats=exp.repeat,
		)


def _ensure_dir(path: Path) -> None:
	path.mkdir(parents=True, exist_ok=True)


def _append_jsonl(path: Path, obj: Dict) -> None:
	with path.open("a") as f:
		f.write(json.dumps(obj) + "\n")


def _append_csv(path: Path, headers: List[str], row: Dict) -> None:
	file_exists = path.exists()
	with path.open("a", newline="") as f:
		writer = csv.DictWriter(f, fieldnames=headers)
		if not file_exists:
			writer.writeheader()
		writer.writerow({k: row.get(k, "") for k in headers})


def _filter_experiments(exps: List[ExperimentConfig], only_names: Sequence[str] | None, only_types: Sequence[str] | None, name_contains: Sequence[str] | None) -> List[ExperimentConfig]:
	if not exps:
		return exps
	filtered = []
	for e in exps:
		if only_names and e.name not in only_names:
			continue
		if only_types and e.type not in only_types:
			continue
		if name_contains and not any(substr in e.name for substr in name_contains):
			continue
		filtered.append(e)
	return filtered


def execute(experiments_file: Path, config: Config, only_names: Sequence[str] | None = None, only_types: Sequence[str] | None = None, name_contains: Sequence[str] | None = None) -> int:
	start_all = time.time()
	all_exps = _load_experiments_file(experiments_file)
	if not all_exps:
		print("No experiments to run.")
		return 0

	# Apply filters if provided
	pre_count = len(all_exps)
	all_exps = _filter_experiments(all_exps, only_names, only_types, name_contains)
	if not all_exps:
		print(f"No experiments match filters (loaded {pre_count}).")
		return 0
	if pre_count != len(all_exps):
		print(f"Selected {len(all_exps)} of {pre_count} experiments after filtering.")

	# Use user-provided experiment_index instead of timestamp; append if already exists.
	run_root = Path(config.output_base_dir) / f"exp-{config.experiment_index}"
	_ensure_dir(run_root)
	print(f"Run root (experiment index): {run_root}")

	runner = Runner(config)
	collector = Collector(config)

	summary_csv = run_root / "run_summary.csv"
	summary_jsonl = run_root / "run_summary.jsonl"

	headers = [
		"experiment_name","run_unit_name","type","status","start_time","end_time","duration_sec","artifact_dir","notes"
	]

	run_results: List[RunResult] = []

	# Helper for safe experiment directory names
	import re as _re  # local import to avoid polluting module namespace
	def _safe(name: str) -> str:
		return _re.sub(r"[^A-Za-z0-9_.-]", "_", name)

	for exp in all_exps:
		experiment_dir = run_root / _safe(exp.name)
		_ensure_dir(experiment_dir)
		for unit in _expand_experiment(exp):
			# Determine repeats for this unit. Priority: unit['repeat'] -> experiment_defaults -> 1
			repeats = (
				unit.repeats
				or config.experiment_defaults.get(unit.type, {}).get("repeat")
				or 1
			)
			base_group_name = unit.safe_name()
			unit_group_dir = experiment_dir / base_group_name
			_ensure_dir(unit_group_dir)
			# Determine existing repeats for continuation
			existing_repeat_dirs = sorted([p for p in unit_group_dir.glob("repeat_*") if p.is_dir()])
			next_index = len(existing_repeat_dirs)
			# Execute new repeats starting from next_index
			for offset, r in enumerate(range(next_index, next_index + int(repeats))):
				unit_start = time.time()
				repeat_dir = unit_group_dir / f"repeat_{r:03d}"
				_ensure_dir(repeat_dir)
				status = "pending"
				notes = ""
				traceback = ""
				run_result: RunResult | None = None
				collector_result: CollectorResult | None = None
				try:
					# Pass repeat info in metadata if runner wants to adapt behavior (offset is relative index this session)
					unit.metadata["repeat_index"] = r
					unit.metadata["repeat_index_session"] = offset
					unit.metadata["total_repeats_requested"] = repeats
					run_result = runner.run(unit, repeat_dir)
					if run_result:
						run_result.repeat_index = r
						run_result.total_repeats = int(repeats) + next_index  # total after this batch finishes
						run_result.group_name = base_group_name
					collector_result = collector.collect(unit, run_result, repeat_dir) if run_result else None
					status = "success" if run_result and run_result.status == "success" else run_result.status if run_result else "error"
					runner._clear_microservice(unit)
				except Exception as e:  # noqa: BLE001
					status = "error"
					if run_result:
						run_result.status = "error"
					notes = str(e.__repr__())
					traceback = tb.format_exc()
				finally:
					end = time.time()
					row = {
						"experiment_name": exp.name,
						"experiment_dir": str(experiment_dir),
						"run_unit_name": unit.name,
						"type": unit.type,
						"apis": unit.apis,
						"status": status,
						"repeat_index": r,
						"total_repeats": int(repeats) + next_index,
						"group_name": base_group_name,
						"start_time": datetime.utcfromtimestamp(unit_start).isoformat(),
						"end_time": datetime.utcfromtimestamp(end).isoformat(),
						"duration_sec": f"{end - unit_start:.2f}",
						"artifact_dir": str(repeat_dir),
						"notes": notes,
						"traceback": traceback,
					}
					for h in ["repeat_index", "total_repeats", "group_name", "experiment_dir"]:
						if h not in headers:
							headers.append(h)
					_append_csv(summary_csv, headers, row)
					_append_jsonl(summary_jsonl, {**row, "run_result": run_result.to_dict() if run_result else None, "collector_result": collector_result.to_dict() if collector_result else None})
					if run_result:
						run_results.append(run_result)

	total_duration = time.time() - start_all
	# Generate report (placeholder implementation inside report module).
	try:
		report_module.generate_report(run_root, all_exps, run_results, total_duration)
	except Exception as e:  # noqa: BLE001
		print(f"Report generation failed: {e.__repr__()}", file=sys.stderr)

	print(f"All done in {total_duration:.1f}s. Summary at {summary_csv}")
	return 0


def parse_args(argv: List[str]) -> argparse.Namespace:
	p = argparse.ArgumentParser(description="Experiment Executor")
	p.add_argument("--experiments-file", required=True, help="Path to experiments.json file")
	p.add_argument("--config", required=False, help="Path to config.json file (overrides defaults)")
	p.add_argument("--only-names", help="Comma-separated exact experiment names to run")
	p.add_argument("--only-types", help="Comma-separated experiment types to run")
	p.add_argument("--name-contains", help="Comma-separated substrings; experiment name must contain at least one")
	return p.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
	ns = parse_args(argv or sys.argv[1:])
	config = load_config(ns.config)
	split = lambda v: [s for s in v.split(",") if s] if v else None  # noqa: E731
	return execute(
		Path(ns.experiments_file),
		config,
		only_names=split(getattr(ns, "only_names", None)),
		only_types=split(getattr(ns, "only_types", None)),
		name_contains=split(getattr(ns, "name_contains", None)),
	)


if __name__ == "__main__":  # pragma: no cover
	raise SystemExit(main())

