"""Plugin-based per-repeat plot generation orchestrator.

Responsibilities:
    * Locate run_summary.jsonl for a given experiment index (append-only storage)
    * Build a context dict for each successful repeat
    * Discover plotting plugins under experiments.exec.plots.plugins
    * Dispatch to plugin functions registered for the experiment type (or generic ones)

Why plugin architecture?
    * Keeps this runner *abstract* and free of experiment-type specific logic
    * Adding a new figure only requires creating a module in plugins/ with a
        ``generate_repeat_plots(ctx)`` function (or a PLUGIN_TYPES mapping)
    * Enables future aggregation / multi-repeat plugins without modifying core

Plugin contract (repeat-level, current scope):
    generate_repeat_plots(ctx: Dict) -> List[pathlib.Path]
        ctx keys provided:
            type, experiment_name, run_unit_name, group_name, repeat_index,
            artifact_dir, metrics_dir, output_dir, metric_files (stem->json), record, slos, bench
            
        Data sources:
            * NEW (RWG-based plugins): Use artifact_dir to load:
              - output/overall-{api}.json (via data_loader.py)
              - output/realtime-{api}.csv (via data_loader.py)
            * LEGACY (Prometheus plugins): Use metric_files dict
            
        Plugins can use either data source based on their implementation.

Discovery rules:
    * If module defines PLUGIN_TYPES = { 'exp-type': func, ... } each func is registered for that type.
    * Else if module has generate_repeat_plots and optional SUPPORTED_TYPES iterable -> register for those types.
    * If no SUPPORTED_TYPES -> module applies to all types (registered under '*').
    * Functions may also carry attribute ``plot_experiment_type`` to target one type.

This file MUST remain lightweight; no matplotlib / pandas dependencies here.
All heavy plotting lives inside plugins.
"""
from __future__ import annotations

import argparse
import importlib
import inspect
import json
import pkgutil
import os
import traceback
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

PlotFunc = Callable[[Dict], List[Path]]
AggregateFunc = Callable[[Dict], List[Path]]  # unit-level
ExpAggregateFunc = Callable[[Dict], List[Path]]  # experiment-level (multi-load)


def _print_plugin_load_hint() -> None:
    """Print a helpful message when no plugins load (often due to missing pandas/matplotlib)."""
    print('No plot plugins discovered; nothing to do.')
    try:
        from exec.plots.data_loader import load_repeat_data  # noqa: F401
    except ModuleNotFoundError as e:
        if 'pandas' in str(e) or 'matplotlib' in str(e):
            print('Hint: Plot plugins require pandas and matplotlib. Install with: pip install pandas matplotlib')
    except Exception:
        pass


def _discover_plugins() -> Dict[str, List[PlotFunc]]:
    """Return mapping: experiment_type -> list[plot functions]. Generic ('*') handlers included separately.

    Discovery order (later discoveries append, preserving earlier ones):
      * PLUGIN_TYPES mapping
      * generate_repeat_plots (+ SUPPORTED_TYPES or generic)
      * functions with attribute plot_experiment_type
    """
    plugins_pkg = None
    tried: List[str] = []
    attempted = []
    for mod_base in ("experiments.exec.plots.plugins", "exec.plots.plugins"):
        tried.append(mod_base)
        try:  # try each base until one succeeds
            plugins_pkg = importlib.import_module(mod_base)  # type: ignore
            attempted.append(mod_base + "=OK")
            break
        except Exception:
            attempted.append(mod_base + "=FAIL")
            continue
    if plugins_pkg is None:
        if os.environ.get('PLOT_DEBUG') == '1':
            print('[plot_runner] plugin package not found; attempts:', ', '.join(attempted))
        return {}
    registry: Dict[str, List[PlotFunc]] = {}
    for mod_info in pkgutil.iter_modules(plugins_pkg.__path__):  # type: ignore[attr-defined]
        mod_name = f"{plugins_pkg.__name__}.{mod_info.name}"
        try:
            module = importlib.import_module(mod_name)
        except Exception:
            if os.environ.get('PLOT_DEBUG') == '1':
                print(f"[plot_runner] failed to import plugin module {mod_name}")
            continue
        # Mapping definition
        mapping = getattr(module, 'PLUGIN_TYPES', None)
        if isinstance(mapping, dict):
            for etype, fn in mapping.items():
                if callable(fn):
                    registry.setdefault(etype, []).append(fn)
        # Single function export path
        if hasattr(module, 'generate_repeat_plots') and callable(getattr(module, 'generate_repeat_plots')):
            fn = getattr(module, 'generate_repeat_plots')
            supported: Optional[Iterable[str]] = getattr(module, 'SUPPORTED_TYPES', None)
            if supported:
                for et in supported:
                    registry.setdefault(et, []).append(fn)
            else:  # generic
                registry.setdefault('*', []).append(fn)
        # Annotated functions
        for _name, obj in inspect.getmembers(module, inspect.isfunction):
            et = getattr(obj, 'plot_experiment_type', None)
            if et and callable(obj):
                registry.setdefault(et, []).append(obj)
    if os.environ.get('PLOT_DEBUG') == '1':
        print('[plot_runner] discovered types:', list(registry.keys()))
    return registry


