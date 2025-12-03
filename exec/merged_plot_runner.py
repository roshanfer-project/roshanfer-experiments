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
        repeat_metric_files = []
        found_records = []
        
        # Find all run_summary.jsonl entries for this experiment (all repeats)
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
                    found_records.append(r)
        
        # For each record, load metrics
        for record in found_records:
            artifact_dir = Path(record.get('artifact_dir', '.'))
            metrics_dir = artifact_dir / 'metrics'
            metric_files = {}
            for fp in metrics_dir.glob('*.json'):
                if fp.name.startswith('_index'):
                    continue
                try:
                    metric_files[fp.stem] = json.loads(fp.read_text())
                except Exception:
                    continue
            repeat_metric_files.append(metric_files)
        
        # Calculate waste data for each repeat using the resource waste plugin logic
        repeat_waste_data = []
        for repeat_files in repeat_metric_files:
            waste_data = _calculate_waste_per_repeat(repeat_files, apis, bench)
            repeat_waste_data.append(waste_data)
        
        if not repeat_waste_data:
            continue
        
        # Get all services from this experiment
        all_services_this_exp = set()
        for waste_data in repeat_waste_data:
            all_services_this_exp.update(waste_data.keys())
        all_services.update(all_services_this_exp)
        
        # Aggregate across repeats (keep APIs separate for multi-API case)
        aggregated_data = {}
        for service in all_services_this_exp:
            aggregated_data[service] = {}
            for api in apis:
                # Collect waste for this service and API across all repeats
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
        # Load all repeats for this experiment (from all exp-XXX dirs)
        repeat_metric_files = []
        found_records = []
        # Find all run_summary.jsonl entries for this experiment (all repeats)
        for exp_index in range(1, 20):
            run_root = experiments_root / f'exp-{exp_index:03d}' if not experiments_root.name.startswith('exp-') else experiments_root
            records = _load_summary(run_root)
            for r in records:
                if r.get('experiment_name') == exp_name:
                    found_records.append(r)
        # For each record, load metrics
        for record in found_records:
            artifact_dir = Path(record.get('artifact_dir', '.'))
            metrics_dir = artifact_dir / 'metrics'
            metric_files = {}
            for fp in metrics_dir.glob('*.json'):
                if fp.name.startswith('_index'):
                    continue
                try:
                    metric_files[fp.stem] = json.loads(fp.read_text())
                except Exception:
                    continue
            repeat_metric_files.append(metric_files)
        # Determine fallback services/apis from config if present
        fallback_services = exp_def.get('services', [])
        fallback_apis = exp_def.get('apis', [])
        # Infer services/apis from metrics (plugin logic)
        services, apis = _infer_services_and_apis(repeat_metric_files, fallback_services, fallback_apis)
        # Normalize service names as in max_queue_unit
        services = [_normalize_service_name(svc) for svc in services]
        all_services.update(services)
        all_apis.update(apis)
        # Build data[service][api] = list of per-repeat maxima
        data = {svc: {api: [] for api in apis} for svc in services}
        for mf in repeat_metric_files:
            for svc in services:
                for api in apis:
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
    
    # Add Ingress service to all experiments
    for ed in exp_data:
        label = ed['label']
        data = ed['data']
        apis = ed['apis']
        
        # Add Ingress to services list if not already present
        if 'ingress' not in ed['services']:
            ed['services'].append('ingress')
        
        # Initialize Ingress data structure
        data['ingress'] = {api: [] for api in apis}
        
        # Calculate Ingress values based on experiment type
        if label.lower() == 'roshanfer':
            print(f"DEBUG: Calculating Ingress for Sidecar experiment '{label}'")
            # For Roshanfer: Ingress = 3 + max(frontend, nginx)
            for api in apis:
                frontend_vals = data.get('frontend', {}).get(api, [])
                nginx_vals = data.get('nginx', {}).get(api, [])
                
                # Calculate per-repeat Ingress values
                max_repeats = max(len(frontend_vals), len(nginx_vals))
                for i in range(max_repeats):
                    frontend_val = frontend_vals[i] if i < len(frontend_vals) else 0.0
                    nginx_val = nginx_vals[i] if i < len(nginx_vals) else 0.0
                    ingress_val = 3.0 + max(frontend_val, nginx_val)
                    data['ingress'][api].append(ingress_val)
        else:
            # For other systems: Ingress = 0
            for api in apis:
                # Get the number of repeats from any existing service
                num_repeats = 0
                for svc_data in data.values():
                    if api in svc_data and len(svc_data[api]) > 0:
                        num_repeats = len(svc_data[api])
                        break
                # Set all repeats to 0 for non-Roshanfer systems
                data['ingress'][api] = [0.0] * num_repeats

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
            if obj.get('status') != 'success':
                continue
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
            run_unit_name = record.get('run_unit_name', '')
            
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
        exp_data = {api: {'latency_p95': [], 'goodput': []} for api in apis}
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
                    exp_data[api]['latency_p95'].append((None, None, None))
                continue
            
            # Aggregate across repeats
            aggregated = aggregate_by_api(all_repeats)
            
            for api in apis:
                if api in aggregated:
                    # Latency P95
                    lat_mean, lat_std, lat_ci = aggregated[api].get('p95_latency', (None, None, None))
                    exp_data[api]['latency_p95'].append((lat_mean, lat_std, lat_ci))
                    
                    # Goodput
                    gp_mean, gp_std, gp_ci = aggregated[api].get('goodput', (None, None, None))
                    exp_data[api]['goodput'].append((gp_mean, gp_std, gp_ci))
                else:
                    exp_data[api]['latency_p95'].append((None, None, None))
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
            latency_data = exp_data[api]['latency_p95']
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
            SubplotGrid, ACM_COMPACT_HALF, plot_line
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
    
    # Use ACM compact style
    style = ACM_COMPACT_HALF
    grid = SubplotGrid(style, layout="1x1")
    ax = grid.get_ax(0, 0)
    
    color_idx = 0
    
    # Track global min/max for axis configuration
    all_throughputs = []
    all_latencies = []

    for exp_name, exp_cfg in include_experiments.items():
        label = exp_cfg.get('label', exp_name)
        
        if exp_name not in experiment_configs:
            print(f"Warning: Experiment '{exp_name}' not found in configs")
            continue
            
        exp_def = experiment_configs[exp_name]
        apis = exp_def.get('apis', [])
        if not apis:
            continue
        api = apis[0] # Support single API for now

        # Collect data for this experiment
        load_levels = []
        throughput_means = []
        latency_means = []
        latency_cis = []
        
        # Find all repeats for this experiment
        # We need to group by load level (which is encoded in unit name usually)
        # But here we can just iterate over all units found for this experiment
        
        # 1. Find all units for this experiment
        # We scan exp-XXX directories
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
                     unit_name = r.get('run_unit_name')
                     if unit_name not in found_units:
                         found_units[unit_name] = []
                     found_units[unit_name].append(Path(r.get('artifact_dir')))

        # 2. Process each unit (load level)
        # We need to sort units by load. Assuming load is in the name or we can infer it.
        # For now, let's try to extract load from unit name if possible, or just use the order.
        # Actually, we can just collect (throughput, latency) pairs and sort by throughput.
        
        exp_points = [] # list of (throughput_mean, latency_mean, latency_ci)

        for unit_name, artifact_dirs in found_units.items():
            unit_throughputs = []
            unit_latencies = []
            
            for artifact_dir in artifact_dirs:
                repeat_data = load_repeat_data(artifact_dir)
                if repeat_data and api in repeat_data:
                    overall, _ = repeat_data[api]
                    if overall is not None:
                         unit_throughputs.append(overall.throughput)
                         unit_latencies.append(overall.p99_latency)
                    else:
                        if os.environ.get('PLOT_DEBUG') == '1':
                            print(f"    [DEBUG] Overall data is None for {api} in {artifact_dir}")
                else:
                    if os.environ.get('PLOT_DEBUG') == '1':
                        print(f"    [DEBUG] No data for {api} in {artifact_dir}")
            
            if unit_throughputs and unit_latencies:
                tp_mean, _, _ = aggregate_overall_metric(unit_throughputs)
                lat_mean, _, lat_ci = aggregate_overall_metric(unit_latencies)
                
                if tp_mean is not None and lat_mean is not None:
                    exp_points.append((tp_mean, lat_mean, lat_ci if lat_ci is not None else 0.0))
                    if os.environ.get('PLOT_DEBUG') == '1':
                        print(f"  [DEBUG] Unit: {unit_name}")
                        print(f"    Samples: {len(unit_throughputs)}")
                        print(f"    Throughput: {tp_mean:.2f}")
                        print(f"    Latency: {lat_mean:.2f} ± {lat_ci if lat_ci else 0:.2f}")

        # Sort by throughput
        exp_points.sort(key=lambda x: x[0])
        
        if os.environ.get('PLOT_DEBUG') == '1':
            print(f"[DEBUG] Experiment: {exp_name} ({label})")
            print(f"  Aggregated Points (TP, Lat, CI):")
            for p in exp_points:
                print(f"    {p[0]:.2f}, {p[1]:.2f}, {p[2]:.2f}")
        
        if not exp_points:
            continue
            
        # Unzip
        tps = [p[0] for p in exp_points]
        lats = [p[1] for p in exp_points]
        cis = [p[2] for p in exp_points]
        
        all_throughputs.extend(tps)
        all_latencies.extend(lats)

        # Plot line for this experiment
        plot_line(
            ax, tps, lats,
            yerr=cis,
            label=label,
            style=style,
            color_idx=color_idx,
            style_idx=color_idx,
            show_markers=True
        )
        color_idx += 1

    # Configure axis
    grid.configure_ax(
        ax,
        xlabel="Throughput (RPS)",
        ylabel="P99 Latency (ms)",
        title=f"Latency vs Throughput",
        x_data=all_throughputs,
        y_data=all_latencies,
        y_step=5,
        ylim=(0, 50),
        y_type="int",
        x_step=1000,
        grid=True
    )

    # Add legend
    grid.add_shared_legend(position="top")

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
