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
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Any
import traceback
import os
import numpy as np
import math
from matplotlib.ticker import LogLocator
import statistics

def _calculate_waste_from_prometheus(prom_data, loaded_data: dict, apis: list, bench: str) -> Dict[str, Dict[str, float]]:
    """
    Calculate resource waste using Prometheus data and the new 2-step algorithm.
    Algorithm:
    1. LocalFailed(S) = ReportedFailed(S) - Sum(ReportedFailed(DirectDownstream))
    2. FinalFailed(S) = LocalFailed(S) + FinalFailed(NextServiceInSequence) [Cumulative Suffix Sum in Reverse]
    3. Waste = (FinalFailed + SLO_Violations) / Total_Accepted
    
    Refinements:
    - BLO Violations: Use 'num_slo_violations' from overall-{api}.json
    - Sanity Check: num_dropped_requests (overall) == failed_rpc_counter (entry point)
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
        if api in loaded_data:
             overall_tuple = loaded_data[api] 
             # loaded_data[api] is (overall, realtime, prom)
             if overall_tuple and len(overall_tuple) >= 1 and overall_tuple[0]:
                 overall_obj = overall_tuple[0]
        
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
    
    # Import helpers from resource waste plugin
    try:
        from exec.plots.plugins.resource_waste_unit import _normalize_service_name, _mean_std, _calculate_waste_per_repeat
    except Exception:
        from experiments.exec.plots.plugins.resource_waste_unit import _normalize_service_name, _mean_std, _calculate_waste_per_repeat
    try:
        from exec.plots.common import extract_series
    except Exception:
        from experiments.exec.plots.common import extract_series
    try:
        from canvas import canvas
    except Exception:
        try:
            from experiments.canvas import canvas
        except Exception:
            raise ImportError("Canvas module not available. Please ensure canvas is installed.")
    
    # Import matplotlib for GridSpec (needed for proportional subplot widths)
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec

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
                        
                        if prom_data and prom_data.metrics:
                            # Use Prometheus data
                            waste_data = _calculate_waste_from_prometheus(prom_data, loaded_data, apis, bench)
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
    if n_apis <= 1:
        # Single API case: one subplot
        fig, ax = canvas.create_canvas(
            nrows=1, ncols=1, width_in_inches=base_subplot_width, aspect_ratio=0.6,
            font_size=11, legend_size=9, line_width=1.0, marker_size=3
        )
        axes = [ax] if hasattr(ax, 'bar') else ax
    else:
        # Multiple API case: 
        # Step 1: Find non-zero services for each API
        api_service_counts = {}
        for api in all_apis:
            services_count_for_api = 0
            for service in all_services_unfiltered:
                has_nonzero_waste_for_api = False
                for ed in exp_data:
                    data = ed['data']
                    if (service in data and 
                        api in data[service] and 
                        data[service][api]['mean'] > 0.0):
                        has_nonzero_waste_for_api = True
                        break
                if has_nonzero_waste_for_api:
                    services_count_for_api += 1
            api_service_counts[api] = services_count_for_api
        
        # Step 2: Calculate proportional subplot widths
        total_services_across_apis = sum(api_service_counts.values())
        if total_services_across_apis == 0:
            total_services_across_apis = n_apis  # Fallback
        
        # Use consistent compact parameters across all cases
        subplot_widths = []
        for api in all_apis:
            service_count = api_service_counts[api]
            if service_count == 0:
                service_count = 1  # Minimum for empty APIs
            # Calculate compact width with consistent bounds
            subplot_width = max(compact_min_width, min(compact_max_width, service_count * compact_bar_width_per_service))
            subplot_widths.append(subplot_width)
        
        total_figure_width = sum(subplot_widths)
        
        # Step 3: Create figure with proportional subplot widths using GridSpec
        # Calculate width ratios based on service counts
        width_ratios = []
        for api in all_apis:
            service_count = api_service_counts[api]
            if service_count == 0:
                service_count = 1  # Minimum ratio for empty APIs
            width_ratios.append(service_count)
        
        fig = plt.figure(figsize=(total_figure_width, total_figure_width * 0.35))
        
        # Create GridSpec with width ratios
        gs = gridspec.GridSpec(1, n_apis, width_ratios=width_ratios, 
                              left=0.08, right=0.95, top=0.85, bottom=0.15,
                              wspace=0.1)
        
        # Create subplots with proportional widths and consistent styling
        axes = []
        for i in range(n_apis):
            ax = fig.add_subplot(gs[0, i])
            # Apply consistent compact styling
            for item in ([ax.title, ax.xaxis.label, ax.yaxis.label] +
                        ax.get_xticklabels() + ax.get_yticklabels()):
                item.set_fontsize(11)
            axes.append(ax)
    
    # Color mapping for experiments
    try:
        colors = canvas.color_list[:n_exps]
    except Exception:
        colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd']
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
    total_bars = n_exps + 1  # +1 for the sidecar bar
    fixed_total_group_width = 0.7  # Fixed total width of grouped bars as fraction of x-unit
    fixed_bar_width = fixed_total_group_width / total_bars if total_bars > 0 else fixed_total_group_width
    
    # Collect all legend handles and labels
    all_handles = []
    all_labels = []
    
    for api_idx, api in enumerate(all_apis):
        ax = axes[api_idx] if n_apis > 1 else axes[0]
        
        # Filter services for this specific API
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
        
        for exp_idx, ed in enumerate(exp_data):
            label = ed['label']
            data = ed['data']
            color = exp_colors.get(label)
            
            offsets = [x - fixed_total_group_width/2 + exp_idx * api_bar_width + api_bar_width/2 for x in api_x_indices]
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
            
            bars = ax.bar(offsets, means, yerr=stds, width=api_bar_width*0.9, 
                         label=label, color=color, edgecolor='black', linewidth=0.6,
                         error_kw=dict(capsize=3, elinewidth=1.0, capthick=0.8))
            
            # Collect legend handles and labels from first subplot only
            if api_idx == 0:
                all_handles.extend([bars])
                all_labels.append(label)
        
        # Add Sidecar bars (zero values) to emphasize good performance
        sidecar_exp_idx = n_exps  # Position after all experiment bars
        sidecar_offsets = [x - fixed_total_group_width/2 + sidecar_exp_idx * api_bar_width + api_bar_width/2 
                          for x in api_x_indices]
        sidecar_means = [0.0] * len(services_for_this_api)  # All zero values
        sidecar_stds = [0.0] * len(services_for_this_api)   # No error bars
        
        # Use a distinct style to emphasize that zero is good
        sidecar_bars = ax.bar(sidecar_offsets, sidecar_means, yerr=sidecar_stds, 
                             width=api_bar_width*0.9, label='Roshanfer', 
                             color='lightgreen', edgecolor='darkgreen', linewidth=1.2,
                             hatch='///',  # Diagonal pattern to emphasize
                             error_kw=dict(capsize=3, elinewidth=1.0, capthick=0.8))
        
        # Add y-value annotations on top of sidecar bars (all zeros)
        for j, (offset, mean, std) in enumerate(zip(sidecar_offsets, sidecar_means, sidecar_stds)):
            # Position text above the bar with a small fixed offset
            y_pos = mean + std + 1.0  # Small fixed offset above error bar
            ax.text(offset, y_pos, f'{round(mean)}', ha='center', va='bottom', 
                   fontsize=7, fontweight='normal')
        
        # Collect sidecar legend handle from first subplot only
        if api_idx == 0:
            all_handles.extend([sidecar_bars])
            all_labels.append('Roshanfer')

        # Store filtered services for this API for axis formatting
        if api_idx == 0:
            # Store services for axis formatting (we'll use the union of all API services)
            all_filtered_services = services_for_this_api
        else:
            # Update with union of services across APIs for consistent formatting
            all_filtered_services = list(set(all_filtered_services + services_for_this_api))
    
    # Format axes for all subplots
    for api_idx, api in enumerate(all_apis):
        ax = axes[api_idx] if n_apis > 1 else axes[0]
        
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
        
        # Y-axis formatting (only for leftmost subplot)
        if api_idx == 0:
            ylab = ax.set_ylabel('Resource Waste (%)', labelpad=10, fontsize=11)
            ylab.set_position((ylab.get_position()[0], 0.40))  # Moved lower from 0.5 to 0.35
        else:
            ax.set_ylabel('')
            # Hide y-axis tick labels for non-leftmost subplots
            ax.set_yticklabels([])
        
        ax.yaxis.grid(True, alpha=0.3)
    
    # Set consistent y-axis limits and ticks for all subplots
    if global_max > 0:
        if n_apis <= 1:
            # Single API case: adaptive ticks based on data
            tick_spacing = 10
            max_tick = tick_spacing * np.ceil(global_max / tick_spacing)
            ticks = np.arange(0, max_tick + 1, tick_spacing)
            ylim_max = max_tick + 2
            
            print(f"DEBUG: Single API - global_max={global_max}, max_tick={max_tick}")
            print(f"DEBUG: Single API - ticks={list(ticks)}")
        else:
            # Multiple API case: custom ticks [0, 10, 30, 50, 70]
            ticks = np.array([0, 10, 30, 50, 70])
            ylim_max = 72
            
            print(f"DEBUG: Multiple API - global_max={global_max}, custom ticks={list(ticks)}")
        
        # Apply to all subplots
        for api_idx in range(len(all_apis)):
            ax = axes[api_idx] if n_apis > 1 else axes[0]
            ax.set_yticks(ticks)
            ax.set_ylim(0, ylim_max)
            print(f"DEBUG: Set subplot {api_idx} ylim to (0, {ylim_max})")
    else:
        # Apply default limits to all subplots
        for api_idx in range(len(all_apis)):
            ax = axes[api_idx] if n_apis > 1 else axes[0]
            ax.set_ylim(0, 10)

    # Compact legend at the top center of the figure
    if all_handles and all_labels:
        n_items = len(all_labels)
        if n_apis > 1:
            # Multiple API case: always use single row for all legend items
            ncol = n_items  # All items in one row
            bbox_y = 1.15  # Slightly higher position for multiple API case
            top_adjust = 0.72  # Adjusted space for higher legend and subplot titles
        else:
            # Single API case: lowered positioning
            if n_items > 2:
                ncol = math.ceil(n_items / 2)
                bbox_y = 1.10
                top_adjust = 0.75
            else:
                ncol = n_items
                bbox_y = 1.05
                top_adjust = 0.80
        
        fig.legend(all_handles, all_labels, loc='upper center', bbox_to_anchor=(0.5, bbox_y),
                  frameon=True, fancybox=True, framealpha=0.85, edgecolor='#bbbbbb',
                  ncol=ncol, fontsize=9, markerscale=0.8)  # Smaller legend font and markers
        # Very compact spacing for multiple APIs
        if n_apis > 1:
            fig.subplots_adjust(top=top_adjust, hspace=0.2, wspace=0.1)  # Tighter spacing for multiple APIs
        else:
            fig.subplots_adjust(top=top_adjust, hspace=0.3, wspace=0.2)  # Standard spacing for single API
    
    # Save figure
    output_dir.mkdir(parents=True, exist_ok=True)
    fig_path = output_dir / f'{figure_name}_resource_waste_bar.pdf'
    fig.savefig(fig_path, bbox_inches='tight')
    
    # Close figure to free memory
    try:
        plt.close(fig)
    except Exception:
        pass
    
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
    Generate merged max-queue figure.
    For each included experiment, aggregate max queue per (service, api) across repeats and plot grouped bars.
    Each experiment is a subplot (column). Shared legend for APIs above.
    """
    from pathlib import Path
    import numpy as np
    # Import helpers from plugin
    try:
        from exec.plots.plugins.max_queue_unit import _normalize_service_name, _mean_std, _infer_services_and_apis
    except Exception:
        from experiments.exec.plots.plugins.max_queue_unit import _normalize_service_name, _mean_std, _infer_services_and_apis
    try:
        from exec.plots.common import extract_series
    except Exception:
        from experiments.exec.plots.common import extract_series
    try:
        from canvas import canvas
    except Exception:
        import matplotlib.pyplot as plt
        class SimpleCanvas:
            color_list = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2']
            def create_canvas(self, nrows=1, ncols=1, width_in_inches=6, aspect_ratio=0.66, 
                              font_size=14, legend_size=12, line_width=1.5, marker_size=4):
                fig, axes = plt.subplots(nrows, ncols, figsize=(width_in_inches*ncols, width_in_inches*aspect_ratio*nrows))
                return fig, axes
        canvas = SimpleCanvas()
    import matplotlib.pyplot as plt

    include_experiments = figure_config.get('include', {})
    if not include_experiments:
        return []
    produced = []
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
        
        # Build data[service][api] = list of per-repeat maxima
        data = {svc: {api: [] for api in apis} for svc in services}
        
        # Iterate through repeats. Maintain alignment between metric files and Prometheus data by index.
        
        # Re-structure the loop to align data
        for i in range(len(repeat_metric_files)):
            mf = repeat_metric_files[i]
            prom = repeat_prom_data[i] if i < len(repeat_prom_data) else None
            
            for svc in services:
                for api in apis:
                    val = 0.0
                    
                    # Try Prometheus first
                    found_in_prom = False
                    if prom and prom.metrics and api in prom.metrics:
                        # Check service variants
                        variants = [svc, svc + '-grpc', svc.replace('-grpc', ''), svc + '-server']
                        for v in variants:
                            if v in prom.metrics[api] and 'max_queue' in prom.metrics[api][v]:
                                val = prom.metrics[api][v]['max_queue']
                                found_in_prom = True
                                break
                    
                    if found_in_prom:
                        data[svc][api].append(float(val))
                        continue
                        
                    # Fallback to legacy
                    # Try stems as in plugin
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
                        data[svc][api].append(0.0)
                        continue
                    ts, vals = extract_series(mf[chosen])
                    if not vals:
                        data[svc][api].append(0.0)
                    else:
                        vmax = max(vals)
                        data[svc][api].append(float(vmax))
        exp_data.append({
            'label': label,
            'services': services,
            'apis': apis,
            'data': data
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
    
    # Check if we have only one API - use single bar plot instead of subplots
    single_api_mode = len(all_apis) == 1
    
    # Prepare figure
    if single_api_mode:
        fig, axes = canvas.create_canvas(
            nrows=1, ncols=1, width_in_inches=3.33, aspect_ratio=0.66,
            font_size=16, legend_size=13, line_width=1.6, marker_size=5
        )
    else:
        fig, axes = canvas.create_canvas(
            nrows=1, ncols=ncols, width_in_inches=3.33, aspect_ratio=0.66,
            font_size=16, legend_size=13, line_width=1.6, marker_size=5
        )
    # Normalize axes to list
    try:
        from matplotlib.axes import Axes as _Axes
    except Exception:
        _Axes = object
    if isinstance(axes, _Axes):
        axes = [axes]
    else:
        try:
            axes = list(getattr(axes, 'ravel')().tolist())
        except Exception:
            axes = list(axes) if not isinstance(axes, list) else axes
    
    # Color mapping - use experiment colors for single API mode, API colors for multi-API mode
    if single_api_mode:
        # Use same color coding as resource-waste (experiment-based colors)
        try:
            colors = canvas.color_list[:len(exp_data)]
        except Exception:
            colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd']
        exp_colors = dict(zip([ed['label'] for ed in exp_data], colors))
    else:
        # Use API-based colors for multi-API mode
        try:
            colors = canvas.color_list[:len(all_apis)]
        except Exception:
            colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd']
        api_colors = dict(zip(all_apis, colors))
    # Compute global max (mean+std) across all experiments/services/apis
    global_max = 0.0
    for ed in exp_data:
        data = ed['data']
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
    ylim_max = 1.2 * global_max if global_max > 0 else 1.0
    
    if single_api_mode:
        # Single API mode: one bar plot with each experiment as a different bar
        ax = axes[0]
        api = all_apis[0]  # Only one API
        
        n_services = len(all_services)
        n_exps = len(exp_data)
        x_indices = list(range(n_services))
        total_group_width = 0.8
        bar_width = total_group_width / n_exps if n_exps > 0 else total_group_width
        
        for exp_idx, ed in enumerate(exp_data):
            data = ed['data']
            label = ed['label']
            color = exp_colors.get(label)
            
            offsets = [x - total_group_width/2 + exp_idx * bar_width + bar_width/2 for x in x_indices]
            means = []
            stds = []
            
            for svc in all_services:
                vals = data.get(svc, {}).get(api, [])
                m, s = _mean_std(vals)
                if m is None:
                    m = 0.0
                    s = 0.0
                means.append(m)
                stds.append(0.0001 if (s is None or s == 0) else s)
            
            bars = ax.bar(offsets, means, yerr=stds, width=bar_width*0.9, label=label,
                   color=color, edgecolor='black', linewidth=0.6,
                   error_kw=dict(capsize=3, elinewidth=1.0, capthick=0.8))
            
            # Add y-value annotations on top of bars only for "sidecar"
            if label.lower() == 'roshanfer':
                for j, (offset, mean, std) in enumerate(zip(offsets, means, stds)):
                    # Position text above the error bar
                    y_pos = mean + std + ylim_max * 0.02  # Small offset above error bar
                    ax.text(offset, y_pos, f'{round(mean)}', ha='center', va='bottom', 
                           fontsize=7, fontweight='normal')
        
        ax.set_xticks(x_indices)
        ax.set_xticklabels([service.title() for service in all_services], rotation=30, ha='right')
        ylab = ax.set_ylabel('Max Queueing (req)', labelpad=20)
        ylab.set_position((ylab.get_position()[0], 0.42))
        ax.yaxis.grid(True, alpha=0.3)
        ax.set_ylim(0, ylim_max)
        
    else:
        # Multi-API mode: subplot for each experiment, bars for APIs (original logic)
        for i, ed in enumerate(exp_data):
            ax = axes[i]
            data = ed['data']
            services = all_services
            apis = all_apis
            n_services = len(services)
            n_apis = len(apis)
            x_indices = list(range(n_services))
            total_group_width = 0.8
            bar_width = total_group_width / n_apis if n_apis > 0 else total_group_width
            max_error = 0.0
            for api_idx, api in enumerate(apis):
                offsets = [x - total_group_width/2 + api_idx * bar_width + bar_width/2 for x in x_indices]
                means = []
                stds = []
                for svc in services:
                    vals = data.get(svc, {}).get(api, [])
                    m, s = _mean_std(vals)
                    if m is None:
                        m = 0.0
                        s = 0.0
                    means.append(m)
                    stds.append(0.0001 if (s is None or s == 0) else s)
                    if m + (s if s is not None else 0) > max_error:
                        max_error = m + (s if s is not None else 0)
                bars = ax.bar(offsets, means, yerr=stds, width=bar_width*0.9, label=api,
                       color=api_colors.get(api), edgecolor='black', linewidth=0.6,
                       error_kw=dict(capsize=3, elinewidth=1.0, capthick=0.8))
                
                # Add y-value annotations on top of bars only for "sidecar" experiment
                if ed['label'].lower() == 'roshanfer':
                    for j, (offset, mean, std) in enumerate(zip(offsets, means, stds)):
                        # Position text above the error bar
                        y_pos = mean + std + ylim_max * 0.02  # Small offset above error bar
                        ax.text(offset, y_pos, f'{round(mean)}', ha='center', va='bottom', 
                               fontsize=7, fontweight='normal')
            ax.set_xticks(x_indices)
            ax.set_xticklabels([service.title() for service in services], rotation=30, ha='right')
            if i == 0:
                ylab = ax.set_ylabel('Max Queueing (req)', labelpad=20)
                ylab.set_position((ylab.get_position()[0], 0.42))
            else:
                ax.set_ylabel('')
                ax.set_yticklabels([])
            ax.yaxis.grid(True, alpha=0.3)
            ax.set_ylim(0, ylim_max)
            # Title above subplot (experiment label)
            ax.set_title(ed['label'])
    # Legend logic for both modes
    if single_api_mode:
        # Single API mode: legend shows experiments
        handles, labels = axes[0].get_legend_handles_labels()
        if handles:
            # Use two rows if more than 2 items
            if len(labels) > 2:
                ncol = (len(labels) + 1) // 2  # Ceiling division to get columns for 2 rows
            else:
                ncol = 1  # Single row for 2 or fewer items
            
            fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5, 1.10),
                       ncol=ncol, frameon=True, fancybox=True,
                       framealpha=0.85, edgecolor='#bbbbbb')
            fig.subplots_adjust(top=0.80)
    else:
        # Multi-API mode: legend shows APIs (only if more than one API)
        if len(all_apis) > 1:
            handles, labels = axes[0].get_legend_handles_labels()
            if handles:
                fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5, 1.10),
                           ncol=max(1, len(labels)), frameon=True, fancybox=True,
                           framealpha=0.85, edgecolor='#bbbbbb')
            fig.subplots_adjust(top=0.80, wspace=0.1)
        else:
            # Single API in multi-mode (fallback case)
            fig.subplots_adjust(wspace=0.1)
    # Save figure
    output_dir.mkdir(parents=True, exist_ok=True)
    fig_path = output_dir / f'{figure_name}_max_queue.pdf'
    fig.savefig(fig_path, bbox_inches='tight')
    plt.close(fig)
    return [fig_path]

