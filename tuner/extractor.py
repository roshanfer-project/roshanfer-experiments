from __future__ import annotations

import json
import subprocess
import os

def extract_metrics_from_output(rwg_output: str):
    overall_output = f"{os.path.dirname(rwg_output)}/overall.json"

    # run parser
    subprocess.run([
        "./rwg/rwg", "parse",
        "--rwg_output", rwg_output,
        "--slo", "60",
        "--version", "2",
        "--overall_output", overall_output,
        "--warmup", "5"
    ])

    with open(overall_output, "r") as f:
        data = json.loads(f.read())
    
    final_goodput = -2000
    final_tail = 2000
    
    if data["num_errors"] >= 1:
        print(f"Found {data['num_errors']} errors in the data.")
        return final_goodput, final_tail

    final_goodput = data["goodput"]
    final_tail = data["p95_latency"]

    return final_goodput, final_tail