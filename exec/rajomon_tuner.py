"""
Template Tuner for Experiment Runner.
Located in 'exec' package to reuse functionality.
"""

import argparse
import json
import logging
from pathlib import Path
from typing import Dict, Any, List

# Reuse framework components
# Note: When identifying as a module 'exec.tuner_template', imports work relative or absolute.
from .config import load_config, Config
from .runner import Runner
from .infra import InfraBuilder
from .models import RunUnit
from .collector import Collector

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
    params: Dict[str, Any]
) -> float:
    """
    Execute a short trial run using the Runner.
    """
    print(f"  [Trial] Testing params: {params}")
    
    # 1. Prepare a temporary RunUnit
    # We create a short 'trial' unit.
    unit = RunUnit(
        name=f"tuning-trial-{system}",
        type="tuning",
        script=None, # Use default wrapper or specific tuning script
        base=0, 
        rate=params.get("rate", 1000), # Example: tune rate or just use fixed?
        duration=10, # Short duration for tuning
        system=system,
        apis=["app"], # Assuming single API or passed via args
        bench=bench,
        services=[], # Fill if needed
        repeats=1,
        generator_hosts=infra_gen,
        deployment_hosts=infra_deploy,
        collector_freq=0, # No realtime needed usually
        warmup=2,
        cooldown=0
    )

    try:
        # 2. Deploy (with current params)
        runner.deploy_system(bench, system, params, infra_deploy)
        
        # 3. Run
        # We need a directory for results
        trial_dir = Path(config.output_base_dir) / "tuning_trials" / f"{system}_{_hash_params(params)}"
        trial_dir.mkdir(parents=True, exist_ok=True)
        
        res = runner.run(unit, trial_dir)
        
        if res.status != "success":
            print("    Trial failed.")
            return -1.0 # Penalize failure

        # 4. Collect & Score
        # Collector checks output/overall-*.json
        col_res = collector.collect(unit, res, trial_dir)
        
        # Parse metrics to get score (e.g., P99 latency)
        # assuming collector generated overall-{api}.json
        overall_file = Path(col_res.metrics_files[0]) if col_res.metrics_files else None
        if overall_file and overall_file.exists():
            data = json.loads(overall_file.read_text())
            # Example objective: Maximize Throughput / Latency (simple reward)
            # Or just minimize Latency (return negative)
            p99 = data.get("p99", 10000)
            return -p99 # Maximize negative latency => Minimize latency
            
    except Exception as e:
        print(f"    Trial Exception: {e}")
        return -10000.0
    finally:
        # cleanup if needed, but maybe keep deployment for next trial if only params change?
        # Runner.deploy_system usually redeploys.
        pass

    return 0.0

def _hash_params(p: Dict[str, Any]) -> str:
    import hashlib
    return hashlib.md5(json.dumps(p, sort_keys=True).encode()).hexdigest()[:8]

def optimize_system(config_path: str, system: str, bench: str) -> Dict[str, Any]:
    # Bootstrap Framework
    config = load_config(config_path)
    runner = Runner(config)
    collector = Collector(config)
    infra = InfraBuilder(Path(config.hosts_file))
    
    # Partition hosts (reuse logic: 1 gen, rest deploy? or 1 and 1)
    # Tuning might need fewer resources or same.
    gens, deploys = infra.partition_hosts(1)
    
    pbounds = {
        'param_a': (1, 10),
        'param_b': (100, 500),
    }

    def objective(param_a, param_b):
        p = {"param_a": int(param_a), "param_b": int(param_b)}
        return run_trial_experiment(config, runner, collector, gens, deploys, bench, system, p)

    if not BayesianOptimization:
        return {}

    optimizer = BayesianOptimization(f=objective, pbounds=pbounds, random_state=1)
    optimizer.maximize(init_points=2, n_iter=2)
    
    return optimizer.max['params']

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
