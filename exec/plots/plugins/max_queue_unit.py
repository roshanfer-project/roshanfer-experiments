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
    # Prepare plotting arrays (single bar per service)
    try:
        from experiments.canvas import canvas  # type: ignore
    except Exception:
        from canvas import canvas  # type: ignore
    import matplotlib.pyplot as plt  # type: ignore
    fig, ax = canvas.create_canvas(width_in_inches= max(3.33, 0.5 * len(services)), aspect_ratio=0.6,
                                   font_size=14, legend_size=12, line_width=1.5, marker_size=4)
    # Grouped bar plotting
    n_services = len(services)
    n_apis = len(apis)
    x_indices = list(range(n_services))
    total_group_width = 0.8
    if n_apis > 0:
        bar_width = total_group_width / n_apis
    else:
        bar_width = total_group_width
    try:
        import matplotlib.pyplot as plt  # type: ignore
        colors = [c['color'] for c in plt.rcParams['axes.prop_cycle']]
    except Exception:
        colors = []
    api_colors = {api: colors[i % len(colors)] if colors else None for i, api in enumerate(apis)}
    max_height = 0.0
    max_error = 0.0
    for api_idx, api in enumerate(apis):
        offsets = [x - total_group_width/2 + api_idx * bar_width + bar_width/2 for x in x_indices]
        means = []
        stds = []
        for svc in services:
            m, s = _mean_std(data[svc][api])
            if m is None:
                m = 0.0
                s = 0.0
            means.append(m)
            stds.append(0.0001 if (s is None or s == 0) else s)
            if m > max_height:
                max_height = m
            if m + (s if s is not None else 0) > max_error:
                max_error = m + (s if s is not None else 0)
        ax.bar(offsets, means, yerr=stds, width=bar_width*0.9, label=api,
               color=api_colors.get(api), edgecolor='black', linewidth=0.6,
               error_kw=dict(capsize=3, elinewidth=1.0, capthick=0.8))
        for ox, m in zip(offsets, means):
            ax.text(ox, m + (0.02 * (max_error if max_error > 0 else 1.0)), f"{m:.0f}", ha='center', va='bottom', fontsize=9)
    ax.set_xticks(x_indices)
    ax.set_xticklabels(services, rotation=30, ha='right')
    ylab = ax.set_ylabel('Max Concurrency (req)', labelpad=20)
    # Move the y-label a little lower (default is y=0.5, try y=0.42)
    ylab.set_position((ylab.get_position()[0], 0.42))
    ax.set_xlabel('Service')
    ax.yaxis.grid(True, alpha=0.3)
    # Set y-limit to 1.2x (max value + error bar), or 1 if all zeros
    ylim_max = 1.2 * max_error if max_error > 0 else 1.0
    ax.set_ylim(0, ylim_max)
    if n_apis > 1:
        try:
            handles, labels = ax.get_legend_handles_labels()
            if handles:
                fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(0.5, 1.04),
                           ncol=max(1, len(labels)), frameon=True, fancybox=True,
                           framealpha=0.85, edgecolor='#bbbbbb')
                fig.subplots_adjust(top=0.84)
        except Exception:
            pass
    fig_path = out_dir / 'max_queue_bar.pdf'
    fig.savefig(fig_path, bbox_inches='tight')
    plt.close(fig)
    return [fig_path]
