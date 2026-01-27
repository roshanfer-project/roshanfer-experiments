"""
Template Tuner for Experiment Runner.
Located in 'exec' package to reuse functionality.
"""

import argparse
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

# Reuse framework components
# Note: When identifying as a module 'exec.tuner_template', imports work relative or absolute.
from .config import load_config, Config
from .runner import Runner
from .infra import InfraBuilder
from .models import RunUnit
from .collector import Collector

tuner_parameters = {
    "initial_point": 10,
    "n_iter": 50,
    "maximum_goodput": 5000,
    "tuner_api": {
        "hotel": "search-hotel",
        "social": "compose-post"
    },
    "tuner_base": 1000,
    "tuner_rate": 5000
}

# Ensure bayes_opt is installed
try:
    from bayes_opt import BayesianOptimization
except ImportError:
    print("Please install bayesian-optimization.")
    BayesianOptimization = None

def run_trial_experiment(
    config: Config, 
    runner: Runner, 
    collector: Collector,
    infra_gen: List[str],
    infra_deploy: List[str],
    bench: str, 
    system: str, 
    params: Dict[str, Any],
    tag: str,
    logs_dir: Optional[Path] = None,
    logger: logging.Logger = None
) -> float:
    """
    Execute a short trial run using the Runner.
    """
    if logger:
        logger.info(f"[Trial] Testing params: {params}")
    else:
        print(f"  [Trial] Testing params: {params}")
    
    # 1. Prepare a temporary RunUnit
    # We create a short 'trial' unit.
    unit = RunUnit(
        name=f"tuning-trial-{system}",
        type="tuning",
        script="run-plain.sh",
        base=tuner_parameters["tuner_base"], 
        rate=tuner_parameters["tuner_rate"],
        duration=10, # Tuner trial duration
        system=system,
        apis=[tuner_parameters["tuner_api"][bench]],
        bench=bench,
        services=[],
        repeats=1,
        generator_hosts=infra_gen,
        deployment_hosts=infra_deploy,
        collector_freq=0,
        warmup=2,
        cooldown=0
    )

    deploy_log = None
    teardown_log = None
    
    if logs_dir:
         # Use a hash or timestamp to differentiate logs per trial
         trial_id = _hash_params(params)
         deploy_log = logs_dir / f"tuner_deploy_{system}_{trial_id}.log"
         teardown_log = logs_dir / f"tuner_teardown_{system}_{trial_id}.log"

    try:
        # 2. Deploy (quietly)
        runner.deploy_system(bench, system, params, infra_deploy, tag=tag, log_path=deploy_log, quiet=True)
        
        # 3. Run
        # We need a directory for results
        trial_dir = Path(config.output_base_dir) / "tuning_trials" / f"{system}_{_hash_params(params)}"
        trial_dir.mkdir(parents=True, exist_ok=True)
        
        res = runner.run(unit, trial_dir)
        
        if res.status != "success":
            msg = f"Trial failed. (Status: {res.status})"
            
            # Aggregate errors
            errors = []
            if res.details.get("error"):
                 errors.append(res.details.get("error"))
            for k, v in res.details.items():
                if k.startswith("error_"):
                    errors.append(f"{k}: {v}")
            
            if errors:
                msg += " Errors: " + "; ".join(errors)
            
            if deploy_log:
                 msg += f". Deployment log: {deploy_log}"
            
            if logger: logger.warning(msg)
            else: print(msg)
            
            # --- FAILURE HANDLING ---
            # 1. Collect Service Logs to trial_dir/raw/service_logs
            try:
                raw_dir = trial_dir / "raw" # Config default
                collector._collect_service_logs(unit, raw_dir)
            except Exception as e:
                if logger: logger.warning(f"Failed to collect service logs: {e}")

            # 2. Move to Failed Trials Directory
            if logs_dir:
                failed_trials_dir = logs_dir / "failed_trials"
                failed_trials_dir.mkdir(parents=True, exist_ok=True)
                
                # Move trial_dir to failed_trials_dir / <system>_<hash>
                import shutil
                from datetime import datetime
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                target_path = failed_trials_dir / f"fail_{system}_{_hash_params(params)}_{ts}"
                
                shutil.move(str(trial_dir), str(target_path))
                if logger: logger.info(f"Failed trial artifacts saved to: {target_path}")

            return -1.0 # Penalize failure

        # 4. Collect & Score (Success Case)
        # Skip service logs for success to save space
        col_res = collector.collect(unit, res, trial_dir, collect_service_logs=False)
        
        # Parse metrics to get score (e.g., P99 latency)
        # assuming collector generated overall-{api}.json
        overall_file = Path(col_res.metrics_files[0]) if col_res.metrics_files else None
        if not overall_file or not overall_file.exists():
            raise Exception("Overall metrics file not found")
        
        data = json.loads(overall_file.read_text())
        goodput = data["goodput"]
        p99 = data["p99_latency"]
        
        score = (goodput - 5 * p99) / tuner_parameters["maximum_goodput"]
        if logger: logger.info(f"Score: {score} (Goodput: {goodput}, P99: {p99})")
        
        # Cleanup Success Trial to save space
        import shutil
        shutil.rmtree(trial_dir)
        
        return score
            
    except Exception as e:
        msg = f"Trial Exception: {e}"
        if deploy_log:
             msg += f" See deployment log: {deploy_log}"
        if logger: logger.error(msg)
        else: print(msg)
        
        # --- FAILURE HANDLING (Exception Case) ---
        # 1. Collect Service Logs
        try:
            raw_dir = trial_dir / "raw"
            collector._collect_service_logs(unit, raw_dir)
        except Exception as ce:
            if logger: logger.warning(f"Failed to collect service logs in exception handler: {ce}")

        # 2. Move to Failed Trials Directory
        if logs_dir:
            failed_trials_dir = logs_dir / "failed_trials"
            failed_trials_dir.mkdir(parents=True, exist_ok=True)
            
            import shutil
            from datetime import datetime
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            target_path = failed_trials_dir / f"fail_{system}_{_hash_params(params)}_{ts}_exception"
            
            try:
                shutil.move(str(trial_dir), str(target_path))
                if logger: logger.info(f"Failed trial artifacts (exception) saved to: {target_path}")
            except Exception as me:
                if logger: logger.warning(f"Failed to move failed trial artifacts: {me}")

        return -10000.0
    finally:
        # Ensure Teardown (quietly)
        try:
             if logger: logger.info("Initiating teardown...")
             runner.teardown_system(bench, system, infra_deploy, log_path=teardown_log, quiet=True)
             if logger: logger.info("Teardown completed.")
        except Exception as e:
             if logger: logger.warning(f"Trial Teardown Failed: {e}")
             else: print(f"    Trial Teardown Failed: {e}")

