"""Data aggregation utilities for RWG metrics.

Provides functions to aggregate overall metrics across multiple repeats,
calculating mean, standard deviation, and 95% confidence intervals.
"""
from __future__ import annotations

from typing import List, Tuple, Dict, Any
import statistics
import math

try:
    from .data_loader import OverallData
except ImportError:
    try:
        from exec.plots.data_loader import OverallData  # type: ignore
    except ImportError:
        from data_loader import OverallData  # type: ignore


def aggregate_overall_metric(values: List[float]) -> Tuple[float, float, float]:
    """Aggregate a metric across repeats.
    
    Args:
        values: List of metric values from different repeats
        
    Returns:
        Tuple of (mean, std, ci_95_margin)
        Returns (None, None, None) if no valid values
    """
    if not values:
        return None, None, None
    
    # Filter out None and NaN values
    filtered = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    
    if not filtered:
        return None, None, None
    
    mean = sum(filtered) / len(filtered)
    
    if len(filtered) < 2:
        return mean, 0.0, 0.0
    
    std = statistics.stdev(filtered)
    
    # 95% CI: 1.96 * std / sqrt(n)
    ci_95 = 1.96 * std / math.sqrt(len(filtered))
    
    return mean, std, ci_95


def aggregate_overall_data(overall_list: List[OverallData]) -> Dict[str, Tuple[float, float, float]]:
    """Aggregate overall data across repeats.
    
    Args:
        overall_list: List of OverallData objects from different repeats
        
    Returns:
        Dictionary mapping metric_name to (mean, std, ci_95)
    """
    metrics = ['goodput', 'slo_violations', 'dropped_requests', 'errors',
               'p50_latency', 'p99_latency', 'num_goodput', 'num_slo_violations',
               'num_dropped_requests', 'num_errors']
    
    result = {}
    for metric in metrics:
        values = []
        for overall in overall_list:
            if hasattr(overall, metric):
                values.append(getattr(overall, metric))
        result[metric] = aggregate_overall_metric(values)
    
    return result


def aggregate_by_api(repeat_data_list: List[Dict[str, Tuple[OverallData, Any]]]) -> Dict[str, Dict[str, Tuple[float, float, float]]]:
    """Aggregate metrics by API across multiple repeats.
    
    Args:
        repeat_data_list: List of repeat data dictionaries
                         Each dict maps api_name to (OverallData, RealtimeData)
    
    Returns:
        Dictionary mapping api_name to aggregated metrics:
        {
            'api_name': {
                'goodput': (mean, std, ci),
                'p99_latency': (mean, std, ci),
                ...
            }
        }
    """
    # Collect all API names
    all_apis = set()
    for repeat_data in repeat_data_list:
        all_apis.update(repeat_data.keys())
    
    # Aggregate each API
    result = {}
    for api in all_apis:
        # Collect OverallData for this API from all repeats
        overall_list = []
        for repeat_data in repeat_data_list:
            if api in repeat_data:
                # Unpack tuple - could be 2 or 3 elements
                # load_repeat_data now returns (overall, realtime, prom_data)
                # old behavior was (overall, realtime)
                data_in = repeat_data[api]
                if len(data_in) == 3:
                     overall, _, _ = data_in
                else:
                     overall, _ = data_in
                if overall is not None:
                    overall_list.append(overall)
        
        # Aggregate metrics
        if overall_list:
            result[api] = aggregate_overall_data(overall_list)
    
    return result
