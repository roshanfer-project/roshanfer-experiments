"""Unit-level bar plot for lb-avg-queue experiments.

Each microservice: allocated CPU (mean app limit across replicas) vs measured
avg_queue (mean across replicas, then mean/std across repeats).
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import statistics
from pathlib import Path
from typing import Dict, List, Tuple

SUPPORTED_TYPES = ["lb-avg-queue"]

# Temporary test switch. Rollback: "avg_queue" / "Avg queue".
QUEUE_METRIC = "avg_queue"
QUEUE_LEGEND = "Avg queue"

APP_CONTAINER = "app"
_ENTRY_NAMES = ("frontend", "nginx")
_INFRA_POD_SUBSTRINGS = ("prometheus", "pushgateway")
_DEPLOYMENT_POD_RE = re.compile(r"^(.+)-[a-z0-9]{5,10}-[a-z0-9]{5}$")


def _is_infra_pod(pod: str) -> bool:
    pod_lower = pod.lower()
    return any(s in pod_lower for s in _INFRA_POD_SUBSTRINGS)


def _microservice_from_pod(pod: str) -> str:
    m = _DEPLOYMENT_POD_RE.match(pod)
    return m.group(1) if m else pod


def _mean_std(vals: List[float]) -> Tuple[float | None, float | None]:
    clean = [v for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not clean:
        return None, None
    m = sum(clean) / len(clean)
    if len(clean) < 2:
        return m, 0.0
    try:
        return m, statistics.pstdev(clean)
    except Exception:
        return m, 0.0


def canonical_ms_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _match_service(key: str, services: List[str]) -> str | None:
    ck = canonical_ms_name(key)
    for svc in sorted(services, key=len, reverse=True):
        if ck == svc or ck.startswith(svc + "-"):
            return svc
    return None


def _load_prom_metrics(artifact_dir: Path) -> dict:
    path = artifact_dir / "metrics" / "prometheus.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _ms_from_endpoint(endpoint: str) -> str:
    return canonical_ms_name(str(endpoint or "").split(":")[0].strip())


def _load_callgraph(bench: str) -> dict | None:
    path = Path("benchmarks") / bench / "callgraph.json"
    if not path.is_file():
        return None
    try:
        cg = json.loads(path.read_text())
    except Exception:
        return None
    return cg if isinstance(cg, dict) else None


def _entry_from_callgraph(bench: str) -> str | None:
    cg = _load_callgraph(bench)
    if not cg:
        return None
    for edge in cg.get("edges") or []:
        if not isinstance(edge, dict) or edge.get("source") != "USER":
            continue
        svc = _ms_from_endpoint(str(edge.get("target") or ""))
        if svc:
            return svc
    return None


def direct_downstreams_from_callgraph(bench: str | None) -> Dict[str, List[str]]:
    """Unique direct child MS per source; first-seen order. Skips USER."""
    if not bench:
        return {}
    cg = _load_callgraph(bench)
    if not cg:
        return {}
    downstreams: Dict[str, List[str]] = {}
    seen: Dict[str, set] = {}
    for edge in cg.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        src = _ms_from_endpoint(str(edge.get("source") or ""))
        dst = _ms_from_endpoint(str(edge.get("target") or ""))
        if not src or not dst or src == "user":
            continue
        kids = downstreams.setdefault(src, [])
        used = seen.setdefault(src, set())
        if dst not in used:
            used.add(dst)
            kids.append(dst)
    return downstreams


def subtract_downstream_queues(
    queue: Dict[str, float],
    downstreams: Dict[str, List[str]],
) -> Dict[str, float]:
    """local = max(0, self - sum of unique direct downstream means)."""
    out: Dict[str, float] = {}
    for svc, q in queue.items():
        child_sum = sum(queue.get(d, 0.0) for d in downstreams.get(svc, []))
        out[svc] = max(0.0, q - child_sum)
    return out


def services_in_callgraph_order(
    services: List[str],
    downstreams: Dict[str, List[str]],
    entry: str | None,
) -> List[str]:
    """BFS from entry following callgraph edge order; leftovers keep discovery order."""
    present = set(services)
    ordered: List[str] = []
    seen: set = set()
    if entry and entry in present:
        queue = [entry]
        seen.add(entry)
        while queue:
            cur = queue.pop(0)
            ordered.append(cur)
            for child in downstreams.get(cur, []):
                if child in present and child not in seen:
                    seen.add(child)
                    queue.append(child)
    leftovers = [s for s in services if s not in seen]
    return ordered + leftovers


def entry_ms_name(services: List[str], bench: str | None) -> str | None:
    """frontend/nginx if plotted, else callgraph USER target."""
    for name in _ENTRY_NAMES:
        if name in services:
            return name
    if not bench:
        return None
    return _entry_from_callgraph(bench)


def allocated_cpu_by_ms(artifact_dir: Path) -> Dict[str, float]:
    """Mean allocated app CPU cores among replicas of each microservice."""
    summary = artifact_dir / "raw" / "cpu_utilization_summary.csv"
    metrics = artifact_dir / "raw" / "cpu_metrics.csv"
    pod_limit: Dict[str, float] = {}

    def _ingest(rows) -> None:
        for row in rows:
            if row.get("container") != APP_CONTAINER:
                continue
            pod = row.get("pod", "")
            if not pod or _is_infra_pod(pod):
                continue
            try:
                limit = float(row["limit"])
            except (KeyError, TypeError, ValueError):
                continue
            if limit <= 0:
                continue
            pod_limit[pod] = limit

    if summary.is_file():
        with summary.open(newline="") as f:
            _ingest(csv.DictReader(f))
    elif metrics.is_file():
        with metrics.open(newline="") as f:
            _ingest(csv.DictReader(f))

    by_ms: Dict[str, List[float]] = {}
    for pod, limit in pod_limit.items():
        ms = canonical_ms_name(_microservice_from_pod(pod))
        by_ms.setdefault(ms, []).append(limit)
    return {ms: sum(vs) / len(vs) for ms, vs in by_ms.items() if vs}


def avg_queue_by_ms(prom: dict, apis: List[str], services: List[str]) -> Dict[str, float]:
    """Mean replica occupancy; occupancy at a replica is the sum of avg_queue over apis."""
    replica_sum: Dict[str, float] = {}
    for api in apis:
        api_data = prom.get(api)
        if not isinstance(api_data, dict):
            continue
        for key, stats in api_data.items():
            if not isinstance(stats, dict) or QUEUE_METRIC not in stats:
                continue
            try:
                replica_sum[key] = replica_sum.get(key, 0.0) + float(stats[QUEUE_METRIC])
            except (TypeError, ValueError):
                continue

    grouped: Dict[str, List[float]] = {s: [] for s in services}
    for key, total in replica_sum.items():
        matched = _match_service(key, services)
        if matched is None:
            continue
        grouped[matched].append(total)
    return {s: (sum(vs) / len(vs) if vs else 0.0) for s, vs in grouped.items()}


def infer_services(
    artifact_dirs: List[Path],
    apis: List[str],
    fallback_services: List[str],
) -> List[str]:
    services: List[str] = [canonical_ms_name(s) for s in (fallback_services or []) if s]
    seen = set(services)
    inferred: List[str] = []
    for ad in artifact_dirs:
        for ms in allocated_cpu_by_ms(ad):
            if ms not in seen:
                inferred.append(ms)
                seen.add(ms)
        prom = _load_prom_metrics(ad)
        for api in apis:
            api_data = prom.get(api)
            if not isinstance(api_data, dict):
                continue
            for key, stats in api_data.items():
                if not isinstance(stats, dict) or QUEUE_METRIC not in stats:
                    continue
                if _match_service(key, list(seen)) is not None:
                    continue
                ck = canonical_ms_name(key)
                if ck not in seen:
                    inferred.append(ck)
                    seen.add(ck)
    if fallback_services:
        return services + inferred
    return inferred or services


def collect_repeat_cpu_queue(
    artifact_dirs: List[Path],
    apis: List[str],
    fallback_services: List[str],
    bench: str | None = None,
) -> Tuple[List[str], Dict[str, List[float]], Dict[str, List[float]]]:
    services = infer_services(artifact_dirs, apis, fallback_services)
    entry = entry_ms_name(services, bench)
    downstreams = direct_downstreams_from_callgraph(bench)
    services = services_in_callgraph_order(services, downstreams, entry)
    services = [s for s in services if s != "ingress"]
    cpu_data: Dict[str, List[float]] = {s: [] for s in services}
    queue_data: Dict[str, List[float]] = {s: [] for s in services}
    for ad in artifact_dirs:
        cpu = allocated_cpu_by_ms(ad)
        queue = subtract_downstream_queues(
            avg_queue_by_ms(_load_prom_metrics(ad), apis, services),
            downstreams,
        )
        for s in services:
            cpu_data[s].append(float(cpu.get(s, 0.0)))
            queue_data[s].append(float(queue.get(s, 0.0)))
    return services, cpu_data, queue_data


def nonzero_services(
    services: List[str],
    cpu_data: Dict[str, List[float]],
    queue_data: Dict[str, List[float]],
) -> List[str]:
    out = []
    for s in services:
        if any(v > 0 for v in cpu_data.get(s, [])) or any(v > 0 for v in queue_data.get(s, [])):
            out.append(s)
    return out


def save_lb_avg_queue_figure(
    services: List[str],
    cpu_data: Dict[str, List[float]] | None = None,
    queue_data: Dict[str, List[float]] | None = None,
    out_path: Path = Path("."),
    title: str = "",
    ylabel: str = "Cores / req",
    ylim: Tuple[float, float] | None = None,
    show_ylabel: bool = True,
    show_yticklabels: bool = True,
    add_legend: bool = True,
    style=None,
    grid=None,
    ax=None,
    series: List[Tuple[str, Dict[str, List[float]], Dict[str, List[float]]]] | None = None,
) -> Path:
    try:
        from ..plotting_primitives import (
            ACM_COMPACT_HALF,
            SubplotGrid,
            plot_grouped_bars,
        )
    except ImportError:
        from exec.plots.plotting_primitives import (  # type: ignore
            ACM_COMPACT_HALF,
            SubplotGrid,
            plot_grouped_bars,
        )

    own_grid = grid is None
    if own_grid:
        style = style or ACM_COMPACT_HALF
        grid = SubplotGrid(style, layout="1x1")
        ax = grid.get_ax(0, 0)
    assert ax is not None and grid is not None and style is not None

    if series is None:
        series = [("", cpu_data or {}, queue_data or {})]

    bar_groups = []
    merged = len(series) > 1
    if merged:
        cpu_means, cpu_stds = [], []
        for s in services:
            vals: List[float] = []
            for _, cpu, _ in series:
                vals.extend(cpu.get(s, []))
            cm, cs = _mean_std(vals)
            cpu_means.append(0.0 if cm is None else cm)
            cpu_stds.append(0.0 if cs is None else cs)
        bar_groups.append(("Allocated CPU", cpu_means, cpu_stds))
        for prefix, _, queue in series:
            q_means, q_stds = [], []
            for s in services:
                qm, qs = _mean_std(queue.get(s, []))
                q_means.append(0.0 if qm is None else qm)
                q_stds.append(0.0 if qs is None else qs)
            bar_groups.append((prefix or QUEUE_LEGEND, q_means, q_stds))
    else:
        for prefix, cpu, queue in series:
            cpu_means, cpu_stds = [], []
            q_means, q_stds = [], []
            for s in services:
                cm, cs = _mean_std(cpu.get(s, []))
                qm, qs = _mean_std(queue.get(s, []))
                cpu_means.append(0.0 if cm is None else cm)
                cpu_stds.append(0.0 if cs is None else cs)
                q_means.append(0.0 if qm is None else qm)
                q_stds.append(0.0 if qs is None else qs)
            p = f"{prefix} " if prefix else ""
            bar_groups.append((f"{p}Allocated CPU", cpu_means, cpu_stds))
            bar_groups.append((f"{p}{QUEUE_LEGEND}", q_means, q_stds))
    plot_grouped_bars(ax, list(range(len(services))), bar_groups, style=style)
    ax.set_xticks(list(range(len(services))))
    ax.set_xticklabels(services, rotation=30, ha="right")

    if ylim is None:
        top = 0.0
        bot = None
        for _, means, stds in bar_groups:
            for m, s in zip(means, stds):
                top = max(top, m + (s or 0.0))
                if m > 0:
                    bot = m if bot is None else min(bot, m)
        ylim = (0.5 * bot if bot else 0.1, 1.2 * top if top > 0 else 10.0)
    elif ylim[0] <= 0:
        ylim = (0.1, ylim[1] if ylim[1] > 0.1 else 10.0)

    grid.configure_ax(
        ax,
        ylabel=ylabel if show_ylabel else "",
        title=title,
        ylim=ylim,
        show_ylabel=show_ylabel,
        log_y=True,
        show_yticklabels=show_yticklabels,
    )
    if add_legend:
        grid.add_shared_legend(position="top")
    if own_grid:
        grid.save(out_path)
    return out_path


def generate_unit_plots(ctx: Dict) -> List[Path]:
    if ctx.get("type") not in SUPPORTED_TYPES:
        return []
    artifact_dirs = [Path(p) for p in (ctx.get("artifact_dirs") or [])]
    if not artifact_dirs:
        return []
    apis = list(ctx.get("apis") or [])
    fallback = ctx.get("services") or []
    if isinstance(fallback, str):
        fallback = [fallback]
    services, cpu_data, queue_data = collect_repeat_cpu_queue(
        artifact_dirs, apis, list(fallback), bench=ctx.get("bench"),
    )
    services = nonzero_services(services, cpu_data, queue_data)
    if os.environ.get("PLOT_DEBUG"):
        print(f"[lb-avg-queue] services={services} apis={apis} repeats={len(artifact_dirs)}")
    if not services:
        print("[lb-avg-queue] All services have zero CPU and avg queue; skipping plots.")
        return []
    out_dir: Path = ctx["output_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "lb_avg_queue_bar.pdf"
    save_lb_avg_queue_figure(services, cpu_data, queue_data, path)
    return [path]
