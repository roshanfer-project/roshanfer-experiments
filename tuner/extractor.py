from __future__ import annotations

import json
import subprocess
import os

def extract_metrics_from_output(rwg_output: str, slo: str) -> tuple[float, float]:
    overall_output = f"{os.path.dirname(rwg_output)}/overall.json"

    # run parser
    subprocess.run([
        "./rwg/rwg", "parse",
        "--rwg_output", rwg_output,
        "--slo", slo,
        "--version", "2",
        "--overall_output", overall_output,
    ],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL)

    final_goodput = -2000
    final_tail = 2000

    if not os.path.exists(overall_output):
        print(f"Overall output file {overall_output} does not exist.")
        return final_goodput, final_tail
    with open(overall_output, "r") as f:
        data = json.loads(f.read())
    
    if data["num_errors"] >= 1:
        print(f"Found {data['num_errors']} errors in the data.")
        return final_goodput, final_tail

    final_goodput = data["goodput"]
    final_tail = data["p95_latency"]

    print(f"Goodput: {final_goodput}, p95 latency: {final_tail}")

    return final_goodput, final_tail