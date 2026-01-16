"""Collector module.

Responsibilities:
1. Collect Generic Service Logs (via collect_logs.sh).
2. Parse RWG Output (CSV -> JSON) locally (using rwg binary).
3. Organize Metrics for Plotting (ensure JSONs in metrics/).
"""

from __future__ import annotations

import json
import subprocess
import shutil
import os
from pathlib import Path
from typing import Any, Dict, List
import logging

from .config import Config
from .models import RunUnit, RunResult, CollectorResult


class Collector:
    def __init__(self, config: Config):
        self.config = config

    def collect(self, unit: RunUnit, run_result: RunResult, unit_dir: Path) -> CollectorResult:
        output_dir = unit_dir / "output"
        raw_dir = unit_dir / self.config.raw_artifact_subdir
        metrics_dir = unit_dir / self.config.metrics_subdir
        
        metrics_dir.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)

        index: Dict[str, Any] = {
            "unit_name": unit.name,
            "apis": unit.apis,
            "reports": {},
            "notes": "",
        }
        metric_files: List[str] = []

        # 1. Collect Service Logs
        self._collect_service_logs(unit, raw_dir)

        # 2. Generate/Validate JSON Reports from CSV (Local processing)
        # We assume Runner has already pulled out-{api}.csv to output_dir
        self._generate_reports(unit, output_dir, index, metric_files)

        # 3. Copy/Link Metrics for Plot Runner
        self._copy_metrics_for_plotting(output_dir, metrics_dir)

        # Persist Index
        (metrics_dir / "_index.json").write_text(json.dumps(index, indent=2))

        return CollectorResult(
            unit_name=unit.name,
            metrics_dir=str(metrics_dir),
            metrics_files=metric_files,
            notes="",
        )

    def _collect_service_logs(self, unit: RunUnit, raw_dir: Path):
        """Invoke benchmarks/<bench>/collect_logs.sh to gather logs."""
        script_path = Path("benchmarks") / unit.bench / "collect_logs.sh"
        if not script_path.exists():
            logging.warning(f"No collect_logs.sh for {unit.bench}, skipping service log collection.")
            return

        # Pass context
        env = os.environ.copy()
        env["DEPLOYMENT_HOSTS"] = ",".join(unit.deployment_hosts)
        env["OUTPUT_DIR"] = str(raw_dir / "service_logs")
        env["SYSTEM"] = unit.system
        
        # Create subfolder
        (raw_dir / "service_logs").mkdir(parents=True, exist_ok=True)

        try:
            logging.info(f"Collecting service logs for {unit.bench}...")
            subprocess.run([str(script_path)], env=env, check=False)
        except Exception as e:
            logging.error(f"Failed to collect logs: {e}")

    def _generate_reports(self, unit: RunUnit, output_dir: Path, index: Dict[str, Any], metric_files: List[str]):
        """Runs `rwg parse` to generate overall.json and realtime.csv."""
        version = "1" if unit.system in ("plain", "sidecar", "envoy") else "2"
        # Determine version more robustly if needed, but this matches legacy.

        for api in unit.apis:
            rwg_output = output_dir / f"out-{api}.csv"
            if not rwg_output.exists():
                index["reports"][api] = {"status": "missing_csv", "file": str(rwg_output)}
                continue
            
            # Overall Report
            overall_json = output_dir / f"overall-{api}.json"
            slo = str(self.config.slos.get(api, 100))
            
            # 1. Overall Report
            cmd_overall = [
                self.config.rwg_binary_path, "parse",
                "--rwg_output", str(rwg_output),
                "--overall_output", str(overall_json),
                "--slo", slo,
                "--version", version,
                "--warmup", str(unit.warmup),
                "--cooldown", str(unit.cooldown),
            ]

            # 2. Realtime Report
            freq = unit.collector_freq if unit.collector_freq > 0 else 100 # Default 100ms
            realtime_csv = output_dir / f"realtime-{api}.csv"
            cmd_realtime = [
                self.config.rwg_binary_path, "parse",
                "--rwg_output", str(rwg_output),
                "--realtime_output", str(realtime_csv), 
                "--freq", str(freq),
                "--slo", slo,
                "--version", version,
                "--warmup", str(unit.warmup),
                "--cooldown", str(unit.cooldown),
            ]

            try:
                # Use venv for python execution
                env = os.environ.copy()
                venv_bin = str((Path("rwg") / ".venv" / "bin").resolve())
                env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"
                
                # Execute Overall
                subprocess.run(cmd_overall, capture_output=True, check=True, env=env)
                metric_files.append(str(overall_json))
                
                # Execute Realtime
                subprocess.run(cmd_realtime, capture_output=True, check=True, env=env)
                
                index["reports"][api] = {"status": "success", "file": str(overall_json)}
            except subprocess.CalledProcessError as e:
                stderr = e.stderr.decode('utf-8') if e.stderr else 'No stderr'
                stdout = e.stdout.decode('utf-8') if e.stdout else 'No stdout'
                err_msg = f"RWG parse failed (code {e.returncode}).\nStderr: {stderr}\nStdout: {stdout}"
                logging.error(err_msg)
                index["reports"][api] = {"status": "error", "msg": err_msg}

    def _copy_metrics_for_plotting(self, output_dir: Path, metrics_dir: Path):
        """Copy overall-*.json from output_dir to metrics_dir so plot runner finds them."""
        for f in output_dir.glob("overall-*.json"):
            shutil.copy(f, metrics_dir / f.name)
