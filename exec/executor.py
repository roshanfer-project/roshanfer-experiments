"""High-level experiment executor.
Refactored for CloudLab orchestration.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
import shutil
from pathlib import Path
import sys
import time
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Sequence, Tuple
import subprocess

from dataclasses import replace as dc_replace
from .config import load_config, Config
from .models import ExperimentConfig, RunUnit, RunResult, CollectorResult
from .runner import Runner
from .collector import Collector
from .infra import InfraBuilder
from . import report as report_module
import traceback as tb

def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _write_infra_partition(
    run_root: Path,
    config: Config,
    generators: List[str],
    deployment: List[str],
    filters: Dict[str, Any] | None,
) -> None:
    data = {
        "hosts_file": str(Path(config.hosts_file).resolve()),
        "generators": generators,
        "kubernetes_nodes_ssh_hosts": deployment,
        "num_generators": config.num_generators,
        "shared_generator": bool(filters and filters.get("shared_generator")),
    }
    (run_root / "infra_partition.json").write_text(json.dumps(data, indent=2) + "\n")


def _write_kubernetes_nodes_listing(run_root: Path, k8s_ran: bool) -> None:
    out = run_root / "kubernetes_nodes.txt"
    if not k8s_ran:
        out.write_text("# k8s_script not configured or skipped; no cluster snapshot.\n")
        return
    try:
        cp = subprocess.run(
            ["kubectl", "get", "nodes", "-o", "wide"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        lines = []
        if cp.returncode != 0:
            lines.append(f"# kubectl exit code {cp.returncode}")
        if cp.stdout:
            lines.append(cp.stdout.rstrip())
        if cp.stderr:
            lines.append("# stderr:")
            lines.append(cp.stderr.rstrip())
        out.write_text("\n".join(lines) + ("\n" if lines else ""))
        if cp.returncode != 0:
            logging.warning("kubectl get nodes failed; see kubernetes_nodes.txt")
    except Exception as e:
        out.write_text(f"# error running kubectl: {e}\n")
        logging.warning(f"kubectl get nodes: {e}")

def _load_experiments_file(path: Path) -> List[ExperimentConfig]:
    with path.open() as f:
        data = json.load(f)
    exps_raw = data.get("experiments", [])
    exps: List[ExperimentConfig] = []
    for raw in exps_raw:
        exps.append(ExperimentConfig.from_dict(raw))
    return exps

def _derive_experiment_name(exp: ExperimentConfig, bench: str) -> str:
    """Derive name from type, bench, system. Include api count when > 1 for uniqueness."""
    bench_slug = bench.split("/")[-1] if bench else ""
    if len(exp.apis) > 1:
        return f"{exp.type}-{bench_slug}-{len(exp.apis)}-{exp.system}"
    return f"{exp.type}-{bench_slug}-{exp.system}"

def _assign_derived_names(exps: List[ExperimentConfig], config: Config) -> List[ExperimentConfig]:
    """Assign derived names when missing, adding suffix for duplicates."""
    seen: Dict[str, int] = {}
    result = []
    for exp in exps:
        if exp.name:
            result.append(exp)
            continue
        bench = exp.bench or getattr(config, "bench", None) or config.extra.get("bench", "")
        base = _derive_experiment_name(exp, bench)
        if base in seen:
            seen[base] += 1
            name = f"{base}-{seen[base]}"
        else:
            seen[base] = 0
            name = base
        result.append(dc_replace(exp, name=name))
    return result

def _expand_experiment_to_units(exp: ExperimentConfig, config: Config, generator_hosts: List[str], deployment: List[str]) -> Iterable[RunUnit]:
    # Custom expansion logic mapping exp params to units
    start = exp.loads.start if exp.loads else exp.base_rate
    end = exp.loads.end + 1 if exp.loads else (exp.base_rate + 1)
    step = exp.loads.step if exp.loads else 1

    bench = exp.bench or getattr(config, "bench", None) or config.extra.get("bench", "")
    script = exp.script or ("run.sh" if exp.system == "sidecar" else "run-plain.sh")

    if exp.type == "throughput-vs-overcommitment":
        raw_ocs = exp.params.get("overcommitments") if exp.params else None
        if not raw_ocs:
            raise ValueError(
                f"Experiment '{exp.name}' type throughput-vs-overcommitment "
                "requires a non-empty overcommitments list"
            )
        loads = [0] if (exp.loads is None and exp.base_rate == 0) else list(range(start, end, step))
        for raw_oc in raw_ocs:
            oc = float(raw_oc)
            pct = int(round(oc * 100))
            for load in loads:
                params = copy.deepcopy(exp.params) if exp.params else {}
                deploy_env = dict(params.get("deploy_env") or {})
                deploy_env["SIDECAR_OVER_COMMIT"] = str(int(oc)) if oc == int(oc) else str(oc)
                params["deploy_env"] = deploy_env
                yield RunUnit(
                    name=f"{exp.name}-oc-{pct}-rate-{load}",
                    type=exp.type,
                    script=script,
                    base=exp.base_rate,
                    rate=load,
                    duration=exp.duration,
                    system=exp.system,
                    apis=exp.apis,
                    bench=bench,
                    collector_freq=exp.collector_freq,
                    warmup=exp.warmup,
                    cooldown=exp.cooldown,
                    services=exp.services,
                    cleanup_args=exp.cleanup_args,
                    execution_args=exp.execution_args,
                    metadata={"overcommitment": oc},
                    repeats=exp.repeat,
                    generator_hosts=generator_hosts,
                    deployment_hosts=deployment,
                    params=params,
                )
        return

    if exp.loads is None and exp.base_rate == 0:
        # Single run, no load sweep?
        yield RunUnit(
            name=exp.name,
            type=exp.type,
            script=script,
            base=0, rate=0, duration=exp.duration,
            system=exp.system, apis=exp.apis, bench=bench,
            collector_freq=exp.collector_freq, warmup=exp.warmup, cooldown=exp.cooldown,
            services=exp.services, execution_args=exp.execution_args,
            repeats=exp.repeat,
            generator_hosts=generator_hosts,
            deployment_hosts=deployment,
            params=exp.params
        )
        return

    for load in range(start, end, step):
        variant_name = f"{exp.name}-rate-{load}"
        yield RunUnit(
            name=variant_name,
            type=exp.type,
            script=script,
            base=exp.base_rate,
            rate=load,
            duration=exp.duration,
            system=exp.system,
            apis=exp.apis,
            bench=bench,
            collector_freq=exp.collector_freq,
            warmup=exp.warmup,
            cooldown=exp.cooldown,
            services=exp.services,
            cleanup_args=exp.cleanup_args,
            execution_args=exp.execution_args,
            metadata={},
            repeats=exp.repeat,
            generator_hosts=generator_hosts,
            deployment_hosts=deployment,
            params=exp.params
        )

def _get_max_apis_needed(exps: List[ExperimentConfig]) -> int:
    mx = 0
    for e in exps:
        mx = max(mx, len(e.apis))
    return mx if mx > 0 else 1

def _run_tuner(system: str, bench: str, tuning_dir: Path, logs_dir: Path, config_path: Path, tag: str, generators: List[str], deployment: List[str]) -> Dict[str, Any]:
    """Run tuner module natively and return params."""
    try:
        import importlib
        import contextlib
        
        # Determine module name
        module_name = f"exec.{system}_tuner"
        
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            logging.warning(f"Could not import tuner module {module_name}. Skipping tuning.")
            return {}

        logging.info(f"Running tuner {module_name} for {system} (Tag: {tag})...")
        
        # Log to a separate file for the tuner
        tuner_log_path = logs_dir / f"tuner_{system}_{_timestamp()}.log"
        
        # Detach executor file handlers to prevent pollution
        root_logger = logging.getLogger()
        executor_handlers = []
        for h in root_logger.handlers:
            if isinstance(h, logging.FileHandler) and "executor_" in str(Path(h.baseFilename).name):
                executor_handlers.append(h)
        
        for h in executor_handlers:
            root_logger.removeHandler(h)

        try:
            # Capture stdout/stderr to the log file with line buffering
            with tuner_log_path.open("w", buffering=1) as f:
                with contextlib.redirect_stdout(f), contextlib.redirect_stderr(f):
                     # Check if module has optimize_system
                     if hasattr(module, "optimize_system"):
                          return module.optimize_system(str(config_path), system, bench, tag=tag, generators=generators, deployment=deployment, logs_dir=logs_dir)
                     else:
                          print(f"Module {module_name} has no optimize_system function.")
                          return {}
        except Exception as e:
            logging.warning(f"Tuner failed for {system}: {e}")
            # Log exception to tuner log if possible
            try:
                 with (logs_dir / f"tuner_{system}_error.log").open("a") as ef:
                     ef.write(str(e) + "\n")
                     import traceback
                     ef.write(traceback.format_exc())
            except: 
                pass
        finally:
            # Re-attach executor handlers
            for h in executor_handlers:
                root_logger.addHandler(h)
    
    except Exception as e:
        logging.warning(f"Top-level Tuner error for {system}: {e}")
    
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
    all_exps = _assign_derived_names(all_exps, config)

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
        if filters.get("only_system"):
            systems = set(s.strip() for s in filters["only_system"].split(","))
            all_exps = [e for e in all_exps if e.system in systems]
        if filters.get("only_num_apis"):
            nums = set(int(x.strip()) for x in filters["only_num_apis"].split(","))
            all_exps = [e for e in all_exps if len(e.apis) in nums]

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

    if config.nanolog_debug:
        os.environ["SIDECAR_ENABLE_NANOLOG"] = "1"
        logging.info("nanolog_debug: SIDECAR_ENABLE_NANOLOG=1 for sidecar builds")
    
    # 2. Infrastructure Setup
    infra = InfraBuilder(Path(config.hosts_file))
    max_apis = _get_max_apis_needed(all_exps)
    shared_generator = filters.get("shared_generator") if filters else False
    if shared_generator:
        effective_min_required = 1
        effective_num_gens = min(len(infra.hosts) - 1, config.num_generators)
    else:
        effective_min_required = max_apis
        effective_num_gens = config.num_generators
    k8s_ran = False
    try:
        generators, deployment = infra.partition_hosts(effective_num_gens, min_required=effective_min_required)
        
        # Define log paths for infra steps
        prov_log = logs_dir / f"provision_{_timestamp()}.log"
        k8s_log = logs_dir / f"k8s_setup_{_timestamp()}.log"
        prov_host_logs_dir = logs_dir / "provision_hosts"
        infra.provision_hosts(
            Path(config.provisioning_script),
            log_path=prov_log,
            provision_host_logs_dir=prov_host_logs_dir,
        )
        
        if hasattr(config, "k8s_script") and config.k8s_script:
            infra.setup_k8s(Path(config.k8s_script), deployment, log_path=k8s_log)
            k8s_ran = True

        _write_infra_partition(run_root, config, generators, deployment, filters)
        _write_kubernetes_nodes_listing(run_root, k8s_ran)
             
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
        
        if not system_exps:
             logging.warning(f"No experiments for system {system}?")
             continue
        
        # Simplify: Pick the first benchmark and assume all experiments for this system use it.
        bench = system_exps[0].bench or getattr(config, "bench", None) or config.extra.get("bench", "")
        
        # A. Build & Deploy (Moved before Tuning so images exist)
        try:
            # Build Step
            path_hash = hashlib.sha256(str(Path(config.output_base_dir).resolve()).encode()).hexdigest()[:8]
            tag_base = f"{config.experiment_index}-{path_hash}"
            tag = _safe_name(tag_base)
            logging.info(f"Tag: {tag}")
            
            # Status file in the run directory (separate marker when NanoLog binary required)
            _bsuf = "_nanolog" if config.nanolog_debug else ""
            build_status_file = run_root / f"build_success_{tag}{_bsuf}"
            
            # Check build
            if not build_status_file.exists():
                build_log = logs_dir / f"build_{system}_{_timestamp()}.log"
                runner.build_system(bench, system, tag, build_status_file, log_path=build_log)
            else:
                logging.info(f"Build for tag {tag} already successful. Skipping.")

        except Exception as e:
            logging.error(f"Skipping system {system} due to build failure: {e}")
            continue

        # B. Tuning
        best_params_file = tuning_dir / "best_params.json"
        if best_params_file.exists():
            logging.info(f"Found existing best parameters at {best_params_file}. Skipping tuning.")
            try:
                tuning_params = json.loads(best_params_file.read_text())
            except Exception as e:
                logging.warning(f"Failed to read best params from {best_params_file}: {e}. Proceeding with tuning.")
                tuning_params = _run_tuner(system, bench, tuning_dir, logs_dir, config_path, tag=tag, generators=generators, deployment=deployment)
        else:
            tuning_params = _run_tuner(system, bench, tuning_dir, logs_dir, config_path, tag=tag, generators=generators, deployment=deployment)
        # Extract 'parameters' key if present, otherwise assume the whole dict is properties
        deploy_params = tuning_params.get("parameters", tuning_params)
        
        if deploy_params:
            try:
                (tuning_dir / "best_params.json").write_text(json.dumps(deploy_params, indent=2))
                logging.info(f"Persisted best parameters to {tuning_dir / 'best_params.json'}")
            except Exception as e:
                logging.warning(f"Failed to persist best params: {e}")
        
        # C. Run Experiments
        try:
            for exp in system_exps:
                logging.info(f"  Running Experiment: {exp.name}")
                exp_dir = run_root / _safe_name(exp.name)
                exp_dir.mkdir(parents=True, exist_ok=True)
                
                # Get params for this experiment's benchmark
                # deploy_params is already set above for the single 'bench'
                unit_generator_hosts = [generators[i % len(generators)] for i in range(len(exp.apis))]
                for unit in _expand_experiment_to_units(exp, config, unit_generator_hosts, deployment):
                    unit_dir = exp_dir / unit.safe_name()
                    unit_dir.mkdir(parents=True, exist_ok=True)
                    
                    # Repeats
                    start_r, end_r = _get_next_repeat_range(unit_dir, unit.repeats)
                    for r in range(start_r, end_r):
                        repeat_dir = unit_dir / f"repeat_{r:03d}"
                        repeat_dir.mkdir(parents=True, exist_ok=True)
                        
                        try:
                            # Merge tuning params with experiment-specific env vars
                            # Priority: deploy_env (exp config) > deploy_params (tuning result)
                            raw_env = unit.params.get("deploy_env")
                            extra_env = raw_env if isinstance(raw_env, dict) else {}
                            final_env_vars = {**deploy_params, **extra_env}

                            # Deploy per repeat
                            deploy_log = logs_dir / f"deploy_{system}_{unit.safe_name()}_r{r}_{_timestamp()}.log"
                            runner.deploy_system(bench, system, final_env_vars, deployment, tag=tag, log_path=deploy_log)
                            
                            wait_sec = getattr(config, "post_deploy_wait_sec", 0.1)
                            if wait_sec > 0:
                                logging.info(f"    Waiting {wait_sec}s for service readiness...")
                                time.sleep(wait_sec)
                            
                            # Add tuning metadata
                            unit.metadata["tuning_params"] = deploy_params
                            
                            # Run
                            res = runner.run(unit, repeat_dir)
                            
                            # Failure Handling from Run (application failure)
                            if res.status == "error":
                                logging.warning(f"    Repeat {r} failed. Status: {res.status}")
                            
                            # Collect
                            col_res = collector.collect(unit, res, repeat_dir)
                            _log_result(summary_csv, summary_jsonl, exp, unit, res, col_res, r, unit.repeats)
                            run_results.append(res)
                            
                        except Exception as e:
                            logging.error(f"Experiment {exp.name} Unit {unit.name} Repeat {r} failed: {e}")
                            # Mark as failed in logs?
                            # Create a failed result to log
                            fail_res = RunResult(
                                unit_name=unit.name,
                                status="error",
                                raw_artifact_dir=str(repeat_dir / "raw"), # approximation
                                details={"error": str(e), "traceback": tb.format_exc()},
                                repeat_index=r
                            )
                            # Create dummy collector result
                            fail_col = CollectorResult(unit.name, str(repeat_dir/"metrics"), [], "failed")
                            _log_result(summary_csv, summary_jsonl, exp, unit, fail_res, fail_col, r, unit.repeats)
                            
                            # Log to main error file
                            try:
                                with (logs_dir / f"errors_{system}_{_timestamp()}.log").open("a") as ef:
                                    ef.write(f"Ref: {exp.name}/{unit.name}/{r}\n")
                                    ef.write(tb.format_exc() + "\n")
                            except: pass
                            
                        finally:
                            # Ensure Teardown per repeat
                            td_log = logs_dir / f"teardown_{system}_{unit.safe_name()}_r{r}_{_timestamp()}.log"
                            try:
                                runner.teardown_system(bench, system, deployment, log_path=td_log)
                            except Exception as te:
                                logging.warning(f"Teardown failed for repeat {r}: {te}")

        except Exception as e:
            logging.error(f"System {system} loop aborted (Unexpected top-level error): {e}", exc_info=True)

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
    p.add_argument("--only-system", help="Comma-separated list of systems (plain, sidecar).")
    p.add_argument("--only-num-apis", help="Comma-separated list of API counts (e.g. 1,3).")
    p.add_argument("--output-base-dir", help="Override output_base_dir from config.json")
    p.add_argument("--hosts-file", help="Override hosts_file from config.json")
    p.add_argument("--num-generators", type=int, help="Override num_generators from config.json")
    p.add_argument("--shared-generator", action="store_true", help="Allow fewer generators than APIs; assign round-robin")
    p.add_argument("--nanolog-debug", action="store_true", help="Build sidecar with NanoLog metrics; collect/decompress/plot for sidecar units.")
    return p.parse_args(argv)

def main(argv: List[str] | None = None) -> int:
    ns = parse_args(argv or sys.argv[1:])
    config = load_config(ns.config)
    if ns.output_base_dir:
        config = dc_replace(config, output_base_dir=ns.output_base_dir)
    if ns.hosts_file:
        config = dc_replace(config, hosts_file=ns.hosts_file)
    if ns.num_generators is not None:
        config = dc_replace(config, num_generators=ns.num_generators)
    if getattr(ns, "nanolog_debug", False):
        config = dc_replace(config, nanolog_debug=True)
    cfg_path = Path(ns.config) if ns.config else Path("config.json")
    
    filters = {
        "only_names": ns.only_names,
        "only_types": ns.only_types,
        "name_contains": ns.name_contains,
        "only_system": ns.only_system,
        "only_num_apis": ns.only_num_apis,
        "shared_generator": ns.shared_generator,
    }
    
    return execute(Path(ns.experiments_file), config, cfg_path, filters)

if __name__ == "__main__":
    sys.exit(main())
