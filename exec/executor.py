"""High-level experiment executor.
Refactored for CloudLab orchestration.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
from pathlib import Path
import sys
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Sequence, Tuple
import subprocess

from .config import load_config, Config
from .models import ExperimentConfig, RunUnit, RunResult, CollectorResult
from .runner import Runner
from .collector import Collector
from .infra import InfraBuilder
from . import report as report_module
import traceback as tb

def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

def _load_experiments_file(path: Path) -> List[ExperimentConfig]:
    with path.open() as f:
        data = json.load(f)
    exps_raw = data.get("experiments", [])
    exps: List[ExperimentConfig] = []
    for raw in exps_raw:
        exps.append(ExperimentConfig.from_dict(raw))
    return exps

def _expand_experiment_to_units(exp: ExperimentConfig, config: Config, generators: List[str], deployment: List[str]) -> Iterable[RunUnit]:
    # Custom expansion logic mapping exp params to units
    # Assuming latency-vs-load type mostly
    start = exp.loads.start if exp.loads else exp.base_rate
    end = exp.loads.end + 1 if exp.loads else (exp.base_rate + 1)
    step = exp.loads.step if exp.loads else 1
    
    if exp.loads is None and exp.base_rate == 0:
        # Single run, no load sweep?
        yield RunUnit(
            name=exp.name,
            type=exp.type,
            script=exp.script,
            base=0, rate=0, duration=exp.duration,
            system=exp.system, apis=exp.apis, bench=exp.bench,
            collector_freq=exp.collector_freq, warmup=exp.warmup, cooldown=exp.cooldown,
            services=exp.services, execution_args=exp.execution_args,
            repeats=exp.repeat,
            generator_hosts=generators,
            deployment_hosts=deployment
        )
        return

    for load in range(start, end, step):
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
            bench=exp.bench,
            collector_freq=exp.collector_freq,
            warmup=exp.warmup,
            cooldown=exp.cooldown,
            services=exp.services,
            cleanup_args=exp.cleanup_args,
            execution_args=exp.execution_args,
            metadata={},
            repeats=exp.repeat,
            generator_hosts=generators,
            deployment_hosts=deployment
        )

def _get_max_apis_needed(exps: List[ExperimentConfig]) -> int:
    mx = 0
    for e in exps:
        mx = max(mx, len(e.apis))
    return mx if mx > 0 else 1

def _run_tuner(system: str, bench: str, tuning_dir: Path, config_path: Path) -> Dict[str, Any]:
    """Run tuner script and return params."""
    tuner_script = Path("exec") / "tuner_template.py" 
    # Logic to pick specific tuner?
    # User said "move template_tuner to exec". 
    # If we want specific tuners, maybe exec/tuners/<system>_tuner.py?
    # Or just check default locations.
    
    possible_scripts = [
        Path("tuner") / f"{system}_tuner.py",
        Path("exec") / f"{system}_tuner.py",
        Path("exec") / "tuner_template.py" # Output of move command
    ]
    
    target_script = None
    for p in possible_scripts:
        if p.exists():
            target_script = p
            break
            
    if not target_script:
        logging.warning(f"No tuner found for {system}, skipping.")
        return {}

    output_file = tuning_dir / f"{system}.json"
    
    cmd = [
        "python", str(target_script),
        "--system", system,
        "--bench", bench,
        "--output", str(output_file),
        "--config", str(config_path)
    ]

    try:
        logging.info(f"Running tuner for {system}...")
        subprocess.run(cmd, check=True)
        if output_file.exists():
            return json.loads(output_file.read_text())

    except Exception as e:

        logging.warning(f"Tuner failed for {system}: {e}")
    
    return {}

    all_exps = _load_experiments_file(experiments_file)
    
    # Filter Experiments (Assuming ns passed or arguments available)
    # We need to access the Namespace arguments here.
    # Updated execute signature to accept Namespace or filter args?
    # Better: Update execute signature to take filter args.
    
    # Wait, I need to update execute signature first. 
    # Let's pivot to updating execute signature to accept 'filters' dict or specific args.
    pass
    
def execute(experiments_file: Path, config: Config, config_path: Path, filters: Dict[str, Any] = None) -> int:
    start_all = time.time()
    all_exps = _load_experiments_file(experiments_file)
    
    if filters:
        if filters.get("only_names"):
            names = set(filters["only_names"].split(","))
            all_exps = [e for e in all_exps if e.name in names]
        if filters.get("only_types"):
            types = set(filters["only_types"].split(","))
            all_exps = [e for e in all_exps if e.type in types]
        if filters.get("name_contains"):
            sub = filters["name_contains"]
            all_exps = [e for e in all_exps if sub in e.name]

    if not all_exps:
        logging.warning("No experiments found matching filters.")
        return 0

    # 1. Prepare Output Root (Moved before Infra for logging)
    run_root = Path(config.output_base_dir) / f"exp-{config.experiment_index}"
    run_root.mkdir(parents=True, exist_ok=True)
    tuning_dir = run_root / "tuning"
    tuning_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = run_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    
    # Configure Global Logging
    log_file = logs_dir / f"executor_{_timestamp()}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logging.info(f"Execution started. Logs at {logs_dir}")
    
    # 2. Infrastructure Setup
    infra = InfraBuilder(Path(config.hosts_file))
    max_apis = _get_max_apis_needed(all_exps)
    try:
        generators, deployment = infra.partition_hosts(config.num_generators, min_required=max_apis)
        
        # Define log paths for infra steps
        prov_log = logs_dir / f"provision_{_timestamp()}.log"
        k8s_log = logs_dir / f"k8s_setup_{_timestamp()}.log"
        
        infra.provision_hosts(Path(config.provisioning_script), log_path=prov_log)
        
        # Setup K8s on deployment nodes (idempotent)
        if hasattr(config, "k8s_script") and config.k8s_script:
             infra.setup_k8s(Path(config.k8s_script), deployment, log_path=k8s_log)
             
    except Exception as e:
        logging.error(f"Infra failure: {e}")
        return 1

    # 3. Initialize Runner & Collector
    # run_root already created above
    
    runner = Runner(config)
    collector = Collector(config)
    summary_csv = run_root / "run_summary.csv"
    summary_jsonl = run_root / "run_summary.jsonl"
    _init_csv(summary_csv)

    run_results = []

    # 3. Group by System
    by_system: Dict[str, List[ExperimentConfig]] = {}
    for e in all_exps:
        by_system.setdefault(e.system, []).append(e)

    # 4. Orchestration Loop
    for system, system_exps in by_system.items():
        logging.info(f"=== Process System: {system} ===")
        
        # A. Tuning
        # Assuming all exps for a system use same benchmark? 
        # If not, we might need multiple tunes. 
        # For simplicity, pick the first bench.
        bench = system_exps[0].bench
        tuning_params = _run_tuner(system, bench, tuning_dir, config_path)
        # Extract 'parameters' key if present
        deploy_params = tuning_params.get("parameters", {})

        # B. Build & Deploy
        try:
            # Build Step
            # User requirement: "build success file ... under directory corresponding to experiment_index"
            # "name should only include experiment_index and system"
            # This allows reuse of artifacts for same system in the same run.
            tag_base = f"{config.experiment_index}"
            tag = _safe_name(tag_base)
            logging.info(f"Tag: {tag}")
            
            # Status file in the run directory (exp-<index>/build_success_<tag>)
            build_status_file = run_root / f"build_success_{tag}"
            
            # Check build
            if not build_status_file.exists():
                build_log = logs_dir / f"build_{system}_{_timestamp()}.log"
                runner.build_system(bench, system, tag, build_status_file, log_path=build_log)
            else:
                logging.info(f"Build for tag {tag} already successful. Skipping.")

            deploy_log = logs_dir / f"deploy_{system}_{_timestamp()}.log"
            runner.deploy_system(bench, system, deploy_params, deployment, tag=tag, log_path=deploy_log)
        except Exception as e:
            logging.error(f"Skipping system {system} due to build/deploy failure: {e}")
            continue

        # C. Run Experiments
        try:
            for exp in system_exps:
                logging.info(f"  Running Experiment: {exp.name}")
                exp_dir = run_root / _safe_name(exp.name)
                exp_dir.mkdir(parents=True, exist_ok=True)

                for unit in _expand_experiment_to_units(exp, config, generators, deployment):
                    unit_dir = exp_dir / unit.safe_name()
                    unit_dir.mkdir(parents=True, exist_ok=True)
                    
                    # Repeats
                    start_r, end_r = _get_next_repeat_range(unit_dir, unit.repeats)
                    for r in range(start_r, end_r):
                        repeat_dir = unit_dir / f"repeat_{r:03d}"
                        repeat_dir.mkdir(parents=True, exist_ok=True)
                        
                        # Add tuning metadata
                        unit.metadata["tuning_params"] = deploy_params
                        
                        # Run
                        res = runner.run(unit, repeat_dir)
                        
                        # Failure Handling: Log and continue (Retry disabled)
                        if res.status == "error":
                            logging.warning(f"    Repeat {r} failed. Status: {res.status}")


                        # Collect
                        col_res = collector.collect(unit, res, repeat_dir)
                        _log_result(summary_csv, summary_jsonl, exp, unit, res, col_res, r, unit.repeats)
                        run_results.append(res)
                        
        except Exception as e:
            logging.error(f"System {system} loop aborted: {e}", exc_info=True)
        finally:
            # D. Teardown
            td_log = logs_dir / f"teardown_{system}_{_timestamp()}.log"
            runner.teardown_system(bench, system, deployment, log_path=td_log)

    # 5. Report
    # report_module.generate_report(...) # Optional
    logging.info(f"Execution finished. Results in {run_root}")
    return 0

def _safe_name(s: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)

def _get_next_repeat_range(path: Path, needed: int) -> Tuple[int, int]:
    existing = list(path.glob("repeat_*"))
    start = len(existing)
    return start, start + needed

def _init_csv(path: Path):
    if not path.exists():
        with path.open("w") as f:
            writer = csv.writer(f)
            writer.writerow(["experiment_name", "unit", "system", "status", "duration", "path"])

def _log_result(csv_path: Path, jsonl_path: Path, exp: ExperimentConfig, unit: RunUnit, run: RunResult, col: CollectorResult, r: int, total: int):
    # CSV
    with csv_path.open("a") as f:
        writer = csv.writer(f)
        writer.writerow([exp.name, unit.name, unit.system, run.status, run.details.get("duration_sec"), run.raw_artifact_dir])
    
    # JSONL
    # Match plot_runner.py expectations: flat structure
    # keys: type, experiment_name, run_unit_name, group_name, repeat_index, artifact_dir, metrics_dir, status ...
    data = {
        "status": run.status,
        "type": exp.type,
        "experiment_name": exp.name,
        "run_unit_name": unit.name,
        "group_name": run.group_name or unit.name,
        "repeat_index": r,
        "artifact_dir": run.raw_artifact_dir.replace("/raw", ""), # Artifact dir is parent of raw
        "raw_artifact_dir": run.raw_artifact_dir,
        "metrics_dir": col.metrics_dir,
        "metric_files": col.metrics_files, # Legacy support
        "start_timestamp": run.start_timestamp,
        "end_timestamp": run.end_timestamp,
        "duration_sec": run.details.get("duration_sec"),
        "config": exp.params, # Store full config params for reference
        "apis": exp.apis,
        "load": unit.rate
    }
    with jsonl_path.open("a") as f:
        f.write(json.dumps(data) + "\n")

def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--experiments-file", required=True)
    p.add_argument("--config", required=False)
    p.add_argument("--only-names", help="Comma-separated list of experiment names to run.")
    p.add_argument("--only-types", help="Comma-separated list of experiment types to run.")
    p.add_argument("--name-contains", help="Run experiments whose name contains this substring.")
    return p.parse_args(argv)

def main(argv: List[str] | None = None) -> int:
    ns = parse_args(argv or sys.argv[1:])
    config = load_config(ns.config)
    cfg_path = Path(ns.config) if ns.config else Path("config.json")
    
    filters = {
        "only_names": ns.only_names,
        "only_types": ns.only_types,
        "name_contains": ns.name_contains
    }
    
    return execute(Path(ns.experiments_file), config, cfg_path, filters)

if __name__ == "__main__":
    sys.exit(main())
