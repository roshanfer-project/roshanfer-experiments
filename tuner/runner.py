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

class Runner:
	def __init__(self, config: Config, env_fields: dict[str, str]):
		self.config = config
		self.env_fields = env_fields
	
	def _write_env_fields(self):
		# create a file named env-setter.env with env fields and then send it to the remote host
		with open("env-setter.env", "w") as f:
			for key, value in self.env_fields.items():
				f.write(f"{key}={value}\n")

		subprocess.run(
			["scp",
			 "env-setter.env",
			f"{self.config.remote_microservice_user}@{self.config.remote_microservice_host}:{self.config.remote_microservice_path}/.."],
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL
		)

	def _prepare_microservice(self, unit: RunUnit) -> subprocess.Popen[bytes]:
		# Implement environment prep (e.g., docker-compose up, ensuring pods healthy, etc.)
		self._write_env_fields()
	
		remote_cmd = f"cd {self.config.remote_microservice_path} && go run main.go"
		if unit.system != "":
			remote_cmd += f" --{unit.system}"

		check_cmd = f"ssh {self.config.remote_microservice_user}@{self.config.remote_microservice_host} 'cat /tmp/{unit.bench.upper()}.ready'"
		
		run_remote_cmd = subprocess.Popen(
			["ssh", f"{self.config.remote_microservice_user}@{self.config.remote_microservice_host}", remote_cmd],
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL
		)
		time.sleep(15)
		
		ready = False
		for _ in range(self.config.default_retries):
			result = subprocess.run(check_cmd, shell=True, capture_output=True, text=True)
			if result.returncode == 0:
				ready = True
				break
			time.sleep(2)

		if ready is False:
			raise RuntimeError(f"Microservice did not become ready after retries, result={result}")

		time.sleep(5)

		return run_remote_cmd

	def _clear_microservice(self, unit: RunUnit) -> None:
		env_cmd = f"cd {self.config.remote_microservice_path}/.. && rm -f env-setter.env"
		remote_cmd = f"cd {self.config.remote_microservice_path} && ./clean.sh"
		subprocess.run(
			["ssh", f"{self.config.remote_microservice_user}@{self.config.remote_microservice_host}", remote_cmd],
			check=True,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL
		)
		subprocess.run(
			["ssh", f"{self.config.remote_microservice_user}@{self.config.remote_microservice_host}", env_cmd],
			check=True,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL
		)

	def _api_list(self, apis: list[str]) -> str:
		if len(apis) > 1:
			return ",".join(apis)
		return apis[0]

	def run(self, unit: RunUnit, unit_dir: Path) -> RunResult:
		start = time.time()
		raw_dir = unit_dir / self.config.raw_artifact_subdir
		raw_dir.mkdir(parents=True, exist_ok=True)
		output_dir = unit_dir / "output"
		output_dir.mkdir(parents=True, exist_ok=True)
		
		self._clear_microservice(unit)
		time.sleep(1)
		microservice_cmd = self._prepare_microservice(unit)

		# Simple illustrative execution strategy:
		# If unit.script points to a Python script, we call it via subprocess with JSON params.
		details: dict[str, Any] = {"started_at": start}
		status = "success"
		start_timestamp = ""
		end_timestamp = ""
		if unit.script:
			try:
				params_path = raw_dir / "unit_runner.json"
				with params_path.open("w") as f:
					json.dump(unit.to_dict(), f, indent=2)
				
				cmd_path = os.path.join(os.path.dirname(__file__), self.config.git_root, self.config.k6_scripts_root)
				cmd = ["./" + unit.script]

				http_type = "http" if unit.system in ("sidecar", "sidecar-queue", "plain", "envoy") else "grpc"
				#duration = f"{int(unit.duration)}s"
				cmd += [
					http_type,
					str(unit.base),
					str(unit.rate),
					str(unit.duration),
					self._api_list(unit.apis),
					str(output_dir),
				]
				# Execute script
				result = subprocess.run(cmd, capture_output=True, text=True, cwd=cmd_path)
				details["returncode"] = result.returncode
				details["stdout_tail"] = result.stdout[-10_000:]
				details["stderr_tail"] = result.stderr[-10_000:]
				(raw_dir / "script_stdout.txt").write_text(result.stdout)
				(raw_dir / "script_stderr.txt").write_text(result.stderr)
				if result.returncode != 0:
					status = "error"			

				with open(os.path.join(unit_dir, "timestamps.csv"), "r") as file:
					timestamps = file.readlines()
					start_timestamp = datetime.fromtimestamp(float(timestamps[0].strip()))
					end_timestamp = datetime.fromtimestamp(float(timestamps[1].strip()))

			except Exception as e:  # noqa: BLE001
				status = "error"
				details["exception"] = str(e.__repr__())
				details["traceback"] = traceback.format_exc()
		else:
			# Placeholder for non-script experiment types (e.g., direct load generator invocation).
			# TODO: implement logic based on unit.type and unit.params
			details["note"] = "No script provided; custom execution logic required (TODO)."
		
		time.sleep(1)
		microservice_cmd.terminate()

		details["ended_at"] = time.time()
		details["duration_sec"] = details["ended_at"] - start
		# Persist run metadata
		(raw_dir / "run_details.json").write_text(json.dumps(details, indent=2))
		return RunResult(
			unit_name=unit.name,
			status=status,
			raw_artifact_dir=str(raw_dir),
			details=details,
			start_timestamp=start_timestamp,
			end_timestamp=end_timestamp,
			output_file=str(output_dir / f"out-{self._api_list(unit.apis)}.csv")
		)
	
index = 0

def run_experiment(bench: str, system: str, api: str, env_fields: dict[str, str]):
	# Example usage
	config = Config()
	base_path = "/home/farzad/files"
	config.remote_microservice_path = f"{base_path}/benchmarks/{bench}/exec"
	config.k6_scripts_root = f"wrapper/{bench}"
	runner = Runner(config, env_fields)

	if bench == "social":
		base = 1000
		rate = 6000
	elif bench == "hotel":
		base = 3000
		rate = 8000
	unit = RunUnit(name="rajomon_tune", script="run.sh", system=system, duration=10, base=base, rate=rate, apis=[api],
				bench=bench, type="latency-and-rate-vs-time")
	global index
	unit_dir = Path(os.getcwd()) / f"tuner_output/{index}"
	index += 1
	result = runner.run(unit, unit_dir)
	return result.output_file