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

from ..data_loader import extract_series


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
                        has_queue = False
                        for svc_key, svc_val in api_val.items():
                            if isinstance(svc_val, dict) and (
                                'max_queue' in svc_val or 'avg_queue' in svc_val
                            ):
                                has_queue = True
                                hierarchical_services.add(_normalize_service_name(svc_key))
                        
                        if has_queue:
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


def _find_svc_stats(prom_api_data: dict, svc: str):
    if svc in prom_api_data:
        return prom_api_data[svc]
    for raw_svc, stats in prom_api_data.items():
        if _normalize_service_name(raw_svc) == svc:
            return stats
    return None


def _read_max_avg_for_repeat(mf: dict, svc: str, api: str) -> Tuple[float, float]:
    """Return (max_queue_sample, avg_queue_sample) for one repeat; legacy stems use max/mean of series."""
    if 'prometheus' in mf:
        prom_data = mf['prometheus']
        if api in prom_data and isinstance(prom_data[api], dict):
            found = _find_svc_stats(prom_data[api], svc)
            if found and isinstance(found, dict):
                mv = float(found['max_queue']) if 'max_queue' in found else None
                av = float(found['avg_queue']) if 'avg_queue' in found else None
                if mv is not None or av is not None:
                    return (mv if mv is not None else 0.0, av if av is not None else 0.0)

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
            f'queue_length_{service_variant}',
        ])

    for st in stem_candidates:
        if st not in mf:
            continue
        ts, vals = extract_series(mf[st])
        if not vals:
            return (0.0, 0.0)
        return (float(max(vals)), float(sum(vals) / len(vals)))

    return (0.0, 0.0)


def _collect_max_avg_data(
    repeat_metric_files: List[Dict[str, dict]],
    services: List[str],
    apis: List[str],
) -> Tuple[Dict[str, Dict[str, List[float]]], Dict[str, Dict[str, List[float]]]]:
    data_max: Dict[str, Dict[str, List[float]]] = {svc: {api: [] for api in apis} for svc in services}
    data_avg: Dict[str, Dict[str, List[float]]] = {svc: {api: [] for api in apis} for svc in services}
    import os

    for repeat_idx, mf in enumerate(repeat_metric_files):
        if os.environ.get('PLOT_DEBUG'):
            print(f"[max-queue][repeat {repeat_idx}] scan start")
        for svc in services:
            for api in apis:
                max_v, avg_v = _read_max_avg_for_repeat(mf, svc, api)
                data_max[svc][api].append(max_v)
                data_avg[svc][api].append(avg_v)
                if os.environ.get('PLOT_DEBUG'):
                    print(f"[max-queue][repeat {repeat_idx}] svc={svc} api={api} max={max_v} avg={avg_v}")
    return data_max, data_avg


def _union_nonzero_services(
    services: List[str],
    apis: List[str],
    data_max: Dict[str, Dict[str, List[float]]],
    data_avg: Dict[str, Dict[str, List[float]]],
) -> List[str]:
    out = []
    for svc in services:
        for api in apis:
            if any(v > 0 for v in data_max[svc][api]) or any(v > 0 for v in data_avg[svc][api]):
                out.append(svc)
                break
    return out


def _save_queue_bar_figure(
    data: Dict[str, Dict[str, List[float]]],
    services: List[str],
    apis: List[str],
    out_path: Path,
    ylabel: str,
    log_y: bool,
) -> None:
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

    style = ACM_QUARTER if len(apis) == 1 else ACM_COMPACT_HALF
    grid = SubplotGrid(style, layout="1x1")
    ax = grid.get_ax(0, 0)

    bar_groups = []
    for api in apis:
        means = []
        stds = []
        for svc in services:
            m, s = _mean_std(data[svc][api])
            if m is None:
                m = 0.0
            if s is None:
                s = 0.0
            means.append(m)
            stds.append(s)
        bar_groups.append((api, means, stds))

    plot_grouped_bars(ax, list(range(len(services))), bar_groups, style=style)
    ax.set_xticks(list(range(len(services))))
    ax.set_xticklabels(services, rotation=30, ha='right')

    max_val = 0.0
    for _, means, stds in bar_groups:
        for m, s in zip(means, stds):
            top = m + (s if s else 0)
            if top > max_val:
                max_val = top

    if log_y:
        ylim_max = 1.2 * max_val if max_val > 0 else 10.0
        ylim_min = 0.9
        grid.configure_ax(ax, ylabel=ylabel, ylim=(ylim_min, ylim_max), log_y=True)
    else:
        ylim_max = 1.2 * max_val if max_val > 0 else 1.0
        ylim_min = 0.0
        grid.configure_ax(ax, ylabel=ylabel, ylim=(ylim_min, ylim_max), log_y=False)

    if len(apis) > 1:
        grid.add_shared_legend(position="top")

    grid.save(out_path)


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

    data_max, data_avg = _collect_max_avg_data(repeat_metric_files, services, apis)

    if os.environ.get('PLOT_DEBUG'):
        counts = {svc: {api: len(lst) for api, lst in apis_dict.items()} for svc, apis_dict in data_max.items()}
        print(f"[max-queue][aggregate] repeat_counts={counts}")

    services_u = _union_nonzero_services(services, apis, data_max, data_avg)
    if os.environ.get('PLOT_DEBUG'):
        print(
            f"[max-queue] Filtering services (union max|avg): original={len(services)} "
            f"kept={len(services_u)} dropped={set(services)-set(services_u)}"
        )

    if not services_u:
        print("[max-queue] All services have zero max and avg queue; skipping plots.")
        return []

    paths: List[Path] = []
    max_path = out_dir / 'max_queue_bar.pdf'
    _save_queue_bar_figure(data_max, services_u, apis, max_path, 'Max Queue (req)', log_y=True)
    paths.append(max_path)

    avg_path = out_dir / 'avg_queue_bar.pdf'
    _save_queue_bar_figure(data_avg, services_u, apis, avg_path, 'Avg Queue (req)', log_y=True)
    paths.append(avg_path)

    return paths