def _discover_aggregate_plugins() -> Dict[str, List[AggregateFunc]]:
    """Discover unit-level aggregate plugins with a generate_unit_plots(ctx) function.

    Mirrors repeat plugin discovery but looks for symbol generate_unit_plots.
    """
    plugins_pkg = None
    for mod_base in ("experiments.exec.plots.plugins", "exec.plots.plugins"):
        try:
            plugins_pkg = importlib.import_module(mod_base)  # type: ignore
            break
        except Exception:
            continue
    if plugins_pkg is None:
        return {}
    registry: Dict[str, List[AggregateFunc]] = {}
    for mod_info in pkgutil.iter_modules(plugins_pkg.__path__):  # type: ignore[attr-defined]
        mod_name = f"{plugins_pkg.__name__}.{mod_info.name}"
        try:
            module = importlib.import_module(mod_name)
        except Exception:
            continue
        if hasattr(module, 'generate_unit_plots') and callable(getattr(module, 'generate_unit_plots')):
            fn = getattr(module, 'generate_unit_plots')
            supported: Optional[Iterable[str]] = getattr(module, 'SUPPORTED_TYPES', None)
            if supported:
                for et in supported:
                    registry.setdefault(et, []).append(fn)
            else:
                registry.setdefault('*', []).append(fn)
    return registry


def _discover_experiment_aggregate_plugins() -> Dict[str, List[ExpAggregateFunc]]:
    """Discover experiment-level aggregate plugins with generate_experiment_plots(ctx)."""
    plugins_pkg = None
    for mod_base in ("experiments.exec.plots.plugins", "exec.plots.plugins"):
        try:
            plugins_pkg = importlib.import_module(mod_base)  # type: ignore
            break
        except Exception:
            continue
    if plugins_pkg is None:
        return {}
    registry: Dict[str, List[ExpAggregateFunc]] = {}
    for mod_info in pkgutil.iter_modules(plugins_pkg.__path__):  # type: ignore[attr-defined]
        mod_name = f"{plugins_pkg.__name__}.{mod_info.name}"
        try:
            module = importlib.import_module(mod_name)
        except Exception:
            continue
        if hasattr(module, 'generate_experiment_plots') and callable(getattr(module, 'generate_experiment_plots')):
            fn = getattr(module, 'generate_experiment_plots')
            supported: Optional[Iterable[str]] = getattr(module, 'SUPPORTED_TYPES', None)
            if supported:
                for et in supported:
                    registry.setdefault(et, []).append(fn)
            else:
                registry.setdefault('*', []).append(fn)
    return registry


def _load_summary(run_root: Path) -> List[Dict]:
    summary_path = run_root / 'run_summary.jsonl'
    if not summary_path.exists():
        raise FileNotFoundError(f'Missing run summary: {summary_path}')
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
    """Load legacy Prometheus metric files (for backward compatibility).
    
    NOTE: New RWG-based plugins should use artifact_dir/output/ instead:
        - output/overall-{api}.json
        - output/realtime-{api}.csv
    
    See data_loader.py for the new data loading utilities.
    """
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


