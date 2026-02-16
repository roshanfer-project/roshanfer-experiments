"""Unit-level aggregate bar plot for max-queue experiments.

Figure spec (grouped bars):
    * One figure per run unit (single offered load).
    * X-axis: services.
    * For each service: one bar per API (grouped). Bar height = mean(max queue length over time) across repeats for that (service, api).
        - For each repeat, we take the time-series max from the corresponding metric file.
        - We then compute mean and std deviation across repeats; show error bars.
    * If a metric file is missing or empty for (service, api) in a repeat, that repeat contributes 0 for that pair.
    * Metric stems: preferred ordering queue_length_<api>_<service>; accept queue_length_<service>_<api> and queue_length_<service>.

We infer services from context['services'] if provided, else from metric stems.
APIs come from ctx['apis'] (list) else inferred similarly.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
import math
import statistics

SUPPORTED_TYPES = ['max-queue']

try:
    from ..common import extract_series
except Exception:  # pragma: no cover
    from experiments.exec.plots.common import extract_series  # type: ignore


def _normalize_service_name(service: str) -> str:
    """Normalize service names. Convert 'frontend-grpc' to 'frontend'."""
    if service == 'frontend-grpc':
        return 'frontend'
    if service == "nginx-grpc":
        return 'nginx'
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


def _infer_services_and_apis(repeat_metric_files: List[Dict[str, dict]], fallback_services, fallback_apis):
    # If services provided in config, trust their order exactly and do NOT replace with inferred values.
    services_order: List[str] = list(fallback_services or [])
    apis_order: List[str] = list(fallback_apis or [])
    # Only infer APIs if not provided or to supplement missing ones.
    import os
    if os.environ.get('PLOT_DEBUG'):
        print(f"[max-queue][infer] fallback_services={services_order} fallback_apis={apis_order}")
    # Heuristic: APIs contain '-' (e.g., search-hotel), services are simple tokens w/o '-'.
    # Stems may be queue_length_<api>_<service> OR queue_length_<service>_<api>.
    # NEW: Also support hierarchical format: prometheus -> api -> service -> max_queue
    
    hierarchical_apis = set()
    hierarchical_services = set()
    
    # Check for hierarchical format first
    for mf in repeat_metric_files:
        # Check specifically for 'prometheus' key which contains the structure
        if 'prometheus' in mf:
            prom_data = mf['prometheus']
            if isinstance(prom_data, dict):
                for api_key, api_val in prom_data.items():
                    if isinstance(api_val, dict):
                        # api_key is likely an API
                        # usage: check if it has services with max_queue
                        has_max_queue = False
                        for svc_key, svc_val in api_val.items():
                            if isinstance(svc_val, dict) and 'max_queue' in svc_val:
                                has_max_queue = True
                                hierarchical_services.add(_normalize_service_name(svc_key))
                        
                        if has_max_queue:
                             hierarchical_apis.add(api_key)

    if hierarchical_apis:
        for api in sorted(hierarchical_apis):
            if api not in apis_order:
                apis_order.append(api)
        for svc in sorted(hierarchical_services):
            if svc not in services_order:
                services_order.append(svc)
        
        if os.environ.get('PLOT_DEBUG'):
             print(f"[max-queue][infer] Found hierarchical data: apis={hierarchical_apis} services={hierarchical_services}")

    raw_stems = set()
    for mf in repeat_metric_files:
        for stem in mf.keys():
            if stem.startswith('queue_length'):
                raw_stems.add(stem)
    if os.environ.get('PLOT_DEBUG'):
        print(f"[max-queue][infer] stems={sorted(raw_stems)[:20]}")
    for stem in sorted(raw_stems):
        parts = stem.split('_')
        if len(parts) < 4:  # queue_length + at least two tokens
            continue
        token1 = parts[2]
        token2 = parts[3]
        # Determine api/service roles
        api_token = None
        service_token = None
        # Prefer token containing hyphen as API
        if '-' in token1 and '-' not in token2:
            api_token, service_token = token1, token2
        elif '-' in token2 and '-' not in token1:
            api_token, service_token = token2, token1
        elif '-' in token1 and '-' in token2:
            api_token, service_token = token1, token2
        else:
            if os.environ.get('PLOT_DEBUG'):
                print(f"[max-queue][infer] ambiguous stem={stem} => token1={token1} token2={token2}")
            # Fallback: if we have configured services, match one of them
            if services_order:
                if token1 in services_order and token2 not in services_order:
                    service_token, api_token = token1, token2
                elif token2 in services_order and token1 not in services_order:
                    service_token, api_token = token2, token1
                else:
                    # Ambiguous; skip
                    continue
            else:
                # Default fallback: treat second as service
                api_token, service_token = token1, token2
        if os.environ.get('PLOT_DEBUG'):
            print(f"[max-queue][infer] stem={stem} => api={api_token} service={service_token}")
        if service_token and service_token not in services_order:
            # Normalize service name before adding
            normalized_service = _normalize_service_name(service_token)
            if normalized_service not in services_order:
                services_order.append(normalized_service)
        if api_token and api_token not in apis_order:
            apis_order.append(api_token)
    if not services_order:
        # Fallback discovery of services only if none provided
        discovered_services = set()
        for mf in repeat_metric_files:
            for stem in mf.keys():
                if not stem.startswith('queue_length'):
                    continue
                parts = stem.split('_')
                if len(parts) >= 3:
                    # Normalize service name before adding
                    normalized_service = _normalize_service_name(parts[2])
                    discovered_services.add(normalized_service)
        services_order = sorted(discovered_services) if discovered_services else ['unknown']
    if not apis_order:
        apis_order = ['default']
    if os.environ.get('PLOT_DEBUG'):
        print(f"[max-queue][infer] final services={services_order} apis={apis_order}")
    return services_order, apis_order


def generate_unit_plots(ctx: Dict) -> List[Path]:  # type: ignore
    if ctx.get('type') not in SUPPORTED_TYPES:
        return []
    repeat_metric_files: List[Dict[str, dict]] = ctx['repeat_metric_files']
    out_dir: Path = ctx['output_dir']
    out_dir.mkdir(parents=True, exist_ok=True)
    fallback_services = ctx.get('services') or []
    fallback_apis = ctx.get('apis') or []
    services, apis = _infer_services_and_apis(repeat_metric_files, fallback_services, fallback_apis)
    
    import os
    if os.environ.get('PLOT_DEBUG'):
        print(f"[max-queue] services={services} apis={apis}")
    # Build nested data structure: data[service][api] -> list of per-repeat maxima
    data: Dict[str, Dict[str, List[float]]] = {svc: {api: [] for api in apis} for svc in services}
    import os
    for repeat_idx, mf in enumerate(repeat_metric_files):
        if os.environ.get('PLOT_DEBUG'):
            print(f"[max-queue][repeat {repeat_idx}] scan start")
        for svc in services:
            for api in apis:
                # Check for hierarchical data first (prometheus -> api -> service -> max_queue)
                hierarchical_val = None
                
                # Check directly in 'prometheus' key if it exists
                if 'prometheus' in mf:
                    prom_data = mf['prometheus']
                    if api in prom_data:
                        api_data = prom_data[api]
                        # Look for service match (handling normalization)
                        found_svc_stats = None
                        
                        # Try direct match first
                        if svc in api_data:
                             found_svc_stats = api_data[svc]
                        
                        # Try iterating to match normalized names if direct match failed
                        if not found_svc_stats:
                            for raw_svc, stats in api_data.items():
                                if _normalize_service_name(raw_svc) == svc:
                                    found_svc_stats = stats
                                    break
                                    
                        if found_svc_stats and isinstance(found_svc_stats, dict) and 'max_queue' in found_svc_stats:
                            hierarchical_val = float(found_svc_stats['max_queue'])
                
                if hierarchical_val is not None:
                    data[svc][api].append(hierarchical_val)
                    if os.environ.get('PLOT_DEBUG'):
                         print(f"[max-queue][repeat {repeat_idx}] found hier val={hierarchical_val} for svc={svc} api={api}")
                    continue

                # Candidate stems in priority order - include both normalized and original service names
                original_service_variants = [svc]
                # If normalized service is 'frontend' or 'nginx', also try their -grpc forms
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
                    # No metric -> treat as zero for this repeat
                    data[svc][api].append(0.0)
                    if os.environ.get('PLOT_DEBUG'):
                        print(f"[max-queue][repeat {repeat_idx}] missing svc={svc} api={api}")
                    continue
                ts, vals = extract_series(mf[chosen])
                if not vals:
                    data[svc][api].append(0.0)
                    if os.environ.get('PLOT_DEBUG'):
                        print(f"[max-queue][repeat {repeat_idx}] empty svc={svc} api={api} stem={chosen}")
                else:
                    vmax = max(vals)
                    data[svc][api].append(float(vmax))
                    if os.environ.get('PLOT_DEBUG'):
                        print(f"[max-queue][repeat {repeat_idx}] svc={svc} api={api} stem={chosen} max={vmax}")
    if os.environ.get('PLOT_DEBUG'):
        counts = {svc: {api: len(lst) for api, lst in apis_dict.items()} for svc, apis_dict in data.items()}
        print(f"[max-queue][aggregate] repeat_counts={counts}")

    # FILTER: Remove services with all-zero values across all APIs
    non_zero_services = []
    for svc in services:
        has_nonzero = False
        for api in apis:
            vals = data[svc][api]
            # check if any value in the list is > 0
            if any(v > 0 for v in vals):
                has_nonzero = True
                break
        if has_nonzero:
            non_zero_services.append(svc)
    
    if os.environ.get('PLOT_DEBUG'):
        print(f"[max-queue] Filtering services: original={len(services)} kept={len(non_zero_services)} dropped={set(services)-set(non_zero_services)}")
    services = non_zero_services

    if not services:
        print("[max-queue] All services have zero max queue length; skipping plot.")
        return []

    # Prepare plotting arrays (single bar per service)
    try:
        from ..plotting_primitives import (
            SubplotGrid, ACM_COMPACT_HALF, ACM_QUARTER, plot_grouped_bars
        )
    except ImportError:
        try:
            from exec.plots.plotting_primitives import (  # type: ignore
                SubplotGrid, ACM_COMPACT_HALF, ACM_QUARTER, plot_grouped_bars
            )
        except ImportError:
            from plotting_primitives import (  # type: ignore
                SubplotGrid, ACM_COMPACT_HALF, ACM_QUARTER, plot_grouped_bars
            )

    # Strict width logic: 1 API -> 120pt, >1 API -> 240pt
    style = ACM_QUARTER if len(apis) == 1 else ACM_COMPACT_HALF
    
    grid = SubplotGrid(style, layout="1x1")
    ax = grid.get_ax(0, 0)

    # Prepare data for plot_grouped_bars
    # bar_groups: List of (label, heights, errors)
    bar_groups = []
    
    for api_idx, api in enumerate(apis):
        means = []
        stds = []
        for svc in services:
            m, s = _mean_std(data[svc][api])
            # Handle None/Zero values safely
            if m is None: m = 0.0
            if s is None: s = 0.0
            
            means.append(m)
            stds.append(s)
            
        bar_groups.append((api, means, stds))

    # Plot grouped bars
    plot_grouped_bars(ax, list(range(len(services))), bar_groups, style=style)
    
    # Configure Axes
    # Use explicit x-ticks for services
    ax.set_xticks(list(range(len(services))))
    ax.set_xticklabels(services, rotation=30, ha='right')
    
    # Determine Y-limit
    # Calculate max value + error for scaling
    max_val = 0.0
    for _, means, stds in bar_groups:
        for m, s in zip(means, stds):
            top = m + (s if s else 0)
            if top > max_val:
                max_val = top
    
    ylim_max = 1.2 * max_val if max_val > 0 else 10.0
    # For log scale, start at something small but positive, e.g. 0.9 or 1
    ylim_min = 0.9
    
    # Configure common axis properties
    grid.configure_ax(ax, ylabel='Max Concurrency (req)', ylim=(ylim_min, ylim_max), log_y=True)
    
    # Add legend if multiple APIs
    if len(apis) > 1:
        grid.add_shared_legend(position="top")

    fig_path = out_dir / 'max_queue_bar.pdf'
    grid.save(fig_path)
    return [fig_path]
