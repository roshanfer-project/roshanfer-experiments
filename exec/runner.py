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

from .config import Config
from .models import RunUnit, RunResult
from .utils import run_with_logging


class Runner:
    def __init__(self, config: Config):
        self.config = config

    def deploy_system(self, bench: str, system: str, tuning_params: Dict[str, Any], deployment_hosts: List[str], log_path: Optional[Path] = None) -> None:
        """
        Deploys the system using benchmarks/<bench>/deploy.sh.
        Injection: tuning_params as Environment Variables.
        Helpers: Passes DEPLOYMENT_HOSTS as comma-separated env var.
        """
        script_path = Path("benchmarks") / bench / "deploy.sh"
        if not script_path.exists():
            raise FileNotFoundError(f"Deploy script not found: {script_path}")

        logging_msg = f"Deploying {system} on {bench}..."
        print(logging_msg)

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
        env["DEPLOYMENT_HOSTS"] = ",".join(deployment_hosts)

        # Run Deploy Script
        try:
            run_with_logging([str(script_path)], env=env, log_path=log_path)
            print(f"Deployment of {system} successful.")
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

        print(f"Tearing down {system} on {bench}...")
        print(f"Tearing down {system} on {bench}...")
        try:
            env = os.environ.copy()
            env["SYSTEM"] = system
            run_with_logging([str(script_path)], env=env, log_path=log_path)
        except Exception as e:
            print(f"Teardown warning: {e}")

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
        
        # Determine protocol and version
        http_type = "http" if unit.system in ("sidecar", "sidecar-queue", "plain", "envoy") else "grpc"
        
        active_processes = []
        
        try:
            # Persist unit params for reference
            (raw_dir / "unit_runner.json").write_text(json.dumps(unit.to_dict(), indent=2))
            
            # Launch RWG for each API on assigned generator host
            if len(unit.apis) > len(unit.generator_hosts):
                raise ValueError(f"Not enough generator hosts ({len(unit.generator_hosts)}) for {len(unit.apis)} APIs")

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
                
                cmd_str = (
                    f"cd {remote_wrapper_path} && "
                    f"mkdir -p {remote_out_dir} && "
                    f"RWG_BINARY={remote_rwg_path} {wrapper_cmd} {http_type} {unit.base} {unit.rate} {unit.duration} {api} {remote_out_dir}"
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
                
                print(f"Starting load on {host} for {api}...")
                proc = subprocess.Popen(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                active_processes.append((api, host, proc, remote_out_dir))

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
                    print(f"Error on {host}: {stderr}")
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
            print(f"Runner Exception: {e}")
        
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
