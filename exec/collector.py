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
import urllib.request
import urllib.error

class Collector:
    def __init__(self, config: Config):
        self.config = config

    def collect(self, unit: RunUnit, run_result: RunResult, unit_dir: Path, collect_service_logs: bool = True) -> CollectorResult:
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

        # 1. Collect Service Logs (Optional)
        if collect_service_logs:
             self._collect_service_logs(unit, raw_dir)

        # 2. Generate/Validate JSON Reports from CSV (Local processing)
        # We assume Runner has already pulled out-{api}.csv to output_dir
        self._generate_reports(unit, output_dir, index, metric_files)

        # 3. Collect Prometheus Metrics (Now after reports to use overall-*.json)
        self._collect_prometheus_metrics(unit, metrics_dir, output_dir)

        # 4. Copy/Link Metrics for Plot Runner
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

    def _collect_prometheus_metrics(self, unit: RunUnit, metrics_dir: Path, output_dir: Path):
        """Collects specific Prometheus metrics and saves to json."""
        prom_metrics_file = metrics_dir / "prometheus.json"
        
        try:
            # 1. Get NodePort
            cmd_port = ["kubectl", "get", "svc", "prometheus-external", "-o", "jsonpath='{.spec.ports[0].nodePort}'"]
            res_port = subprocess.run(cmd_port, capture_output=True, text=True)
            if res_port.returncode != 0:
                logging.warning("Could not find prometheus-external service. Skipping Prometheus collection.")
                prom_metrics_file.write_text("{}")
                return
            
            node_port = res_port.stdout.strip("'")
            if not node_port.isdigit():
                 # Handle cases where output might be quoted or empty
                 node_port = node_port.replace("'", "")
            
            if not node_port or not node_port.isdigit():
                logging.warning(f"Invalid NodePort found: {node_port}. Skipping.")
                prom_metrics_file.write_text("{}")
                return

            # 2. Get Node IP (any node)
            if not unit.deployment_hosts:
                logging.warning("No deployment hosts found. Skipping Prometheus collection.")
                prom_metrics_file.write_text("{}")
                return
            
            node_ip = unit.deployment_hosts[0]
            if "@" in node_ip:
                node_ip = node_ip.split("@")[1]
            
            prom_url = f"http://{node_ip}:{node_port}"
            logging.info(f"Querying Prometheus at {prom_url}...")

            metrics_to_collect = [
                "max_queue",
                "avg_queue",
                "accepted_rpc_counter",
                "failed_rpc_counter",
            ]

            # Nested structure: data[api][service][metric] = value
            data: Dict[str, Dict[str, Dict[str, Any]]] = {}

            # Fetch and Parse
            for metric in metrics_to_collect:
                try:
                    query_url = f"{prom_url}/api/v1/query?query={metric}"
                    with urllib.request.urlopen(query_url, timeout=5) as response:
                        result = json.loads(response.read().decode('utf-8'))
                        
                        # Parse results
                        for item in result.get("data", {}).get("result", []):
                            m_labels = item.get("metric", {})
                            api = m_labels.get("api", "unknown")
                            service = m_labels.get("job", "unknown")
                            
                            # Value tuple: [timestamp, value_str]
                            val_tuple = item.get("value", [])
                            if len(val_tuple) < 2:
                                continue
                            
                            try:
                                # Parse as float, or keep as string if desired. User said: 
                                # "value list is a tuple... I care about the second item".
                                # Usually better to store as number if possible
                                val_str = val_tuple[1]
                                val_num = float(val_str) if "." in val_str else int(val_str)
                            except ValueError:
                                val_num = val_tuple[1]

                            if api not in data:
                                data[api] = {}
                            if service not in data[api]:
                                data[api][service] = {}
                            
                            data[api][service][metric] = val_num
                            
                except Exception as e:
                    logging.warning(f"Failed to query metric {metric}: {e}")

            # Ingress Calculation
            # Iterate over APIs we found in metrics
            # Or should we iterate over unit.apis?
            # It's safer to iterate over keys in 'data' or unit.apis.
            # Let's iterate over unit.apis to ensure we cover expected ones
            
            for api in unit.apis:
                if api not in data:
                     # No metrics for this API, skip ingress calc
                     continue
                
                # Read overall-{api}.json
                overall_file = output_dir / f"overall-{api}.json"
                if not overall_file.exists():
                    logging.warning(f"Skipping ingress calc for {api}: {overall_file} not found")
                    continue
                
                try:
                    overall_data = json.loads(overall_file.read_text())
                    max_workers = overall_data.get("maximum_workers")
                    
                    if max_workers is None:
                        logging.warning(f"Skipping ingress calc for {api}: maximum_workers missing in overall json")
                        continue
                    
                    # Check for frontend vs nginx
                    has_frontend = "frontend" in data[api] and "max_queue" in data[api]["frontend"]
                    has_nginx = "nginx" in data[api] and "max_queue" in data[api]["nginx"]
                    
                    if has_frontend and has_nginx:
                        logging.warning(f"Ambiguous ingress calc for {api}: Both 'frontend' and 'nginx' services found.")
                        continue
                    
                    if not has_frontend and not has_nginx:
                        logging.warning(f"Skipping ingress calc for {api}: Neither 'frontend' nor 'nginx' service found.")
                        continue
                    
                    # Exactly one exists
                    frontend_svc = "frontend" if has_frontend else "nginx"
                    frontend_queue = data[api][frontend_svc]["max_queue"]
                    
                    ingress_val = max_workers - frontend_queue
                    
                    if "ingress" not in data[api]:
                        data[api]["ingress"] = {}
                    
                    data[api]["ingress"]["max_queue"] = ingress_val

                    fe = data[api][frontend_svc]
                    if "avg_queue" in fe:
                        data[api]["ingress"]["avg_queue"] = max_workers - fe["avg_queue"]
                    
                except Exception as e:
                    logging.warning(f"Error during ingress calc for {api}: {e}")

            
            prom_metrics_file.write_text(json.dumps(data, indent=2))
            logging.info(f"Prometheus metrics saved to {prom_metrics_file}")

        except Exception as e:
            logging.warning(f"Top-level error collecting Prometheus metrics: {e}")
            prom_metrics_file.write_text("{}")


    def _generate_reports(self, unit: RunUnit, output_dir: Path, index: Dict[str, Any], metric_files: List[str]):
        """Runs `rwg parse` to generate overall.json and realtime.csv."""
        version = "1"
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
