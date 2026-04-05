"""Merged experiment plot generation.

This module handles merging multiple experiments into unified figures based on
a YAML configuration file. It focuses on creating combined visualizations
where different systems (sidecar, rajomon, dagor) are compared in the same plot.

Supported merge types:
1. latency-and-goodput-vs-load: Single figure with all experiments as legend entries
2. latency-and-rate-vs-time: Side-by-side subplots with labels as titles  
3. max-queue: Same as latency-and-rate-vs-time
4. resource-waste-bar: Grouped bar chart comparing resource waste across experiments

The runner discovers and uses processed data from existing plugins rather than
reprocessing raw metrics.
"""
from __future__ import annotations

import argparse
import json
import re
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Any
import traceback
import os
import numpy as np
import math
from matplotlib.ticker import LogLocator
import statistics

try:
    from exec.plots.data_loader import PrometheusData
except ImportError:
    PrometheusData = None


def _calculate_waste_from_prometheus(prom_data, loaded_data: dict, apis: list, bench: str, is_roshanfer: bool = False) -> Dict[str, Dict[str, float]]:
    try:
        from exec.plots.plugins.resource_waste_unit import _normalize_service_name
    except ImportError:
        from experiments.exec.plots.plugins.resource_waste_unit import _normalize_service_name

    """
    Calculate resource waste using Prometheus data and the new 2-step algorithm.
    Algorithm:
    1. LocalFailed(S) = ReportedFailed(S) - Sum(ReportedFailed(DirectDownstream))
    2. FinalFailed(S) = LocalFailed(S) + FinalFailed(NextServiceInSequence) [Cumulative Suffix Sum in Reverse]
    3. Waste = (FinalFailed + SLO_Violations) / Total_Accepted
    
    Refinements:
    - BLO Violations: Use 'num_slo_violations' from overall-{api}.json
    - Sanity Check: num_dropped_requests (overall) == failed_rpc_counter (entry point)
    - Roshanfer: Only SLO violations contribute to waste (no propagated failures).
    """
    waste_results = {} # service -> api -> waste_pct
    
    # Topology Definitions
    # Direct Downstreams (for Local Failed calc)
    direct_downstreams = {
        'hotel': {
            'search-hotel': {
                'frontend': ['search', 'reservation', 'profile'],
                'search': ['geo', 'rate'],
                # Leaves have []
            },
            'reserve-hotel': {
                'frontend': ['user', 'reservation'],
                'user': ['reservation'],
            }
        },
        'social': {
            'read-user-timeline': {
                'nginx': ['user'],
                'user': ['posts'],
            },
            'read-home-timeline': {
                'nginx': ['home'],
                'home': ['posts'],
            },
            'compose-post': {
                'nginx': ['compose'],
                'compose': ['posts', 'user', 'home'],
                'home': ['graph'],
            }
        }
    }
    
    # Execution Sequences (for Final Failed propagation - Suffix Sum)
    # Ordered by Start Time (we will iterate in Reverse)
    execution_sequences = {
        'hotel': {
            'search-hotel': ['frontend', 'search', 'geo', 'rate', 'reservation', 'profile'],
            'reserve-hotel': ['frontend', 'user', 'reservation'],
        },
        'social': {
            'read-user-timeline': ['nginx', 'user', 'posts'],
            'read-home-timeline': ['nginx', 'home', 'posts'],
            'compose-post': ['nginx', 'compose', 'posts', 'user', 'home', 'graph'],
        }
    }

    # Helper to get ReportedFailed from Prometheus Data
    # prom_data.metrics structure: data[api][service][metric]
    def get_metric(api, service, metric):
        if api not in prom_data.metrics: return 0.0
        # Try exact match first
        if service in prom_data.metrics[api]:
            return prom_data.metrics[api][service].get(metric, 0.0)
        # Try variants (e.g. frontend vs frontend-grpc)
        variants = [service + '-grpc', service.replace('-grpc', '')]
        for v in variants:
            if v in prom_data.metrics[api]:
                return prom_data.metrics[api][v].get(metric, 0.0)
        
        # DEBUG: If metric not found and it's roshanfer, print available keys
        if is_roshanfer and metric == 'accepted_rpc_counter':
             print(f"DEBUG: {service} {metric} not found. Available services in {api}: {list(prom_data.metrics[api].keys())}")
             if service in prom_data.metrics[api]:
                 print(f"DEBUG: Available metrics for {service}: {list(prom_data.metrics[api][service].keys())}")
        
        return 0.0

    for api in apis:
        if bench not in execution_sequences or api not in execution_sequences[bench]:
            continue
            
        seq = execution_sequences[bench][api]
        downstream_map = direct_downstreams[bench].get(api, {})
        
        local_failed = {} # service -> count
        final_failed = {} # service -> count
        
        # Determine entry point service for sanity check
        entry_service = 'nginx' if bench == 'social' else 'frontend'
        
        # 1. Calculate Local Failed
        for service in seq:
            reported_failed = get_metric(api, service, 'failed_rpc_counter')
            
            # Sum downstream reported failures
            downstream_failed_sum = 0.0
            for child in downstream_map.get(service, []):
                downstream_failed_sum += get_metric(api, child, 'failed_rpc_counter')
            
            local = max(0.0, reported_failed - downstream_failed_sum)
            local_failed[service] = local
            
        # 2. Calculate Final Failed (Cumulative Suffix Sum in Reverse)
        accumulator = 0.0
        for service in reversed(seq):
            accumulator += local_failed.get(service, 0.0)
            final_failed[service] = accumulator
            
        # SANITY CHECK: Compare FinalFailed of entry point to num_dropped_requests from overall report.
        # We expect ReportedFailed(Entry) ≈ num_dropped_requests.
        
        # Get overall data for this API
        overall_obj = None
        if loaded_data and api in loaded_data:
             overall_tuple = loaded_data[api] 
             # loaded_data[api] is (overall, realtime, prom)
             if overall_tuple and len(overall_tuple) >= 1 and overall_tuple[0]:
                 overall_obj = overall_tuple[0]
        
        # Special logic for Roshanfer: Use global stats only (no per-service propogation)
        if is_roshanfer:
            slo_violations = overall_obj.num_slo_violations if overall_obj else 0
            # User specified: use num_throughput as total accepted
            total_accepted = overall_obj.num_throughput if overall_obj else 0
            
            waste_pct = 0.0
            if total_accepted > 0:
                waste_pct = (slo_violations / total_accepted) * 100.0
            
            # Assign same global waste to all services in sequence
            for service in seq:
                 if service not in waste_results: waste_results[service] = {}
                 waste_results[service][api] = waste_pct
                 
                 # Normalize
                 norm_service = _normalize_service_name(service)
                 if norm_service != service:
                    if norm_service not in waste_results: waste_results[norm_service] = {}
                    waste_results[norm_service][api] = waste_pct
            
            continue # Skip rest of loop for this API

        num_dropped = overall_obj.num_dropped_requests if overall_obj else 0
        entry_reported_failed = get_metric(api, entry_service, 'failed_rpc_counter')
        
        # Logging check (print for now, maybe warn?)
        # Allow small deviation? User said "equal".
        # Let's print warning if diff > 5% or absolute diff > 100 ?
        diff = abs(num_dropped - entry_reported_failed)
        if diff > 0:
            # We used PLOT_DEBUG env var elsewhere, but standard print is safer for visibility
            if diff > (num_dropped * 0.1) and num_dropped > 10: # >10% mismatch
                 print(f"WARNING: [Sanity Check Failed] {api} - Dropped: {num_dropped}, {entry_service}.Failed: {entry_reported_failed} (Diff: {diff})")

        # 3. Calculate Waste %
        # Waste = (FinalFailed + SLO_Violations) / Total_Accepted
        
        # Get SLO Violations from overall stats
        slo_violations = overall_obj.num_slo_violations if overall_obj else 0
        
        for service in seq:
            accepted = get_metric(api, service, 'accepted_rpc_counter')
            
            # Add Global SLO violations to the numerator for each service
            # This follows the requirement: Total Waste = (FinalFailed + SLO_Violations) / Total_Accepted
            
            if is_roshanfer:
                # For Roshanfer, only SLO violations contribute to waste.
                total_waste_count = float(slo_violations)
            else:
                total_waste_count = final_failed[service] + slo_violations
            
            if accepted > 0:
                waste_pct = (total_waste_count / accepted) * 100.0
            else:
                waste_pct = 0.0
                
            if service not in waste_results: waste_results[service] = {}
            waste_results[service][api] = waste_pct

    return waste_results


