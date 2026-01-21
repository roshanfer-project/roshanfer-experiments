"""Runner: executes a concrete RunUnit and produces raw artifacts.

Responsibilities:
1. Deploy System (via deploy_system)
2. Execute Workload (via run) - Remote RWG execution
3. Teardown System (via teardown_system)
"""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import traceback
from typing import Any, Dict, List, Optional
import subprocess
import time
import shutil
import logging


from .config import Config
from .models import RunUnit, RunResult
from .utils import run_with_logging
import threading
import csv
from typing import Any, Dict, List, Optional, Tuple





class ResourceMonitor:
    def __init__(self, raw_dir: Path, interval: float = 2.0):
        self.cpu_output_file = raw_dir / "cpu_metrics.csv"
        self.interval = interval
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._msg_loop, daemon=True)
        # Cache for rate calculation: (node, ns, pod, container) -> (timestamp, usage_nanoseconds)
        self.prev_cpu: Dict[Tuple[str, str, str, str], Tuple[float, int]] = {} 

    def start(self):
        # Init CPU CSV
        if not self.cpu_output_file.exists():
            with self.cpu_output_file.open("w") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp", "node", "namespace", "pod", "container", "utilization", "limit"])
                
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)

    def _get_container_metadata(self) -> Dict[str, Dict[str, any]]:
        """Fetch container metadata from kubectl to map container ID -> pod info"""
        try:
            cmd = ["kubectl", "get", "pods", "--all-namespaces", "-o", "json"]
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            pods_data = json.loads(res.stdout)
            
            # Build mapping: container_id -> {namespace, pod, container, limit}
            container_map = {}
            
            for pod in pods_data.get("items", []):
                ns = pod["metadata"]["namespace"]
                pod_name = pod["metadata"]["name"]
                
                # Build limits map
                limits = {}
                for container in pod["spec"].get("containers", []):
                    container_name = container["name"]
                    resources = container.get("resources", {})
                    cpu_limit_str = resources.get("limits", {}).get("cpu", "0")
                    limits[container_name] = self._parse_cpu_limit(cpu_limit_str)
                
                # Map container IDs
                for status in pod["status"].get("containerStatuses", []):
                    container_id_full = status.get("containerID", "")
                    if not container_id_full:
                        continue
                    
                    # Extract ID from "containerd://abc123" format
                    if "://" in container_id_full:
                        container_id = container_id_full.split("://", 1)[1]
                    else:
                        container_id = container_id_full
                    
                    container_map[container_id] = {
                        "namespace": ns,
                        "pod": pod_name,
                        "container": status["name"],
                        "limit": limits.get(status["name"], 0.0)
                    }
            
            logging.info(f"ResourceMonitor: Mapped {len(container_map)} containers")
            return container_map
        except Exception as e:
            logging.warning(f"ResourceMonitor: Failed to get container metadata: {e}")
            return {}
    
    def _parse_cpu_limit(self, cpu_str: str) -> float:
        """Parse Kubernetes CPU limit string to float (cores)"""
        try:
            if not cpu_str or cpu_str == "0":
                return 0.0
            if cpu_str.endswith("m"):
                return float(cpu_str[:-1]) / 1000.0
            return float(cpu_str)
        except Exception:
            return 0.0
    
    def _get_nodes_map(self) -> Dict[str, str]:
        """Returns mapping of NodeName -> InternalIP"""
        try:
            cmd = ["kubectl", "get", "nodes", "-o", "wide", "--no-headers"]
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            mapping = {}
            for line in res.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 6:
                    name = parts[0]
                    ip = parts[5] # INTERNAL-IP
                    mapping[name] = ip
            return mapping
        except Exception as e:
            logging.warning(f"ResourceMonitor: Failed to get node IPs: {e}")
            return {}

    def _fetch_metrics(self, node_name: str, node_ip: str) -> dict:
        """Fetch JSON metrics from cpu-stats-exporter running on node:9100"""
        try:
            import urllib.request
            url = f"http://{node_ip}:9100/metrics"
            req = urllib.request.Request(url, headers={'User-Agent': 'ResourceMonitor/1.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = response.read().decode('utf-8')
                return json.loads(data)
        except Exception as e:
            logging.warning(f"ResourceMonitor: Failed to fetch from cpu-stats-exporter on {node_ip}:9100: {e}")
            return {}

    def _msg_loop(self):
        node_map = self._get_nodes_map()
        
        # Fetch container metadata once at start and refresh periodically
        container_metadata = self._get_container_metadata()
        metadata_refresh_time = time.time()
        
        while not self.stop_event.is_set():
            start_t = time.time()
            ts_str = datetime.utcnow().isoformat()
            
            # Refresh metadata every 30 seconds
            if time.time() - metadata_refresh_time > 30:
                container_metadata = self._get_container_metadata()
                metadata_refresh_time = time.time()
            
            cpu_rows = []
            
            for node, ip in node_map.items():
                metrics_data = self._fetch_metrics(node, ip)
                if not metrics_data:
                    continue
                    
                current_time = time.time()
                containers = metrics_data.get("containers", [])
                
                # Process each container
                for container_data in containers:
                    container_id = container_data.get("container_id", "")
                    cpu_nanos = container_data.get("cpu_usage_nanoseconds", 0)
                    
                    if not container_id:
                        continue
                    
                    # Look up metadata
                    metadata = container_metadata.get(container_id)
                    if not metadata:
                        logging.debug(f"No metadata for container {container_id[:12]}")
                        continue
                    
                    ns = metadata["namespace"]
                    pod = metadata["pod"]
                    container = metadata["container"]
                    cpu_limit = metadata["limit"]
                    
                    key = (node, ns, pod, container)
                    rate = 0.0
                    
                    if key in self.prev_cpu:
                        prev_t, prev_nanos = self.prev_cpu[key]
                        dt = current_time - prev_t
                        if dt > 0 and cpu_nanos >= prev_nanos:
                            # Calculate rate in cores/second
                            # (current_nanos - prev_nanos) / dt gives nanos/second
                            # Divide by 1e9 to convert to cores/second
                            rate = (cpu_nanos - prev_nanos) / (dt * 1e9)
                    
                    self.prev_cpu[key] = (current_time, cpu_nanos)
                    
                    # Calculate unnormalized utilization (percentage * 100)
                    # rate is in cores/sec (e.g., 2.5 means 2.5 cores used)
                    # We want 0-500 scale where 500 = 5 cores at 100%
                    # So: utilization = rate * 100
                    utilization = rate * 100.0
                    
                    # Always log all containers found in metrics
                    # Rate will be 0 on first iteration, which is expected
                    cpu_rows.append([ts_str, node, ns, pod, container, f"{utilization:.2f}", f"{cpu_limit:.2f}"])

            # Write Rows
            if cpu_rows:
                try:
                    with self.cpu_output_file.open("a") as f:
                        csv.writer(f).writerows(cpu_rows)
                except Exception as e:
                    logging.error(f"ResourceMonitor: Failed to write CPU CSV: {e}")

            elapsed = time.time() - start_t
            sleep_time = max(0.0, self.interval - elapsed)
            self.stop_event.wait(sleep_time)



class Runner:
    def __init__(self, config: Config):
        self.config = config

    def build_system(self, bench: str, system: str, tag: str, status_file: Optional[Path] = None, log_path: Optional[Path] = None) -> None:
        """
        Builds the system using benchmarks/<bench>/build.sh.
        Checks for success status file to potentially skip.
        """
        script_path = Path("benchmarks") / bench / "build.sh"
        if not script_path.exists():
             # If build.sh doesn't exist, we assume no build needed or legacy?
             # User prompt implies we should add it. If missing, warn.
             logging.error(f"Build script not found: {script_path}. Skipping build.")
             return

        logging_msg = f"Building {system} for {bench} (Tag: {tag})..."
        logging.info(logging_msg)

        # Build Args
        cmd = [str(script_path), tag]
        if status_file:
            cmd.append(str(status_file))

        try:
             # Run synchronously
             run_with_logging(cmd, env=os.environ.copy(), log_path=log_path)
             logging.info(f"Build of {system} successful.")
        except subprocess.CalledProcessError as e:
             raise RuntimeError(f"Build failed for {system}: {e}")

    def deploy_system(self, bench: str, system: str, tuning_params: Dict[str, Any], deployment_hosts: List[str], tag: str, log_path: Optional[Path] = None) -> None:
        """
        Deploys the system using benchmarks/<bench>/deploy.sh.
        Injection: tuning_params as Environment Variables.
        Helpers: Passes DEPLOYMENT_HOSTS as comma-separated env var.
        """
        script_path = Path("benchmarks") / bench / "deploy.sh"
        if not script_path.exists():
            raise FileNotFoundError(f"Deploy script not found: {script_path}")

        logging_msg = f"Deploying {system} on {bench} (Tag: {tag})..."
        logging.info(logging_msg)

        # Always ensure clean slate
        pre_teardown_log = None
        if log_path:
            pre_teardown_log = log_path.with_name(log_path.stem + "_pre_teardown" + log_path.suffix)
            
        self.teardown_system(bench, system, log_path=pre_teardown_log)

        # Prepare Environment
        env = os.environ.copy()
        # Inject Tuning Params
        for k, v in tuning_params.items():
            env[str(k).upper()] = str(v)
        
        env["SYSTEM"] = system
        env["TAG"] = tag
        env["DEPLOYMENT_HOSTS"] = ",".join(deployment_hosts)

        # Run Deploy Script
        try:
            run_with_logging([str(script_path)], env=env, log_path=log_path)
            logging.info(f"Deployment of {system} successful.")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Deployment failed for {system}: {e}")

    def teardown_system(self, bench: str, system: str, log_path: Optional[Path] = None) -> None:
        """
        Teardowns the system using benchmarks/<bench>/destroy.sh or clean.sh
        """
        # Try destroy.sh first, then clean.sh, or assume deploy handles cleanup? 
        # Typically good to have explicit teardown.
        script_path = Path("benchmarks") / bench / "destroy.sh"
        if not script_path.exists():
            raise FileNotFoundError(f"Destroy script not found: {script_path}")

        logging.info(f"Tearing down {system} on {bench}...")
        try:
            env = os.environ.copy()
            env["SYSTEM"] = system
            run_with_logging([str(script_path)], env=env, log_path=log_path)
        except Exception as e:
            logging.warning(f"Teardown warning: {e}")

    def run(self, unit: RunUnit, unit_dir: Path) -> RunResult:
        """
        Runs the workload generator (RWG) remotely on generator hosts.
        One RWG instance per API, assigned to distinct generator hosts.
        """
        start = time.time()
        raw_dir = unit_dir / self.config.raw_artifact_subdir
        raw_dir.mkdir(parents=True, exist_ok=True)
        output_dir = unit_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        details: dict[str, Any] = {"started_at": start}
        status = "success"
        start_timestamp = ""
        end_timestamp = ""
        
        # Start Resource Monitor (CPU + Network)
        # User requested per-repeat monitoring, output in results.
        # raw_dir is typically specific to this repeat (passed as unit_dir in Executor).
        
        monitor = ResourceMonitor(raw_dir=raw_dir, interval=2.0)
        monitor.start()

        # Determine protocol and version
        http_type = "http" if unit.system in ("sidecar", "sidecar-queue", "plain", "envoy") else "grpc"
        
        active_processes = []
        
        try:
            # Persist unit params for reference
            (raw_dir / "unit_runner.json").write_text(json.dumps(unit.to_dict(), indent=2))
            
            # Launch RWG for each API on assigned generator host
            if len(unit.apis) > len(unit.generator_hosts):
                raise ValueError(f"Not enough generator hosts ({len(unit.generator_hosts)}) for {len(unit.apis)} APIs")

            # Phase 1: Prepare commands for all APIs
            prepared_commands = []
            for idx, api in enumerate(unit.apis):
                host = unit.generator_hosts[idx]
                
                # Construct Remote RWG Command
                # We assume 'rwg' is built and in the path or we use config.rwg_binary_path relative to repo root on remote?
                # The provisioner builds rwg in `~/roshanfer-experments/rwg`.
                # We should use strict paths.
                remote_repo_path = "~/roshanfer-experments" # Assumption from provisioner logic
                remote_rwg_path = f"{remote_repo_path}/rwg/rwg"
                
                # ...
                
                # Command construction
                # Protocol logic? RWG takes specific args.
                # Previous runner used "./wrapper/{bench}/run.sh".
                # User wants "SSH -> Run rwg (remote)".
                # We need to construct the RWG arguments directly.
                # wrapper/hotel/run.sh: ./rwg/rwg -u http://... -d ...
                # We should replicate the wrapper logic or call the wrapper remotely?
                # User said: "Execute RWG (Rust Workload Generator) or similar on Generator hosts."
                # Calling wrapper is safer if it encapsulates API-specific URL headers.
                # wrapper scripts are in `wrapper/{bench}/{script}`.
                
                remote_wrapper_path = f"{remote_repo_path}/wrapper/{unit.bench}"
                wrapper_script_name = unit.script if unit.script else "run.sh" 
                # Note: unit.script might be full path "experiments/latency-vs-load/run.py" or just filename?
                # In current config, exp.script is often "experiments/latency-vs-load/run.py".
                # But `build_wrapper_command` used `wrapper/{unit.bench}/{unit.script}` if multiple apis, or `run.sh` if single.
                # Just use `run.sh` for now as generic wrapper if custom script not provided.
                if "/" in str(unit.script):
                     # Likely a local python orchestrator script, not the remote wrapper.
                     # Default to run.sh for the remote wrapper
                     wrapper_cmd = "./run.sh"
                else:
                     wrapper_cmd = f"./{unit.script}" if unit.script else "./run.sh"

                # Command: cd wrapper/{bench} && {wrapper_cmd} {protocol} {base} {rate} {duration} {api} {output_dir}
                # Output dir on remote? No, usually wrapper writes to local given path.
                # We need to specify a remote temp output dir, then pull it.
                remote_out_dir = f"/tmp/rwg_out_{unit.safe_name()}_{idx}"
                
                target_addr = "node0"
                if unit.deployment_hosts:
                    # Resolve node alias from hosts.txt
                    # We need to find the index of this host in the master hosts file.
                    try:
                        hosts_path = Path(self.config.hosts_file)
                        all_hosts = []
                        if hosts_path.exists():
                            with hosts_path.open() as f:
                                for line in f:
                                    line = line.strip()
                                    if line and not line.startswith("#"):
                                        all_hosts.append(line)
                        
                        deploy_host_raw = unit.deployment_hosts[0]
                        # We need to match exact string from hosts.txt
                        # unit.deployment_hosts comes from InfraBuilder which reads the same file, 
                        # so strings should be identical.
                        
                        if deploy_host_raw in all_hosts:
                            host_idx = all_hosts.index(deploy_host_raw)
                            target_addr = f"node{host_idx}"
                        else:
                            logging.warning(f"Warning: Host {deploy_host_raw} not found in {hosts_path}, defaulting to node0 logic fallback.")
                            # Fallback: keep previous logic or default?
                            # Previous logic:
                            raw = deploy_host_raw
                            if "@" in raw:
                                raw = raw.split("@")[1]
                            target_addr = raw.split(".")[0]
                            
                    except Exception as e:
                         logging.warning(f"Warning: Could not resolve node alias: {e}")
                         target_addr = "node0"

                cmd_str = (
                    f"cd {remote_wrapper_path} && "
                    f"mkdir -p {remote_out_dir} && "
                    f"TARGET_ADDR={target_addr} RWG_BINARY={remote_rwg_path} {wrapper_cmd} {http_type} {unit.base} {unit.rate} {unit.duration} {api} {remote_out_dir}"
                )
                
                # Add extra execution args
                if unit.execution_args:
                    cmd_str += " " + " ".join(unit.execution_args)

                ssh_cmd = [
                    "ssh", 
                    "-o", "StrictHostKeyChecking=no", 
                    "-o", "UserKnownHostsFile=/dev/null", 
                    host, 
                    cmd_str
                ]
                
                prepared_commands.append({
                    "api": api,
                    "host": host,
                    "ssh_cmd": ssh_cmd,
                    "remote_out_dir": remote_out_dir
                })

            # Phase 2: Launch all processes uniformly
            for item in prepared_commands:
                logging.info(f"Starting load on {item['host']} for {item['api']}...")
                proc = subprocess.Popen(item['ssh_cmd'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                active_processes.append((item['api'], item['host'], proc, item['remote_out_dir']))

            # Wait for all
            start_timestamp = datetime.now().isoformat()
            
            for api, host, proc, remote_out_dir in active_processes:
                stdout, stderr = proc.communicate()
                
                # Save logs
                (raw_dir / f"wrapper_stdout_{api}_{host}.txt").write_text(stdout)
                (raw_dir / f"wrapper_stderr_{api}_{host}.txt").write_text(stderr)
                
                if proc.returncode != 0:
                    status = "error"
                    details[f"error_{api}"] = f"RWG failed on {host} code={proc.returncode}"
                    logging.error(f"Error on {host}: {stderr}")
                else:
                    # Pull output
                    # Remote: {remote_out_dir}/out-{api}.csv and overall-{api}.json ?
                    # Wrapper usually produces out.csv or out-{api}.csv?
                    # `runner.py` previously expected `out-{api}.csv`.
                    # We will SCP everything from remote_out_dir to local output_dir.
                    scp_cmd = [
                        "scp", 
                        "-o", "StrictHostKeyChecking=no", 
                        "-o", "UserKnownHostsFile=/dev/null", 
                        f"{host}:{remote_out_dir}/*", 
                        str(output_dir)
                    ]
                    subprocess.run(scp_cmd, check=True)
                    
                    # Cleanup remote
                    subprocess.run([
                        "ssh", 
                        "-o", "StrictHostKeyChecking=no", 
                        "-o", "UserKnownHostsFile=/dev/null", 
                        host, 
                        f"rm -rf {remote_out_dir}"
                    ], check=False)

            end_timestamp = datetime.now().isoformat()
            
            # Logic to extract/merge metrics from the pulled files?
            # Result Collector handles aggregation, but Runner usually returns some summary stats?
            # Old runner did extraction here. We can leave that to Collector or do it here.
            # Plan says "Runner runs load", "Collector collects".
            # The files are now in `output_dir`.
            
        except Exception as e:
            status = "error"
            details["exception"] = str(e)
            details["traceback"] = traceback.format_exc()
            logging.error(f"Runner Exception: {e}")
        finally:
            monitor.stop()
        
        details["ended_at"] = time.time()
        details["duration_sec"] = details["ended_at"] - start
        
        (raw_dir / "run_details.json").write_text(json.dumps(details, indent=2))

        return RunResult(
            unit_name=unit.name,
            status=status,
            raw_artifact_dir=str(raw_dir),
            details=details,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            output_file=str(output_dir) # directory
        )
