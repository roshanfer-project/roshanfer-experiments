"""RWG output data loading utilities.

This module provides centralized data loading for RWG outputs:
- overall-{api}.json: Aggregated metrics for entire run
- realtime-{api}.csv: Time-series metrics at configured frequency

Design goals:
- Single source of truth for RWG data parsing
- Clean dataclass-based API
- Reusable across all experiment types
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import pandas as pd
import json


@dataclass
class RealtimeData:
    """Parsed realtime-{api}.csv data from RWG.
    
    Contains time-series metrics at configured frequency (e.g., 1 second intervals).
    Used primarily for latency-and-rate-vs-time experiments.
    """
    api_name: str
    df: pd.DataFrame  # Columns: timestamp, relative_time, goodput, slo_violations,
                      #          dropped_requests, errors, p50_latency, p99_latency, total_requests
    
    @classmethod
    def from_csv(cls, csv_path: Path, api_name: str) -> 'RealtimeData':
        """Load from realtime-{api}.csv file.
        
        Args:
            csv_path: Path to realtime CSV file
            api_name: API identifier (extracted from filename)
            
        Returns:
            RealtimeData instance with parsed DataFrame
            
        Raises:
            FileNotFoundError: If CSV file doesn't exist
            ValueError: If CSV has unexpected format
        """
        if not csv_path.exists():
            raise FileNotFoundError(f"Realtime CSV not found: {csv_path}")
        
        df = pd.read_csv(csv_path)
        
        # Validate expected columns
        expected_cols = {'relative_time', 'throughput_rate', 'p50_latency', 'p99_latency'}
        if not expected_cols.issubset(set(df.columns)):
            missing = expected_cols - set(df.columns)
            raise ValueError(f"Realtime CSV missing expected columns: {missing}")
        
        return cls(api_name=api_name, df=df)


@dataclass
class OverallData:
    """Parsed overall-{api}.json data from RWG.
    
    Contains aggregated metrics for entire run (mean values).
    Used for most experiment types that aggregate across repeats.
    """
    api_name: str
    goodput: float  # requests/sec
    num_goodput: int  # count of successful requests within SLO
    slo_ms: float  # SLO threshold in milliseconds
    slo_violations: float  # requests/sec
    num_slo_violations: int  # count of requests exceeding SLO
    dropped_requests: float  # requests/sec
    num_dropped_requests: int  # count of dropped requests
    errors: float  # requests/sec
    num_errors: int  # count of error responses
    throughput: float  # requests/sec (successful requests)
    num_throughput: int  # count of successful requests
    p50_latency: float  # milliseconds
    p99_latency: float  # milliseconds
    total_requests: int
    duration_seconds: float
    start_time: str  # ISO timestamp
    end_time: str  # ISO timestamp
    
    @classmethod
    def from_json(cls, json_path: Path, api_name: str) -> 'OverallData':
        """Load from overall-{api}.json file.
        
        Args:
            json_path: Path to overall JSON file
            api_name: API identifier (extracted from filename)
            
        Returns:
            OverallData instance with parsed metrics
            
        Raises:
            FileNotFoundError: If JSON file doesn't exist
            ValueError: If JSON has unexpected format
        """
        if not json_path.exists():
            raise FileNotFoundError(f"Overall JSON not found: {json_path}")
        
        with open(json_path) as f:
            data = json.load(f)
        
        # Validate required fields
        required_fields = {'goodput', 'num_errors', 'p50_latency', 'p99_latency'}
        if not required_fields.issubset(set(data.keys())):
            missing = required_fields - set(data.keys())
            raise ValueError(f"Overall JSON missing required fields: {missing}")
        
        return cls(
            api_name=api_name,
            goodput=data['goodput'],
            num_goodput=data['num_goodput'],
            slo_ms=data['slo_ms'],
            slo_violations=data['slo_violations'],
            num_slo_violations=data['num_slo_violations'],
            dropped_requests=data['dropped_requests'],
            num_dropped_requests=data['num_dropped_requests'],
            errors=data['errors'],
            num_errors=data['num_errors'],
            throughput=data.get('throughput', data.get('success', 0.0)),
            num_throughput=data.get('num_throughput', data.get('num_success', 0)),
            p50_latency=data['p50_latency'],
            p99_latency=data['p99_latency'],
            total_requests=data['total_requests'],
            duration_seconds=data['duration_seconds'],
            start_time=data['start_time'],
            end_time=data['end_time']
        )


def load_repeat_data(repeat_dir: Path) -> Dict[str, Tuple[OverallData, Optional[RealtimeData]]]:
    """Load all API data for a single repeat.
    
    Scans the output/ directory for overall-{api}.json and realtime-{api}.csv files.
    
    Args:
        repeat_dir: Path to repeat directory (e.g., repeat_000/)
        
    Returns:
        Dictionary mapping api_name -> (overall_data, realtime_data)
        realtime_data may be None if not generated for this experiment type
        
    Example:
        >>> data = load_repeat_data(Path("experiment_runs/exp-001/.../repeat_000"))
        >>> api_data = data["search-hotel"]
        >>> overall, realtime = api_data
        >>> print(overall.goodput, overall.p99_latency)
    """
    output_dir = repeat_dir / "output"
    
    if not output_dir.exists():
        return {}
    
    result = {}
    
    # Find all overall-*.json files
    for overall_file in output_dir.glob("overall-*.json"):
        # Extract API name: "overall-search-hotel.json" -> "search-hotel"
        api_name = overall_file.stem.replace("overall-", "")
        
        # Load overall data
        overall = OverallData.from_json(overall_file, api_name)
        
        # Load realtime data if available (only for certain experiment types)
        realtime_file = output_dir / f"realtime-{api_name}.csv"
        realtime = RealtimeData.from_csv(realtime_file, api_name) if realtime_file.exists() else None
        
        result[api_name] = (overall, realtime)
    
    return result


def load_unit_data(unit_dir: Path) -> List[Dict[str, Tuple[OverallData, Optional[RealtimeData]]]]:
    """Load all repeats for a unit (load point).
    
    A unit contains multiple repeats (repeat_000, repeat_001, ...).
    This function loads data for all repeats to enable aggregation.
    
    Args:
        unit_dir: Path to unit directory containing repeat_XXX subdirectories
        
    Returns:
        List of dictionaries (one per repeat), each mapping api_name -> (overall, realtime)
        
    Example:
        >>> repeats = load_unit_data(Path("exp-001/exp-name/unit-rate-8000"))
        >>> # repeats[0] is first repeat, repeats[0]["search-hotel"] is (overall, realtime)
        >>> # Can aggregate: means = [r["search-hotel"][0].goodput for r in repeats]
    """
    repeats = []
    
    for repeat_dir in sorted(unit_dir.glob("repeat_*")):
        if repeat_dir.is_dir():
            repeat_data = load_repeat_data(repeat_dir)
            if repeat_data:  # Only add if we found data
                repeats.append(repeat_data)
    
    return repeats


def load_experiment_data(experiment_dir: Path) -> Dict[str, List[Dict[str, Tuple[OverallData, Optional[RealtimeData]]]]]:
    """Load all units for an experiment.
    
    An experiment contains multiple units (different load points).
    
    Args:
        experiment_dir: Path to experiment directory containing unit subdirectories
        
    Returns:
        Dictionary mapping unit_name -> list of repeat data
        
    Example:
        >>> exp_data = load_experiment_data(Path("exp-001/latency-vs-load-experiment"))
        >>> unit_data = exp_data["latency-vs-load-experiment-rate-8000"]
        >>> # unit_data is list of repeats, can aggregate across them
    """
    units = {}
    
    for unit_dir in sorted(experiment_dir.iterdir()):
        if unit_dir.is_dir() and not unit_dir.name.startswith('.'):
            unit_name = unit_dir.name
            unit_repeats = load_unit_data(unit_dir)
            if unit_repeats:
                units[unit_name] = unit_repeats
    
    return units

