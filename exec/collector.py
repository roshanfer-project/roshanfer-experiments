"""Collector module.

Scaffolding to collect metrics after a RunUnit execution. The design goals:
1. Parameterized entirely by Config (no hard-coded IPs / queries here).
2. Append-only: every collect writes new files; never overwrites previous runs.
3. Simple health checks based on RWG output files.

High-level flow in collect():
  a. Validate that overall-{api}.json files exist for each API
  b. Check num_errors >= 1 (raise exception if health check fails)
  c. For "latency-and-rate-vs-time" experiments: generate realtime CSV reports
  d. Build a lightweight summary index
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Any
import json
import subprocess
import os

from .config import Config
from .models import RunUnit, RunResult, CollectorResult


class Collector:
	"""Collects metrics for a completed run.

	Attributes:
		config: Global configuration.
	"""

	def __init__(self, config: Config):
		self.config = config

	# ------------------------------------------------------------------
	# Public API
	# ------------------------------------------------------------------
	def collect(self, unit: RunUnit, run_result: RunResult, unit_dir: Path) -> CollectorResult:
		"""Collect metrics for a single run unit.

		Returns a CollectorResult listing produced metric files.
		Raises exceptions on health check failures.
		"""
		output_dir = unit_dir / "output"
		
		# Build index for summary
		index: Dict[str, Any] = {
			"unit_name": unit.name,
			"apis": unit.apis,
			"health_checks": {},
			"realtime_reports": {},
			"notes": "",
		}

		metric_files: List[str] = []

		# Health check: verify overall-{api}.json exists and has no errors
		self._evaluate_health(unit, output_dir, index)

		# Generate realtime reports for specific experiment types
		if unit.type == "latency-and-rate-vs-time":
			self._generate_realtime_reports(unit, run_result, output_dir, index, metric_files)

		# Persist index file
		metrics_dir = unit_dir / self.config.metrics_subdir
		metrics_dir.mkdir(parents=True, exist_ok=True)
		(metrics_dir / "_index.json").write_text(json.dumps(index, indent=2))

		return CollectorResult(
			unit_name=unit.name,
			metrics_dir=str(metrics_dir),
			metrics_files=metric_files,
			notes="",
		)

	# ------------------------------------------------------------------
	# Helper / internal
	# ------------------------------------------------------------------
	def _evaluate_health(self, unit: RunUnit, output_dir: Path, index: Dict[str, Any]) -> None:
		"""Health check: verify overall-{api}.json files exist and have num_errors < 1.

		Raises:
			Exception: If any overall-{api}.json is missing or has errors
		"""
		for api in unit.apis:
			overall_file = output_dir / f"overall-{api}.json"
			
			if not overall_file.exists():
				raise Exception(f"Health check failed: overall-{api}.json not found at {overall_file}")
			
			# Load and check for errors
			with open(overall_file, "r") as f:
				data = json.load(f)
			
			num_errors = data.get("num_errors", 0)
			if num_errors >= 1:
				raise Exception(f"Health check failed for {api}: num_errors={num_errors} (expected < 1)")
			
			# Record success
			index["health_checks"][api] = {
				"status": "passed",
				"num_errors": num_errors,
				"overall_file": str(overall_file),
			}

	def _generate_realtime_reports(
		self,
		unit: RunUnit,
		run_result: RunResult,
		output_dir: Path,
		index: Dict[str, Any],
		metric_files: List[str]
	) -> None:
		"""Generate realtime CSV reports for each API using rwg parse.

		Args:
			unit: The run unit configuration
			run_result: The result from the runner
			output_dir: Directory containing RWG output files
			index: Index dict to update with realtime report info
			metric_files: List to append generated file paths
		"""
		# Validate collector_freq is set
		if unit.collector_freq <= 0:
			raise Exception(
				f"collector_freq must be set for experiment type '{unit.type}' but got {unit.collector_freq}"
			)

		# Determine HTTP version from system
		version = self._get_version_from_system(unit.system)

		for api in unit.apis:
			# Input: out-{api}.csv
			rwg_output = output_dir / f"out-{api}.csv"
			if not rwg_output.exists():
				index["realtime_reports"][api] = {"error": f"Missing input file: {rwg_output}"}
				continue

			# Output: realtime-{api}.csv
			realtime_output = output_dir / f"realtime-{api}.csv"

			# Get SLO for this API
			slo = str(self.config.slos.get(api, 100))

			try:
				# Run rwg parse with realtime_output flag
				result = subprocess.run([
					self.config.rwg_binary_path, "parse",
					"--rwg_output", str(rwg_output),
					"--slo", slo,
					"--version", version,
					"--realtime_output", str(realtime_output),
					"--freq", str(unit.collector_freq),
				],
				capture_output=True,
				text=True)

				if result.returncode != 0:
					error_msg = f"rwg parse failed with exit code {result.returncode}"
					if result.stderr:
						error_msg += f"\nStderr: {result.stderr.strip()}"
					index["realtime_reports"][api] = {"error": error_msg}
					continue

				if not realtime_output.exists():
					index["realtime_reports"][api] = {"error": f"rwg parse did not generate {realtime_output}"}
					continue

				# Success
				metric_files.append(str(realtime_output))
				index["realtime_reports"][api] = {
					"status": "success",
					"file": str(realtime_output),
					"freq_ms": unit.collector_freq,
				}

			except Exception as e:
				index["realtime_reports"][api] = {"error": str(e.__repr__())}

	def _get_version_from_system(self, system: str) -> str:
		"""Determine HTTP version based on system type."""
		if system in ("plain", "sidecar"):
			return "1"
		else:
			return "2"