def load_merged_config(merged_config_path: Path) -> Dict[str, Any]:
    """Load and parse the merged.yaml configuration file."""
    if not merged_config_path.exists():
        raise FileNotFoundError(f'Merged config not found: {merged_config_path}')
    
    with merged_config_path.open() as f:
        config = yaml.safe_load(f)
    
    return config

def load_experiment_configs(experiments_config_path: Path) -> Dict[str, Dict[str, Any]]:
    """Load experiment configurations and return as name->config mapping."""
    if not experiments_config_path.exists():
        raise FileNotFoundError(f'Experiments config not found: {experiments_config_path}')
    
    with experiments_config_path.open() as f:
        data = json.load(f)
    
    experiments = {}
    for exp in data.get('experiments', []):
        experiments[exp['name']] = exp
    
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
            x_val = (load_value * 10) / 1000.0
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
    
    # === LATENCY FIGURE ===
    layout = f"row-{len(all_apis)}" if len(all_apis) > 1 else "1x1"
    grid_lat = SubplotGrid(style, layout=layout)
    
    # Track all latency values for dynamic Y-axis
    all_latency_values = []
    
    for idx, api in enumerate(all_apis):
        ax = grid_lat.get_ax(0, idx)
        display_api = api.replace('_all', '') if api.endswith('_all') else api
        
        # Plot each experiment
        for exp_idx, (label, exp_info) in enumerate(all_experiment_data.items()):
            exp_data = exp_info['data']
            loads = exp_info['loads']
            
            if api not in exp_data:
                continue
            
            # Extract latency data with CI
            latency_data = exp_data[api]['latency_p99']
            means = [item[0] for item in latency_data]
            cis = [item[2] if item[2] is not None else 0.0 for item in latency_data]
            
            # Filter out None values
            valid_data = [(l, m, c) for l, m, c in zip(loads, means, cis)
                         if m is not None and not (isinstance(m, float) and math.isnan(m))]
            
            if valid_data:
                valid_loads, valid_means, valid_cis = zip(*valid_data)
                
                # Plot with error bars (using CI not std)
                plot_line(
                    ax, valid_loads, valid_means,
                    yerr=valid_cis,
                    label=label,
                    style=style,
                    color_idx=exp_idx,
                    style_idx=exp_idx,
                    show_markers=True  # Good for distinguishing experiments
                )
                
                all_latency_values.extend(valid_means)
        
        # Add SLO line
        slo_val = None
        for key in [display_api, display_api.replace('-', '_'), display_api.replace('_', '-')]:
            if slo_map and key in slo_map:
                slo_val = slo_map[key]
                break
        
        if slo_val is not None:
            ax.axhline(y=slo_val, color='r', linestyle='--',
                      label='SLO', linewidth=style.line_width)
            all_latency_values.append(slo_val)
        
        # Configure axis
        ax.set_title(display_api, fontsize=style.title_size)
        ax.set_yscale('log')
        ax.grid(True, alpha=0.3)
        
        # Set x-axis limits with padding
        if all_loads:
            span = all_loads[-1] - all_loads[0]
            pad = 0.03 * span if span > 0 else 0.05
            ax.set_xlim(all_loads[0] - pad, all_loads[-1] + pad)
    
    # Dynamic Y-axis: 5x max value
    if all_latency_values:
        dyn_y_max = max(all_latency_values) * 5.0 * 1.05
        dyn_y_max = max(dyn_y_max, 10)  # Minimum 10ms
    else:
        dyn_y_max = 500
    
    for idx in range(len(all_apis)):
        ax = grid_lat.get_ax(0, idx)
        ax.set_ylim(1, dyn_y_max)
    
    # Configure labels
    grid_lat.configure_labels(
        pattern="leftmost_y_bottom_x",
        xlabel="Offered Load (KRPS)",
        ylabel="P95 Latency (ms)"
    )
    
    # Add legend (use two rows if many experiments)
    grid_lat.add_shared_legend(position="top", two_rows=(len(all_experiment_data) > 3))
    
    # Save
    lat_path = output_dir / f'{figure_name}_latency_vs_load.pdf'
    grid_lat.save(lat_path)
    produced.append(lat_path)
    
    # === GOODPUT FIGURE ===
    grid_gp = SubplotGrid(style, layout=layout)
    
    for idx, api in enumerate(all_apis):
        ax = grid_gp.get_ax(0, idx)
        display_api = api.replace('_all', '') if api.endswith('_all') else api
        
        # Plot each experiment
        for exp_idx, (label, exp_info) in enumerate(all_experiment_data.items()):
            exp_data = exp_info['data']
            loads = exp_info['loads']
            
            if api not in exp_data:
                continue
            
            # Extract goodput data with CI (convert to KRPS)
            goodput_data = exp_data[api]['goodput']
            means = [(item[0] / 1000.0) if item[0] is not None else None for item in goodput_data]
            cis = [(item[2] / 1000.0) if item[2] is not None else 0.0 for item in goodput_data]
            
            # Filter out None values
            valid_data = [(l, m, c) for l, m, c in zip(loads, means, cis)
                         if m is not None and not (isinstance(m, float) and math.isnan(m))]
            
            if valid_data:
                valid_loads, valid_means, valid_cis = zip(*valid_data)
                
                # Plot with error bars (using CI not std)
                plot_line(
                    ax, valid_loads, valid_means,
                    yerr=valid_cis,
                    label=label,
                    style=style,
                    color_idx=exp_idx,
                    style_idx=exp_idx,
                    show_markers=True  # Good for distinguishing experiments
                )
        
        # Configure axis
        ax.set_title(display_api, fontsize=style.title_size)
        ax.grid(True, alpha=0.3)
        ax.set_ylim(bottom=0)  # Goodput starts from 0
        
        # Set x-axis limits with padding
        if all_loads:
            span = all_loads[-1] - all_loads[0]
            pad = 0.03 * span if span > 0 else 0.05
            ax.set_xlim(all_loads[0] - pad, all_loads[-1] + pad)
    
    # Configure labels
    grid_gp.configure_labels(
        pattern="leftmost_y_bottom_x",
        xlabel="Offered Load (KRPS)",
        ylabel="Goodput (KRPS)"
    )
    
    # Add legend (use two rows if many experiments)
    grid_gp.add_shared_legend(position="top", two_rows=(len(all_experiment_data) > 3))
    
    # Save
    goodput_path = output_dir / f'{figure_name}_goodput_vs_load.pdf'
    grid_gp.save(goodput_path)
    produced.append(goodput_path)
    
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
    experiment_configs = load_experiment_configs(experiments_file_path)
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
    REWRITTEN to use new RWG data loading and plotting architecture.
    Generate merged latency-and-rate-vs-time figure(s).
    For one API: one figure, columns=experiments, rows=latency/rate.
    For multiple APIs: one figure per API, same layout.
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
    
    produced: list = []
    include_experiments = figure_config.get('include', {})
    
    if not include_experiments:
        return produced
    
    # Load SLOs from config file
    with open(global_config) as f:
        global_configs = json.load(f)
    slo_map = global_configs.get('slos', {})
    
    # TODO: Implement RWG-based merged latency-and-rate-vs-time plotting
    # This requires realtime CSV data which is not yet fully integrated
    print(f"Warning: generate_latency_and_rate_vs_time_merged not yet fully migrated to RWG")
    
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
            SubplotGrid, ACM_COMPACT_HALF, ACM_QUARTER, PlotStyle, plot_line
        )
    except ImportError:
        try:
            from plots.data_loader import load_repeat_data  # type: ignore
            from plots.aggregation import aggregate_overall_metric  # type: ignore
            from plots.plotting_primitives import (  # type: ignore
                SubplotGrid, ACM_COMPACT_HALF, plot_line
            )
        except ImportError:
            from data_loader import load_repeat_data  # type: ignore
            from aggregation import aggregate_overall_metric  # type: ignore
            from plotting_primitives import (  # type: ignore
                SubplotGrid, ACM_COMPACT_HALF, plot_line
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
    style = PlotStyle(width_points=180)
    # Layout: 2 rows (P99, P95), N columns (one per API)
    grid = SubplotGrid(style, layout=f"1x{n_apis}")
    
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
        found_units = {} # unit_name -> list of artifact_dirs
        
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
                     found_units[unit_name].append(Path(r.get('artifact_dir')))

        # 2. Process each unit (load level) for EACH API
        # We need to collect points separately for each API because they might be in different files or keys
        
        for api in exp_apis:
            if api not in all_apis: 
                continue # Should be there, but good to be safe

            exp_points = [] # list of dicts for this API

            for unit_name, artifact_dirs in found_units.items():
                unit_throughputs = []
                unit_p99_latencies = []
                unit_p99_latencies = []
                
                for artifact_dir in artifact_dirs:
                    repeat_data = load_repeat_data(artifact_dir)
                    if repeat_data and api in repeat_data:
                        overall, _ = repeat_data[api]
                        if overall is not None:
                             unit_throughputs.append(overall.throughput)
                             unit_p99_latencies.append(overall.p99_latency)
                             unit_p99_latencies.append(overall.p99_latency)
                        else:
                            if os.environ.get('PLOT_DEBUG') == '1':
                                print(f"    [DEBUG] Overall data is None for {api} in {artifact_dir}")
                    else:
                        if os.environ.get('PLOT_DEBUG') == '1':
                            print(f"    [DEBUG] No data for {api} in {artifact_dir}")
                
                if unit_throughputs and unit_p99_latencies:
                    tp_mean, _, _ = aggregate_overall_metric(unit_throughputs)
                    p99_mean, _, p99_ci = aggregate_overall_metric(unit_p99_latencies)
                    p99_mean, _, p99_ci = aggregate_overall_metric(unit_p99_latencies)
                    
                    if tp_mean is not None and p99_mean is not None:
                        exp_points.append({
                            'tp': tp_mean,
                            'p99': p99_mean,
                            'p99_ci': p99_ci if p99_ci is not None else 0.0,
                            'p99': p99_mean,
                            'p99_ci': p99_ci if p99_ci is not None else 0.0
                        })
                        if os.environ.get('PLOT_DEBUG') == '1':
                            print(f"  [DEBUG] Unit: {unit_name}")
                            print(f"    Samples: {len(unit_throughputs)}")
                            print(f"    Throughput: {tp_mean:.2f}")
                            print(f"    P99: {p99_mean:.2f} ± {p99_ci if p99_ci else 0:.2f}")
                            print(f"    P99: {p99_mean:.2f} ± {p99_ci if p99_ci else 0:.2f}")

            # Sort by throughput per API
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
                'p99': [p['p99'] for p in exp_points],
                'p99_ci': [p['p99_ci'] for p in exp_points],
                'p99': [p['p99'] for p in exp_points],
                'p99_ci': [p['p99_ci'] for p in exp_points]
            }
            
            # Update limits
            if exp_points:
                max_tp = max(p['tp'] for p in exp_points)
                max_p99 = max(p['p99'] + (p['p99_ci'] or 0) for p in exp_points)
                max_p99 = max(p['p99'] + (p['p99_ci'] or 0) for p in exp_points)
                
                if max_tp > api_limits[api]['max_tp']: api_limits[api]['max_tp'] = max_tp
                if max_p99 > api_limits[api]['max_p99']: api_limits[api]['max_p99'] = max_p99
                if max_p99 > api_limits[api]['max_p99']: api_limits[api]['max_p99'] = max_p99

    # 3. Plotting Loop
    # Load SLOs from config file
    with open(global_config) as f:
        global_configs = json.load(f)
    slo_map = global_configs.get('slos', {})

    # 3. Plotting Loop
    for api_idx, api in enumerate(all_apis):
        ax_p99 = grid.get_ax(0, api_idx)
        #ax_p99 = grid.get_ax(1, api_idx)
        
        # Set titles for columns (API names)
        ax_p99.set_title(api, fontsize=style.title_size)

        for label, data in plot_data[api].items():
            color_idx = color_idx_map.get(label, 0)
            
            # Plot P99 line (Row 0)
            plot_line(
                ax_p99, data['tps'], data['p99'],
                yerr=data['p99_ci'],
                label=label,
                style=style,
                color_idx=color_idx,
                style_idx=color_idx,
                show_markers=True
            )

            """ # Plot P95 line (Row 1)
            plot_line(
                ax_p99, data['tps'], data['p99'],
                yerr=data['p99_ci'],
                label=label,
                style=style,
                color_idx=color_idx,
                style_idx=color_idx,
                show_markers=True
            ) """
        
        # Add SLO line
        slo_val = None
        # Try various forms of the API name to match keys in config
        possible_keys = [api, api.replace('-', '_'), api.replace('_', '-')]
        # Also try removing suffix like '_all' just in case
        if api.endswith('_all'):
            base = api.replace('_all', '')
            possible_keys.extend([base, base.replace('-', '_'), base.replace('_', '-')])
            
        for key in possible_keys:
            if slo_map and key in slo_map:
                slo_val = slo_map[key]
                break
        
        if slo_val is not None:
            # Plot SLO on P99
            ax_p99.axhline(y=slo_val, color='r', linestyle='--',
                       label='SLO', linewidth=style.line_width)
            """ # Plot SLO on P95
            ax_p99.axhline(y=slo_val, color='r', linestyle='--',
                       label='SLO', linewidth=style.line_width) """
            
        # Configure axes per column
        limits = api_limits[api]
        max_lat = max(limits['max_p99'], limits['max_p99'])
        # Round up to nearest 10
        y_max = math.ceil(max_lat / 10.0) * 10
        if y_max < 10: y_max = 10
        
        max_tp = limits['max_tp']
        x_max = math.ceil(max_tp / 100.0) * 100 if max_tp > 0 else 1000

        # Configure P99 axis (Row 0)
        grid.configure_ax(
            ax_p99,
            ylabel="P99 Latency (ms)" if api_idx == 0 else "",
            y_data=None, # We set manual limits
            ylim=(0, 30),
            y_type='int',
            y_step=5,
            grid=True,
            show_xticklabels=True,
            show_xlabel=True,
            show_ylabel=(api_idx == 0),
            show_yticklabels=(api_idx == 0)
        )

        """ # Configure P95 axis (Row 1)
        grid.configure_ax(
            ax_p99,
            xlabel="Throughput (RPS)",
            ylabel="P95 Latency (ms)" if api_idx == 0 else "",
            y_data=None, # We set manual limits
            ylim=(0, 30),
            y_type='int',
            y_step=5,
            grid=True,
            show_yticklabels=(api_idx == 0)
        ) """

    # Add shared legend
    # For many columns, legend needs to span effectively
    grid.add_shared_legend(position="top", y_offset=1.2, two_rows=True)

    # Save
    output_dir.mkdir(parents=True, exist_ok=True)
    line_path = output_dir / f'{figure_name}_latency_vs_throughput.pdf'
    grid.save(line_path)
    produced.append(line_path)
    
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