def generate_for_index(experiment_index: str, experiments_root: Path, output_root: Path, experiment_name_filter: Optional[str] = None, config_file: Optional[str] = None) -> None:
    try:
        with open(config_file, 'r') as cf:
            config = json.load(cf)
    except Exception as e:
        print(f"Error loading config file {config_file}: {e}")
        return

    run_root = experiments_root / f'exp-{experiment_index}'
    records = _load_summary(run_root)
    if not records:
        print(f'No successful repeats found under {run_root}')
        return
    
    # Filter records by experiment name if specified
    if experiment_name_filter:
        original_count = len(records)
        records = [r for r in records if r.get('experiment_name') == experiment_name_filter]
        if not records:
            print(f'No records found for experiment name: {experiment_name_filter}')
            return
        print(f'Filtered to {len(records)} records (from {original_count}) for experiment: {experiment_name_filter}')
    
    registry = _discover_plugins()
    aggregate_registry = _discover_aggregate_plugins()
    exp_aggregate_registry = _discover_experiment_aggregate_plugins()
    if os.environ.get('PLOT_DEBUG') == '1':
        if exp_aggregate_registry:
            print('[plot_runner] discovered experiment-level aggregate types:', list(exp_aggregate_registry.keys()))
        if aggregate_registry:
            print('[plot_runner] discovered unit-level aggregate types:', list(aggregate_registry.keys()))
    if not registry:
        _print_plugin_load_hint()
        return
    generic_funcs = registry.get('*', [])
    print(f'Discovered plugin types: {sorted(t for t in registry.keys() if t != "*")}, generic={len(generic_funcs)}')
    # Group records for potential aggregate (unit-level) plots keyed by (experiment_name, run_unit_name)
    grouped: Dict[tuple, List[Dict]] = {}
    experiment_grouped: Dict[str, Dict[str, dict]] = {}
    for rec in records:
        key = (rec.get('experiment_name'), rec.get('run_unit_name'))
        grouped.setdefault(key, []).append(rec)
        # Build experiment-level container (all run units under experiment)
        exp_name = rec.get('experiment_name')
        run_unit_name = rec.get('run_unit_name')
        eg = experiment_grouped.setdefault(exp_name, {})
        eg.setdefault(run_unit_name, {'records': []})['records'].append(rec)
    for rec in records:
        exp_type = rec.get('type')
        funcs = registry.get(exp_type, []) + generic_funcs
        if not funcs:
            # Suppress skip if experiment-level OR unit-level aggregate plugin exists
            has_exp_agg = exp_type in exp_aggregate_registry or '*' in exp_aggregate_registry
            has_unit_agg = exp_type in aggregate_registry or '*' in aggregate_registry
            if os.environ.get('PLOT_DEBUG') == '1' and not (has_exp_agg or has_unit_agg):
                print(f"[plot_runner] skip type '{exp_type}' (no per-repeat plugin)")
            continue
        artifact_dir = Path(rec.get('artifact_dir', '.'))
        repeat_index = rec.get('repeat_index')
        metrics_dir = artifact_dir / 'metrics'
        
        # Load legacy Prometheus metrics (for backward compatibility)
        metric_files = _load_metric_files(metrics_dir)
        
        # Check for new RWG data sources
        output_dir_data = artifact_dir / 'output'
        has_rwg_data = output_dir_data.exists() and any(output_dir_data.glob('overall-*.json'))
        has_cpu_data = (artifact_dir / 'raw' / 'cpu_metrics.csv').is_file()
        
        # Load Prometheus data via data_loader
        # We check both legacy and new data availability
        prometheus_data = None
        try:
            from exec.plots.data_loader import load_repeat_data
            rwg_data = load_repeat_data(artifact_dir)
            if rwg_data:
                # Extract Prometheus data (it's the same for all APIs in the repeat)
                # Just take the first one
                first_api = next(iter(rwg_data))
                _, _, prometheus_data = rwg_data[first_api]
        except Exception:
            if os.environ.get('PLOT_DEBUG') == '1':
                traceback.print_exc()
        
        # Skip if no data available (neither legacy nor new nor prometheus nor cpu metrics)
        if not metric_files and not has_rwg_data and not prometheus_data and not has_cpu_data:
            if os.environ.get('PLOT_DEBUG') == '1':
                print(f"[plot_runner] skip repeat {repeat_index}: no data (checked metrics_dir, output/, raw/cpu_metrics.csv)")
            continue
        
        # rwg_data might be None if load failed or empty
        rwg_data = rwg_data if 'rwg_data' in locals() and rwg_data else {}
        
        if os.environ.get('PLOT_DEBUG') == '1':
            data_sources = []
            if metric_files:
                data_sources.append(f"legacy({len(metric_files)} files)")
            if has_rwg_data:
                rwg_files = list(output_dir_data.glob('overall-*.json'))
                data_sources.append(f"RWG({len(rwg_files)} APIs)")
            if prometheus_data:
                 data_sources.append("PrometheusData")
            if has_cpu_data:
                data_sources.append("cpu_metrics")
            print(f"[plot_runner] repeat {repeat_index} data: {', '.join(data_sources)}")
        
        out_dir = output_root / rec.get('experiment_name') / rec.get('group_name', rec.get('run_unit_name','')) / f'repeat_{int(repeat_index):03d}'
        # Inject SLOs from record/config if present
        slos = config.get('slos')
        if os.environ.get('PLOT_DEBUG') == '1':
            print(f"[plot_runner] using SLOs from config: {list(slos.keys()) if slos else None}")
        ctx = {
            'type': exp_type,
            'experiment_name': rec.get('experiment_name'),
            'run_unit_name': rec.get('run_unit_name'),
            'group_name': rec.get('group_name', rec.get('run_unit_name')),
            'repeat_index': repeat_index,
            'artifact_dir': artifact_dir,
            'metrics_dir': metrics_dir,
            'output_dir': out_dir,
            'metric_files': metric_files,  # Legacy - for backward compatibility
            'prometheus_data': prometheus_data, # New - parsed Prometheus metrics
            'rwg_data': rwg_data, # New - Full RWG data (Overall, Realtime, Prom)
            'record': rec,
            'slos': slos,
            "bench": config.get("bench")
        }
        for fn in funcs:
            try:
                if os.environ.get('PLOT_DEBUG') == '1':
                    print(f"[plot_runner] calling plugin {fn.__module__}.{fn.__name__} for repeat {repeat_index}")
                result = fn(ctx)
                if os.environ.get('PLOT_DEBUG') == '1' and result:
                    print(f"[plot_runner] plugin generated {len(result)} plots: {[p.name for p in result]}")
            except Exception:
                if os.environ.get('PLOT_DEBUG') == '1':
                    print(f"[plot_runner] plugin {fn.__module__}.{fn.__name__} failed for repeat {rec.get('repeat_index')} type {exp_type}")
                    traceback.print_exc()
                continue

    # After per-repeat, run aggregate plugins
    if aggregate_registry:
        for (experiment_name, run_unit_name), recs in grouped.items():
            if not recs:
                continue
            exp_type = recs[0].get('type')
            # latency-and-goodput-vs-load: experiment-level plots span all loads; skip per-unit *_unit.pdf.
            if exp_type == 'latency-and-goodput-vs-load':
                continue
            agg_funcs = aggregate_registry.get(exp_type, []) + aggregate_registry.get('*', [])
            if not agg_funcs:
                continue
            # Collect per-repeat metric_files for this unit
            repeat_metric_files = []
            repeat_prometheus_data = []
            repeat_rwg_data = [] # New
            artifact_dirs = []
            
            for r in recs:
                artifact_dir = Path(r.get('artifact_dir', '.'))
                metrics_dir = artifact_dir / 'metrics'
                
                # Legacy files
                metric_files = _load_metric_files(metrics_dir)
                if metric_files:
                    repeat_metric_files.append(metric_files)
                else:
                    repeat_metric_files.append({}) # Consistent length
                
                # Prometheus data
                prom_data = None
                try:
                    from exec.plots.data_loader import load_repeat_data
                    loaded_data = load_repeat_data(artifact_dir)
                    if loaded_data:
                        first_api = next(iter(loaded_data))
                        _, _, prom_data = loaded_data[first_api]
                    else:
                        loaded_data = {} # Ensure defined
                except Exception:
                    loaded_data = {}
                    pass
                if prom_data:
                    repeat_prometheus_data.append(prom_data)
                
                # Append RWG data for this repeat (or empty dict if failed)
                repeat_rwg_data.append(loaded_data)
                
                if not prom_data and metric_files: 
                    # If we found legacy files but no loaded prom data, append None to keep indexing if needed?
                    # Generally plugins usually iterate lists.
                    pass
                
                # Check data existence for at least one source
                if metric_files or prom_data or (artifact_dir/'output').exists() or (
                    (artifact_dir / 'raw' / 'cpu_metrics.csv').is_file()
                    or (artifact_dir / 'raw' / 'cpu_utilization_summary.csv').is_file()
                ):
                    artifact_dirs.append(artifact_dir)

            if not artifact_dirs:
                continue
                
            # Derive load from run_unit_name if it encodes rate-<num> else use first record's load
            load_value = None
            run_unit = run_unit_name or ''
            # heuristic: look for 'rate-' substring or trailing number segments
            import re
            m = re.search(r"rate-(\d+)", run_unit)
            if m:
                load_value = int(m.group(1))
            else:
                # fallback: try loads.start from config stored in record
                cfg = recs[0].get('config') or {}
                load_value = cfg.get('base_rate')
            # API list from first record
            apis = recs[0].get('apis') or []
            slos = config.get('slos')
            cfg = recs[0].get('config') or {}
            unit_out_dir = output_root / experiment_name / run_unit_name
            agg_ctx = {
                'type': exp_type,
                'experiment_name': experiment_name,
                'run_unit_name': run_unit_name,
                'group_name': run_unit_name,
                'artifact_dirs': artifact_dirs,
                'repeat_metric_files': repeat_metric_files,
                'repeat_prometheus_data': repeat_prometheus_data,
                'repeat_rwg_data': repeat_rwg_data, # New
                'output_dir': unit_out_dir,
                'load_value': load_value,
                'apis': apis,
                'slos': slos,
                'services': cfg.get('services') if isinstance(cfg, dict) else None,
                'bench': config.get("bench")
            }
            if os.environ.get('PLOT_DEBUG') == '1' and exp_type == 'max-queue':
                try:
                    print(f"[plot_runner][max-queue] unit={run_unit_name} services={agg_ctx['services']} apis={apis} repeats={len(repeat_metric_files)}")
                except Exception:
                    pass
            for fn in agg_funcs:
                try:
                    fn(agg_ctx)
                except Exception:
                    if os.environ.get('PLOT_DEBUG') == '1':
                        print(f"[plot_runner] aggregate plugin {fn.__module__}.{fn.__name__} failed for unit {run_unit_name} type {exp_type}")
                        traceback.print_exc()
                    continue
    # Experiment-level aggregation across loads
    if exp_aggregate_registry:
        for experiment_name, units in experiment_grouped.items():
            # Collect type & shared fields from first record of first unit
            some_unit = next(iter(units.values()))
            first_rec = some_unit['records'][0]
            exp_type = first_rec.get('type')
            agg_funcs = exp_aggregate_registry.get(exp_type, []) + exp_aggregate_registry.get('*', [])
            if not agg_funcs:
                continue
            # Build structure per unit with repeats metric files and load value
            unit_entries = []
            for run_unit_name, info in units.items():
                recs = info['records']
                # Collect metric files per repeat (legacy Prometheus) or RWG data
                repeat_metric_files = []
                repeat_prometheus_data = []
                # Initialize RWG data list
                repeat_rwg_data = [] 
                artifact_dirs = []
                
                for r in recs:
                    artifact_dir = Path(r.get('artifact_dir', '.'))
                    
                    # Check for legacy Prometheus metrics
                    mdir = artifact_dir / 'metrics'
                    mf = _load_metric_files(mdir)
                    
                    # Check for Prometheus data
                    prom_data = None
                    try:
                        from exec.plots.data_loader import load_repeat_data
                        rwg_data = load_repeat_data(artifact_dir)
                        if rwg_data:
                            first_api = next(iter(rwg_data))
                            _, _, prom_data = rwg_data[first_api]
                        else:
                            rwg_data = {}
                    except Exception:
                        rwg_data = {}
                        pass
                    
                    if mf:
                        repeat_metric_files.append(mf)
                    else:
                        repeat_metric_files.append({})
                        
                    if prom_data:
                        repeat_prometheus_data.append(prom_data)
                    
                    # Append RWG data
                    repeat_rwg_data.append(rwg_data)


                    # Check for new RWG data
                    output_dir_data = artifact_dir / 'output'
                    has_rwg_data = output_dir_data.exists() and any(output_dir_data.glob('overall-*.json'))
                    
                    if mf or prom_data or has_rwg_data:
                        artifact_dirs.append(artifact_dir)
                
                if not artifact_dirs:
                    continue
                # derive load value
                import re
                load_value = None
                m = re.search(r"rate-(\d+)", run_unit_name or '')
                if m:
                    load_value = int(m.group(1))
                else:
                    cfg = recs[0].get('config') or {}
                    load_value = cfg.get('base_rate')
                unit_entries.append({
                    'run_unit_name': run_unit_name,
                    'repeat_metric_files': repeat_metric_files,
                    'repeat_prometheus_data': repeat_prometheus_data,
                    'repeat_rwg_data': repeat_rwg_data, # New
                    'artifact_dirs': artifact_dirs,
                    'load_value': load_value,
                })
            if not unit_entries:
                continue
            slos = config.get('slos')
            cfg = first_rec.get('config') or {}
            apis = first_rec.get('apis') or []
            if not apis:
                # Attempt to infer APIs from metric file stems of first unit entry
                try:
                    sample_metrics = unit_entries[0]['repeat_metric_files'][0]
                    apis_found = set()
                    # Look for stems like goodput_search-hotel_all
                    for stem in sample_metrics.keys():
                        if stem.startswith('goodput_') and stem.endswith('_all'):
                            api = stem[len('goodput_'):-len('_all')]
                            apis_found.add(api)
                    if apis_found:
                        apis = sorted(list(apis_found))
                except Exception:
                    pass
            exp_out_dir = output_root / experiment_name
            exp_ctx = {
                'type': exp_type,
                'experiment_name': experiment_name,
                'unit_entries': unit_entries,
                'output_dir': exp_out_dir,
                'slos': slos,
                'apis': apis,
                'bench': config.get("bench"),
            }
            for fn in agg_funcs:
                try:
                    if os.environ.get('PLOT_DEBUG') == '1':
                        print(f"[plot_runner] running experiment aggregate plugin {fn.__module__}.{fn.__name__} for {experiment_name} loads={len(unit_entries)}")
                    fn(exp_ctx)
                    if os.environ.get('PLOT_DEBUG') == '1':
                        print(f"[plot_runner] finished experiment aggregate plugin {fn.__module__}.{fn.__name__} for {experiment_name}")
                except Exception:
                    if os.environ.get('PLOT_DEBUG') == '1':
                        print(f"[plot_runner] experiment aggregate plugin {fn.__module__}.{fn.__name__} failed for experiment {experiment_name} type {exp_type}")
                        traceback.print_exc()
                    continue


def parse_args(argv=None):
    p = argparse.ArgumentParser(description='Generate per-repeat plots via plugins')
    p.add_argument('--experiment-index', required=True)
    p.add_argument('--experiment-name', help='Generate figures for a single experiment only')
    p.add_argument('--experiments-root', default='experiment_runs')
    p.add_argument('--config-file', required=True)
    p.add_argument('--output-dir', default='generated_plots')
    return p.parse_args(argv)


def main(argv=None):
    ns = parse_args(argv)
    generate_for_index(ns.experiment_index, Path(ns.experiments_root), Path(ns.output_dir), ns.experiment_name, ns.config_file)


if __name__ == '__main__':  # pragma: no cover
    main()
