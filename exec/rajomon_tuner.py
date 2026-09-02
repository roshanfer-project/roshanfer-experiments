"""
Template Tuner for Experiment Runner.
Located in 'exec' package to reuse functionality.
"""

import argparse
import json
import logging
import pprint
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

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
        "social": "compose-post",
    },
    "tuner_base": 1000,
    "tuner_rate": 8000,
}

# Keys merged from config "tuner" object (excluding tune_api).
_TUNER_KNOB_KEYS = (
    "initial_point",
    "n_iter",
    "maximum_goodput",
    "tuner_base",
    "tuner_rate",
    "trial_duration_sec",
    "trial_warmup_sec",
    "trial_cooldown_sec",
    "trial_collector_freq",
)

_DEFAULT_KNOBS: Dict[str, Any] = {
    "initial_point": tuner_parameters["initial_point"],
    "n_iter": tuner_parameters["n_iter"],
    "maximum_goodput": tuner_parameters["maximum_goodput"],
    "tuner_base": tuner_parameters["tuner_base"],
    "tuner_rate": tuner_parameters["tuner_rate"],
    "trial_duration_sec": 10,
    "trial_warmup_sec": 2,
    "trial_cooldown_sec": 0,
    "trial_collector_freq": 0,
}

try:
    from bayes_opt import BayesianOptimization
except ImportError:
    print("Please install bayesian-optimization.")
    BayesianOptimization = None


def _resolve_tune_api(config: Config, bench: str) -> str:
    t = config.tuner
    if isinstance(t, dict):
        v = t.get("tune_api")
        if isinstance(v, str) and v.strip():
            return v.strip()
    mapped = tuner_parameters["tuner_api"].get(bench)
    if isinstance(mapped, str) and mapped.strip():
        return mapped.strip()
    raise ValueError(
        f"Rajomon tuning needs tuner.tune_api in config.json for bench {bench!r}, "
        f"or bench must be in the legacy tuner_api map (hotel, social)."
    )


def _coerce_knob(key: str, val: Any) -> Any:
    if key == "maximum_goodput":
        return int(val) if val is not None else _DEFAULT_KNOBS[key]
    return int(val)


def _effective_tuner_knobs(config: Config) -> Dict[str, Any]:
    out = dict(_DEFAULT_KNOBS)
    raw = config.tuner
    if not isinstance(raw, dict):
        return out
    for k in _TUNER_KNOB_KEYS:
        if k not in raw or raw[k] is None:
            continue
        try:
            out[k] = _coerce_knob(k, raw[k])
        except (TypeError, ValueError):
            logging.warning("Ignoring invalid tuner.%s: %r", k, raw[k])
    return out


def _overall_json_path(tune_api: str, trial_dir: Path, col_metrics_files: List[str]) -> Optional[Path]:
    direct = trial_dir / "output" / f"overall-{tune_api}.json"
    if direct.exists():
        return direct
    for p in col_metrics_files:
        path = Path(p)
        if path.name == f"overall-{tune_api}.json":
            return path
    return None


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
    tune_api: str,
    knobs: Dict[str, Any],
    logs_dir: Optional[Path] = None,
    logger: Optional[logging.Logger] = None,
) -> float:
    if logger:
        logger.info(f"[Trial] Testing params: {params}")
    else:
        print(f"  [Trial] Testing params: {params}")

    unit = RunUnit(
        name=f"tuning-trial-{system}",
        type="tuning",
        script="run-plain.sh",
        base=int(knobs["tuner_base"]),
        rate=int(knobs["tuner_rate"]),
        duration=int(knobs["trial_duration_sec"]),
        system=system,
        apis=[tune_api],
        bench=bench,
        services=[],
        repeats=1,
        generator_hosts=infra_gen,
        deployment_hosts=infra_deploy,
        collector_freq=int(knobs["trial_collector_freq"]),
        warmup=int(knobs["trial_warmup_sec"]),
        cooldown=int(knobs["trial_cooldown_sec"]),
    )

    deploy_log = None
    teardown_log = None

    if logs_dir:
        trial_id = _hash_params(params)
        deploy_log = logs_dir / f"tuner_deploy_{system}_{trial_id}.log"
        teardown_log = logs_dir / f"tuner_teardown_{system}_{trial_id}.log"

    trial_dir: Optional[Path] = None
    try:
        runner.deploy_system(bench, system, params, infra_deploy, tag=tag, log_path=deploy_log, quiet=True)

        trial_dir = Path(config.output_base_dir) / "tuning_trials" / f"{system}_{_hash_params(params)}"
        trial_dir.mkdir(parents=True, exist_ok=True)

        res = runner.run(unit, trial_dir)

        if res.status != "success":
            msg = f"Trial failed. (Status: {res.status})"

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

            if logger:
                logger.warning(msg)
            else:
                print(msg)

            if trial_dir is not None:
                try:
                    raw_dir = trial_dir / "raw"
                    collector._collect_service_logs(unit, raw_dir)
                except Exception as e:
                    if logger:
                        logger.warning(f"Failed to collect service logs: {e}")

            if logs_dir and trial_dir is not None:
                failed_trials_dir = logs_dir / "failed_trials"
                failed_trials_dir.mkdir(parents=True, exist_ok=True)

                import shutil
                from datetime import datetime

                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                target_path = failed_trials_dir / f"fail_{system}_{_hash_params(params)}_{ts}"

                shutil.move(str(trial_dir), str(target_path))
                if logger:
                    logger.info(f"Failed trial artifacts saved to: {target_path}")

            return -1.0

        col_res = collector.collect(unit, res, trial_dir, collect_service_logs=False)

        overall_file = _overall_json_path(tune_api, trial_dir, col_res.metrics_files)
        if not overall_file or not overall_file.exists():
            raise FileNotFoundError(f"Overall metrics not found for tune_api={tune_api!r}")

        data = json.loads(overall_file.read_text())
        overall_blob = json.dumps(data, indent=2, sort_keys=True)
        if logger:
            logger.info("[Trial] overall-%s.json:\n%s", tune_api, overall_blob)
        else:
            print(f"  [Trial] overall-{tune_api}.json:\n{overall_blob}")

        goodput = data["goodput"]
        p99 = data["p99_latency"]

        max_gp = int(knobs["maximum_goodput"])
        lat_term = max(0, p99-data["slo_ms"])
        score = (goodput - 10 * lat_term) / max_gp
        if logger:
            logger.info(f"Score: {score} (Goodput: {goodput}, P99: {p99})")

        import shutil

        shutil.rmtree(trial_dir)

        return float(score)

    except Exception as e:
        msg = f"Trial Exception: {e}"
        if deploy_log:
            msg += f" See deployment log: {deploy_log}"
        if logger:
            logger.error(msg)
        else:
            print(msg)

        if trial_dir is not None:
            try:
                raw_dir = trial_dir / "raw"
                collector._collect_service_logs(unit, raw_dir)
            except Exception as ce:
                if logger:
                    logger.warning(f"Failed to collect service logs in exception handler: {ce}")

        if logs_dir and trial_dir is not None:
            failed_trials_dir = logs_dir / "failed_trials"
            failed_trials_dir.mkdir(parents=True, exist_ok=True)

            import shutil
            from datetime import datetime

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            target_path = failed_trials_dir / f"fail_{system}_{_hash_params(params)}_{ts}_exception"

            try:
                shutil.move(str(trial_dir), str(target_path))
                if logger:
                    logger.info(f"Failed trial artifacts (exception) saved to: {target_path}")
            except Exception as me:
                if logger:
                    logger.warning(f"Failed to move failed trial artifacts: {me}")

        return -10000.0
    finally:
        try:
            if logger:
                logger.info("Initiating teardown...")
            runner.teardown_system(bench, system, infra_deploy, log_path=teardown_log, quiet=True)
            if logger:
                logger.info("Teardown completed.")
        except Exception as e:
            if logger:
                logger.warning(f"Trial Teardown Failed: {e}")
            else:
                print(f"    Trial Teardown Failed: {e}")