def _hash_params(p: Dict[str, Any]) -> str:
    import hashlib
    return hashlib.md5(json.dumps(p, sort_keys=True).encode()).hexdigest()[:8]

def optimize_system(config_path: str, system: str, bench: str, tag: str = "latest", generators: List[str] = None, deployment: List[str] = None, logs_dir: Path = None) -> Dict[str, Any]:
    # Bootstrap Framework
    config = load_config(config_path)
    runner = Runner(config)
    collector = Collector(config)
    
    # Setup Tuner Logger - Attach to ROOT logger to capture Runner output
    root_logger = logging.getLogger()
    tuner_handler = None
    
    if logs_dir:
        log_file = logs_dir / f"tuner_{system}_{bench}.log"
        tuner_handler = logging.FileHandler(log_file)
        tuner_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        root_logger.addHandler(tuner_handler)
    
    logging.info(f"Starting tuning for {system} on {bench}")
    
    # Use passed hosts or fallback to reading file (but don't fail if passed)
    if not generators or not deployment:
         # Fallback (old behavior) if called standalone without lists?
         infra = InfraBuilder(Path(config.hosts_file))
         gens, deploys = infra.partition_hosts(1)
    else:
         gens, deploys = generators, deployment
    
    pbounds_rajomon = {
        'priceUpdateRate': (1000, 10000),    # Price update frequency range (us)
        'tokenUpdateRate': (1000, 50000),   # Token update frequency range (us)
        'priceStep': (1, 400),               # Price step size range
        'latencyThreshold': (700, 10000),         # Latency threshold range (us)
        'tokenUpdateStep': (1, 30)
    }

    def objective(priceUpdateRate, tokenUpdateRate, latencyThreshold, priceStep, tokenUpdateStep):
        # We pass the root logger (or None, since it's global logging now)
        return run_trial_experiment(config, runner, collector, gens, deploys, bench, system, {
            'priceUpdateRate': int(priceUpdateRate),
            'tokenUpdateRate': int(tokenUpdateRate),
            'priceStep': int(priceStep),
            'latencyThreshold': int(latencyThreshold),
            'tokenUpdateStep': int(tokenUpdateStep)
        }, tag=tag, logs_dir=logs_dir, logger=logging.getLogger()) # Use root logger adapter

    try:
        if not BayesianOptimization:
            logging.error("BayesianOptimization not available.")
            return {}

        optimizer = BayesianOptimization(f=objective, pbounds=pbounds_rajomon, random_state=1, allow_duplicate_points=True)
        optimizer.maximize(init_points=tuner_parameters["initial_point"], n_iter=tuner_parameters["n_iter"])
        
        logging.info(f"Best params: {optimizer.max['params']}")
        return optimizer.max['params']
    finally:
        # Cleanup Handler
        if tuner_handler:
            root_logger.removeHandler(tuner_handler)
            tuner_handler.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--system", required=True)
    parser.add_argument("--bench", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--config", required=True, help="Path to config.json")
    args = parser.parse_args()

    best_params = optimize_system(args.config, args.system, args.bench)
    
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"parameters": best_params}, indent=2))

if __name__ == "__main__":
    main()
