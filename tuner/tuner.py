from glob import glob
import os
from time import time
from bayes_opt import BayesianOptimization
import json
from .runner import run_experiment
from .extractor import extract_metrics_from_output
import sys

tuner_parameters = {
    "initial_point": 50,
    "n_iter": 250,
    "method": "dodo",
    "max_goodput": 4500,
    "system": "dodo",
    "bench": "dodo",
}

rajomon_config = {
    'price_update_rate': 5000,      # How often prices are updated
    'token_update_rate': 5000,      # How often tokens are updated  
    'latency_threshold': 5000,      # Latency threshold for decisions
    'price_step': 10,               # Step size for price adjustments
    'price_strategy': 'expdecay',   # Price adjustment strategy
    'lazy_update': 'false',         # Whether to use lazy updates
    'rate_limiting': 'true',        # Enable rate limiting
    'only_frontend': 'false',       # Apply only to frontend
    'fast_drop': 'false'           # Enable fast dropping
}

pbounds_breakwater = {
    'breakwaterSLO': (5000, 5000),
    'breakwaterClientExpiration': (300, 300),
    'breakwaterAFactor': (0, 2),
    'breakwaterBFactor': (0, 1),
    'breakwaterRTT_MICROSECOND': (3000, 5000)
}

pbounds_dagor = {
    'Alpha': (0, 1),
    'Beta': (0, 1),
    'QueuingThresh': (1000, 5000),
    'AdmissionLevelUpdateInterval': (5000, 15000),
    'AddmissionUpdateN': (80, 500)
}

pbounds_rajomon = {
    'priceUpdateRate': (1000, 10000),    # Price update frequency range (us)
    'tokenUpdateRate': (1000, 50000),   # Token update frequency range (us)
    'priceStep': (1, 400),               # Price step size range
    'latencyThreshold': (700, 10000),         # Latency threshold range (us)
    'tokenUpdateStep': (1, 30)
}

def objective_breakwater(breakwaterSLO, breakwaterClientExpiration, breakwaterAFactor, breakwaterBFactor, breakwaterRTT_MICROSECOND):
    # Run experiment with given parameters
    path, slo = run_experiment(
        tuner_parameters["bench"],
        "breakwater",
        tuner_parameters["method"],
        {
        'breakwaterSLO': breakwaterSLO,
        'breakwaterClientExpiration': breakwaterClientExpiration,
        'breakwaterAFactor': breakwaterAFactor,
        'breakwaterBFactor': breakwaterBFactor,
        'breakwaterRTT_MICROSECOND': breakwaterRTT_MICROSECOND
    })

    # Calculate metrics from results
    goodput, tail_latency = extract_metrics_from_output(path, slo)

    # Objective function combines goodput and latency penalty
    objective = goodput - 10 * tail_latency

    return objective / tuner_parameters["max_goodput"]

def objective_dagor(Alpha, Beta, QueuingThresh, AdmissionLevelUpdateInterval, AddmissionUpdateN):
    # Run experiment with given parameters
    path, slo = run_experiment(
        tuner_parameters["bench"],
        "dagor",
        tuner_parameters["method"],
        {
            'Alpha': Alpha,
            'Beta': Beta,
            'QueuingThresh': QueuingThresh,
            'AdmissionLevelUpdateInterval': AdmissionLevelUpdateInterval,
            'AddmissionUpdateN': AddmissionUpdateN
        }
    )

    # Calculate metrics from results
    goodput, tail_latency = extract_metrics_from_output(path, slo)

    # Objective function combines goodput and latency penalty
    if tuner_parameters["bench"] == "social":
        objective = goodput - 5 * tail_latency
    else:
        objective = goodput - 10 * tail_latency

    return objective / tuner_parameters["max_goodput"]

def objective_rajomon(priceUpdateRate, tokenUpdateRate, latencyThreshold, priceStep, tokenUpdateStep):
    # Run experiment with given parameters
    path, slo = run_experiment(
        tuner_parameters["bench"],
        "rajomon",
        tuner_parameters["method"],
        {
        'priceUpdateRate': priceUpdateRate,
        'tokenUpdateRate': tokenUpdateRate,
        'latencyThreshold': latencyThreshold,
        'priceStep': priceStep,
        'tokenUpdateStep': tokenUpdateStep
    })

    # Calculate metrics from results
    goodput, tail_latency = extract_metrics_from_output(path, slo)

    # Objective function combines goodput and latency penalty
    if tuner_parameters["bench"] == "rajomon":
        objective = goodput - 5 * tail_latency
    else:
        objective = goodput - 10 * tail_latency

    return objective / tuner_parameters["max_goodput"]

def load_optimal_parameters(method):
    # load most recent paramters from a file in this format: bopt_rajomon_<method>_<timestamp>.json
    files = glob(f'rajomon_tune_run/bopt_{tuner_parameters["bench"]}_{tuner_parameters["system"]}_{method}_*.json')
    if not files:
        raise Exception("not found")
    latest_file = max(files, key=os.path.getctime)
    with open(latest_file, 'r') as f:
        return json.load(f)

def save_results(filename, results):
    os.makedirs('rajomon_tune_run', exist_ok=True)
    with open(f'rajomon_tune_run/{filename}', 'w') as f:
        json.dump(results, f, indent=2)

def optimize_rajomon():

    if tuner_parameters["system"] == "breakwater":
        func = objective_breakwater
        bounds = pbounds_breakwater
    elif tuner_parameters["system"] == "rajomon":
        func = objective_rajomon
        bounds = pbounds_rajomon
    elif tuner_parameters["system"] == "dagor":
        func = objective_dagor
        bounds = pbounds_dagor
    else:
        raise ValueError(f"Unknown system: {tuner_parameters['system']}")

    # Initialize Bayesian optimizer
    optimizer = BayesianOptimization(
        f=func,
        pbounds=bounds,
        random_state=1,
        allow_duplicate_points=True
    )
   
    # Load previous best results if available
    try:
        previous_results = load_optimal_parameters(tuner_parameters["method"])
        if previous_results:
            optimizer.probe(params=previous_results["parameters"])
    except:
        print("Starting fresh optimization")
   # Run optimization
    optimizer.maximize(init_points=tuner_parameters["initial_point"], n_iter=tuner_parameters["n_iter"])

    # Save best parameters
    best_params = optimizer.max['params']
    timestamp = int(time())
    results = {
        'target': optimizer.max['target'],
        'parameters': best_params,
        'method': tuner_parameters["method"],
        'timestamp': timestamp,
        'tuner_parameters': tuner_parameters
    }

    save_results(f'bopt_{tuner_parameters["bench"]}_{tuner_parameters["system"]}_{tuner_parameters["method"]}_{timestamp}.json', results)

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python tuner.py <system> <bench> <method>")
        sys.exit(1)

    tuner_parameters["system"] = sys.argv[1]
    tuner_parameters["bench"] = sys.argv[2]
    tuner_parameters["method"] = sys.argv[3]
    optimize_rajomon()