def _hash_params(p: Dict[str, Any]) -> str:
    import hashlib

    return hashlib.md5(json.dumps(p, sort_keys=True).encode()).hexdigest()[:8]


def _log_slug(s: str) -> str:
    """Safe single path segment for log filenames (bench may contain /)."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)


def optimize_system(
    config_path: str,
    system: str,
    bench: str,
    tag: str = "latest",
    generators: Optional[List[str]] = None,
    deployment: Optional[List[str]] = None,
    logs_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    config = load_config(config_path)
    tune_api = _resolve_tune_api(config, bench)
    knobs = _effective_tuner_knobs(config)

    runner = Runner(config)
    collector = Collector(config)

    root_logger = logging.getLogger()
    tuner_handler = None

    if logs_dir:
        log_file = logs_dir / f"tuner_{_log_slug(system)}_{_log_slug(bench)}.log"
        tuner_handler = logging.FileHandler(log_file)
        tuner_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        root_logger.addHandler(tuner_handler)

    logging.info(f"Starting tuning for {system} on {bench} (tune_api={tune_api})")

    if not generators or not deployment:
        infra = InfraBuilder(Path(config.hosts_file))
        gens, deploys = infra.partition_hosts(1)
    else:
        gens, deploys = generators, deployment

    pbounds_rajomon = {
        "priceUpdateRate": (1000, 10000),
        "tokenUpdateRate": (1000, 50000),
        "priceStep": (1, 400),
        "latencyThreshold": (700, 10000),
        "tokenUpdateStep": (1, 30),
    }

    def objective(
        priceUpdateRate,
        tokenUpdateRate,
        latencyThreshold,
        priceStep,
        tokenUpdateStep,
    ):
        return run_trial_experiment(
            config,
            runner,
            collector,
            gens,
            deploys,
            bench,
            system,
            {
                "priceUpdateRate": int(priceUpdateRate),
                "tokenUpdateRate": int(tokenUpdateRate),
                "priceStep": int(priceStep),
                "latencyThreshold": int(latencyThreshold),
                "tokenUpdateStep": int(tokenUpdateStep),
            },
            tag=tag,
            tune_api=tune_api,
            knobs=knobs,
            logs_dir=logs_dir,
            logger=logging.getLogger(),
        )

    try:
        if not BayesianOptimization:
            logging.error("BayesianOptimization not available.")
            return {}

        optimizer = BayesianOptimization(
            f=objective,
            pbounds=pbounds_rajomon,
            random_state=1,
            allow_duplicate_points=True,
        )
        optimizer.maximize(
            init_points=int(knobs["initial_point"]),
            n_iter=int(knobs["n_iter"]),
        )

        logging.info(f"Best params: {optimizer.max['params']}")
        return optimizer.max["params"]
    finally:
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
