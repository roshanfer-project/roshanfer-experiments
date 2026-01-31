"""Unit-level aggregate resource waste visualization plugin.

Generates architecture diagrams showing resource waste percentages per service and API:
  * resource_waste_diagram.pdf : Network topology with color-coded service boxes showing waste %

Assumptions:
  * Experiment type: 'resource-waste'
  * Hard-coded hotel benchmark topology
  * Metrics available: ppm_accepted_rpc_total, ppm_failed_rpc_total, ppm_k6_slo_violation_counter_total
  * Service name normalization: 'frontend-grpc' → 'frontend'
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
import math
import statistics

SUPPORTED_TYPES = ['resource-waste']

try:
    from exec.plots.data_loader import PrometheusData
except ImportError:
    PrometheusData = None

try:
    from ..common import extract_series
except Exception:  # pragma: no cover
    from experiments.exec.plots.common import extract_series  # type: ignore

def _calculate_waste_from_prometheus(prom_data, rwg_data: dict, apis: list, bench: str) -> Dict[str, Dict[str, float]]:
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
        if not prom_data or not prom_data.metrics or api not in prom_data.metrics: return 0.0
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
        if rwg_data and api in rwg_data:
             overall_tuple = rwg_data[api] 
             # rwg_data[api] is (overall, realtime, prom)
             if overall_tuple and len(overall_tuple) >= 1 and overall_tuple[0]:
                 overall_obj = overall_tuple[0]
        
        num_dropped = overall_obj.num_dropped_requests if overall_obj else 0
        entry_reported_failed = get_metric(api, entry_service, 'failed_rpc_counter')
        
        # Logging check (print for now, maybe warn?)
        diff = abs(num_dropped - entry_reported_failed)
        if diff > 0 and num_dropped > 10 and diff > (num_dropped * 0.1):
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
            
            # Normalize service name for consistency with legacy map
            norm_service = _normalize_service_name(service)
            if norm_service != service:
                if norm_service not in waste_results: waste_results[norm_service] = {}
                waste_results[norm_service][api] = waste_pct

    return waste_results




def _normalize_service_name(service: str) -> str:
    """Normalize service names. Convert 'frontend-grpc' to 'frontend'."""
    if service == 'frontend-grpc':
        return 'frontend'
    if service == "nginx-grpc":
        return "nginx"
    return service


def _mean_std(vals: List[float]) -> Tuple[float | None, float | None]:
    clean = [v for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not clean:
        return None, None
    m = sum(clean)/len(clean)
    if len(clean) < 2:
        return m, 0.0
    try:
        return m, statistics.pstdev(clean)
    except Exception:
        return m, 0.0


def _calculate_waste_per_repeat(metric_files: Dict[str, dict], apis: List[str], bench: str, prom_data=None, rwg_data=None) -> Dict[str, Dict[str, float]]:
    """Calculate resource waste percentages for each service and API in a single repeat.
    
    Args:
        metric_files: Dictionary of legacy metric files
        apis: List of APIs to process
        bench: Benchmark name (hotel or social)
        prom_data: Optional PrometheusData object. If present, use new calculation method.
        rwg_data: Optional RWG data containing OverallData for SLO violation counts.
    
    Returns: {service: {api: waste_percentage}}
    """
    # If Prometheus data is available, use the new algorithm
    if prom_data and prom_data.metrics:
        try:
            return _calculate_waste_from_prometheus(prom_data, rwg_data, apis, bench)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Warning: Failed to calculate waste from Prometheus data: {e}. Falling back to legacy.")

    import os
    debug = os.environ.get('PLOT_DEBUG') == '1'
    
    if debug:
        print(f"[resource-waste] Available metric files: {sorted(metric_files.keys())}")
    
    # Service list for hotel benchmark
    if bench.lower() == "hotel":
        service_list = ["frontend-grpc", "search", "reservation", "profile", "rate", "geo", "user"]
    elif bench.lower() == "social":
        service_list = ["nginx-grpc", "compose", "home", "user", "posts", "graph"]
    
    waste_data = {}
    
    for api in apis:
        if debug:
            print(f"[resource-waste] Processing API: {api}")
        
        # Get accepted requests per service for this API
        accepted = {}
        for service in service_list:
            # Look for accepted RPC metrics
            possible_stems = [
                f'accepted_rate_{api}_{service}',
                f'accepted_rate_{service}_{api}',
                f'accepted_rate_{service}',
                f'accepted_rpc_{api}_{service}',
                f'accepted_rpc_{service}_{api}',
                f'accepted_rpc_{service}',
                f'ppm_accepted_rpc_total_{service}_{api}',
                f'ppm_accepted_rpc_total_{api}_{service}',
                f'ppm_accepted_rpc_total_{service}',
                # Try without method suffix
                f'accepted_rate_{service}',
                f'accepted_rpc_{service}',
                f'ppm_accepted_rpc_total'
            ]
            
            found_stem = None
            for stem in possible_stems:
                if stem in metric_files:
                    found_stem = stem
                    break
            
            if found_stem:
                ts, vals = extract_series(metric_files[found_stem])
                if vals:
                    accepted[service] = float(vals[-1])
                    if debug:
                        print(f"[resource-waste] {service} {api} accepted: {accepted[service]} (from {found_stem})")
                else:
                    accepted[service] = 0.0
                    if debug:
                        print(f"[resource-waste] {service} {api} accepted: 0.0 (empty values from {found_stem})")
            else:
                accepted[service] = 0.0
                if debug:
                    print(f"[resource-waste] {service} {api} accepted: 0.0 (no metric found, tried: {possible_stems[:3]}...)")
        
        # Get failed/dropped requests per service for this API
        dropped = {}
        for service in service_list:
            possible_stems = [
                f'dropped_rate_{api}_{service}',
                f'dropped_rate_{service}_{api}',
                f'dropped_rate_{service}',
                f'failed_rpc_{api}_{service}',
                f'failed_rpc_{service}_{api}',
                f'failed_rpc_{service}',
                f'ppm_failed_rpc_total_{service}_{api}',
                f'ppm_failed_rpc_total_{api}_{service}',
                f'ppm_failed_rpc_total_{service}',
                # Try without method suffix
                f'dropped_rate_{service}',
                f'failed_rpc_{service}',
                f'ppm_failed_rpc_total'
            ]
            
            found_stem = None
            for stem in possible_stems:
                if stem in metric_files:
                    found_stem = stem
                    break
            
            if found_stem:
                ts, vals = extract_series(metric_files[found_stem])
                if vals:
                    dropped[service] = float(vals[-1])
                    if debug:
                        print(f"[resource-waste] {service} {api} dropped: {dropped[service]} (from {found_stem})")
                else:
                    dropped[service] = 0.0
                    if debug:
                        print(f"[resource-waste] {service} {api} dropped: 0.0 (empty values from {found_stem})")
            else:
                dropped[service] = 0.0
                if debug:
                    print(f"[resource-waste] {service} {api} dropped: 0.0 (no metric found, tried: {possible_stems[:3]}...)")
        
        # Store original dropped values for debugging
        original_dropped = dropped.copy()
        
        # Apply the waste propagation logic from the original code
        # Note: This logic is specific to the hotel benchmark topology
        if api == "search-hotel":
            if debug:
                print(f"[resource-waste] Applying search-hotel waste propagation logic")
                print(f"[resource-waste] Original dropped values: {original_dropped}")
            
            # frontend-grpc gets waste from downstream services
            dropped["frontend-grpc"] = dropped["reservation"] + dropped["search"] + dropped["profile"]
            
            # search gets waste from downstream
            dropped["search"] = dropped["geo"] + dropped["rate"] + dropped["reservation"] + dropped["profile"]
            
            # geo gets waste from downstream
            dropped["geo"] = dropped["rate"] + dropped["reservation"] + dropped["profile"]

            dropped["rate"] = dropped["reservation"] + dropped["profile"]
            
            # reservation gets waste from downstream
            dropped["reservation"] = dropped["profile"]
            
            # profile and rate are leaf services
            dropped["profile"] = 0
            
            if debug:
                print(f"[resource-waste] After propagation dropped values: {dropped}")
            
        elif api == "reserve-hotel":
            if debug:
                print(f"[resource-waste] Applying reserve-hotel waste propagation logic")
                print(f"[resource-waste] Original dropped values: {original_dropped}")
            
            # frontend-grpc gets waste from downstream services
            dropped["frontend-grpc"] = dropped["reservation"] + dropped["user"]

            # user gets waste from downstream
            dropped["user"] = dropped["reservation"]
            
            # reservation is leaf service for this API
            dropped["reservation"] = 0
            
            if debug:
                print(f"[resource-waste] After propagation dropped values: {dropped}")
        elif api == "read-user-timeline":
            dropped["nginx-grpc"] = dropped["user"]

            ### User
            dropped["user"] = dropped["posts"]

            dropped["posts"] = 0
        
        elif api == "read-home-timeline":
            dropped["nginx-grpc"] = dropped["user"]

            ### User
            dropped["user"] = dropped["posts"]

            dropped["posts"] = 0

        elif api == "compose-post":
            if debug:
                print(f"[resource-waste] Dropped keys: {list(dropped.keys())}")
            original_nginx_dropped = dropped["nginx-grpc"]

            ### Nginx
            dropped["nginx-grpc"] = dropped["compose"]

            dropped['compose'] = dropped["posts"] + dropped["home"] + dropped["user"]

            dropped['posts'] = dropped['home'] + dropped["user"]

            dropped['user'] = dropped['home']

            dropped['home'] = dropped["graph"]

            dropped['graph'] = 0


        else:
            # For any other API, raise an error since we don't have the topology
            raise ValueError(f"Unsupported API '{api}' for waste propagation. Only 'search-hotel' and 'reserve-hotel' are supported for hotel benchmark.")
        
        # Get SLO violations for this API
        slo_violation = 0.0
        possible_slo_stems = [
            f'slo_violation_rate_{api}_{service_list[0]}',  # Try with first service (might be aggregated)
            f'slo_violation_rate_{api}_frontend-grpc',      # Try with frontend specifically
            f'slo_violation_rate_{api}',
            f'slo_violation_{api}',
            f'ppm_k6_slo_violation_counter_total_{api}',
            'slo_violation_rate',
            'slo_violation',
            'ppm_k6_slo_violation_counter_total',
            f'slo_violation_counter_{api}',
            f'k6_slo_violation_{api}',
        ]
        
        found_slo_stem = None
        for stem in possible_slo_stems:
            if stem in metric_files:
                found_slo_stem = stem
                break
        
        if found_slo_stem:
            ts, vals = extract_series(metric_files[found_slo_stem])
            if vals:
                slo_violation = float(vals[-1])
                if debug:
                    print(f"[resource-waste] {api} SLO violations: {slo_violation} (from {found_slo_stem})")
            else:
                if debug:
                    print(f"[resource-waste] {api} SLO violations: 0.0 (empty values from {found_slo_stem})")
        else:
            if debug:
                print(f"[resource-waste] {api} SLO violations: 0.0 (no metric found, tried: {possible_slo_stems[:3]}...)")
        
        # Calculate waste percentage for each service
        for service in service_list:
            normalized_service = _normalize_service_name(service)
            if normalized_service not in waste_data:
                waste_data[normalized_service] = {}
            
            if accepted[service] > 0:
                # Waste = (dropped requests + SLO violations) / accepted requests * 100
                waste_pct = (dropped[service] + slo_violation) / accepted[service] * 100
                if debug:
                    print(f"[resource-waste] {normalized_service} {api} waste calculation: ({dropped[service]} + {slo_violation}) / {accepted[service]} * 100 = {waste_pct:.2f}%")
            else:
                waste_pct = 0.0
                if debug:
                    print(f"[resource-waste] {normalized_service} {api} waste: 0.0% (no accepted requests)")
            
            waste_data[normalized_service][api] = waste_pct
    
    if debug:
        print(f"[resource-waste] Final waste data: {waste_data}")
    
    return waste_data


def _get_color(waste_pct: float) -> str:
    """Get color based on waste percentage - red sooner for waste visualization."""
    if waste_pct == 0:
        return '#E8F5E8'  # Very light green
    elif waste_pct < 1:
        return '#C8E6C9'  # Light green  
    elif waste_pct < 2:
        return '#FFF9C4'  # Light yellow
    elif waste_pct < 3:
        return '#FFEB3B'  # Yellow
    elif waste_pct < 4:
        return '#FFC107'  # Amber
    elif waste_pct < 5:
        return '#FF9800'  # Orange
    elif waste_pct < 7:
        return '#FF5722'  # Deep orange
    else:
        return '#F44336'  # Red


def generate_unit_plots(ctx: Dict) -> List[Path]:  # type: ignore
    if ctx.get('type') != 'resource-waste':
        return []
    
    import os
    debug = os.environ.get('PLOT_DEBUG') == '1'
    
    repeat_metric_files: List[Dict[str, dict]] = ctx['repeat_metric_files']
    repeat_rwg_data = ctx.get('repeat_rwg_data', [])
    artifact_dirs = ctx.get('artifact_dirs', [])
    out_dir: Path = ctx['output_dir']
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # Get APIs from context (should be provided by the experiment config)
    apis = ctx.get('apis', [])
    if not apis:
        raise ValueError("No APIs found in unit config. Please specify 'apis' in the experiment configuration.")
    
    # Validate benchmark type and APIs
    bench = ctx.get("bench", None)
    if bench is None:
        raise ValueError("Benchmark type 'bench' not specified in context. Please provide 'bench' in the experiment configuration.")

    # Supported benchmarks and APIs
    if bench == "hotel":
        expected_apis = ["search-hotel", "reserve-hotel"]
        if not all(api in expected_apis for api in apis):
            unsupported_apis = [api for api in apis if api not in expected_apis]
            raise ValueError(f"Unsupported APIs {unsupported_apis} for hotel benchmark. Supported APIs are: {expected_apis}")
    elif bench.lower() == "social":
        expected_apis = ["compose-post", "read-home-timeline", "read-user-timeline"]
        if not all(api in expected_apis for api in apis):
            unsupported_apis = [api for api in apis if api not in expected_apis]
            raise ValueError(f"Unsupported APIs {unsupported_apis} for Social benchmark. Supported APIs are: {expected_apis}")
    else:
        raise ValueError(f"Unsupported benchmark '{bench}'. Supported: 'hotel', 'Social'.")

    if debug:
        print(f"[resource-waste] Processing {len(repeat_metric_files)} repeats for {bench} benchmark")
        print(f"[resource-waste] APIs: {apis}")

    # Calculate waste data for each repeat
    # Calculate waste data for each repeat
    repeat_waste_data = []
    num_repeats = len(repeat_metric_files)
    for i in range(num_repeats):
        repeat_files = repeat_metric_files[i]

        # Try to load Prometheus Data for this repeat
        prom_data = None
        if i < len(artifact_dirs) and PrometheusData:
            try:
                p_path = artifact_dirs[i] / "metrics" / "prometheus.json"
                if p_path.exists():
                     prom_data = PrometheusData.from_json(p_path)
            except Exception:
                pass

        if debug:
            print(f"[resource-waste] Processing repeat {i+1}/{num_repeats}")
        
        # Get RWG data for this repeat if available
        rwg_data = repeat_rwg_data[i] if i < len(repeat_rwg_data) else None
        
        waste_data = _calculate_waste_per_repeat(repeat_files, apis, bench, prom_data=prom_data, rwg_data=rwg_data)
        repeat_waste_data.append(waste_data)
        if debug:
            print(f"[resource-waste] Repeat {i+1} waste data: {waste_data}")

    if not repeat_waste_data:
        if debug:
            print("[resource-waste] No waste data collected")
        return []

    # Aggregate across repeats (mean and std)
    all_services = set()
    all_apis = set()
    for waste_data in repeat_waste_data:
        all_services.update(waste_data.keys())
        for service_data in waste_data.values():
            all_apis.update(service_data.keys())

    if debug:
        print(f"[resource-waste] All services found: {sorted(all_services)}")
        print(f"[resource-waste] All APIs found: {sorted(all_apis)}")

    # Calculate mean and std for each service (summing across APIs)
    aggregated_data = {}
    for service in all_services:
        # Collect total waste for this service across all repeats (summing APIs)
        values = []
        for waste_data in repeat_waste_data:
            service_total = 0.0
            if service in waste_data:
                # Sum waste across all APIs for this service in this repeat
                for api in all_apis:
                    if api in waste_data[service]:
                        service_total += waste_data[service][api]
            values.append(service_total)
        mean_val, std_val = _mean_std(values)
        aggregated_data[service] = {
            'mean': mean_val or 0.0,
            'std': std_val or 0.0
        }
        if debug:
            print(f"[resource-waste] {service} total waste: values={values}, mean={mean_val}, std={std_val}")

    if debug:
        print(f"[resource-waste] Final aggregated data (summed across APIs): {aggregated_data}")

    # Find and print max waste for visibility
    if aggregated_data:
        # Service with Max Mean
        max_mean_service = max(aggregated_data, key=lambda s: aggregated_data[s]['mean'])
        max_mean_val = aggregated_data[max_mean_service]['mean']
        max_mean_std = aggregated_data[max_mean_service]['std']
        
        print(f"[resource-waste-unit] Max Mean Waste: {max_mean_val:.2f} \u00b1 {max_mean_std:.2f}% (Service: {max_mean_service})")
        
        # Service with Max Upper Bound (Mean + Std)
        max_upper_service = max(aggregated_data, key=lambda s: aggregated_data[s]['mean'] + aggregated_data[s]['std'])
        if max_upper_service != max_mean_service:
            max_upper_mean = aggregated_data[max_upper_service]['mean']
            max_upper_std = aggregated_data[max_upper_service]['std']
            print(f"[resource-waste-unit] Max Upper Bound Waste: {max_upper_mean:.2f} \u00b1 {max_upper_std:.2f}% (Service: {max_upper_service})")
    else:
        print("[resource-waste-unit] No aggregated data to find max waste.")

    # Create the visualization
    # Create the visualization
    try:
        from ..plotting_primitives import (  # type: ignore
            SubplotGrid, ACM_QUARTER
        )
    except ImportError:
        try:
            from exec.plots.plotting_primitives import (  # type: ignore
                SubplotGrid, ACM_QUARTER
            )
        except ImportError:
            from plotting_primitives import (  # type: ignore
                SubplotGrid, ACM_QUARTER
            )

    import matplotlib.pyplot as plt  # type: ignore
    from matplotlib.patches import FancyBboxPatch  # type: ignore

    # Use strict ACM_QUARTER style (120pt)
    style = ACM_QUARTER
    grid = SubplotGrid(style, layout="1x1")
    ax = grid.get_ax(0, 0)
    fig = grid.fig # Access underlying figure for patch color
    
    ax.set_facecolor('#FAFAFA')
    fig.patch.set_facecolor('white')
    ax.grid(True, alpha=0.1, color='#BDC3C7', linestyle='-', linewidth=0.5)

    if bench == "hotel":
        positions = {
        'frontend': (1.5, 1.75),
        'search': (3.2, 2.3),
        'reservation': (3.2, 1.75),
        'profile': (3.2, 1.2),
        'user': (3.2, 0.6),
        'geo': (5, 2.6),
        'rate': (5, 2.0)
    }
    
        # Draw service boxes with enhanced styling
        for service, (x, y) in positions.items():
            if service not in aggregated_data:
                continue
            
            # Get total waste (already summed across APIs)
            total_waste = aggregated_data[service]['mean']
            color = _get_color(total_waste)
            
            # Service name with better formatting and size adjustment
            if service == 'reservation':
                service_display = 'Reservation'  # Use full name
                font_size = 8  # Smaller font for longer text
            elif service == 'frontend':
                service_display = 'Frontend'
                font_size = 9
            elif len(service) > 8:
                service_display = service[:8] + '...'
                font_size = 9
            else:
                service_display = service.title()
                font_size = 9
            
            # Draw service box with enhanced styling (fixed size for all)
            # Use consistent box size for all services
            box_width = 0.8
            box_height = 0.35
            
            box = FancyBboxPatch((x-box_width/2, y-box_height/2), box_width, box_height,
                                boxstyle="round,pad=0.03",
                                facecolor=color,
                                edgecolor='#2C3E50',  # Dark blue-gray
                                linewidth=2,
                                alpha=0.9)
            ax.add_patch(box)
            
            # Add subtle shadow effect
            shadow = FancyBboxPatch((x-box_width/2+0.02, y-box_height/2-0.02), box_width, box_height,
                                boxstyle="round,pad=0.03",
                                facecolor='#34495E',
                                alpha=0.2,
                                zorder=-1)
            ax.add_patch(shadow)
            
            # Service name (already calculated above)
            ax.text(x, y+0.08, service_display, 
                ha='center', va='center', fontsize=font_size, fontweight='bold',
                color='#2C3E50')
            
            # Total waste percentage with confidence interval
            mean_waste = aggregated_data[service]['mean']
            std_waste = aggregated_data[service]['std']
            
            if len(repeat_metric_files) > 1:
                # Show confidence interval
                text = f'{mean_waste:.1f}±{std_waste:.1f}%'
            else:
                # Single repeat, no CI
                text = f'{mean_waste:.1f}%'
            
            ax.text(x, y - 0.08, text,
                ha='center', va='center', fontsize=8,
                style='italic', color='#D32F2F', fontweight='bold',
                bbox=dict(boxstyle="round,pad=0.2", facecolor='white', alpha=0.8))
        
        # API color mapping
        api_colors = {
            'search-hotel': '#2196F3',    # Blue
            'reserve-hotel': '#E53935',   # Red
        }
        
        # Draw incoming arrows to frontend with API labels
        frontend_pos = positions['frontend']
        fx, fy = frontend_pos
        
        # Two incoming arrows for different APIs
        api_list = sorted(all_apis)
        if len(api_list) >= 1:
            # First API (search-hotel) - from top-left, stopping at box edge
            api1 = api_list[0]
            start_x1, start_y1 = fx - 0.8, fy + 0.4
            end_x1, end_y1 = fx - 0.4, fy + 0.1  # Stop at box edge, not inside
            
            ax.annotate('', xy=(end_x1, end_y1), xytext=(start_x1, start_y1),
                    arrowprops=dict(
                        arrowstyle='-|>',
                        lw=2.5,
                        color=api_colors.get(api1, '#2196F3'),
                        shrinkA=0,
                        shrinkB=0
                    ))
        
        if len(api_list) >= 2:
            # Second API (reserve-hotel) - from bottom-left, stopping at box edge
            api2 = api_list[1]
            start_x2, start_y2 = fx - 0.8, fy - 0.4
            end_x2, end_y2 = fx - 0.4, fy - 0.1  # Stop at box edge, not inside
            
            ax.annotate('', xy=(end_x2, end_y2), xytext=(start_x2, start_y2),
                    arrowprops=dict(
                        arrowstyle='-|>',
                        lw=2.5,
                        color=api_colors.get(api2, '#E53935'),
                        shrinkA=0,
                        shrinkB=0
                    ))
        
        # Helper function to get box dimensions for a service (fixed size)
        def get_box_dimensions(service):
            return 0.8, 0.35  # Fixed width and height for all services
        
        # Draw internal service connections with API-specific colors
        # Blue connections (search-hotel API path) 
        blue_connections = [
            ('frontend', 'search'),
            ('frontend', 'profile'),
            ('frontend', 'reservation'),  # Blue path to reservation
            ('search', 'geo'),
            ('search', 'rate')
        ]
        
        # Red connections (reserve-hotel API path)
        red_connections = [
            ('frontend', 'reservation'),  # Red path to reservation (second arrow)
            ('frontend', 'user')
        ]
        
        # Draw blue connections (search API) with even spacing from frontend IF API present
        if 'search-hotel' in all_apis:
            for i, (source, target) in enumerate(blue_connections):
                if source in positions and target in positions:
                    x1, y1 = positions[source]
                    x2, y2 = positions[target]
                    
                    # Get box dimensions for connection points
                    source_width, source_height = get_box_dimensions(source)
                    target_width, target_height = get_box_dimensions(target)
                    
                    # Calculate connection points with proper margin to avoid going into rectangles
                    dx = x2 - x1
                    dy = y2 - y1
                    
                    # Source point with even spacing for frontend connections
                    if source == 'frontend':
                        # Use different vertical offsets for even spacing
                        if target == 'search':
                            start_y_offset = 0.12  # Top connection
                        elif target == 'profile':
                            start_y_offset = 0.04  # Mid-top connection  
                        elif target == 'reservation':
                            start_y_offset = -0.04  # Mid-bottom connection (blue arrow)
                        else:
                            start_y_offset = 0
                        
                        start_x = x1 + source_width/2 + 0.05
                        start_y = y1 + start_y_offset
                    else:
                        if abs(dx) > abs(dy):
                            start_x = x1 + (source_width/2 + 0.05 if dx > 0 else -source_width/2 - 0.05)
                            start_y = y1
                        else:
                            start_x = x1
                            start_y = y1 + (source_height/2 + 0.05 if dy > 0 else -source_height/2 - 0.05)
                    
                    # Target point
                    if abs(dx) > abs(dy):
                        end_x = x2 + (-target_width/2 - 0.05 if dx > 0 else target_width/2 + 0.05)
                        end_y = y2
                    else:
                        end_x = x2
                        end_y = y2 + (-target_height/2 - 0.05 if dy > 0 else target_height/2 + 0.05)
                    
                    # Draw blue arrow
                    ax.annotate('', xy=(end_x, end_y), xytext=(start_x, start_y),
                            arrowprops=dict(
                                arrowstyle='-|>',
                                lw=2.5,
                                color='#2196F3',  # Blue
                                alpha=0.8,
                                shrinkA=0,
                                shrinkB=0
                            ))

        # Draw red connections (reserve API) with even spacing from frontend IF API present
        if 'reserve-hotel' in all_apis:
            for source, target in red_connections:
                if source in positions and target in positions:
                    x1, y1 = positions[source]
                    x2, y2 = positions[target]
                    
                    # Get box dimensions for connection points
                    source_width, source_height = get_box_dimensions(source)
                    target_width, target_height = get_box_dimensions(target)
                    
                    # Calculate connection points with proper margin to avoid going into rectangles
                    dx = x2 - x1
                    dy = y2 - y1
                    
                    # Source point with even spacing for frontend connections
                    if source == 'frontend':
                        # Use different vertical offsets for even spacing
                        if target == 'reservation':
                            start_y_offset = -0.12  # Bottom connection (red arrow)
                        elif target == 'user':
                            start_y_offset = -0.16  # Bottom-most connection
                        else:
                            start_y_offset = 0
                        
                        start_x = x1 + source_width/2 + 0.05
                        start_y = y1 + start_y_offset
                    else:
                        if abs(dx) > abs(dy):
                            start_x = x1 + (source_width/2 + 0.05 if dx > 0 else -source_width/2 - 0.05)
                            start_y = y1
                        else:
                            start_x = x1
                            start_y = y1 + (source_height/2 + 0.05 if dy > 0 else -source_height/2 - 0.05)
                    
                    # Target point
                    if abs(dx) > abs(dy):
                        end_x = x2 + (-target_width/2 - 0.05 if dx > 0 else target_width/2 + 0.05)
                        end_y = y2
                    else:
                        end_x = x2
                        end_y = y2 + (-target_height/2 - 0.05 if dy > 0 else target_height/2 + 0.05)
                    
                    # Draw red arrow
                    ax.annotate('', xy=(end_x, end_y), xytext=(start_x, start_y),
                            arrowprops=dict(
                                arrowstyle='-|>',
                                lw=2.5,
                                color='#E53935',  # Red
                                alpha=0.8,
                                shrinkA=0,
                                shrinkB=0
                            ))
        
        # Add legend with full API names (only APIs present)
        legend_x = 0.8
        legend_y = 0.6
        
        # Legend title
        ax.text(legend_x, legend_y + 0.3, 'API:', 
            ha='left', va='center', fontsize=9, fontweight='bold',
            color='#2C3E50')
        
        # Legend entries with full API names (only APIs present)
        api_list = [api for api in ["search-hotel", "reserve-hotel"] if api in all_apis]
        for i, api in enumerate(api_list):
            y_pos = legend_y + 0.1 - (i * 0.15)
            color = api_colors.get(api, '#2C3E50')
            
            # Color indicator (small rectangle)
            legend_box = FancyBboxPatch((legend_x, y_pos - 0.03), 0.15, 0.06,
                                    boxstyle="round,pad=0.01",
                                    facecolor=color,
                                    edgecolor=color,
                                    alpha=0.8)
            ax.add_patch(legend_box)
            
            # Full API name
            full_name = api.replace('-', ' ').title()
            ax.text(legend_x + 0.2, y_pos, full_name,
                ha='left', va='center', fontsize=8,
                color='#2C3E50')
        
        # Set limits and clean layout (more compact)
        ax.set_xlim(0.8, 5.8)
        ax.set_ylim(0.2, 3.0)
        ax.set_aspect('equal')
        ax.axis('off')
        
        # Save plot
        fig_path = out_dir / 'resource_waste_diagram.pdf'
        grid.save(fig_path)
        
        #return [fig_path]
        bar_plot_path = _generate_resource_waste_bar_plot(aggregated_data, out_dir, len(repeat_metric_files))
        
        return [fig_path, bar_plot_path]


        """ # ...existing code for hotel visualization...
        positions = {
            'frontend': (1.5, 1.75),
            'search': (3.2, 2.3),
            'reservation': (3.2, 1.75),
            'profile': (3.2, 1.2),
            'user': (3.2, 0.6),
            'geo': (5, 2.6),
            'rate': (5, 2.0)
        }
        # ...existing code for drawing hotel nodes, arrows, legend, etc...
        # ...existing code...
        # Save plot
        fig_path = out_dir / 'resource_waste_diagram.pdf'
        fig.savefig(fig_path, bbox_inches='tight')
        plt.close(fig)
        return [fig_path] """
    elif bench.lower() == "social":
        # Social benchmark visualization
        positions = {
            "nginx-grpc": (1.2, 1.5),
            "compose": (2.5, 2.1),
            "home": (3.8, 2.1),
            "user": (3.8, 1.0),
            "posts": (5.0, 1.5),
            "graph": (5.0, 2.6)
        }

        # Draw service boxes
        for service, (x, y) in positions.items():
            normalized_service = _normalize_service_name(service)
            if normalized_service not in aggregated_data:
                continue
            total_waste = aggregated_data[normalized_service]['mean']
            color = _get_color(total_waste)
            # Use "Nginx" for normalized_service == "nginx", else .title()
            service_display = "Nginx" if normalized_service == "nginx" else normalized_service.title()
            font_size = 9
            box_width = 0.8
            box_height = 0.35
            box = FancyBboxPatch((x-box_width/2, y-box_height/2), box_width, box_height,
                                boxstyle="round,pad=0.03",
                                facecolor=color,
                                edgecolor='#2C3E50',
                                linewidth=2,
                                alpha=0.9)
            ax.add_patch(box)
            shadow = FancyBboxPatch((x-box_width/2+0.02, y-box_height/2-0.02), box_width, box_height,
                                   boxstyle="round,pad=0.03",
                                   facecolor='#34495E',
                                   alpha=0.2,
                                   zorder=-1)
            ax.add_patch(shadow)
            ax.text(x, y+0.08, service_display,
                   ha='center', va='center', fontsize=font_size, fontweight='bold', color='#2C3E50')
            mean_waste = aggregated_data[normalized_service]['mean']
            std_waste = aggregated_data[normalized_service]['std']
            if len(repeat_metric_files) > 1:
                text = f'{mean_waste:.1f}±{std_waste:.1f}%'
            else:
                text = f'{mean_waste:.1f}%'
            ax.text(x, y - 0.08, text,
                   ha='center', va='center', fontsize=8,
                   style='italic', color='#D32F2F', fontweight='bold',
                   bbox=dict(boxstyle="round,pad=0.2", facecolor='white', alpha=0.8))

        # API color mapping
        api_colors = {
            'compose-post': '#2196F3',
            'read-home-timeline': '#E53935',
            'read-user-timeline': '#43A047',
        }

        # Draw connections for each API (only if API present)
        def draw_arrow(source, target, color, style='solid'):
            if source in positions and target in positions:
                x1, y1 = positions[source]
                x2, y2 = positions[target]
                box_width, box_height = 0.8, 0.35
                dx = x2 - x1
                dy = y2 - y1
                # Start/end points: right edge of source to left edge of target
                start_x = x1 + box_width/2
                start_y = y1
                end_x = x2 - box_width/2
                end_y = y2
                ax.annotate('', xy=(end_x, end_y), xytext=(start_x, start_y),
                           arrowprops=dict(
                               arrowstyle='-|>',
                               lw=2.5,
                               color=color,
                               alpha=0.8,
                               shrinkA=0,
                               shrinkB=0,
                               linestyle=style
                           ))

        # Compose-post API (solid lines)
        if 'compose-post' in all_apis:
            draw_arrow("nginx-grpc", "compose", api_colors['compose-post'], style='solid')
            draw_arrow("compose", "posts", api_colors['compose-post'], style='solid')
            draw_arrow("compose", "home", api_colors['compose-post'], style='solid')
            draw_arrow("compose", "user", api_colors['compose-post'], style='solid')
            draw_arrow("home", "graph", api_colors['compose-post'], style='solid')

        # Read-home-timeline API (dashed lines)
        if 'read-home-timeline' in all_apis:
            draw_arrow("nginx-grpc", "home", api_colors['read-home-timeline'], style='dashed')
            draw_arrow("home", "posts", api_colors['read-home-timeline'], style='dashed')

        # Read-user-timeline API (dotted lines)
        if 'read-user-timeline' in all_apis:
            draw_arrow("nginx-grpc", "user", api_colors['read-user-timeline'], style='dotted')
            draw_arrow("user", "posts", api_colors['read-user-timeline'], style='dotted')

        # Add legend (only for APIs present)
        legend_x = 0.8
        legend_y = 0.6
        ax.text(legend_x, legend_y + 0.3, 'API:',
               ha='left', va='center', fontsize=9, fontweight='bold', color='#2C3E50')
        api_list = [api for api in ["compose-post", "read-home-timeline", "read-user-timeline"] if api in all_apis]
        for i, api in enumerate(api_list):
            y_pos = legend_y + 0.1 - (i * 0.15)
            color = api_colors.get(api, '#2C3E50')
            legend_box = FancyBboxPatch((legend_x, y_pos - 0.03), 0.15, 0.06,
                                       boxstyle="round,pad=0.01",
                                       facecolor=color,
                                       edgecolor=color,
                                       alpha=0.8)
            ax.add_patch(legend_box)
            full_name = api.replace('-', ' ').title()
            ax.text(legend_x + 0.2, y_pos, full_name,
                   ha='left', va='center', fontsize=8, color='#2C3E50')

        ax.set_xlim(0.5, 5.8)
        ax.set_ylim(0.2, 3.0)
        ax.set_aspect('equal')
        ax.axis('off')
        fig_path = out_dir / 'resource_waste_diagram.pdf'
        grid.save(fig_path)
        
        # Generate additional bar plot
        print(f"### Generating resource waste bar plot in {out_dir}")
        bar_plot_path = _generate_resource_waste_bar_plot(aggregated_data, out_dir, len(repeat_metric_files))
        
        return [fig_path, bar_plot_path]


def _calculate_95_ci(values: List[float]) -> Tuple[float, float]:
    """Calculate 95% confidence interval for a list of values.
    Returns (mean, margin_of_error)"""
    import math
    
    if not values:
        return 0.0, 0.0
    
    clean = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not clean:
        return 0.0, 0.0
    
    n = len(clean)
    mean = sum(clean) / n
    
    if n < 2:
        return mean, 0.0
    
    # Calculate standard error
    variance = sum((x - mean) ** 2 for x in clean) / (n - 1)
    std_error = math.sqrt(variance / n)
    
    # 95% CI margin of error (using normal approximation)
    # For small samples, we could use t-distribution, but normal is commonly used
    margin_of_error = 1.96 * std_error
    
    return mean, margin_of_error


def _generate_resource_waste_bar_plot(aggregated_data: Dict, out_dir: Path, num_repeats: int) -> Path:
    """Generate bar plot for resource waste with 95% confidence intervals."""
    import math
    
    # Re-calculate 95% CI from the raw repeat values
    # We need to recalculate because aggregated_data only has mean/std, not raw values
    # For now, convert std to CI approximation: CI ≈ 1.96 * std / sqrt(n)
    services = list(aggregated_data.keys())
    means = []
    ci_margins = []
    
    for service in services:
        mean_val = aggregated_data[service]['mean']
        std_val = aggregated_data[service]['std']
        
        # Convert standard deviation to 95% CI margin of error
        if num_repeats > 1 and std_val > 0:
            ci_margin = 1.96 * std_val / math.sqrt(num_repeats)
        else:
            ci_margin = 0.0
        
        means.append(mean_val)
        ci_margins.append(ci_margin)
    
    # Create bar plot with max-queue styling
    try:
        from ..plotting_primitives import (  # type: ignore
            SubplotGrid, ACM_QUARTER
        )
    except ImportError:
        try:
            from exec.plots.plotting_primitives import (  # type: ignore
                SubplotGrid, ACM_QUARTER
            )
        except ImportError:
            from plotting_primitives import (  # type: ignore
                SubplotGrid, ACM_QUARTER
            )
            
    # Strict ACM_QUARTER style (120pt)
    style = ACM_QUARTER
    grid = SubplotGrid(style, layout="1x1")
    ax = grid.get_ax(0, 0)
    
    # Create single-series bar plot (no grouping needed since we're showing total waste per service)
    x_indices = list(range(len(services)))
    
    # Get colors from matplotlib default cycle
    try:
        colors = [c['color'] for c in plt.rcParams['axes.prop_cycle']]
    except Exception:
        colors = ['#1f77b4']  # fallback blue
    
    bar_color = colors[0] if colors else '#1f77b4'
    
    # Calculate max for y-axis scaling
    max_value = max(means) if means else 0
    max_with_error = max(m + ci for m, ci in zip(means, ci_margins)) if means else 0
    
    # Plot bars with error bars
    ax.bar(x_indices, means, yerr=ci_margins, width=0.6, 
           color=bar_color, edgecolor='black', linewidth=0.6,
           error_kw=dict(capsize=3, elinewidth=1.0))

    # Styling similar to max-queue plots
    ax.set_xticks(x_indices)
    ax.set_xticklabels(services, rotation=30, ha='right')
    
    # Set y-axis limits and ticks dynamically based on max value + error
    import numpy as np
    
    # Calculate dynamic ylim based on max_with_error with 15% padding
    ylim_max = math.ceil(max_with_error * 1.15)
    
    # Ensure it's usually aligned to tens if possible for nicer ticks, but prioritize visibility
    if ylim_max < 10:
         ylim_max = math.ceil(ylim_max)
    else:
         # Round up to nearest 5 or 10
         ylim_max = 5 * math.ceil(ylim_max / 5)

    # Use configure_ax for consistent styling
    grid.configure_ax(ax, ylabel='Resource Waste (%)', ylim=(0, ylim_max))

    # Save bar plot
    bar_fig_path = out_dir / 'resource_waste_bar.pdf'
    grid.save(bar_fig_path)
    
    return bar_fig_path