def generate_resource_waste_bar_merged(
    figure_name: str,
    figure_config: dict,
    experiment_configs: dict,
    experiments_root: Path,
    output_dir: Path,
    global_config: str = None
) -> list:
    """
    Generate merged resource-waste-bar figure.
    For each included experiment, aggregate resource waste per service across repeats and plot grouped bars.
    Each experiment gets its own color, with services on x-axis.
    """
    from pathlib import Path
    import numpy as np
    import math
    import statistics
    
    # Import primitives
    try:
        from exec.plots.plugins.resource_waste_unit import _normalize_service_name, _mean_std, _calculate_waste_per_repeat
    except Exception:
        from experiments.exec.plots.plugins.resource_waste_unit import _normalize_service_name, _mean_std, _calculate_waste_per_repeat
    try:
        from exec.plots.data_loader import extract_series
    except Exception:
        from experiments.exec.plots.data_loader import extract_series  # type: ignore
        
    try:
        from exec.plots.plotting_primitives import (
            SubplotGrid, plot_grouped_bars, ACM_COMPACT_HALF, ACM_QUARTER
        )
    except ImportError:
         try:
            from plots.plotting_primitives import (  # type: ignore
                SubplotGrid, plot_grouped_bars, ACM_COMPACT_HALF, ACM_QUARTER
            )
         except ImportError:
            from plotting_primitives import (  # type: ignore
                SubplotGrid, plot_grouped_bars, ACM_COMPACT_HALF, ACM_QUARTER
            )
    import matplotlib.pyplot as plt

    include_experiments = figure_config.get('include', {})
    if not include_experiments:
        return []
    
    produced = []
    exp_names = list(include_experiments.keys())
    
    # For each experiment, aggregate resource waste per service
    exp_data = []
    all_services = set()
    
    for exp_name in exp_names:
        exp_cfg = include_experiments[exp_name]
        label = exp_cfg.get('label', exp_name)
        
        # Find experiment definition
        if exp_name not in experiment_configs:
            raise Exception(f"Experiment '{exp_name}' not found in experiment configs")
        exp_def = experiment_configs[exp_name]
        
        # Get APIs and benchmark type from experiment config
        apis = exp_def.get('apis', [])
        bench = exp_def.get('bench', 'hotel')  # Default to hotel
        
        # Load all repeats for this experiment (from all exp-XXX dirs)
        repeat_waste_data = []
        
        # Determine roots to scan
        roots_to_scan = []
        if experiments_root.name.startswith('exp-'):
            roots_to_scan.append(experiments_root)
        else:
            for exp_index in range(1, 20):
                roots_to_scan.append(experiments_root / f'exp-{exp_index:03d}')
        
        # Scan roots
        repeat_metric_files = [] # For legacy fallback
        
        for run_root in roots_to_scan:
            try:
                # Need to use data_loader directly to get prometheus data
                from exec.plots.data_loader import load_repeat_data
                
                # Check if this run_root is for our experiment
                # We need to find the correct repeat dir. 'run_root' is likely 'exp-XXX'
                # Inside exp-XXX, we might have multiple units, or it might be the unit itself.
                # Based on usage, 'experiments_root' is usually the base dir containing 'exp-001', etc.
                # But 'run_root' implies a specific run.
                # Let's follow existing logic: _load_summary finds summary.jsonl.
                
                # If we use existing _load_summary logic to find artifact dirs:
                records = _load_summary(run_root)
                for record in records:
                    if record.get('experiment_name') == exp_name:
                        artifact_dir = Path(record.get('artifact_dir', '.'))
                        # artifact_dir is the repeat dir
                        
                        # Load data using new loader
                        loaded_data = load_repeat_data(artifact_dir)
                        
                        # Check if we have any data (loaded_data is dict: api -> (overall, realtime, prom))
                        if not loaded_data:
                            continue
                            
                        # Extract Prometheus data (it's the same for all APIs in the repeat)
                        first_valid_api = next(iter(loaded_data))
                        _, _, prom_data = loaded_data[first_valid_api]
                        
                        # Check condition: Prom data exists OR it is Roshanfer (which can use overall data)
                        is_roshanfer_exp = (label == 'Roshanfer' or 'sidecar' in exp_name)
                        
                        if (prom_data and prom_data.metrics) or is_roshanfer_exp:
                            waste_data = _calculate_waste_from_prometheus(prom_data, loaded_data, apis, bench, is_roshanfer=is_roshanfer_exp)
                            repeat_waste_data.append(waste_data)
                        else:
                            # Fallback to legacy logic
                            metrics_dir = artifact_dir / 'metrics'
                            metric_files = {}
                            for fp in metrics_dir.glob('*.json'):
                                if fp.name.startswith('_index') or fp.name == 'prometheus.json':
                                    continue
                                try:
                                    metric_files[fp.stem] = json.loads(fp.read_text())
                                except Exception:
                                    continue
                            if metric_files:
                                waste_data = _calculate_waste_per_repeat(metric_files, apis, bench)
                                repeat_waste_data.append(waste_data)
                                
            except Exception as e:
                print(f"Error loading data for {exp_name} in {run_root}: {e}")
                traceback.print_exc()
                continue
        
        if not repeat_waste_data:
            continue
        
        # Get all services from this experiment
        all_services_this_exp = set()
        for waste_data in repeat_waste_data:
            all_services_this_exp.update(waste_data.keys())
        all_services.update(all_services_this_exp)
        
        # Aggregate across repeats
        aggregated_data = {}
        for service in all_services_this_exp:
            aggregated_data[service] = {}
            for api in apis:
                values = []
                for waste_data in repeat_waste_data:
                    if service in waste_data and api in waste_data[service]:
                        values.append(waste_data[service][api])
                    else:
                        values.append(0.0)
                mean_val, std_val = _mean_std(values)
                aggregated_data[service][api] = {
                    'mean': mean_val or 0.0,
                    'std': std_val or 0.0,
                    'values': values
                }
        
        exp_data.append({
            'label': label,
            'services': list(all_services_this_exp),
            'data': aggregated_data
        })
    
    # Union of all services across experiments, preserve order of first appearance
    def unique_ordered(seq):
        seen = set()
        out = []
        for x in seq:
            if x not in seen:
                out.append(x)
                seen.add(x)
        return out
    
    all_services_unfiltered = unique_ordered([svc for ed in exp_data for svc in ed['services']])
    
    # Get all unique APIs across experiments
    all_apis = set()
    for ed in exp_data:
        exp_def = experiment_configs[list(include_experiments.keys())[exp_data.index(ed)]]
        apis = exp_def.get('apis', [])
        all_apis.update(apis)
    all_apis = sorted(list(all_apis))
    n_apis = len(all_apis)  # Define n_apis early
    
    # For single API case: filter out services with zero resource waste across all experiments and APIs
    # For multiple API case: we'll filter per API later
    if n_apis <= 1:
        all_services = []
        for service in all_services_unfiltered:
            has_nonzero_waste = False
            for ed in exp_data:
                data = ed['data']
                if service in data:
                    for api in all_apis:
                        if api in data[service] and data[service][api]['mean'] > 0:
                            has_nonzero_waste = True
                            break
                if has_nonzero_waste:
                    break
            if has_nonzero_waste:
                all_services.append(service)
    else:
        # For multiple APIs, we'll use all services initially and filter per subplot
        all_services = all_services_unfiltered
    
    # Prepare figure
    n_exps = len(exp_data)
    n_services = len(all_services)
    
    # Define compact and consistent parameters for all cases
    compact_bar_width_per_service = 0.4  # inches per service - compact sizing
    compact_min_width = 1.5  # minimum width for readability
    compact_max_width = 3.0  # maximum width to keep compact
    
    # Calculate compact subplot width based on number of services
    base_subplot_width = max(compact_min_width, min(compact_max_width, n_services * compact_bar_width_per_service))
    
    # Decide layout based on number of APIs
    # Decide layout based on number of APIs
    if n_apis <= 1:
        # Single API case: one subplot
        style = ACM_QUARTER
        grid = SubplotGrid(style, layout="1x1")
    else:
        # Multiple API case
        # Step 1: Find non-zero services (count)
        api_service_counts = {}
        for api in all_apis:
            services_count_for_api = 0
            for service in all_services_unfiltered:
                has_nonzero_waste_for_api = False
                for ed in exp_data:
                    data = ed['data']
                    if (service in data and api in data[service] and data[service][api]['mean'] > 0.0):
                        has_nonzero_waste_for_api = True
                        break
                if has_nonzero_waste_for_api:
                    services_count_for_api += 1
            api_service_counts[api] = services_count_for_api
            
        # Step 2: Calculate proportional subplot widths
        width_ratios = []
        for api in all_apis:
            c = api_service_counts[api]
            width_ratios.append(c if c > 0 else 1)
            
        style = ACM_COMPACT_HALF
        # Actually aspect ratio applies to total height. 0.6 is default.
        # User requested 240pt total width.
        grid = SubplotGrid(style, layout=f"1x{n_apis}", width_ratios=width_ratios)
    
    colors = style.colors[:n_exps] if n_exps <= len(style.colors) else (style.colors * ((n_exps // len(style.colors)) + 1))[:n_exps]
    exp_colors = dict(zip([ed['label'] for ed in exp_data], colors))
    
    # Compute global max across all experiments/services/apis for consistent y-axis
    global_max = 0.0
    for ed in exp_data:
        data = ed['data']
        for service in all_services:
            if service in data:
                for api in all_apis:
                    if api in data[service]:
                        mean_val = data[service][api]['mean']
                        std_val = data[service][api]['std']
                        # Use the same 95% CI calculation as in plotting
                        n_repeats = len(data[service][api]['values'])
                        if n_repeats > 1 and std_val > 0:
                            ci_margin = 1.96 * std_val / math.sqrt(n_repeats)
                        else:
                            ci_margin = 0.0
                        total_with_error = mean_val + ci_margin
                        if total_with_error > global_max:
                            global_max = total_with_error
                            print(f"DEBUG: New global_max={global_max:.2f} from {ed['label']}/{service}/{api} (mean={mean_val:.2f}, ci={ci_margin:.2f}, n={n_repeats})")
    
    # Step 1: Set a fixed bar width (consistent across all APIs)
    # Include sidecar bar in the total count
    total_bars = n_exps
    fixed_total_group_width = 0.7  # Fixed total width of grouped bars as fraction of x-unit
    fixed_bar_width = fixed_total_group_width / total_bars if total_bars > 0 else fixed_total_group_width
    
    # Collect all legend handles and labels
    all_handles = []
    all_labels = []
    
    for api_idx, api in enumerate(all_apis):
        ax = grid.get_ax(0, api_idx) if n_apis > 1 else grid.get_ax(0, 0)
        
        # Filter services for this specific API
        if n_apis > 1:
            # Filter services that have non-zero resource waste for this specific API only
            services_for_this_api = []
            for service in all_services:
                # Check if EVERY experiment has non-zero waste for this service+API combination
                # Wait, original logic was 'ANY'. Let's stick to original logic.
                # Check if ANY experiment has non-zero waste for this service+API combination
                has_nonzero_waste_for_api = False
                for ed in exp_data:
                    data = ed['data']
                    if (service in data and 
                        api in data[service] and 
                        data[service][api]['mean'] > 0.0):
                        has_nonzero_waste_for_api = True
                        break
                # Only include service if it has waste for this specific API
                if has_nonzero_waste_for_api:
                    services_for_this_api.append(service)
        else:
            # Single API case uses pre-filtered services
            services_for_this_api = all_services
        
        # Skip this subplot if no services have non-zero waste for this API
        if not services_for_this_api:
            continue
        
        # Create x_indices based on filtered services for this API
        api_x_indices = list(range(len(services_for_this_api)))
        # Use fixed bar width across all APIs
        api_bar_width = fixed_bar_width
        
        # Add API name as compact subplot title
        if n_apis > 1:
            ax.set_title(api.replace('-', '-'), fontsize=10, fontweight='bold', pad=4)
        
        # Prepare bar groups
        # Format: (label, heights, errors)
        bar_groups = []
        
        # Add experiment bars
        for exp_idx, ed in enumerate(exp_data):
            label = ed['label']
            data = ed['data']
            
            means = []
            stds = []
            
            for service in services_for_this_api:
                if service in data and api in data[service]:
                    mean_val = data[service][api]['mean']
                    std_val = data[service][api]['std']
                    means.append(mean_val)
                    # Convert std to 95% CI margin of error
                    n_repeats = len(data[service][api]['values'])
                    if n_repeats > 1 and std_val > 0:
                        ci_margin = 1.96 * std_val / math.sqrt(n_repeats)
                    else:
                        ci_margin = 0.0
                    stds.append(ci_margin)
                else:
                    means.append(0.0)
                    stds.append(0.0)
            
            bar_groups.append((label, means, stds))
            
        plot_grouped_bars(ax, api_x_indices, bar_groups, style=style)
        
        # Add labels for Roshanfer
        n_groups = len(bar_groups)
        bar_width = style.bar_width_fraction / n_groups
        
        for g_i, (label, heights, errors) in enumerate(bar_groups):
            if label == 'Roshanfer' or 'sidecar' in label.lower():
                 offsets = [x - style.bar_width_fraction/2 + g_i*bar_width + bar_width/2
                          for x in api_x_indices]
                 
                 for x, h, err in zip(offsets, heights, errors):
                     # Always plot or maybe only if > 0? User asked for value.
                     # Let's verify if h is 0. 
                     label_text = f"{h:.1f}" if h < 10 else f"{int(round(h))}"
                     
                     # Position above bar + error
                     y_pos = h + (err if err else 0) + (global_max * 0.2 if global_max > 0 else 1.0)
                     
                     ax.text(x, y_pos, label_text, ha='center', va='bottom',
                            fontsize=style.font_size*0.6, fontweight='normal')

        # Store filtered services for this API for axis formatting
        if api_idx == 0:
            # Store services for axis formatting (we'll use the union of all API services)
            all_filtered_services = services_for_this_api
        else:
            # Update with union of services across APIs for consistent formatting
            all_filtered_services = list(set(all_filtered_services + services_for_this_api))
    
    # Format axes for all subplots
    for api_idx, api in enumerate(all_apis):
        ax = grid.get_ax(0, api_idx) if n_apis > 1 else grid.get_ax(0, 0)
        
        # Get services for this specific API (same filtering logic as in plotting)
        if n_apis > 1:
            # Filter services that have non-zero resource waste for this specific API only
            services_for_this_api = []
            for service in all_services:
                # Check if ANY experiment has non-zero waste for this service+API combination
                has_nonzero_waste_for_api = False
                for ed in exp_data:
                    data = ed['data']
                    if (service in data and 
                        api in data[service] and 
                        data[service][api]['mean'] > 0.0):
                        has_nonzero_waste_for_api = True
                        break
                # Only include service if it has waste for this specific API
                if has_nonzero_waste_for_api:
                    services_for_this_api.append(service)
        else:
            services_for_this_api = all_services
        
        # Skip formatting if no services for this API
        if not services_for_this_api:
            continue
        
        # X-axis formatting with API-specific services
        api_x_indices = list(range(len(services_for_this_api)))
        ax.set_xticks(api_x_indices)
        ax.set_xticklabels([service.title() for service in services_for_this_api], rotation=30, ha='right')
        
    # Set consistent y-axis limits and ticks for all subplots
    if global_max > 0:
        # Calculate dynamic ylim based on global_max with padding
        # Use 10% padding or at least +2 units
        ylim_max = math.ceil(global_max * 1.15)
        
        # Ensure it's usually aligned to tens if possible for nicer ticks, but prioritize visibility
        if ylim_max < 10:
             ylim_max = math.ceil(ylim_max)
        else:
             # Round up to nearest 5 or 10
             ylim_max = 5 * math.ceil(ylim_max / 5)
        
        # Override ylim_max to 100 as requested
        ylim_max = 100
        
        for api_idx in range(len(all_apis)):
            ax = grid.get_ax(0, api_idx) if n_apis > 1 else grid.get_ax(0, 0)
            
            # Configure axis using primitives
            grid.configure_ax(ax,
                ylabel='Waste (%)' if api_idx == 0 else '',
                ylim=(0, ylim_max),
                y_type='int',
                y_step=20,
                show_ylabel=(api_idx == 0),
                show_yticklabels=(api_idx == 0)
            )

    # Compact legend at the top center of the figure
    grid.add_shared_legend(position="top")
    
    # Save figure
    output_dir.mkdir(parents=True, exist_ok=True)
    fig_path = output_dir / f'{figure_name}_resource_waste_bar.pdf'
    grid.save(fig_path)
    return [fig_path]


def generate_max_queue_merged(
    figure_name: str,
    figure_config: dict,
    experiment_configs: dict,
    experiments_root: Path,
    output_dir: Path,
    global_config: str = None
) -> list:
    """
    Generate merged max-queue and avg-queue figures.
    For each included experiment, aggregate per (service, api) across repeats and plot grouped bars.
    Each experiment is a subplot (column). Shared legend for APIs above.
    """
    from pathlib import Path
    import statistics
    # Import helpers from plugin
    # Import primitives
    try:
        from exec.plots.plugins.max_queue_unit import _normalize_service_name, _mean_std, _infer_services_and_apis
    except Exception:
        from experiments.exec.plots.plugins.max_queue_unit import _normalize_service_name, _mean_std, _infer_services_and_apis
    try:
        from exec.plots.data_loader import extract_series
    except Exception:
        from experiments.exec.plots.data_loader import extract_series  # type: ignore
        
    try:
        from exec.plots.plotting_primitives import (
            SubplotGrid, plot_grouped_bars, ACM_COMPACT_HALF, ACM_QUARTER
        )
    except ImportError:
         try:
            from plots.plotting_primitives import (  # type: ignore
                SubplotGrid, plot_grouped_bars, ACM_COMPACT_HALF, ACM_QUARTER
            )
         except ImportError:
            from plotting_primitives import (  # type: ignore
                SubplotGrid, plot_grouped_bars, ACM_COMPACT_HALF, ACM_QUARTER
            )

    include_experiments = figure_config.get('include', {})
    if not include_experiments:
        return []
    exp_names = list(include_experiments.keys())
    ncols = len(exp_names)
    # For each experiment, aggregate max queue per (service, api)
    exp_data = []
    all_services = set()
    all_apis = set()
    for exp_name in exp_names:
        exp_cfg = include_experiments[exp_name]
        label = exp_cfg.get('label', exp_name)
        # Find experiment definition
        if exp_name not in experiment_configs:
            raise Exception(f"Experiment '{exp_name}' not found in experiment configs")
        exp_def = experiment_configs[exp_name]
        # Scan roots
        repeat_metric_files = [] 
        repeat_prom_data = [] # Store prom data for repeats
        roots_to_scan = [experiments_root]
        
        for run_root in roots_to_scan:
            try:
                # Use data_loader to get prometheus data
                from exec.plots.data_loader import load_repeat_data
                
                records = _load_summary(run_root)
                for record in records:
                    if record.get('experiment_name') == exp_name:
                        artifact_dir = Path(record.get('artifact_dir', '.'))
                        
                        # Load data using new loader
                        loaded_data = load_repeat_data(artifact_dir)
                        
                        if loaded_data:
                            # Check for Prometheus data
                            first_valid_api = next(iter(loaded_data))
                            _, _, prom_data = loaded_data[first_valid_api]
                            if prom_data and prom_data.metrics:
                                repeat_prom_data.append(prom_data)
                        
                        # Also load legacy metric files for fallback or if mixed
                        metrics_dir = artifact_dir / 'metrics'
                        metric_files = {}
                        for fp in metrics_dir.glob('*.json'):
                            if fp.name.startswith('_index') or fp.name == 'prometheus.json':
                                continue
                            try:
                                metric_files[fp.stem] = json.loads(fp.read_text())
                            except Exception:
                                continue
                        repeat_metric_files.append(metric_files)
            except Exception as e:
                print(f"Error loading data for {exp_name} in {run_root}: {e}")
                traceback.print_exc()
                continue

        # Determine services and APIs
        fallback_services = exp_def.get('services', [])
        fallback_apis = exp_def.get('apis', [])
        
        # Use legacy inference to get list of services, but then pull data from Prom if available.
        services, apis = _infer_services_and_apis(repeat_metric_files, fallback_services, fallback_apis)
        services = [_normalize_service_name(svc) for svc in services]
        
        # Explicitly check for ingress in Prometheus data
        triggers_ingress = False
        for p in repeat_prom_data:
             if p and p.metrics:
                 for api in apis:
                      if api in p.metrics and 'ingress' in p.metrics[api]:
                           triggers_ingress = True
                           break
             if triggers_ingress: break
        
        if triggers_ingress and 'ingress' not in services:
             services.append('ingress')
        
        all_services.update(services)
        all_apis.update(apis)
        
        data_max = {svc: {api: [] for api in apis} for svc in services}
        data_avg = {svc: {api: [] for api in apis} for svc in services}

        for i in range(len(repeat_metric_files)):
            mf = repeat_metric_files[i]
            prom = repeat_prom_data[i] if i < len(repeat_prom_data) else None

            for svc in services:
                for api in apis:
                    val_max = 0.0
                    val_avg = 0.0
                    found_prom = False

                    if prom and prom.metrics and api in prom.metrics:
                        variants = [svc, svc + '-grpc', svc.replace('-grpc', ''), svc + '-server']
                        for v in variants:
                            if v not in prom.metrics[api]:
                                continue
                            node = prom.metrics[api][v]
                            if 'max_queue' in node:
                                val_max = float(node['max_queue'])
                                found_prom = True
                            if 'avg_queue' in node:
                                val_avg = float(node['avg_queue'])
                                found_prom = True
                            if found_prom:
                                break

                    if found_prom:
                        data_max[svc][api].append(val_max)
                        data_avg[svc][api].append(val_avg)
                        continue

                    original_service_variants = [svc]
                    if svc == 'frontend':
                        original_service_variants.append('frontend-grpc')
                    elif svc == 'nginx':
                        original_service_variants.append('nginx-grpc')
                    stem_candidates = []
                    for service_variant in original_service_variants:
                        stem_candidates.extend([
                            f'queue_length_{api}_{service_variant}',
                            f'queue_length_{service_variant}_{api}',
                            f'queue_length_{service_variant}'
                        ])
                    chosen = None
                    for st in stem_candidates:
                        if st in mf:
                            chosen = st
                            break
                    if not chosen:
                        data_max[svc][api].append(0.0)
                        data_avg[svc][api].append(0.0)
                        continue
                    ts, vals = extract_series(mf[chosen])
                    if not vals:
                        data_max[svc][api].append(0.0)
                        data_avg[svc][api].append(0.0)
                    else:
                        data_max[svc][api].append(float(max(vals)))
                        data_avg[svc][api].append(float(statistics.mean(vals)))

        exp_data.append({
            'label': label,
            'services': services,
            'apis': apis,
            'data_max': data_max,
            'data_avg': data_avg,
        })

    # Union of all services/apis across experiments, preserve order of first appearance
    def unique_ordered(seq):
        seen = set()
        out = []
        for x in seq:
            if x not in seen:
                out.append(x)
                seen.add(x)
        return out
    all_services = unique_ordered([svc for ed in exp_data for svc in ed['services']])
    all_apis = unique_ordered([api for ed in exp_data for api in ed['apis']])
    
    non_zero_services = []
    for svc in all_services:
        has_nonzero = False
        for ed in exp_data:
            for api in all_apis:
                vm = ed['data_max'].get(svc, {}).get(api, [])
                va = ed['data_avg'].get(svc, {}).get(api, [])
                if any(v > 0 for v in vm) or any(v > 0 for v in va):
                    has_nonzero = True
                    break
            if has_nonzero:
                break
        if has_nonzero:
            non_zero_services.append(svc)

    if os.environ.get('PLOT_DEBUG'):
        print(f"[max-queue-merged] Filtering services: original={len(all_services)} kept={len(non_zero_services)} dropped={set(all_services)-set(non_zero_services)}")
    all_services = non_zero_services

    if not all_services:
        print("[max-queue-merged] All services have zero max and avg queue; skipping plots.")
        return []

    single_api_mode = len(all_apis) == 1

    def _save_one_merged(data_key: str, ylabel: str, log_y: bool, file_suffix: str) -> Path:
        if single_api_mode:
            style = ACM_QUARTER
            grid = SubplotGrid(style, layout="1x1")
        else:
            style = ACM_COMPACT_HALF
            grid = SubplotGrid(style, layout=f"1x{ncols}")

        global_max = 0.0
        for ed in exp_data:
            data = ed[data_key]
            for svc in all_services:
                for api in all_apis:
                    vals = data.get(svc, {}).get(api, [])
                    m, s = _mean_std(vals)
                    if m is None:
                        m = 0.0
                    if s is None:
                        s = 0.0
                    if m + s > global_max:
                        global_max = m + s

        if log_y:
            ylim_max = 1.2 * global_max if global_max > 0 else 10.0
            ylim_min = 0.9
        else:
            ylim_max = 1.2 * global_max if global_max > 0 else 1.0
            ylim_min = 0.0

        if single_api_mode:
            ax = grid.get_ax(0, 0)
            api = all_apis[0]
            x_indices = list(range(len(all_services)))
            bar_groups = []
            for ed in exp_data:
                data = ed[data_key]
                means = []
                stds = []
                for svc in all_services:
                    vals = data.get(svc, {}).get(api, [])
                    m, s = _mean_std(vals)
                    if m is None:
                        m = 0.0
                    if s is None:
                        s = 0.0
                    means.append(m)
                    stds.append(0.0001 if (s is None or s == 0) else s)
                bar_groups.append((ed['label'], means, stds))
            plot_grouped_bars(ax, x_indices, bar_groups, style=style)
            ax.set_xticks(x_indices)
            ax.set_xticklabels([service.title() for service in all_services], rotation=30, ha='right')
            grid.configure_ax(ax, ylabel=ylabel, ylim=(ylim_min, ylim_max), log_y=log_y)
        else:
            for i, ed in enumerate(exp_data):
                ax = grid.get_ax(0, i)
                data = ed[data_key]
                x_indices = list(range(len(all_services)))
                bar_groups = []
                for api in all_apis:
                    means = []
                    stds = []
                    for svc in all_services:
                        vals = data.get(svc, {}).get(api, [])
                        m, s = _mean_std(vals)
                        if m is None:
                            m = 0.0
                        if s is None:
                            s = 0.0
                        means.append(m)
                        stds.append(0.0001 if (s is None or s == 0) else s)
                    bar_groups.append((api, means, stds))
                plot_grouped_bars(ax, x_indices, bar_groups, style=style)
                ax.set_xticks(x_indices)
                ax.set_xticklabels([service.title() for service in all_services], rotation=30, ha='right')
                grid.configure_ax(
                    ax,
                    ylabel=ylabel if i == 0 else '',
                    title=ed['label'],
                    ylim=(ylim_min, ylim_max),
                    show_ylabel=(i == 0),
                    log_y=log_y,
                    show_yticklabels=(i == 0),
                )

        grid.add_shared_legend(position="top")
        output_dir.mkdir(parents=True, exist_ok=True)
        fig_path = output_dir / f'{figure_name}{file_suffix}'
        grid.save(fig_path)
        return fig_path

    produced = [
        _save_one_merged('data_max', 'Max Queueing (req)', True, '_max_queue.pdf'),
        _save_one_merged('data_avg', 'Avg Queueing (req)', True, '_avg_queue.pdf'),
    ]
    return produced

def load_merged_config(merged_config_path: Path) -> Dict[str, Any]:
    """Load and parse the merged.yaml configuration file."""
    if not merged_config_path.exists():
        raise FileNotFoundError(f'Merged config not found: {merged_config_path}')
    
    with merged_config_path.open() as f:
        config = yaml.safe_load(f)
    
    return config

def _derive_experiment_name(exp: Dict[str, Any], bench: str) -> str:
    """Derive name from type, bench, system (matches executor logic)."""
    bench_slug = bench.split("/")[-1] if bench else ""
    exp_type = exp.get('type', '')
    system = exp.get('system', '')
    apis = exp.get('apis', [])
    if len(apis) > 1:
        return f"{exp_type}-{bench_slug}-{len(apis)}-{system}"
    return f"{exp_type}-{bench_slug}-{system}"


def load_experiment_configs(experiments_config_path: Path, bench: str = "") -> Dict[str, Dict[str, Any]]:
    """Load experiment configurations and return as name->config mapping.
    Derives names when missing (matches executor _assign_derived_names)."""
    if not experiments_config_path.exists():
        raise FileNotFoundError(f'Experiments config not found: {experiments_config_path}')
    
    with experiments_config_path.open() as f:
        data = json.load(f)
    
    experiments = {}
    seen: Dict[str, int] = {}
    for exp in data.get('experiments', []):
        name = exp.get('name')
        if not name:
            base = _derive_experiment_name(exp, bench)
            if base in seen:
                seen[base] += 1
                name = f"{base}-{seen[base]}"
            else:
                seen[base] = 0
                name = base
            exp = {**exp, 'name': name}
        experiments[name] = exp
    
    return experiments


def _load_summary(run_root: Path) -> List[Dict]:
    """Load run summary from experiment runs directory."""
    summary_path = run_root / 'run_summary.jsonl'
    if not summary_path.exists():
        return []
    
    records: List[Dict] = []
    with summary_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            
            # Check nested run_result status or top-level status
            run_result = obj.get('run_result', {})
            status = run_result.get('status')
            if status is None:
                status = obj.get('status')
            
            if status != 'success':
                continue
                
            # Flatten or use run_result for artifact_dir lookups later
            # The code usually expects 'artifact_dir' at top level or we need to adapt usage
            # Adapting usage: let's populate top-level fields needed by consumers
            if 'raw_artifact_dir' in run_result:
                obj['artifact_dir'] = Path(run_result['raw_artifact_dir']).parent
            
            # Map 'experiment' to 'experiment_name' if needed
            if 'experiment' in obj and 'experiment_name' not in obj:
                obj['experiment_name'] = obj['experiment']
            
            records.append(obj)
    return records


def _load_metric_files(metrics_dir: Path) -> Dict[str, dict]:
    """Load metric files from a metrics directory."""
    out: Dict[str, dict] = {}
    if not metrics_dir.exists():
        return out
    
    for fp in metrics_dir.glob('*.json'):
        if fp.name.startswith('_index'):
            continue
        try:
            out[fp.stem] = json.loads(fp.read_text())
        except Exception:
            continue
    return out


def generate_latency_goodput_vs_load_merged(
    figure_name: str,
    figure_config: Dict[str, Any],
    experiment_configs: Dict[str, Dict[str, Any]],
    experiments_root: Path,
    output_dir: Path,
    global_config: Path
) -> List[Path]:
    """Generate merged latency-and-goodput-vs-load figure.
    
    REWRITTEN to use new RWG data loading and plotting architecture.
    Combines multiple experiments into a single figure with legend entries.
    """
    # Import new RWG data loading and plotting
    try:
        from exec.plots.data_loader import load_repeat_data
        from exec.plots.aggregation import aggregate_by_api
        from exec.plots.plotting_primitives import (
            SubplotGrid, ACM_COMPACT_HALF, plot_line
        )
    except ImportError:
        try:
            from plots.data_loader import load_repeat_data  # type: ignore
            from plots.aggregation import aggregate_by_api  # type: ignore
            from plots.plotting_primitives import (  # type: ignore
                SubplotGrid, ACM_COMPACT_HALF, plot_line
            )
        except ImportError:
            from data_loader import load_repeat_data  # type: ignore
            from aggregation import aggregate_by_api  # type: ignore
            from plotting_primitives import (  # type: ignore
                SubplotGrid, ACM_COMPACT_HALF, plot_line
            )
    
    import matplotlib.pyplot as plt
    import math
    from dataclasses import replace
    
    produced: List[Path] = []
    include_experiments = figure_config.get('include', {})
    
    if not include_experiments:
        return produced
    
    # Load SLOs from config file
    with open(global_config) as f:
        global_configs = json.load(f)
    slo_map = global_configs.get('slos', {})
    
    # Collect data from all experiments
    all_experiment_data = {}
    all_apis = set()
    
    for exp_name, exp_config in include_experiments.items():
        label = exp_config.get('label', exp_name)
        
        if exp_name not in experiment_configs:
            raise Exception(f"Experiment '{exp_name}' not found in experiment configs")
        
        exp_def = experiment_configs[exp_name]
        apis = exp_def.get('apis', [])
        all_apis.update(apis)
        
        # Load experiment run data
        records = []
        for exp_index in range(1, 20):  # Try multiple experiment indices
            try:
                run_root = experiments_root / f'exp-{exp_index:03d}' if not experiments_root.name.startswith('exp-') else experiments_root
                exp_records = _load_summary(run_root)
                # Filter for this specific experiment
                exp_records = [r for r in exp_records if r.get('experiment_name') == exp_name]
                records.extend(exp_records)
                if exp_records:
                    break
            except Exception:
                continue
        
        if not records:
            raise Exception(f"No run data found for experiment '{exp_name}' in {experiments_root}")
        
        # Group by load
        load_groups = {}
        for record in records:
            run_unit_name = record.get('unit', record.get('run_unit_name', ''))
            
            # Extract load from run_unit_name (pattern: rate-XXX)
            import re
            m = re.search(r"rate-(\d+)", run_unit_name)
            if m:
                load_value = int(m.group(1))
            else:
                cfg = record.get('config', {})
                load_value = cfg.get('base_rate')
            
            if load_value is None:
                continue
                
            load_groups.setdefault(load_value, []).append(record)
        
        # Process each load and collect metrics using RWG data
        exp_data = {api: {'latency_p99': [], 'goodput': []} for api in apis}
        loads = []
        
        for load_value in sorted(load_groups.keys()):
            load_records = load_groups[load_value]
            
            # Convert to KRPS for x-axis
            x_val = load_value / 1000.0
            loads.append(x_val)
            
            # Collect all repeats for this load using RWG data
            all_repeats = []
            for record in load_records:
                artifact_dir = Path(record.get('artifact_dir', '.'))
                try:
                    repeat_data = load_repeat_data(artifact_dir)
                    if repeat_data:
                        all_repeats.append(repeat_data)
                except Exception:
                    continue
            
            if not all_repeats:
                # No data for this load - add None placeholders
                for api in apis:
                    exp_data[api]['goodput'].append((None, None, None))
                    exp_data[api]['latency_p99'].append((None, None, None))
                continue
            
            # Aggregate across repeats
            aggregated = aggregate_by_api(all_repeats)
            
            for api in apis:
                if api in aggregated:
                    # Latency P95
                    lat_mean, lat_std, lat_ci = aggregated[api].get('p99_latency', (None, None, None))
                    exp_data[api]['latency_p99'].append((lat_mean, lat_std, lat_ci))
                    
                    # Goodput
                    gp_mean, gp_std, gp_ci = aggregated[api].get('goodput', (None, None, None))
                    exp_data[api]['goodput'].append((gp_mean, gp_std, gp_ci))
                else:
                    exp_data[api]['latency_p99'].append((None, None, None))
                    exp_data[api]['goodput'].append((None, None, None))
        
        all_experiment_data[label] = {
            'data': exp_data,
            'loads': loads,
            'apis': apis
        }
    
    if not all_experiment_data:
        return produced
    
    # Get all unique APIs and loads
    all_apis = list(all_apis)
    all_loads = []
    for exp_data in all_experiment_data.values():
        all_loads.extend(exp_data['loads'])
    all_loads = sorted(set(all_loads))
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Use ACM compact style
    style = ACM_COMPACT_HALF
    
    # Determine layout
    n_apis = len(all_apis)
    if n_apis == 1:
        # Single API: Side-by-side (Latency, Goodput)
        layout = "1x2"
        # We need a bit more width for side-by-side? 
        # ACM_COMPACT_HALF is 240pt. 120pt per plot is standard quarter width. 
        # So "1x2" in 240pt means two 120pt plots side-by-side. This is perfect.
        # However, we need space for the inner goodput label
        style = replace(style, wspace=0.15)
    else:
        # Multi API: Goodput first row, Latency second row
        layout = f"2x{n_apis}"
        
    grid = SubplotGrid(style, layout=layout)
    
    # Track all latency values for dynamic Y-axis
    all_latency_values = []
    
    for idx, api in enumerate(all_apis):
        # Determine axes based on layout
        if n_apis == 1:
            ax_lat = grid.get_ax(0, 0)
            ax_gp = grid.get_ax(0, 1)
        else:
            ax_gp = grid.get_ax(0, idx)     # Row 0: Goodput
            ax_lat = grid.get_ax(1, idx)    # Row 1: Latency
            
        display_api = api.replace('_all', '') if api.endswith('_all') else api
        
        # --- Plotting ---
        for exp_idx, (label, exp_info) in enumerate(all_experiment_data.items()):
            exp_data = exp_info['data']
            loads = exp_info['loads']
            
            if api not in exp_data:
                continue
            
            # 1. Latency Data
            latency_data = exp_data[api]['latency_p99']
            lat_means = [item[0] for item in latency_data]
            lat_cis = [item[2] if item[2] is not None else 0.0 for item in latency_data]
            
            # Filter None
            valid_lat_data = [(l, m, c) for l, m, c in zip(loads, lat_means, lat_cis)
                             if m is not None and not (isinstance(m, float) and math.isnan(m))]
            
            if valid_lat_data:
                v_loads, v_means, v_cis = zip(*valid_lat_data)
                
                lat_errs = None
                if any(c is not None for c in v_cis):
                     lat_lower = [min(c if c is not None else 0, m if m is not None else 0) for m, c in zip(v_means, v_cis)]
                     # For log scale, we also need to ensure we don't hit 0 exactly if we want log plotting to be happy?
                     # Actually matplotlib handles 0 in error bar lower limit by just clipping drawing usually, or we can clamp to slightly less than mean.
                     # But min(ci, mean) ensures lower bound is at worst 0. 
                     # If y scales are log, 0 is -inf. 
                     # If mean is > 0, and we subtract mean, we get 0. 
                     # Usually for log plot, we might want to clamp lower bound to something positive if mean-ci <= 0.
                     # But here we are producing (lower_delta, upper_delta).
                     # The checked value is y - lower_delta.
                     # If lower_delta = mean, y - lower_delta = 0. Log(0) is undefined.
                     # So for latency (log scale), we should perhaps clamp such that y-delta > 0.
                     # Let's just stick to clamping delta = min(ci, mean - epsilon) if we are strict, or just min(ci, mean) and hope matplotlib ignores 0 on log scale.
                     # Standard behavior for negative/zero lower bounds in log plots is often to clip them to a small positive number or not draw them.
                     
                     lat_upper = [c if c is not None else 0 for c in v_cis]
                     lat_errs = [lat_lower, lat_upper]

                plot_line(
                    ax_lat, v_loads, v_means, yerr=lat_errs,
                    label=label, style=style, color_idx=exp_idx, style_idx=exp_idx,
                    show_markers=True
                )
                all_latency_values.extend(v_means)

            # 2. Goodput Data
            goodput_data = exp_data[api]['goodput']
            gp_means = [(item[0] / 1000.0) if item[0] is not None else None for item in goodput_data]
            gp_cis = [(item[2] / 1000.0) if item[2] is not None else 0.0 for item in goodput_data]
            
            # Filter None
            valid_gp_data = [(l, m, c) for l, m, c in zip(loads, gp_means, gp_cis)
                            if m is not None and not (isinstance(m, float) and math.isnan(m))]

            if valid_gp_data:
                v_loads, v_means, v_cis = zip(*valid_gp_data)
                
                gp_errs = None
                if any(c is not None for c in v_cis):
                     gp_lower = [min(c if c is not None else 0, m if m is not None else 0) for m, c in zip(v_means, v_cis)]
                     gp_upper = [c if c is not None else 0 for c in v_cis]
                     gp_errs = [gp_lower, gp_upper]

                plot_line(
                    ax_gp, v_loads, v_means, yerr=gp_errs,
                    label=label, style=style, color_idx=exp_idx, style_idx=exp_idx,
                    show_markers=True
                )

        # --- Latency Axis Config ---
        # Add SLO line
        slo_val = None
        for key in [display_api, display_api.replace('-', '_'), display_api.replace('_', '-')]:
            if slo_map and key in slo_map:
                slo_val = slo_map[key]
                break
        
        if slo_val is not None:
            ax_lat.axhline(y=slo_val, color='r', linestyle='--',
                       label='SLO', linewidth=style.line_width)
            all_latency_values.append(slo_val)

        # Config Latency Axis
        ax_lat.set_yscale('log')
        ax_lat.grid(True, alpha=0.3)
        # Ensure minor ticks on Y axis only

        if n_apis > 1:
             # In 2xN grid, Latency is Row 1 (bottom), Goodput is Row 0 (top)
             # Latency axis title might not be needed if Goodput has it, or vice versa.
             # Typically top row has titles. Goodput is top row.
             # But we want to label the column.
             # Put title on Goodput (top)
             ax_gp.set_title(display_api, fontsize=style.title_size)
        else:
            # Single API side-by-side -> Each might need title? Or one title for whole?
            # Typically separate titles "Latency", "Goodput" or just axis labels?
            # Axis labels handle "Latency" vs "Goodput".
            # Maybe title the whole figure or subplots?
            # Let's title subplots with "Latency" and "Goodput" maybe? 
            # Or just rely on Y-axis labels.
            # But we need to ID the API. 
            pass # We will handle titles in configure_ax/labels if needed.
                 # Actually grid.configure_labels handles overall pattern.
            
            # For 1x2, maybe title both with API name? Or Set global title?
            # Let's simple set title on both for now or just Left one. 
            # But clearer: columns are metric types in 1x2.
            # Actually standard practice: Y-label says metric. 
            # We just need to know it IS "Search Hotel".
            # grid.fig.suptitle(display_api) ?
            pass

    # Dynamic Y-axis for Latency
    if all_latency_values:
        dyn_y_max = max(all_latency_values) * 5.0 * 1.05
        dyn_y_max = max(dyn_y_max, 10)
    else:
        dyn_y_max = 500

    for idx in range(n_apis):
        if n_apis == 1:
            ax = grid.get_ax(0, 0) # Latency is col 0
        else:
            ax = grid.get_ax(1, idx) # Latency is row 1
        ax.set_ylim(1, dyn_y_max)
        
    # --- Configuration ---
    if n_apis == 1:
        # Layout: 1x2. Col 0: Latency, Col 1: Goodput
        # Both share X: Offered Load.
        # Y labels separate.
        
        # Latency (0,0)
        grid.configure_ax(grid.get_ax(0,0), 
                          ylabel="P99 Latency (ms)", 
                          xlabel="Offered Load (KRPS)",
                          x_step=2.0,
                          x_data=all_loads,
                          log_y=True)
        
        # Goodput (0,1)
        grid.configure_ax(grid.get_ax(0,1), 
                          ylabel="Goodput (KRPS)", 
                          xlabel="Offered Load (KRPS)",
                          x_step=2.0,
                          x_data=all_loads,
                          log_y=False)
                          
    else:
        # Layout 2xN. Row 0 Goodput. Row 1 Latency.
        # Row 0 (Goodput): Show Y label only on Left. X ticks/labels hidden? 
        # Actually usually top row has no X labels if shared x.
        # Bottom row (Latency): X labels.
        
        # We can use configure_labels logic or manual.
        # Let's iterate.
        for idx in range(n_apis):
             # Row 0: Goodput
             ax_gp = grid.get_ax(0, idx)
             grid.configure_ax(ax_gp,
                 ylabel="Goodput (KRPS)" if idx == 0 else "",
                 xlabel="",
                 show_xlabel=False,
                 show_xticklabels=False,
                 x_step=2.0,
                 x_data=all_loads,
                 log_y=False,
                 show_ylabel=(idx==0),
                 show_yticklabels=(idx==0)
             )
             
             # Row 1: Latency
             ax_lat = grid.get_ax(1, idx)
             grid.configure_ax(ax_lat,
                 ylabel="P99 Latency (ms)" if idx == 0 else "",
                 xlabel="Offered Load (KRPS)",
                 x_step=2.0,
                 x_data=all_loads,
                 log_y=True,
                 show_ylabel=(idx==0),
                 show_yticklabels=(idx==0)
             )

    grid.add_shared_legend(position="top")

    # Save
    comb_path = output_dir / f'{figure_name}_combined.pdf'
    grid.save(comb_path)
    produced.append(comb_path)

    return produced


def generate_merged_figures(
    merged_config_path: Path,
    experiments_file_path: Path,
    experiments_root: Path,
    output_dir: Path,
    experiment_index: str = None,
    global_config_path: str = None
) -> None:
    """Generate all merged figures based on configuration."""
    # Load configurations
    merged_config = load_merged_config(merged_config_path)
    bench = ""
    if global_config_path:
        try:
            with open(global_config_path) as f:
                global_cfg = json.load(f)
            bench = global_cfg.get('bench', '')
        except Exception:
            pass
    experiment_configs = load_experiment_configs(experiments_file_path, bench=bench)
    figures = merged_config.get('figures', {})
    
    # If experiment_index is set, use only that experiment run directory
    if experiment_index:
        experiments_root = experiments_root / f'exp-{experiment_index}'
        print(f"Using only experiment run directory: {experiments_root}")
    
    for figure_name, figure_config in figures.items():
        figure_type = figure_config.get('type')
        
        if not figure_type:
            print(f"Warning: Figure '{figure_name}' has no type specified")
            continue
        
        print(f"Generating merged figure: {figure_name} (type: {figure_type})")
        
        try:
            if figure_type == 'latency-and-goodput-vs-load':
                produced = generate_latency_goodput_vs_load_merged(
                    figure_name, figure_config, experiment_configs,
                    experiments_root, output_dir, global_config_path
                )
                print(f"  Generated {len(produced)} files: {[p.name for p in produced]}")
            elif figure_type == 'latency-and-rate-vs-time':
                produced = generate_latency_and_rate_vs_time_merged(
                    figure_name, figure_config, experiment_configs,
                    experiments_root, output_dir, global_config_path
                )
                print(f"  Generated {len(produced)} files: {[p.name for p in produced]}")
            elif figure_type == 'max-queue':
                produced = generate_max_queue_merged(
                    figure_name, figure_config, experiment_configs,
                    experiments_root, output_dir, global_config_path
                )
                print(f"  Generated {len(produced)} files: {[p.name for p in produced]}")
            elif figure_type == 'resource-waste-bar':
                produced = generate_resource_waste_bar_merged(
                    figure_name, figure_config, experiment_configs,
                    experiments_root, output_dir, global_config_path
                )
                print(f"  Generated {len(produced)} files: {[p.name for p in produced]}")
            elif figure_type == 'latency-vs-throughput':
                produced = generate_latency_vs_throughput_merged(
                    figure_name, figure_config, experiment_configs,
                    experiments_root, output_dir, global_config_path
                )
                print(f"  Generated {len(produced)} files: {[p.name for p in produced]}")
            else:
                print(f"  Unknown figure type: {figure_type}")
        except Exception as e:
            print(f"  Error generating figure '{figure_name}': {e}")
            if os.environ.get('PLOT_DEBUG') == '1':
                traceback.print_exc()


def generate_latency_and_rate_vs_time_merged(
    figure_name: str,
    figure_config: dict,
    experiment_configs: dict,
    experiments_root: Path,
    output_dir: Path,
    global_config: str = None
) -> list:
    """
    Generate merged latency-and-rate-vs-time figures.
    Produces two separate figures:
      1. {name}_rate_vs_time.pdf: Stacked Rate vs Time (1 row x N experiments)
      2. {name}_latency_vs_time.pdf: Latency Percentiles vs Time (1 row x N experiments)
    Constraint: Only supports 1 API.
    """
    # Import new RWG data loading and plotting
    try:
        from exec.plots.data_loader import load_repeat_data
        from exec.plots.plotting_primitives import (
            SubplotGrid, ACM_COMPACT_HALF, plot_line, plot_stacked_area
        )
    except ImportError:
        try:
            from plots.data_loader import load_repeat_data  # type: ignore
            from plots.plotting_primitives import (  # type: ignore
                SubplotGrid, ACM_COMPACT_HALF, plot_line, plot_stacked_area
            )
        except ImportError:
            from data_loader import load_repeat_data  # type: ignore
            from plotting_primitives import (  # type: ignore
                SubplotGrid, ACM_COMPACT_HALF, plot_line, plot_stacked_area
            )
    
    import matplotlib.pyplot as plt
    import numpy as np
    import json
    import matplotlib.patches as mpatches
    from matplotlib.lines import Line2D
    
    produced: list = []
    include_experiments = figure_config.get('include', {})
    
    if not include_experiments:
        return produced
    
    # Load SLOs from config file
    slo_map = {}
    if global_config:
        try:
            with open(global_config) as f:
                global_configs = json.load(f)
            slo_map = global_configs.get('slos', {})
        except Exception:
            pass
            
    # Scan all experiment runs once to build an index
    run_index = {}
    roots_to_scan = []
    if experiments_root.name.startswith('exp-'):
        roots_to_scan.append(experiments_root)
    else:
        for i in range(1, 51):
            roots_to_scan.append(experiments_root / f'exp-{i:03d}')
            
    print(f"Scanning {len(roots_to_scan)} potential run directories...")
    for run_root in roots_to_scan:
        if not run_root.exists(): continue
        try:
            records = _load_summary(run_root)
            for rec in records:
                e_name = rec.get('experiment_name')
                u_name = rec.get('unit', rec.get('run_unit_name'))
                if e_name and u_name:
                    key = (e_name, u_name)
                    if key not in run_index: run_index[key] = []
                    run_index[key].append(rec)
        except Exception: continue

    # Color map for Rate
    RATE_COLOR_MAP = {
        'goodput': '#4daf4a',       # Green
        'SLO violation': '#e41a1c', # Red
        'dropped': '#ff7f00',       # Orange
        'errors': '#999999',        # Gray
    }

    # Collect data
    plot_data = [] 
    all_found_apis = set()
    
    for exp_name, cfg in include_experiments.items():
        label = cfg.get('label', exp_name)
        target_unit = cfg.get('unit')
        target_repeat = cfg.get('repeat')
        
        if target_unit is None or target_repeat is None:
            print(f"Skipping {exp_name}: 'unit' and 'repeat' must be specified")
            continue
            
        key = (exp_name, target_unit)
        candidates = run_index.get(key, [])
        chosen_rec = None
        for rec in candidates:
            if str(rec.get('repeat_index')) == str(target_repeat):
                chosen_rec = rec
                break
        
        if not chosen_rec:
            print(f"Warning: Could not find run for {exp_name} unit={target_unit} repeat={target_repeat}")
            continue
            
        artifact_dir = Path(chosen_rec.get('artifact_dir', '.'))
        try:
            loaded = load_repeat_data(artifact_dir)
            if not loaded: continue
            for api, vals in loaded.items():
                if len(vals) == 3: overall, realtime, _ = vals
                else: overall, realtime = vals
                if realtime and not realtime.df.empty:
                    all_found_apis.add(api)
                    plot_data.append({
                        'label': label,
                        'realtime': realtime,
                        'api': api
                    })
        except Exception: continue

    if not plot_data:
        return []

    if len(all_found_apis) != 1:
        print(f"Warning: 'latency-and-rate-vs-time' merged expects 1 API, found {len(all_found_apis)}. Using first.")
        
    target_api = list(all_found_apis)[0]
    final_data = [d for d in plot_data if d['api'] == target_api]
    n_plots = len(final_data)
    if n_plots == 0: return []

    # Common lookup
    slo_val = 60.0
    for key in [target_api, target_api.replace('-', '_'), target_api.replace('_', '-')]:
        if key in slo_map:
            slo_val = slo_map[key]
            break
    
    output_dir.mkdir(parents=True, exist_ok=True)
    style = ACM_COMPACT_HALF

    # --- 1. GENERATE RATE PLOT ---
    print(f"Generating merged rate plot for {target_api}...")
    grid_rate = SubplotGrid(style, layout=f"1x{n_plots}")
    
    rate_max = 0.0
    for item in final_data:
        df = item['realtime'].df
        df = df[df['relative_time'] <= 15.0]
        cols = ['goodput', 'slo_violations', 'dropped_requests']
        existing = [c for c in cols if c in df.columns]
        if existing:
            total = df[existing].sum(axis=1)
            if not total.empty:
                m = total.max() / 1000.0
                if m > rate_max: rate_max = m
    rate_ylim = (0, rate_max * 1.1 if rate_max > 0 else 1.0)

    for i, item in enumerate(final_data):
        ax = grid_rate.get_ax(0, i)
        df = item['realtime'].df
        df = df[df['relative_time'] <= 15.0].copy()
        time_x = df['relative_time'].values
        
        y_series = {}
        if 'goodput' in df.columns: y_series['goodput'] = df['goodput'].values / 1000.0
        if 'slo_violations' in df.columns: y_series['SLO violation'] = df['slo_violations'].values / 1000.0
        if 'dropped_requests' in df.columns: y_series['dropped'] = df['dropped_requests'].values / 1000.0

        
        if y_series:
            plot_stacked_area(ax, time_x, y_series, style=style, color_map=RATE_COLOR_MAP)
            
        # Title & Axis
        ax.set_title(item['label'], fontsize=style.title_size)
        grid_rate.configure_ax(ax, 
            ylabel="Rate (KRPS)" if i == 0 else "",
            xlabel="Time (s)",
            ylim=rate_ylim,
            y_step=2,
            show_ylabel=(i==0),
            show_yticklabels=(i==0),
            x_data=time_x, x_type='int', x_step=3
        )
        
    # Rate Legend
    rate_handles = [mpatches.Patch(color=v, label=k.title()) for k, v in RATE_COLOR_MAP.items() if k != 'errors']
    rate_labels = [h.get_label() for h in rate_handles]
    grid_rate.add_shared_legend(position="top", handles=rate_handles, labels=rate_labels)
    
    rate_path = output_dir / f'{figure_name}_rate_vs_time.pdf'
    grid_rate.save(rate_path)
    produced.append(rate_path)

    # --- 2. GENERATE LATENCY PLOT ---
    print(f"Generating merged latency plot for {target_api}...")
    grid_lat = SubplotGrid(style, layout=f"1x{n_plots}")
    
    lat_max = 500.0 # Default cap
    
    for i, item in enumerate(final_data):
        ax = grid_lat.get_ax(0, i)
        df = item['realtime'].df
        df = df[df['relative_time'] <= 15.0].copy()
        time_x = df['relative_time'].values
        
        # Plot P50 (Red typically in ACM styles if color_idx=0)
        if 'p50_latency' in df.columns:
            plot_line(ax, time_x, df['p50_latency'].values, label='P50', style=style, color_idx=0)
            
        # Plot P99 (Blue typically in ACM styles if color_idx=1)
        if 'p99_latency' in df.columns:
            plot_line(ax, time_x, df['p99_latency'].values, label='P99', style=style, color_idx=1)
            
        # SLO Line
        ax.axhline(y=slo_val, color='r', linestyle='--', linewidth=style.line_width, label='SLO')
        
        # Title & Axis
        ax.set_title(item['label'], fontsize=style.title_size)
        grid_lat.configure_ax(ax,
            ylabel="Latency (ms)" if i == 0 else "",
            xlabel="Time (s)",
            ylim=(1, lat_max),
            log_y=True,
            show_ylabel=(i==0),
            show_yticklabels=(i==0),
            x_data=time_x, x_type='int', x_step=3
        )
        
    # Latency Legend
    # Since we used plot_line with indices, we should match handles.
    # P50 (idx 0), P99 (idx 1), SLO (Red Dashed)
    lat_handles = []
    lat_handles.append(Line2D([0], [0], color=style.colors[0], linewidth=style.line_width, label='P50'))
    lat_handles.append(Line2D([0], [0], color=style.colors[1], linewidth=style.line_width, label='P99'))
    lat_handles.append(Line2D([0], [0], color='r', linestyle='--', linewidth=style.line_width, label='SLO'))
    lat_labels = [h.get_label() for h in lat_handles]
    
    grid_lat.add_shared_legend(position="top", handles=lat_handles, labels=lat_labels)
    
    lat_path = output_dir / f'{figure_name}_latency_vs_time.pdf'
    grid_lat.save(lat_path)
    produced.append(lat_path)
    
    return produced


def generate_latency_vs_throughput_merged(
    figure_name: str,
    figure_config: dict,
    experiment_configs: dict,
    experiments_root: Path,
    output_dir: Path,
    global_config: str = None
) -> list:
    """
    Generate merged latency-vs-throughput figure.
    Plots lines for multiple experiments on the same axes.
    """
    # Import new RWG data loading and plotting
    try:
        from exec.plots.data_loader import load_repeat_data
        from exec.plots.aggregation import aggregate_overall_metric
        from exec.plots.plotting_primitives import (
            SubplotGrid, ACM_COMPACT_HALF, ACM_QUARTER, plot_line, plot_grouped_bars
        )
    except ImportError:
        try:
            from plots.data_loader import load_repeat_data  # type: ignore
            from plots.aggregation import aggregate_overall_metric  # type: ignore
            from plots.plotting_primitives import (  # type: ignore
                SubplotGrid, ACM_COMPACT_HALF, plot_line, ACM_QUARTER
            )
        except ImportError:
            from data_loader import load_repeat_data  # type: ignore
            from aggregation import aggregate_overall_metric  # type: ignore
            from plotting_primitives import (  # type: ignore
                SubplotGrid, ACM_COMPACT_HALF, plot_line, plot_grouped_bars, ACM_QUARTER
            )

    include_experiments = figure_config.get('include', {})
    if not include_experiments:
        return []

    produced = []
    
    # 1. Collect all unique APIs across all included experiments
    all_apis = set()
    for exp_name in include_experiments.keys():
        if exp_name not in experiment_configs:
            continue
        exp_def = experiment_configs[exp_name]
        apis = exp_def.get('apis', [])
        all_apis.update(apis)
    
    all_apis = sorted(list(all_apis))
    if not all_apis:
        print("Warning: No APIs found in any included experiments")
        return []

    n_apis = len(all_apis)

    # Use ACM compact style
    style = ACM_QUARTER if n_apis == 1 else ACM_COMPACT_HALF

    # Data structure: data[api][exp_label] = {'tps': [], 'p99': [], ...}
    plot_data = {api: {} for api in all_apis}
    
    # Track global min/max for axis configuration per API
    # limits[api] = {'max_tp': 0, 'max_lat': 0}
    api_limits = {api: {'max_tp': 0, 'max_p99': 0} for api in all_apis}

    color_idx_map = {} # label -> color_idx

    for exp_idx, (exp_name, exp_cfg) in enumerate(include_experiments.items()):
        label = exp_cfg.get('label', exp_name)
        color_idx_map[label] = exp_idx
        
        if exp_name not in experiment_configs:
            print(f"Warning: Experiment '{exp_name}' not found in configs")
            continue
            
        exp_def = experiment_configs[exp_name]
        exp_apis = exp_def.get('apis', [])
        
        # 1. Find all units for this experiment
        found_units = {}  # unit_name -> list of artifact_dirs
        unit_load_value = {}  # unit_name -> offered load (same heuristic as plot_runner)

        # Determine roots to scan
        roots_to_scan = []
        if experiments_root.name.startswith('exp-'):
            roots_to_scan.append(experiments_root)
        else:
            for exp_index in range(1, 20):
                roots_to_scan.append(experiments_root / f'exp-{exp_index:03d}')

        for run_root in roots_to_scan:
            records = _load_summary(run_root)
            for r in records:
                if r.get('experiment_name') == exp_name:
                    unit_name = r.get('unit', r.get('run_unit_name'))
                    if unit_name not in found_units:
                        found_units[unit_name] = []
                        m = re.search(r'rate-(\d+)', str(unit_name) or '')
                        if m:
                            unit_load_value[unit_name] = int(m.group(1))
                        else:
                            cfg = r.get('config') or {}
                            br = cfg.get('base_rate')
                            unit_load_value[unit_name] = int(br) if br is not None else None
                    found_units[unit_name].append(Path(r.get('artifact_dir')))

        # 2. Process each unit (load level) for EACH API
        # We need to collect points separately for each API because they might be in different files or keys
        
        for api in exp_apis:
            if api not in all_apis: 
                continue # Should be there, but good to be safe

            exp_points = [] # list of dicts for this API

            for unit_name, artifact_dirs in found_units.items():
                # One sample per repeat from overall-*.json; CI across repeats.
                repeat_throughputs: List[float] = []
                repeat_p99: List[float] = []
                repeat_goodputs: List[float] = []
                repeat_p75: List[float] = []

                for artifact_dir in artifact_dirs:
                    repeat_data = load_repeat_data(artifact_dir)
                    if repeat_data and api in repeat_data:
                        vals = repeat_data[api]
                        if len(vals) == 3:
                            overall, _, _ = vals
                        else:
                            overall, _ = vals
                        if overall is not None:
                            repeat_throughputs.append(float(overall.throughput))
                            repeat_p99.append(float(overall.p99_latency))
                            repeat_goodputs.append(float(overall.goodput))
                            repeat_p75.append(float(overall.p75_latency))
                        elif os.environ.get('PLOT_DEBUG') == '1':
                            print(f"    [DEBUG] Overall data is None for {api} in {artifact_dir}")
                    elif os.environ.get('PLOT_DEBUG') == '1':
                        print(f"    [DEBUG] No data for {api} in {artifact_dir}")

                if repeat_throughputs and repeat_p99:
                    tp_mean, _, tp_ci = aggregate_overall_metric(repeat_throughputs)
                    gp_mean, _, gp_ci = aggregate_overall_metric(repeat_goodputs)
                    p99_mean, _, p99_ci = aggregate_overall_metric(repeat_p99)
                    p75_mean, _, p75_ci = (
                        aggregate_overall_metric(repeat_p75)
                        if repeat_p75
                        else (None, None, None)
                    )

                    if tp_mean is not None and p99_mean is not None:
                        exp_points.append({
                            'tp': tp_mean,
                            'tp_ci': tp_ci if tp_ci is not None else 0.0,
                            'gp': gp_mean if gp_mean is not None else 0.0,
                            'gp_ci': gp_ci if gp_ci is not None else 0.0,
                            'p99': p99_mean,
                            'p99_ci': p99_ci if p99_ci is not None else 0.0,
                            'p75': p75_mean if p75_mean is not None else 0.0,
                            'p75_ci': p75_ci if p75_ci is not None else 0.0,
                            'load_value': unit_load_value.get(unit_name),
                        })
                        if os.environ.get('PLOT_DEBUG') == '1':
                            print(f"  [DEBUG] Unit: {unit_name}")
                            print(f"    Repeats: {len(repeat_throughputs)}")
                            print(f"    Throughput: {tp_mean:.2f}")
                            print(f"    P99: {p99_mean:.2f} ± {p99_ci if p99_ci else 0:.2f}")


            # Order by offered load (matches latency_vs_throughput_experiment), not by achieved tp
            if any(p.get('load_value') is not None for p in exp_points):
                exp_points.sort(
                    key=lambda x: (
                        float('inf') if x.get('load_value') is None else x['load_value'],
                        x['tp'],
                    )
                )
            else:
                exp_points.sort(key=lambda x: x['tp'])
            
            if os.environ.get('PLOT_DEBUG') == '1':
                print(f"[DEBUG] Experiment: {exp_name} ({label}) API: {api}")
                print(f"  Aggregated Points (TP, P99, P95):")
                for p in exp_points:
                    print(f"    {p['tp']:.2f}, {p['p99']:.2f}, {p['p99']:.2f}")

            if not exp_points:
                continue

            # Store aggregated data to be plotted later
            plot_data[api][label] = {
                'tps': [p['tp'] for p in exp_points],
                'tp_ci': [p['tp_ci'] for p in exp_points],
                'goodputs': [p['gp'] for p in exp_points],
                'goodput_ci': [p['gp_ci'] for p in exp_points],
                'p99': [p['p99'] for p in exp_points],
                'p99_ci': [p['p99_ci'] for p in exp_points],
                'p75': [p['p75'] for p in exp_points],
                'p75_ci': [p['p75_ci'] for p in exp_points],
            }
            
            # Update limits
            if exp_points:
                max_tp = max(p['tp'] for p in exp_points)
                max_p99 = max(p['p99'] + (p['p99_ci'] or 0) for p in exp_points)
                
                if max_tp > api_limits[api]['max_tp']: api_limits[api]['max_tp'] = max_tp
                if max_p99 > api_limits[api]['max_p99']: api_limits[api]['max_p99'] = max_p99

    # Load SLOs from config file
    with open(global_config) as f:
        global_configs = json.load(f)
    slo_map = global_configs.get('slos', {})

    line_specs = [
        ('p99', 'p99_ci', 'P99 Latency (ms)', f'{figure_name}_latency_vs_throughput.pdf'),
        ('p75', 'p75_ci', 'P75 Latency (ms)', f'{figure_name}_latency_vs_throughput_p75.pdf'),
    ]

    output_dir.mkdir(parents=True, exist_ok=True)

    for val_key, ci_key, y_axis_label, pdf_name in line_specs:
        grid = SubplotGrid(style, layout=f"1x{n_apis}")

        for api_idx, api in enumerate(all_apis):
            ax_lat = grid.get_ax(0, api_idx)

            if len(all_apis) > 1:
                ax_lat.set_title(api, fontsize=style.title_size)

            for label, data in plot_data[api].items():
                color_idx = color_idx_map.get(label, 0)

                lat_vals = data[val_key]
                lat_cis = data[ci_key]

                # Symmetric CI (same as latency_vs_throughput_experiment); omit only if length mismatch
                lat_errs = (
                    [float(c) if c is not None else 0.0 for c in lat_cis]
                    if len(lat_cis) == len(lat_vals)
                    else None
                )

                plot_line(
                    ax_lat, data['tps'], lat_vals,
                    yerr=lat_errs,
                    label=label,
                    style=style,
                    color_idx=color_idx,
                    style_idx=color_idx,
                    show_markers=True,
                )

            slo_val = None
            possible_keys = [api, api.replace('-', '_'), api.replace('_', '-')]
            if api.endswith('_all'):
                base = api.replace('_all', '')
                possible_keys.extend([base, base.replace('-', '_'), base.replace('_', '-')])

            for key in possible_keys:
                if slo_map and key in slo_map:
                    slo_val = slo_map[key]
                    break

            if slo_val is not None:
                slo_val = float(slo_val)
                ax_lat.axhline(
                    y=slo_val, color='r', linestyle='--',
                    label='SLO', linewidth=style.line_width,
                )
            else:
                raise Exception("SLO is None")

            grid.configure_ax(
                ax_lat,
                ylabel=y_axis_label if api_idx == 0 else "",
                xlabel="Throughput (RPS)",
                ylim=(0, 2 * slo_val),
                grid=True,
                show_xticklabels=True,
                show_xlabel=True,
                show_ylabel=(api_idx == 0),
                show_yticklabels=True,
                x_type='int',
                x_step=1000
            )

        grid.add_shared_legend(position="top")
        line_path = output_dir / pdf_name
        grid.save(line_path)
        produced.append(line_path)
    

    
    # 4. Generate Latency vs Throughput Bar Plot (Goodput at Max Rate)
    # This plot visualizes the peak goodput for each included experiment/API as a grouped bar chart
    
    print(f"Generating merged latency-vs-throughput bar plot...")
    
    # Create grid (1x1) using user-specified width
    bar_style = ACM_QUARTER
    bar_grid = SubplotGrid(bar_style, layout="1x1")
    ax_bar = bar_grid.get_ax(0, 0)
    
    # Prepare data for plot_grouped_bars
    # Grouping: Experiments (X-axis)
    # Bars: APIs (Colors/Legend)
    
    # We need to preserve the order from include_experiments
    sorted_exp_items = []
    for exp_idx, (exp_name, exp_cfg) in enumerate(include_experiments.items()):
        if exp_name in experiment_configs:
            sorted_exp_items.append((exp_name, exp_cfg))
    
    x_positions = list(range(len(sorted_exp_items)))
    exp_labels = [item[1].get('label', item[0]) for item in sorted_exp_items]
    
    bar_groups = []
    max_goodput = 0
    
    for api in all_apis:
        heights = []
        errors = []
        
        has_data = False
        for exp_name, exp_cfg in sorted_exp_items:
            label = exp_cfg.get('label', exp_name)
            
            # Get data for this API and Experiment
            if label in plot_data[api]:
                d = plot_data[api][label]
                if d['goodputs']:
                    # Last point = highest offered load (exp_points sorted by load_value)
                    heights.append(d['goodputs'][-1])
                    errors.append(d['goodput_ci'][-1] if 'goodput_ci' in d else 0.0)
                    has_data = True
                else:
                    heights.append(0.0)
                    errors.append(0.0)
            else:
                heights.append(0.0)
                errors.append(0.0)
        
        # Add API to groups if it has any data (or maybe just add anyway for consistency)
        if has_data:
            bar_groups.append((api, heights, errors))
        if max(heights) > max_goodput:
            max_goodput = max(heights)
            
    if bar_groups:
        plot_grouped_bars(ax_bar, x_positions, bar_groups, style=bar_style)
        
        # Configure Axis
        bar_grid.configure_ax(ax_bar,
            xlabel="",
            ylabel="Goodput (RPS)",
            show_xticklabels=True,
            y_guard=0.05,
            ylim=(0, max_goodput * 1.1),
            y_step=100,
            y_type='int'
        )
        
        # Set X-tick labels to Experiment Labels
        ax_bar.set_xticks(x_positions)
        ax_bar.set_xticklabels(exp_labels, rotation=0 if len(exp_labels) < 4 else 30, ha='center', fontsize=bar_style.font_size - 1)
        
        # Legend
        bar_grid.add_shared_legend(position="top")
        
        # Save
        bar_path = output_dir / f'{figure_name}_goodput_bar.pdf'
        bar_grid.save(bar_path)
        produced.append(bar_path)
    
    return produced


# ============================================================================
# CLI and Main Execution
# ============================================================================


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='Generate merged experiment plots')
    parser.add_argument('--merged-config', type=Path, required=True,
                       help='Path to merged.yaml configuration file')
    parser.add_argument('--experiments-file', type=Path, required=True,
                       help='Path to experiments.json configuration file')
    parser.add_argument('--experiments-root', type=Path, default=Path('experiment_runs'),
                       help='Root directory containing experiment runs')
    parser.add_argument('--output-dir', type=Path, default=Path('generated_plots/merged'),
                       help='Output directory for merged plots')
    parser.add_argument('--experiment-index', type=str, default=None,
                       help='Experiment index (e.g., 001) to use. If set, only that experiment run will be used.')
    parser.add_argument('--config', required=True, help='Path to global config.json')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        generate_merged_figures(
            args.merged_config,
            args.experiments_file, 
            args.experiments_root,
            args.output_dir,
            experiment_index=args.experiment_index,
            global_config_path=args.config
        )
    except Exception as e:
        print(f"Error: {e}")
        if os.environ.get('PLOT_DEBUG') == '1':
            traceback.print_exc()
        return 1
    return 0


if __name__ == '__main__':
    exit(main())
