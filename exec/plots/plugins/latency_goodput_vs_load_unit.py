"""Unit-level aggregation plugin for latency-and-goodput-vs-load experiments.

REWRITTEN to use new RWG data loading and plotting architecture.

Generates per run-unit (load point) PDF figures combining repeats:
  * latency_vs_load_unit.pdf : P95 (per API) with error bars across repeats
  * goodput_vs_load_unit.pdf : goodput (per API) with error bars across repeats

Context provided via aggregate key:
  {
    'type': str,
    'experiment_name': str,
    'run_unit_name': str,
    'group_name': str,
    'artifact_dirs': [Path per repeat],
    'repeat_metric_files': [ { stem->json } per repeat ] - IGNORED (legacy),
    'output_dir': Path (unit level),
    'load_value': int load value (base load),
    'apis': List[str],
    'slos': { api: ms }
  }
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional
import os

# Import new RWG data loading and plotting
try:
    from ..data_loader import load_unit_data
    from ..aggregation import aggregate_by_api
    from ..plotting_primitives import (
        SubplotGrid, ACM_COMPACT_HALF, plot_line
    )
except ImportError:
    try:
        from exec.plots.data_loader import load_unit_data  # type: ignore
        from exec.plots.aggregation import aggregate_by_api  # type: ignore
        from exec.plots.plotting_primitives import (  # type: ignore
            SubplotGrid, ACM_COMPACT_HALF, plot_line
        )
    except ImportError:
        from data_loader import load_unit_data  # type: ignore
        from aggregation import aggregate_by_api  # type: ignore
        from plotting_primitives import (  # type: ignore
            SubplotGrid, ACM_COMPACT_HALF, plot_line
        )

SUPPORTED_TYPES = ['latency-and-goodput-vs-load']


def _lookup_slo(slos: Optional[Dict[str, float]], api: str) -> float:
    """Return SLO ms for api using flexible key matching.
    
    Tries variants: exact, hyphen/underscore swapped, lowercase, stripped _all.
    Defaults to 60.0ms if not found.
    """
    if not slos or not api:
        return 60.0
    
    candidates = [api]
    if api.endswith('_all'):
        candidates.append(api[:-4])
    if '-' in api:
        candidates.append(api.replace('-', '_'))
    if '_' in api:
        candidates.append(api.replace('_', '-'))
    candidates.extend({c.lower() for c in candidates})
    
    seen = []
    ordered = []
    for c in candidates:
        if c not in seen:
            seen.append(c)
            ordered.append(c)
    
    for key in ordered:
        if key in slos:
            return slos[key]
    
    return 60.0  # Default SLO


def generate_unit_plots(ctx: Dict) -> List[Path]:
    """Generate unit-level aggregated plots using RWG data.
    
    Loads overall-{api}.json from each repeat, aggregates metrics,
    and plots latency and goodput with error bars.
    """
    apis: List[str] = ctx.get('apis') or []
    artifact_dirs: List[Path] = ctx.get('artifact_dirs') or []
    slos: Optional[Dict[str, float]] = ctx.get('slos')
    load_val = ctx.get('load_value')
    out_dir: Path = ctx['output_dir']
    
    out_dir.mkdir(parents=True, exist_ok=True)
    produced: List[Path] = []
    
    if not apis or not artifact_dirs or load_val is None:
        if os.environ.get('PLOT_DEBUG'):
            print(f"[latency_goodput_vs_load_unit] Missing required data: apis={apis}, dirs={len(artifact_dirs)}, load={load_val}")
        return produced
    
    # X-axis value: load * 10 / 1000 = KRPS
    x_value = (load_val * 10) / 1000.0
    
    # Load data from all repeats using new data loader
    try:
        # Aggregate across all artifact directories
        all_repeats = []
        for artifact_dir in artifact_dirs:
            repeat_data = load_unit_data(artifact_dir.parent)  # Parent contains repeat_XXX dirs
            if repeat_data:
                # We want just this specific repeat
                all_repeats.extend(repeat_data)
        
        if not all_repeats:
            if os.environ.get('PLOT_DEBUG'):
                print(f"[latency_goodput_vs_load_unit] No repeat data loaded")
            return produced
        
        # Aggregate metrics by API
        aggregated = aggregate_by_api(all_repeats)
        
    except Exception as e:
        if os.environ.get('PLOT_DEBUG'):
            print(f"[latency_goodput_vs_load_unit] Error loading data: {e}")
            import traceback
            traceback.print_exc()
        return produced
    
    # Use ACM compact style
    style = ACM_COMPACT_HALF
    
    # === LATENCY FIGURE ===
    layout = f"row-{len(apis)}" if len(apis) > 1 else "1x1"
    grid_lat = SubplotGrid(style, layout=layout)
    
    for idx, api in enumerate(apis):
        ax = grid_lat.get_ax(0, idx)
        
        if api not in aggregated:
            continue
        
        api_metrics = aggregated[api]
        
        # Extract P95 latency with CI
        p95_mean, p95_std, p95_ci = api_metrics.get('p95_latency', (None, None, None))
        
        if p95_mean is not None:
            # Plot with error bars (use CI, not std)
            plot_line(
                ax, [x_value], [p95_mean], 
                yerr=[p95_ci if p95_ci is not None else 0.0],
                label='P95',
                style=style,
                color_idx=1,
                show_markers=True  # Sparse data, markers help
            )
        
        # Add SLO line
        slo_val = _lookup_slo(slos, api)
        ax.axhline(y=slo_val, color='r', linestyle='--', 
                  label='SLO', linewidth=style.line_width)
        
        # Configure axis
        display_api = api.replace('_all', '') if api.endswith('_all') else api
        ax.set_title(display_api, fontsize=style.title_size)
        ax.set_xticks([x_value])
        ax.set_xticklabels([f'{x_value:.1f}'])
        ax.set_yscale('log')
        ax.set_ylim(1, 500)
        ax.grid(True, alpha=0.3)
    
    # Configure labels
    grid_lat.configure_labels(
        pattern="leftmost_y_bottom_x",
        xlabel="Offered Load (KRPS)",
        ylabel="P95 Latency (ms)"
    )
    
    # Add legend
    grid_lat.add_shared_legend(position="top")
    
    # Save
    lat_path = out_dir / 'latency_vs_load_unit.pdf'
    grid_lat.save(lat_path)
    produced.append(lat_path)
    
    # === GOODPUT FIGURE ===
    grid_gp = SubplotGrid(style, layout=layout)
    
    for idx, api in enumerate(apis):
        ax = grid_gp.get_ax(0, idx)
        
        if api not in aggregated:
            continue
        
        api_metrics = aggregated[api]
        
        # Extract goodput with CI (convert to KRPS)
        gp_mean, gp_std, gp_ci = api_metrics.get('goodput', (None, None, None))
        
        if gp_mean is not None:
            # Convert to KRPS
            gp_mean_krps = gp_mean / 1000.0
            gp_ci_krps = (gp_ci if gp_ci is not None else 0.0) / 1000.0
            
            # Plot with error bars
            plot_line(
                ax, [x_value], [gp_mean_krps],
                yerr=[gp_ci_krps],
                label='Goodput',
                style=style,
                color_idx=0,
                show_markers=True  # Sparse data, markers help
            )
        
        # Configure axis
        display_api = api.replace('_all', '') if api.endswith('_all') else api
        ax.set_title(display_api, fontsize=style.title_size)
        ax.set_xticks([x_value])
        ax.set_xticklabels([f'{x_value:.1f}'])
        ax.grid(True, alpha=0.3)
    
    # Configure labels
    grid_gp.configure_labels(
        pattern="leftmost_y_bottom_x",
        xlabel="Offered Load (KRPS)",
        ylabel="Goodput (KRPS)"
    )
    
    # Add legend
    grid_gp.add_shared_legend(position="top")
    
    # Save
    goodput_path = out_dir / 'goodput_vs_load_unit.pdf'
    grid_gp.save(goodput_path)
    produced.append(goodput_path)
    
    if os.environ.get('PLOT_DEBUG'):
        print(f"[latency_goodput_vs_load_unit] Generated {len(produced)} plots: {[p.name for p in produced]}")
    
    return produced
