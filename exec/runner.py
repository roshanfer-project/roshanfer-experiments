"""Runner: executes a concrete RunUnit and produces raw artifacts.

Responsibilities:
1. Prepare environment for the run (start load generator, etc.).
2. Execute the run with provided parameters.
3. Persist raw outputs under unit_dir / config.raw_artifact_subdir.

Complex domain-specific logic (service orchestration, container mgmt, etc.) is left
to you and marked as TODO.
"""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import traceback
from typing import Any
import subprocess
import sys
import time

from .config import Config
from .models import RunUnit, RunResult
from .extractor import extract_detailed_metrics_from_output


class Runner:
	def __init__(self, config: Config):
		self.config = config
	
	def _prepare_microservice(self, unit: RunUnit, stdout_file: Any, stderr_file: Any) -> subprocess.Popen[bytes]:
		# Implement environment prep (e.g., docker-compose up, ensuring pods healthy, etc.)
		remote_cmd = f"cd {self.config.remote_microservice_path} && go run main.go"
		if unit.system != "":
			remote_cmd += f" --{unit.system}"
		
		# Add configurable execution arguments
		if unit.execution_args:
			remote_cmd += " " + " ".join(unit.execution_args)

		check_cmd = f"ssh {self.config.remote_microservice_user}@{self.config.remote_microservice_host} 'cat /tmp/{unit.bench.upper()}.ready'"
		
		run_remote_cmd = subprocess.Popen(
			["ssh", f"{self.config.remote_microservice_user}@{self.config.remote_microservice_host}", remote_cmd],
			stdout=stdout_file,
			stderr=stderr_file
		)
		time.sleep(10)
		
		ready = False
		for _ in range(self.config.default_retries):
			result = subprocess.run(check_cmd, shell=True, capture_output=True, text=True)
			if result.returncode == 0:
				ready = True
				break
			time.sleep(1)

		if ready is False:
			raise RuntimeError(f"Microservice did not become ready after retries, result={result}")

		return run_remote_cmd

	def _clear_microservice(self, unit: RunUnit) -> None:
		remote_cmd = f"cd {self.config.remote_microservice_path} && ./clean.sh"
		
		# Add configurable cleanup arguments
		if unit.cleanup_args:
			remote_cmd += " " + " ".join(unit.cleanup_args)
		
		subprocess.run(
			["ssh", f"{self.config.remote_microservice_user}@{self.config.remote_microservice_host}", remote_cmd],
			check=True,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL
		)

	def _api_list(self, apis: list[str]) -> str:
		if len(apis) > 1:
			return ",".join(apis)
		return apis[0]

	def _get_version_from_system(self, system: str) -> str:
		"""Determine HTTP version based on system type."""
		if system in ("plain", "sidecar", "envoy"):
			return "1"
		else:
			return "2"

	def _get_slo_for_api(self, api: str) -> str:
		"""Get SLO threshold for API from config, with fallback to default."""
		return str(self.config.slos.get(api, 100))

	def _build_wrapper_command(self, unit: RunUnit, protocol: str, output_dir: str) -> list[str]:
		"""Build wrapper script command for workload generation."""
		# Determine which wrapper script to use
		if len(unit.apis) == 1:
			wrapper_script = f"./wrapper/{unit.bench}/run.sh"
		else:
			wrapper_script = f"./wrapper/{unit.bench}/run-multi-api.sh"
		
		# Build command arguments
		cmd = [wrapper_script, protocol, str(unit.base), str(unit.rate), str(unit.duration)]
		
		# Add API(s) - single API or comma-separated list
		if len(unit.apis) == 1:
			cmd.append(unit.apis[0])
		else:
			cmd.append(",".join(unit.apis))
		
		# Add output directory
		cmd.append(output_dir)
		
		return cmd

	def run(self, unit: RunUnit, unit_dir: Path) -> RunResult:
		start = time.time()
		raw_dir = unit_dir / self.config.raw_artifact_subdir
		raw_dir.mkdir(parents=True, exist_ok=True)
		output_dir = unit_dir / "output"
		output_dir.mkdir(parents=True, exist_ok=True)
		
		self._clear_microservice(unit)
		time.sleep(1)

		stdout_file = (raw_dir / "microservice_stdout.txt").open("w")
		stderr_file = (raw_dir / "microservice_stderr.txt").open("w")

		microservice_cmd = self._prepare_microservice(unit, stdout_file, stderr_file)

		# Use RWG for workload generation instead of K6 scripts
		details: dict[str, Any] = {"started_at": start}
		status = "success"
		start_timestamp = ""
		end_timestamp = ""
		
		try:
			params_path = raw_dir / "unit_runner.json"
			with params_path.open("w") as f:
				json.dump(unit.to_dict(), f, indent=2)
			
			# Determine protocol and version
			http_type = "http" if unit.system in ("sidecar", "sidecar-queue", "plain", "envoy") else "grpc"
			version = self._get_version_from_system(unit.system)
			
			# Build wrapper script command
			cmd = self._build_wrapper_command(unit, http_type, str(output_dir))
			
			# Execute wrapper script
			cmd_path = os.path.join(os.path.dirname(__file__), self.config.git_root)
			result = subprocess.run(cmd, capture_output=True, text=True, cwd=cmd_path)
			details["returncode"] = result.returncode
			details["stdout_tail"] = result.stdout[-10_000:]
			details["stderr_tail"] = result.stderr[-10_000:]
			(raw_dir / "wrapper_stdout.txt").write_text(result.stdout)
			(raw_dir / "wrapper_stderr.txt").write_text(result.stderr)
			
			if result.returncode != 0:
				status = "error"
			else:
				# Extract metrics from RWG output files (one per API)
				combined_metrics = {}
				api_metrics = {}
				
				for api in unit.apis:
					output_file = output_dir / f"out-{api}.csv"
					if os.path.exists(str(output_file)):
						try:
							# Get SLO for this specific API
							slo = self._get_slo_for_api(api)
							metrics = extract_detailed_metrics_from_output(str(output_file), slo, version, self.config.rwg_binary_path)
							api_metrics[api] = metrics
							
							# Aggregate metrics (sum goodput, max latency, sum errors)
							if not combined_metrics:
								combined_metrics = metrics.copy()
							else:
								combined_metrics["goodput"] = combined_metrics.get("goodput", 0) + metrics.get("goodput", 0)
								combined_metrics["p95_latency"] = max(combined_metrics.get("p95_latency", 0), metrics.get("p95_latency", 0))
								combined_metrics["num_errors"] = combined_metrics.get("num_errors", 0) + metrics.get("num_errors", 0)
						except Exception as e:
							details[f"metrics_extraction_error_{api}"] = str(e)
					else:
						details[f"missing_output_file_{api}"] = str(output_file)
				
				# Store both individual API metrics and combined metrics
				details["api_metrics"] = api_metrics
				details["rwg_metrics"] = combined_metrics
				details["goodput"] = combined_metrics.get("goodput", 0)
				details["p95_latency"] = combined_metrics.get("p95_latency", 0)
				details["num_errors"] = combined_metrics.get("num_errors", 0)

			# Record timestamps (RWG handles timing internally)
			start_timestamp = datetime.fromtimestamp(start)
			end_timestamp = datetime.fromtimestamp(time.time())

		except Exception as e:  # noqa: BLE001
			status = "error"
			details["exception"] = str(e.__repr__())
			details["traceback"] = traceback.format_exc()
		
		time.sleep(1)
		microservice_cmd.terminate()
		stdout_file.close()
		stderr_file.close()

		details["ended_at"] = time.time()
		details["duration_sec"] = details["ended_at"] - start
		# Persist run metadata
		(raw_dir / "run_details.json").write_text(json.dumps(details, indent=2))
		
		# Determine output file path(s) - for multiple APIs, we have multiple files
		if len(unit.apis) == 1:
			output_file_path = str(output_dir / f"out-{unit.apis[0]}.csv")
		else:
			# For multiple APIs, store the directory path since we have multiple output files
			output_file_path = str(output_dir)
		
		return RunResult(
			unit_name=unit.name,
			status=status,
			raw_artifact_dir=str(raw_dir),
			details=details,
			start_timestamp=start_timestamp,
			end_timestamp=end_timestamp,
			output_file=output_file_path
		)

