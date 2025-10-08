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
    
    This gets processed data from the experiment-level aggregate plugins and
    combines multiple experiments into a single figure with legend entries.
    """
    # Import functions from existing modules
    try:
        from exec.plots.common import extract_series
    except Exception:
        try:
            from plots.common import extract_series  
        except Exception:
            # Manual implementation if import fails
            def extract_series(metric_json: dict):
                """Extract (timestamps, values) from a Prometheus range vector JSON."""
                res = metric_json.get('result')
                if not isinstance(res, list) or not res:
                    return [], []
                series = res[0]
                values = series.get('values') or []
                ts_list = []
                val_list = []
                for ts, val in values:
                    try:
                        ts_list.append(float(ts))
                        val_list.append(float(val))
                    except Exception:
                        continue
                return ts_list, val_list
    # Import helper functions
    try:
        from exec.plots.plugins.latency_goodput_vs_load_experiment import _windowed_mean, _mean_std
    except Exception:
        try:
            from plots.plugins.latency_goodput_vs_load_experiment import _windowed_mean, _mean_std
        except Exception:
            # Manual implementations if import fails
            def _windowed_mean(ts, vals, ignore_first=5.0, last_window=10.0):
                """Compute mean over filtered window."""
                if not ts or not vals:
                    return None
                if len(ts) != len(vals):
                    return None
                t0 = min(ts)
                rel = [t - t0 for t in ts]
                max_rel = max(rel)
                lower_bound = max(ignore_first, max_rel - last_window)
                filtered = [v for tr, v in zip(rel, vals) if tr >= lower_bound]
                if not filtered:
                    return None
                return sum(filtered) / len(filtered)
            
            def _mean_std(values):
                """Return (mean, std) ignoring NaN/None."""
                if not values:
                    return None, None
                filtered = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
                if not filtered:
                    return None, None
                m = sum(filtered) / len(filtered)
                if len(filtered) < 2:
                    return m, 0.0
                try:
                    return m, statistics.pstdev(filtered)
                except Exception:
                    return m, 0.0
    
    # Import canvas
    try:
        from canvas import canvas
    except Exception:
        try:
            import matplotlib.pyplot as plt
            # Create a simple canvas substitute if canvas is not available
            class SimpleCanvas:
                color_list = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2']
                def create_canvas(self, nrows=1, ncols=1, width_in_inches=6, aspect_ratio=0.66, 
                                line_width=2, font_size=12, legend_size=12, marker_size=5):
                    fig, axes = plt.subplots(nrows, ncols, figsize=(width_in_inches*ncols, width_in_inches*aspect_ratio*nrows))
                    return fig, axes
            canvas = SimpleCanvas()
        except Exception as e:
            raise ImportError("Neither canvas nor matplotlib available") from e
    
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MultipleLocator
    import math
    
    produced: List[Path] = []
    include_experiments = figure_config.get('include', {})
    
    if not include_experiments:
        return produced
    
    # Load SLOs from config file (already available in input)
    with open(global_config) as f:
        global_configs = json.load(f)
    slo_map = None
    try:
        """ with open('exec/config.sample.json') as f:
            config_data = json.load(f)
            slo_map = config_data.get('slos', {}) """
        slo_map = global_configs.get('slos')
    except Exception:
        slo_map = {}
    
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
        
        # Process each load and collect metrics
        exp_data = {api: {'latency_p95': [], 'goodput': []} for api in apis}
        loads = []
        
        for load_value in sorted(load_groups.keys()):
            load_records = load_groups[load_value]
            
            # Convert to KRPS for x-axis
            x_val = (load_value * 10) / 1000.0
            loads.append(x_val)
            
            # Collect metrics from all repeats for this load
            for api in apis:
                # Goodput
                gp_repeat_vals = []
                lat_repeat_vals = []
                
                for record in load_records:
                    artifact_dir = Path(record.get('artifact_dir', '.'))
                    metrics_dir = artifact_dir / 'metrics'
                    metric_files = _load_metric_files(metrics_dir)
                    
                    # Goodput
                    gp_json = metric_files.get(f'goodput_{api}_all') or metric_files.get(f'goodput_{api}')
                    if gp_json:
                        ts, vals = extract_series(gp_json)
                        wm = _windowed_mean(ts, vals)
                        if wm is not None:
                            gp_repeat_vals.append(wm)
                    
                    # Latency P95
                    lat_json = metric_files.get(f'latency_p95_{api}_all') or metric_files.get(f'latency_p95_{api}')
                    if lat_json:
                        ts, vals = extract_series(lat_json)
                        wm = _windowed_mean(ts, vals)
                        if wm is not None:
                            lat_repeat_vals.append(wm)
                
                # Store mean/std for this load
                gp_mean, gp_std = _mean_std(gp_repeat_vals)
                lat_mean, lat_std = _mean_std(lat_repeat_vals)
                
                exp_data[api]['goodput'].append((gp_mean, gp_std))
                exp_data[api]['latency_p95'].append((lat_mean, lat_std))
        
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
    
    # Generate latency figure
    fig_l, axes_l = canvas.create_canvas(
        nrows=1, ncols=len(all_apis), width_in_inches=3.33, aspect_ratio=0.66,
        line_width=2, font_size=16, legend_size=14, marker_size=5
    )
    
    # Normalize axes to list
    try:
        from matplotlib.axes import Axes as _Axes
    except Exception:
        _Axes = object
    
    if isinstance(axes_l, _Axes):
        axes_l = [axes_l]
    else:
        try:
            axes_l = list(getattr(axes_l, 'ravel')().tolist())
        except Exception:
            axes_l = list(axes_l) if not isinstance(axes_l, list) else axes_l
    
    # Color, marker, and line style mapping for different experiments
    try:
        colors = canvas.color_list[:len(all_experiment_data)]
    except Exception:
        colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd']
    color_map = dict(zip(all_experiment_data.keys(), colors))
    marker_list = ['o', '^', 's', 'D', 'v', 'P', 'X', '*']
    style_list = ['-', '--', '-.', ':']
    marker_map = {}
    style_map = {}
    for i, label in enumerate(all_experiment_data.keys()):
        marker_map[label] = marker_list[i % len(marker_list)]
        style_map[label] = style_list[i % len(style_list)]
    
    global_latency_values = []
    
    for ax, api in zip(axes_l, all_apis):
        display_api = api[:-4] if api.endswith('_all') else api
        for label, exp_info in all_experiment_data.items():
            exp_data = exp_info['data']
            loads = exp_info['loads']
            if api not in exp_data:
                continue
            # Extract latency data
            latency_data = exp_data[api]['latency_p95']
            means = [item[0] for item in latency_data]
            stds = [item[1] if item[1] is not None else 0.0 for item in latency_data]
            valid_data = [(l, m, s) for l, m, s in zip(loads, means, stds) 
                         if m is not None and not (isinstance(m, float) and math.isnan(m))]
            if valid_data:
                valid_loads, valid_means, valid_stds = zip(*valid_data)
                color = color_map.get(label)
                marker = marker_map.get(label, 'o')
                style = style_map.get(label, '-')
                ax.errorbar(
                    valid_loads, valid_means, yerr=valid_stds,
                    fmt=style, marker=marker, label=label,
                    linewidth=2.8, color=color, markersize=6.5,
                    markerfacecolor=color, markeredgecolor='black',
                    markeredgewidth=0.6, capsize=4, elinewidth=1.4
                )
                global_latency_values.extend(valid_means)
        # Add SLO line if available
        slo_val = None
        for key in [display_api, display_api.replace('-', '_'), display_api.replace('_', '-')]:
            if slo_map and key in slo_map:
                slo_val = slo_map[key]
                break
        if slo_val is not None:
            ax.axhline(y=slo_val, color='r', linestyle='--', label='SLO')
        else:
            print(f"No SLO found for API '{display_api}'")
        ax.set_xlabel('Offered Load (KRPS)')
        if ax == axes_l[0]:
            ax.set_ylabel('P95 Latency (ms)')
            ax.set_yscale('log')
            # Add log y-axis ticks: major at 1,10,100..., minor at 2-9 in each decade
            ax.yaxis.set_major_locator(LogLocator(base=10.0, subs=(1.0,), numticks=10))
            ax.yaxis.set_minor_locator(LogLocator(base=10.0, subs=np.arange(2.0, 10.0), numticks=10))
            ax.tick_params(axis='y', which='both', length=5)
        ax.set_title(display_api)
        ax.xaxis.set_major_locator(MultipleLocator(2))
        ax.grid(True, which='major', axis='both', alpha=0.3)
        # Set x-axis limits with padding
        if all_loads:
            span = all_loads[-1] - all_loads[0]
            pad = 0.03 * span if span > 0 else 0.05
            ax.set_xlim(all_loads[0] - pad, all_loads[-1] + pad)
    # Set dynamic y-limits
    if global_latency_values:
        dyn_y_max = max(global_latency_values) * 5.0 * 1.05
        if dyn_y_max < 10:
            dyn_y_max = 10
    else:
        dyn_y_max = 500
    for ax in axes_l:
        try:
            ax.set_ylim(1, dyn_y_max)
        except Exception:
            continue
    # Add legend
    try:
        handles, labels = [], []
        for ax in axes_l:
            h, l = ax.get_legend_handles_labels()
            for hh, ll in zip(h, l):
                if ll not in labels:
                    handles.append(hh)
                    labels.append(ll)
        if handles:
            fig_l.legend(
                handles, labels, loc='upper center', bbox_to_anchor=(0.5, 1.18),
                ncol=2, frameon=True, fancybox=True,
                framealpha=0.85, edgecolor='#bbbbbb'
            )
            fig_l.subplots_adjust(top=0.78)
    except Exception:
        pass
    
    # Save latency figure
    lat_path = output_dir / f'{figure_name}_latency_vs_load.pdf'
    fig_l.savefig(lat_path, bbox_inches='tight')
    plt.close(fig_l)
    produced.append(lat_path)
    
    # Generate goodput figure (similar structure)
    fig_g, axes_g = canvas.create_canvas(
        nrows=1, ncols=len(all_apis), width_in_inches=3.33, aspect_ratio=0.66,
        line_width=2, font_size=16, legend_size=14, marker_size=5
    )
    
    # Normalize axes
    if isinstance(axes_g, _Axes):
        axes_g = [axes_g]
    else:
        try:
            axes_g = list(getattr(axes_g, 'ravel')().tolist())
        except Exception:
            axes_g = list(axes_g) if not isinstance(axes_g, list) else axes_g
    
    for ax, api in zip(axes_g, all_apis):
        display_api = api[:-4] if api.endswith('_all') else api
        for label, exp_info in all_experiment_data.items():
            exp_data = exp_info['data']
            loads = exp_info['loads']
            if api not in exp_data:
                continue
            # Extract goodput data
            goodput_data = exp_data[api]['goodput']
            means = [item[0]/1000.0 if item[0] is not None else None for item in goodput_data]  # Convert to KRPS
            stds = [(item[1] or 0)/1000.0 for item in goodput_data]
            valid_data = [(l, m, s) for l, m, s in zip(loads, means, stds) 
                         if m is not None and not (isinstance(m, float) and math.isnan(m))]
            if valid_data:
                valid_loads, valid_means, valid_stds = zip(*valid_data)
                color = color_map.get(label)
                marker = marker_map.get(label, 'o')
                style = style_map.get(label, '-')
                ax.errorbar(
                    valid_loads, valid_means, yerr=valid_stds,
                    fmt=style, marker=marker, color=color, label=label, linewidth=2.4,
                    markersize=6.0, markerfacecolor=color,
                    markeredgecolor='black', markeredgewidth=0.6,
                    capsize=4, elinewidth=1.3
                )
        
        ax.set_xlabel('Offered Load (KRPS)')
        if ax == axes_g[0]:
            ax.set_ylabel('Goodput (KRPS)')
        ax.set_title(display_api)
        ax.xaxis.set_major_locator(MultipleLocator(2))
        ax.grid(True, which='major', axis='both', alpha=0.3)
        
        # Set y-axis to start from 0 for goodput
        ax.set_ylim(bottom=0)
        
        # Set x-axis limits
        if all_loads:
            span = all_loads[-1] - all_loads[0]
            pad = 0.03 * span if span > 0 else 0.05
            ax.set_xlim(all_loads[0] - pad, all_loads[-1] + pad)
    
    # Add legend
    try:
        handles, labels = [], []
        for ax in axes_g:
            h, l = ax.get_legend_handles_labels()
            for hh, ll in zip(h, l):
                if ll not in labels:
                    handles.append(hh)
                    labels.append(ll)
        
        if handles:
            fig_g.legend(
                handles, labels, loc='upper center', bbox_to_anchor=(0.5, 1.12),
                ncol=max(1, len(labels)), frameon=True, fancybox=True,
                framealpha=0.85, edgecolor='#bbbbbb'
            )
            fig_g.subplots_adjust(top=0.80)
    except Exception:
        pass
    
    # Save goodput figure
    goodput_path = output_dir / f'{figure_name}_goodput_vs_load.pdf'
    fig_g.savefig(goodput_path, bbox_inches='tight')
    plt.close(fig_g)
    produced.append(goodput_path)
    
    # Create merged figure with latency and goodput as subplots using canvas
    n_apis = len(all_apis)
    if n_apis == 1:
        fig, axes = canvas.create_canvas(
            nrows=1, ncols=2, width_in_inches=3.33, aspect_ratio=0.75,
            line_width=2, font_size=16, legend_size=14, marker_size=5
        )
        axes = np.array(axes).reshape(1, 2)
        # axes[0, 0]: latency, axes[0, 1]: goodput
        # Latency subplot
        ax_lat = axes[0, 0]
        api = all_apis[0]
        display_api = api[:-4] if api.endswith('_all') else api
        for label, exp_info in all_experiment_data.items():
            exp_data = exp_info['data']
            loads = exp_info['loads']
            if api not in exp_data:
                continue
            latency_data = exp_data[api]['latency_p95']
            means = [item[0] for item in latency_data]
            stds = [item[1] if item[1] is not None else 0.0 for item in latency_data]
            valid_data = [(l, m, s) for l, m, s in zip(loads, means, stds)
                         if m is not None and not (isinstance(m, float) and math.isnan(m))]
            if valid_data:
                valid_loads, valid_means, valid_stds = zip(*valid_data)
                color = color_map.get(label)
                marker = marker_map.get(label, 'o')
                style = style_map.get(label, '-')
                ax_lat.errorbar(
                    valid_loads, valid_means, yerr=valid_stds,
                    fmt=style, marker=marker, label=label,
                    linewidth=2.8, color=color, markersize=6.5,
                    markerfacecolor=color, markeredgecolor='black',
                    markeredgewidth=0.6, capsize=4, elinewidth=1.4
                )
        slo_val = None
        for key in [display_api, display_api.replace('-', '_'), display_api.replace('_', '-')]:
            if slo_map and key in slo_map:
                slo_val = slo_map[key]
                break
        if slo_val is not None:
            ax_lat.axhline(y=slo_val, color='r', linestyle='--', label='SLO')
        else:
            print(f"No SLO found for API '{display_api}'")
        ax_lat.set_ylabel('P95 Latency (ms)')
        ax_lat.set_yscale('log')
        # Add log y-axis ticks: major at 1,10,100..., minor at 2-9 in each decade
        ax_lat.yaxis.set_major_locator(LogLocator(base=10.0, subs=(1.0,), numticks=10))
        ax_lat.yaxis.set_minor_locator(LogLocator(base=10.0, subs=np.arange(2.0, 10.0), numticks=10))
        ax_lat.tick_params(axis='y', which='both', length=5)
        ax_lat.grid(True, which='major', axis='both', alpha=0.3)
        # Goodput subplot
        ax_gp = axes[0, 1]
        for label, exp_info in all_experiment_data.items():
            exp_data = exp_info['data']
            loads = exp_info['loads']
            if api not in exp_data:
                continue
            goodput_data = exp_data[api]['goodput']
            means = [item[0]/1000.0 if item[0] is not None else None for item in goodput_data]
            stds = [(item[1] or 0)/1000.0 for item in goodput_data]
            valid_data = [(l, m, s) for l, m, s in zip(loads, means, stds)
                         if m is not None and not (isinstance(m, float) and math.isnan(m))]
            if valid_data:
                valid_loads, valid_means, valid_stds = zip(*valid_data)
                color = color_map.get(label)
                marker = marker_map.get(label, 'o')
                style = style_map.get(label, '-')
                ax_gp.errorbar(
                    valid_loads, valid_means, yerr=valid_stds,
                    fmt=style, marker=marker, color=color, label=label, linewidth=2.4,
                    markersize=6.0, markerfacecolor=color,
                    markeredgecolor='black', markeredgewidth=0.6,
                    capsize=4, elinewidth=1.3
                )
        ax_gp.set_ylabel('Goodput (KRPS)')
        ax_gp.grid(True, which='major', axis='both', alpha=0.3)
        
        # Set y-axis to start from 0 for goodput
        ax_gp.set_ylim(bottom=0)
        
        # Only bottom x-labels
        # Set x-ticks: even numbers, plus first/last
        for ax in [ax_lat, ax_gp]:
            if all_loads:
                min_x = min(all_loads)
                max_x = max(all_loads)
                # Even numbers in range
                xticks = [x for x in range(int(min_x), int(max_x)+1) if x % 2 == 0]
                # Ensure first and last are present
                if min_x not in xticks:
                    xticks = [min_x] + xticks
                if max_x not in xticks:
                    xticks = xticks + [max_x]
                ax.set_xticks(xticks)
                ax.set_xticklabels([str(int(x)) for x in xticks])
            ax.set_xlabel('Offered Load (KRPS)')
        # Set y-axis grid for goodput every 1k
        import matplotlib.ticker as mticker
        ax_gp.yaxis.set_major_locator(mticker.MultipleLocator(1))
        # No API name for single API case
        # Add legends
        handles, labels = [], []
        for ax in [ax_lat, ax_gp]:
            h, l = ax.get_legend_handles_labels()
            for hh, ll in zip(h, l):
                if ll not in labels:
                    handles.append(hh)
                    labels.append(ll)
        if handles:
            fig.legend(
                handles, labels, loc='upper center', bbox_to_anchor=(0.5, 1.05),
                ncol=max(1, len(labels)), frameon=True, fancybox=True,
                framealpha=0.85, edgecolor='#bbbbbb'
            )
            fig.subplots_adjust(top=0.90)
    else:
        fig, axes = canvas.create_canvas(
            nrows=2, ncols=n_apis, width_in_inches=3.33, aspect_ratio=0.75,
            line_width=2, font_size=16, legend_size=14, marker_size=5
        )
        axes = np.array(axes)
        if axes.ndim == 1:
            axes = axes.reshape(2, n_apis)
        # Add API names above columns, even lower (e.g., y=0.91)
        for i, api in enumerate(all_apis):
            display_api = api[:-4] if api.endswith('_all') else api
            fig.text(
                x=(i + 0.5) / n_apis,
                y=0.91,
                s=display_api,
                ha='center', va='bottom', fontsize=16, fontweight='bold', transform=fig.transFigure
            )
        # First row: goodput, second row: latency
        for i, api in enumerate(all_apis):
            display_api = api[:-4] if api.endswith('_all') else api
            # Goodput subplot
            ax_gp = axes[0, i]
            for label, exp_info in all_experiment_data.items():
                exp_data = exp_info['data']
                loads = exp_info['loads']
                if api not in exp_data:
                    continue
                goodput_data = exp_data[api]['goodput']
                means = [item[0]/1000.0 if item[0] is not None else None for item in goodput_data]
                stds = [(item[1] or 0)/1000.0 for item in goodput_data]
                valid_data = [(l, m, s) for l, m, s in zip(loads, means, stds)
                             if m is not None and not (isinstance(m, float) and math.isnan(m))]
                if valid_data:
                    valid_loads, valid_means, valid_stds = zip(*valid_data)
                    color = color_map.get(label)
                    marker = marker_map.get(label, 'o')
                    style = style_map.get(label, '-')
                    ax_gp.errorbar(
                        valid_loads, valid_means, yerr=valid_stds,
                        fmt=style, marker=marker, color=color, label=label, linewidth=2.4,
                        markersize=6.0, markerfacecolor=color,
                        markeredgecolor='black', markeredgewidth=0.6,
                        capsize=4, elinewidth=1.3
                    )
            # Only leftmost y-label
            if i == 0:
                ax_gp.set_ylabel('Goodput (KRPS)')
            else:
                ax_gp.set_ylabel('')
            # Only bottom x-labels for bottom row
            ax_gp.set_xlabel('')
            
            # Set y-axis to start from 0 for goodput
            ax_gp.set_ylim(bottom=0)
            
            # Set x-ticks: even numbers, plus first/last
            if all_loads:
                min_x = min(all_loads)
                max_x = max(all_loads)
                xticks = [x for x in range(int(min_x), int(max_x)+1) if x % 2 == 0]
                if min_x not in xticks:
                    xticks = [min_x] + xticks
                if max_x not in xticks:
                    xticks = xticks + [max_x]
                ax_gp.set_xticks(xticks)
                ax_gp.set_xticklabels(['' for _ in xticks])
            # Set y-axis grid for goodput every 1k and ensure grid is visible
            import matplotlib.ticker as mticker
            ax_gp.yaxis.set_major_locator(mticker.MultipleLocator(1))
            # Add x-axis grid for goodput figures
            ax_gp.grid(True, which='major', axis='both', alpha=0.3)
            # Latency subplot
            ax_lat = axes[1, i]
            for label, exp_info in all_experiment_data.items():
                exp_data = exp_info['data']
                loads = exp_info['loads']
                if api not in exp_data:
                    continue
                latency_data = exp_data[api]['latency_p95']
                means = [item[0] for item in latency_data]
                stds = [item[1] if item[1] is not None else 0.0 for item in latency_data]
                valid_data = [(l, m, s) for l, m, s in zip(loads, means, stds)
                             if m is not None and not (isinstance(m, float) and math.isnan(m))]
                if valid_data:
                    valid_loads, valid_means, valid_stds = zip(*valid_data)
                    color = color_map.get(label)
                    marker = marker_map.get(label, 'o')
                    style = style_map.get(label, '-')
                    ax_lat.errorbar(
                        valid_loads, valid_means, yerr=valid_stds,
                        fmt=style, marker=marker, label=label,
                        linewidth=2.8, color=color, markersize=6.5,
                        markerfacecolor=color, markeredgecolor='black',
                        markeredgewidth=0.6, capsize=4, elinewidth=1.4
                    )
            slo_val = None
            for key in [display_api, display_api.replace('-', '_'), display_api.replace('_', '-')]:
                if slo_map and key in slo_map:
                    slo_val = slo_map[key]
                    break
            if slo_val is not None:
                ax_lat.axhline(y=slo_val, color='r', linestyle='--', label='SLO')
            # Only leftmost y-label
            if i == 0:
                ax_lat.set_ylabel('P95 Latency (ms)')
            else:
                ax_lat.set_ylabel('')
            # Only bottom x-labels for bottom row
            ax_lat.set_xlabel('Offered Load (KRPS)')
            ax_lat.set_yscale('log')
            # Add log y-axis ticks: major at 1,10,100..., minor at 2-9 in each decade
            ax_lat.yaxis.set_major_locator(LogLocator(base=10.0, subs=(1.0,), numticks=10))
            ax_lat.yaxis.set_minor_locator(LogLocator(base=10.0, subs=np.arange(2.0, 10.0), numticks=10))
            ax_lat.tick_params(axis='y', which='both', length=5)
            ax_lat.grid(True, which='major', axis='both', alpha=0.3)
            # Set x-ticks: even numbers, plus first/last
            if all_loads:
                min_x = min(all_loads)
                max_x = max(all_loads)
                xticks = [x for x in range(int(min_x), int(max_x)+1) if x % 2 == 0]
                if min_x not in xticks:
                    xticks = [min_x] + xticks
                if max_x not in xticks:
                    xticks = xticks + [max_x]
                ax_lat.set_xticks(xticks)
                ax_lat.set_xticklabels([str(int(x)) for x in xticks])
        # Add legends
        handles, labels = [], []
        for i in range(n_apis):
            for ax in [axes[0, i], axes[1, i]]:
                h, l = ax.get_legend_handles_labels()
                for hh, ll in zip(h, l):
                    if ll not in labels:
                        handles.append(hh)
                        labels.append(ll)
        if handles:
            fig.legend(
                handles, labels, loc='upper center', bbox_to_anchor=(0.5, 1.05),
                ncol=max(1, len(labels)), frameon=True, fancybox=True,
                framealpha=0.85, edgecolor='#bbbbbb'
            )
            fig.subplots_adjust(top=0.90)
    # Save merged figure
    merged_path = output_dir / f'{figure_name}_latency_goodput_vs_load.pdf'
    fig.savefig(merged_path, bbox_inches='tight')
    try:
        import matplotlib.pyplot as plt
        plt.close(fig)
    except Exception:
        pass
    produced = [merged_path]
    return produced


def generate_merged_figures(
    merged_config_path: Path,
    experiments_config_path: Path,
    experiments_root: Path,
    output_dir: Path,
    experiment_index: str = None,
    experiment_config: str = None
) -> None:
    """Generate all merged figures based on configuration."""
    # Load configurations
    merged_config = load_merged_config(merged_config_path)
    experiment_configs = load_experiment_configs(experiments_config_path)
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
                    experiments_root, output_dir, experiment_config
                )
                print(f"  Generated {len(produced)} files: {[p.name for p in produced]}")
            elif figure_type == 'latency-and-rate-vs-time':
                produced = generate_latency_and_rate_vs_time_merged(
                    figure_name, figure_config, experiment_configs,
                    experiments_root, output_dir, experiment_config
                )
                print(f"  Generated {len(produced)} files: {[p.name for p in produced]}")
            elif figure_type == 'max-queue':
                produced = generate_max_queue_merged(
                    figure_name, figure_config, experiment_configs,
                    experiments_root, output_dir, experiment_config
                )
                print(f"  Generated {len(produced)} files: {[p.name for p in produced]}")
            elif figure_type == 'resource-waste-bar':
                produced = generate_resource_waste_bar_merged(
                    figure_name, figure_config, experiment_configs,
                    experiments_root, output_dir, experiment_config
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
    Generate merged latency-and-rate-vs-time figure(s).
    For one API: one figure, columns=experiments, rows=latency/rate.
    For multiple APIs: one figure per API, same layout.
    Data is loaded from plugins for the specified unit/repeat per experiment.
    """
    import numpy as np
    from pathlib import Path
    # Import plugin helpers and canvas
    try:
        from exec.plots.common import extract_series
    except Exception:
        from plots.common import extract_series
    try:
        from canvas import canvas
    except Exception:
        import matplotlib.pyplot as plt
        class SimpleCanvas:
            def create_canvas(self, nrows=1, ncols=1, width_in_inches=6, aspect_ratio=0.66, **kwargs):
                fig, axes = plt.subplots(nrows, ncols, figsize=(width_in_inches*ncols, width_in_inches*aspect_ratio*nrows))
                return fig, axes
        canvas = SimpleCanvas()

    with open(global_config) as f:
        global_configs = json.load(f)

    # Get experiment list and config
    include_experiments = figure_config.get('include', {})
    if not include_experiments:
        return []
    # Gather all APIs
    all_apis = set()
    exp_units = {}
    for exp_name, exp_cfg in include_experiments.items():
        if exp_name not in experiment_configs:
            raise Exception(f"Experiment '{exp_name}' not found in experiment configs")
        exp_def = experiment_configs[exp_name]
        apis = exp_def.get('apis', [])
        all_apis.update(apis)
        exp_units[exp_name] = {
            'unit': exp_cfg.get('unit'),
            'repeat': exp_cfg.get('repeat', 1),
            'label': exp_cfg.get('label', exp_name)
        }
    all_apis = list(all_apis)
    produced = []
    # For each API (or just once if one API)
    api_list = all_apis if len(all_apis) > 1 else [all_apis[0]]
    for api in api_list:
        # Collect data for each experiment
        exp_data = {}
        for exp_name, unit_cfg in exp_units.items():
            label = unit_cfg['label']
            unit = unit_cfg['unit']
            repeat = unit_cfg['repeat']
            # Find run dir
            found_record = None
            for exp_index in range(1, 20):
                run_root = experiments_root / f'exp-{exp_index:03d}' if not experiments_root.name.startswith('exp-') else experiments_root
                summary_path = run_root / 'run_summary.jsonl'
                if not summary_path.exists():
                    continue
                with summary_path.open() as f:
                    for line in f:
                        obj = None
                        try:
                            obj = json.loads(line.strip())
                        except Exception:
                            continue
                        if obj and obj.get('experiment_name') == exp_name and obj.get('run_unit_name') == unit and obj.get('repeat_index', 1) == repeat:
                            found_record = obj
                            break
                if found_record:
                    break
            if not found_record:
                raise Exception(f"No run data found for experiment '{exp_name}' unit '{unit}'")
            artifact_dir = Path(found_record.get('artifact_dir', '.'))
            metrics_dir = artifact_dir / 'metrics'
            metric_files = {}
            for fp in metrics_dir.glob('*.json'):
                if fp.name.startswith('_index'):
                    continue
                try:
                    metric_files[fp.stem] = json.loads(fp.read_text())
                except Exception:
                    continue
            # Latency
            lat_json = metric_files.get(f'latency_p95_{api}_all') or metric_files.get(f'latency_p95_{api}')
            ts_lat, vals_lat = extract_series(lat_json) if lat_json else ([], [])
            # Rate
            rate_json = metric_files.get(f'goodput_{api}_all') or metric_files.get(f'goodput_{api}')
            ts_rate, vals_rate = extract_series(rate_json) if rate_json else ([], [])
            # Convert goodput to KRPS
            vals_rate = [v/1000.0 for v in vals_rate]
            exp_data[label] = {
                'latency': (ts_lat, vals_lat),
                'rate': (ts_rate, vals_rate)
            }
        # Figure layout: columns=experiments, rows=latency/rate
        n_exps = len(exp_data)
        fig, axes = canvas.create_canvas(
            nrows=2, ncols=n_exps, width_in_inches=3, aspect_ratio=0.75,
            line_width=2, font_size=16, legend_size=14, marker_size=5
        )
        axes = np.array(axes)
        if axes.ndim == 1:
            axes = axes.reshape(2, n_exps)
        # Plot latency (row 0)
        latency_ymin, latency_ymax = None, None
        latency_label = 'Latency (ms)'
        all_latency_vals = []
        for j, (label, data) in enumerate(exp_data.items()):
            ax_lat = axes[0, j]
            # Extract p50, p95, and SLO
            ts_p95, vals_p95 = data['latency']
            # Use relative time
            if ts_p95:
                t0 = ts_p95[0]
                ts_p95 = [t-t0 for t in ts_p95]
            # Try to get p50 from metrics
            found_record = None
            for exp_name, unit_cfg in exp_units.items():
                if unit_cfg['label'] == label:
                    found_record = exp_name
                    break
            p50_json = None
            if found_record:
                # Find metrics for p50
                exp_name = found_record
                unit = exp_units[exp_name]['unit']
                for exp_index in range(1, 20):
                    run_root = experiments_root / f'exp-{exp_index:03d}' if not experiments_root.name.startswith('exp-') else experiments_root
                    summary_path = run_root / 'run_summary.jsonl'
                    if not summary_path.exists():
                        continue
                    with summary_path.open() as f:
                        for line in f:
                            obj = None
                            try:
                                obj = json.loads(line.strip())
                            except Exception:
                                continue
                            if obj and obj.get('experiment_name') == exp_name and obj.get('run_unit_name') == unit and exp_units[exp_name]['repeat'] == obj.get('repeat_index', 1):
                                artifact_dir = Path(obj.get('artifact_dir', '.'))
                                metrics_dir = artifact_dir / 'metrics'
                                metric_files = {}
                                for fp in metrics_dir.glob('*.json'):
                                    if fp.name.startswith('_index'):
                                        continue
                                    try:
                                        metric_files[fp.stem] = json.loads(fp.read_text())
                                    except Exception:
                                        continue
                                p50_json = metric_files.get(f'latency_p50_{api}_all') or metric_files.get(f'latency_p50_{api}')
                                break
                    if p50_json:
                        break
            ts_p50, vals_p50 = extract_series(p50_json) if p50_json else ([], [])
            if ts_p50:
                t0 = ts_p50[0]
                ts_p50 = [t-t0 for t in ts_p50]
            # SLO value
            slo_val = None
            try:
                """ with open('exec/config.sample.json') as f:
                    config_data = json.load(f)
                    slo_map = config_data.get('slos', {})
                display_api = api[:-4] if api.endswith('_all') else api
                for key in [display_api, display_api.replace('-', '_'), display_api.replace('_', '-')]:
                    if slo_map and key in slo_map:
                        slo_val = slo_map[key]
                        break """
                display_api = api[:-4] if api.endswith('_all') else api
                for key in [display_api, display_api.replace('-', '_'), display_api.replace('_', '-')]: 
                    slo_val = global_configs.get('slos')[key]
                    break
            except Exception:
                slo_val = None
            # Draw p95, p50, SLO with different colors/styles
            if ts_p95 and vals_p95:
                ax_lat.plot(ts_p95, vals_p95, label='P95', linewidth=2.2, color='#1f77b4', linestyle='-')
                all_latency_vals.extend(vals_p95)
            if ts_p50 and vals_p50:
                ax_lat.plot(ts_p50, vals_p50, label='P50', linewidth=2.2, color='#ff7f0e', linestyle='--')
                all_latency_vals.extend(vals_p50)
            if slo_val is not None:
                ax_lat.axhline(y=slo_val, color='r', linestyle=':', label='SLO', linewidth=2.0)
                all_latency_vals.append(slo_val)
            # Plugin-style axis config
            if j == 0:
                ax_lat.set_ylabel(latency_label)
                ax_lat.yaxis.set_tick_params(labelleft=True)
            else:
                ax_lat.set_ylabel('')
                ax_lat.yaxis.set_tick_params(labelleft=False)
            ax_lat.set_yscale('log')
            ax_lat.set_xlabel('')
            # X ticks: plugin style (show every 5s, first/last)
            if ts_p95:
                min_x = min(ts_p95)
                max_x = max(ts_p95)
                xticks = [x for x in range(int(min_x), int(max_x)+1) if x % 5 == 0]
                if min_x not in xticks:
                    xticks = [min_x] + xticks
                if max_x not in xticks:
                    xticks = xticks + [max_x]
                ax_lat.set_xticks(xticks)
                ax_lat.set_xticklabels(['' for _ in xticks])
            # Grid config as in plugin
            ax_lat.grid(True, which='major', axis='both', alpha=0.3)
        # Set consistent y limits for latency row
        if all_latency_vals:
            ymin = min([v for v in all_latency_vals if v > 0])
            ymax = max(all_latency_vals) * 1.05
            for j in range(len(exp_data)):
                axes[0, j].set_ylim(ymin, ymax)
        # Plot rate stack (row 1)
        for j, (label, data) in enumerate(exp_data.items()):
            ax_rate = axes[1, j]
            # Get goodput, SLO violation, dropped from metrics
            found_record = None
            for exp_name, unit_cfg in exp_units.items():
                if unit_cfg['label'] == label:
                    found_record = exp_name
                    break
            goodput_json = slo_json = dropped_json = None
            ts_gp = vals_gp = ts_slo = vals_slo = ts_drop = vals_drop = []
            if found_record:
                exp_name = found_record
                unit = exp_units[exp_name]['unit']
                for exp_index in range(1, 20):
                    run_root = experiments_root / f'exp-{exp_index:03d}' if not experiments_root.name.startswith('exp-') else experiments_root
                    summary_path = run_root / 'run_summary.jsonl'
                    if not summary_path.exists():
                        continue
                    with summary_path.open() as f:
                        for line in f:
                            obj = None
                            try:
                                obj = json.loads(line.strip())
                            except Exception:
                                continue
                            if obj and obj.get('experiment_name') == exp_name and obj.get('run_unit_name') == unit and exp_units[exp_name]['repeat'] == obj.get('repeat_index', 1):
                                artifact_dir = Path(obj.get('artifact_dir', '.'))
                                metrics_dir = artifact_dir / 'metrics'
                                metric_files = {}
                                for fp in metrics_dir.glob('*.json'):
                                    if fp.name.startswith('_index'):
                                        continue
                                    try:
                                        metric_files[fp.stem] = json.loads(fp.read_text())
                                    except Exception:
                                        continue
                                goodput_json = metric_files.get(f'goodput_{api}_all') or metric_files.get(f'goodput_{api}')
                                slo_json = metric_files.get(f'slo_violation_{api}_all') or metric_files.get(f'slo_violation_{api}')
                                dropped_json = metric_files.get(f'dropped_{api}_all') or metric_files.get(f'dropped_{api}')
                                break
                    if goodput_json:
                        ts_gp, vals_gp = extract_series(goodput_json)
                        vals_gp = [v/1000.0 for v in vals_gp]
                    if slo_json:
                        ts_slo, vals_slo = extract_series(slo_json)
                        vals_slo = [v/1000.0 for v in vals_slo]
                    if dropped_json:
                        ts_drop, vals_drop = extract_series(dropped_json)
                        vals_drop = [v/1000.0 for v in vals_drop]
            # Use relative time
            if ts_gp:
                t0 = ts_gp[0]
                ts_gp = [t-t0 for t in ts_gp]
            if ts_slo:
                t0 = ts_slo[0]
                ts_slo = [t-t0 for t in ts_slo]
            if ts_drop:
                t0 = ts_drop[0]
                ts_drop = [t-t0 for t in ts_drop]
            # Cap to 15s
            def cap_15s(ts, vals):
                return zip(*[(t, v) for t, v in zip(ts, vals) if t <= 15]) if ts and vals else ([], [])
            ts_gp, vals_gp = cap_15s(ts_gp, vals_gp)
            ts_slo, vals_slo = cap_15s(ts_slo, vals_slo)
            ts_drop, vals_drop = cap_15s(ts_drop, vals_drop)
            ts_gp, vals_gp = list(ts_gp), list(vals_gp)
            ts_slo, vals_slo = list(ts_slo), list(vals_slo)
            ts_drop, vals_drop = list(ts_drop), list(vals_drop)
            # Stack plot
            # Align all arrays to a common time axis
            def align_series(ts_list, vals_list):
                import numpy as np
                if not ts_list or not vals_list or not any(ts_list):
                    return [], [[] for _ in vals_list]
                all_ts = np.unique(np.concatenate([np.array(ts) for ts in ts_list if len(ts) > 0]))
                aligned = []
                for ts, vals in zip(ts_list, vals_list):
                    interp = np.interp(all_ts, ts, vals) if len(ts) == len(vals) and len(ts) > 1 else np.zeros_like(all_ts)
                    aligned.append(interp)
                return all_ts, aligned
            common_ts, [aligned_gp, aligned_slo, aligned_drop] = align_series([ts_gp, ts_slo, ts_drop], [vals_gp, vals_slo, vals_drop])
            if len(common_ts) > 0:
                ax_rate.stackplot(common_ts, aligned_gp, aligned_slo, aligned_drop, labels=['Goodput', 'SLO Violation', 'Dropped'], colors=['#1f77b4', '#d62728', '#bbbbbb'], alpha=0.85)
            # Plugin-style axis config
            if j == 0:
                ax_rate.set_ylabel('Rate (KRPS)')
                ax_rate.yaxis.set_tick_params(labelleft=True)
            else:
                ax_rate.set_ylabel('')
                ax_rate.yaxis.set_tick_params(labelleft=False)
            ax_rate.set_xlabel('Time (s)')
            # X ticks: plugin style (show every 5s, first/last)
            if len(common_ts) > 0:
                min_x = min(common_ts)
                max_x = max(common_ts)
                xticks = [x for x in range(int(min_x), int(max_x)+1) if x % 5 == 0]
                if min_x not in xticks:
                    xticks = [min_x] + xticks
                if max_x not in xticks:
                    xticks = xticks + [max_x]
                ax_rate.set_xticks(xticks)
                ax_rate.set_xticklabels([str(int(x)) for x in xticks])
            # Grid config as in plugin
            import matplotlib.ticker as mticker
            ax_rate.yaxis.set_major_locator(mticker.MultipleLocator(2))
            ax_rate.grid(True, which='major', axis='both', alpha=0.3)
        # Add experiment names above columns
        """ for j, label in enumerate(exp_data.keys()):
            fig.text(
                x=(j + 0.5) / n_exps,
                y=0.80,
                s=label,
                ha='center', va='bottom', fontsize=16, fontweight='bold', transform=fig.transFigure
            ) """
        print("[DEBUG] Number of axes rows:", axes.shape[0])
        print("[DEBUG] Number of axes columns:", axes.shape[1])
        handles_lat, labels_lat = axes[0,0].get_legend_handles_labels()
        handles_rate, labels_rate = axes[1,0].get_legend_handles_labels()
        print("[DEBUG] Latency legend handles:", handles_lat)
        print("[DEBUG] Latency legend labels:", labels_lat)
        print("[DEBUG] Rate legend handles:", handles_rate)
        print("[DEBUG] Rate legend labels:", labels_rate)
        # Remove any previous legends
        if hasattr(fig, 'legends'):
            print("[DEBUG] Removing previous legends, count:", len(getattr(fig, 'legends', [])))
            for leg in getattr(fig, 'legends', []):
                try:
                    leg.remove()
                except Exception as e:
                    print("[DEBUG] Exception removing legend:", e)
            fig.legends = []
        # Latency legend above system names
        leg1 = None
        leg2 = None
        if handles_lat:
            print("[DEBUG] Adding latency legend")
            leg1 = fig.legend(
                handles_lat, labels_lat, loc='upper center', bbox_to_anchor=(0.5, 1.05),
                ncol=max(1, len(labels_lat)), frameon=True, fancybox=True,
                framealpha=0.85, edgecolor='#bbbbbb', title=None
            )
        if handles_rate:
            print("[DEBUG] Adding rate legend")
            leg2 = fig.add_artist(
                fig.legend(
                    handles_rate, labels_rate, loc='lower center', bbox_to_anchor=(0.5, 0.50),
                    ncol=max(1, len(labels_rate)), frameon=True, fancybox=True,
                    framealpha=0.85, edgecolor='#bbbbbb', title=None
                )
            )
        # Store legends for later removal if needed
        fig.legends = []
        if leg1:
            fig.legends.append(leg1)
        if leg2:
            # fig.add_artist returns None, so get the legend from fig.get_legend()
            legend_objs = [leg for leg in fig.get_children() if hasattr(leg, 'get_texts')]
            if len(legend_objs) > 1:
                fig.legends.append(legend_objs[-1])
        print("[DEBUG] Legend objects:", fig.legends)
        # Remove any extra legends (keep only two)
        if hasattr(fig, 'legends') and len(fig.legends) > 2:
            print("[DEBUG] Forcibly removing extra legend, count:", len(fig.legends) - 2)
            while len(fig.legends) > 2:
                leg = fig.legends.pop()
                try:
                    leg.remove()
                except Exception as e:
                    print("[DEBUG] Exception removing extra legend:", e)
        print("[DEBUG] Number of legends after adding and cleanup:", len(getattr(fig, 'legends', [])))
        fig.subplots_adjust(top=0.86, bottom=0.22, hspace=0.45, wspace=0.1)
        # Add experiment names above columns
        for j, label in enumerate(exp_data.keys()):
            fig.text(
                x=(j + 0.5) / n_exps,
                y=0.88,
                s=label,
                ha='center', va='bottom', fontsize=16, fontweight='bold', transform=fig.transFigure
            )
        """ # Add legends
        handles, labels_ = [], []
        for i in range(n_exps):
            for ax in [axes[0, i], axes[1, i]]:
                h, l = ax.get_legend_handles_labels()
                for hh, ll in zip(h, l):
                    if ll not in labels_:
                        handles.append(hh)
                        labels_.append(ll)
        if handles:
            fig.legend(
                handles, labels_, loc='upper center', bbox_to_anchor=(0.5, 1.05),
                ncol=max(1, len(labels_)), frameon=True, fancybox=True,
                framealpha=0.85, edgecolor='#bbbbbb'
            )
            fig.subplots_adjust(top=0.90) """
        # Save figure
        import matplotlib.pyplot as plt
        if len(all_apis) > 1:
            out_path = output_dir / f'{figure_name}_{api}_latency_rate_vs_time.pdf'
        else:
            out_path = output_dir / f'{figure_name}_latency_rate_vs_time.pdf'
        fig.savefig(out_path, bbox_inches='tight')
        try:
            plt.close(fig)
        except Exception:
            pass
        produced.append(out_path)
    return produced


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='Generate merged experiment plots')
    parser.add_argument('--merged-config', type=Path, required=True,
                       help='Path to merged.yaml configuration file')
    parser.add_argument('--experiments-config', type=Path, required=True,
                       help='Path to experiments.json configuration file')
    parser.add_argument('--experiments-root', type=Path, default=Path('experiment_runs'),
                       help='Root directory containing experiment runs')
    parser.add_argument('--output-dir', type=Path, default=Path('generated_plots/merged'),
                       help='Output directory for merged plots')
    parser.add_argument('--experiment-index', type=str, default=None,
                       help='Experiment index (e.g., 001) to use. If set, only that experiment run will be used.')
    parser.add_argument('--experiment-config', required=True)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        generate_merged_figures(
            args.merged_config,
            args.experiments_config, 
            args.experiments_root,
            args.output_dir,
            experiment_index=args.experiment_index,
            experiment_config=args.experiment_config
        )
    except Exception as e:
        print(f"Error: {e}")
        if os.environ.get('PLOT_DEBUG') == '1':
            traceback.print_exc()
        return 1
    return 0


if __name__ == '__main__':
    exit(main())
