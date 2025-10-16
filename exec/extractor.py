from __future__ import annotations

import json
import subprocess
import os
from pathlib import Path

def extract_detailed_metrics_from_output(rwg_output: str, slo: str, version: str, rwg_binary_path: str = "./rwg/rwg") -> dict:
    """Extract detailed metrics from RWG output using rwg parse command.
    
    Args:
        rwg_output: Path to the RWG CSV output file (e.g., output/out-{api}.csv)
        slo: SLO threshold in milliseconds
        version: HTTP version (1 or 2)
        rwg_binary_path: Path to the RWG binary
        
    Returns:
        Dictionary containing all parsed metrics
        
    Raises:
        RuntimeError: If overall-{api}.json is not generated
    """
    # Extract API name from rwg_output filename (e.g., "out-ComposePost.csv" -> "ComposePost")
    base_name = os.path.basename(rwg_output)
    if base_name.startswith("out-") and base_name.endswith(".csv"):
        api_name = base_name[4:-4]  # Remove "out-" prefix and ".csv" suffix
    else:
        # Fallback to overall.json if filename doesn't match expected pattern
        api_name = None
    
    if api_name:
        overall_output = f"{os.path.dirname(rwg_output)}/overall-{api_name}.json"
    else:
        overall_output = f"{os.path.dirname(rwg_output)}/overall.json"

    # run parser
    result = subprocess.run([
        rwg_binary_path, "parse",
        "--rwg_output", rwg_output,
        "--slo", slo,
        "--version", version,
        "--overall_output", overall_output,
    ],
    capture_output=True,
    text=True)

    if not os.path.exists(overall_output):
        error_msg = f"RWG parser failed to generate {os.path.basename(overall_output)} at {overall_output}. Parser exit code: {result.returncode}"
        if result.stderr:
            error_msg += f"\nParser stderr: {result.stderr.strip()}"
        if result.stdout:
            error_msg += f"\nParser stdout: {result.stdout.strip()}"
        raise RuntimeError(error_msg)
    
    with open(overall_output, "r") as f:
        data = json.loads(f.read())
    
    return data
