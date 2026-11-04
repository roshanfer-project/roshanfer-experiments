"""Wall-clock timing helpers for executor and run_tests.sh."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def format_dur(sec: float) -> str:
    s = int(round(float(sec or 0)))
    if s < 0:
        s = 0
    m, r = divmod(s, 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}m{r:02d}s"
    if m == 0:
        return f"{r}s"
    return f"{m}m{r:02d}s"


def empty_timings() -> Dict[str, Any]:
    return {
        "setup": {"provision_sec": 0.0, "k8s_sec": 0.0, "build": {}},
        "tuning": {},
        "experiments": [],
        "plot_sec": 0.0,
        "total_sec": 0.0,
    }


def recompute_e2e(timings: Dict[str, Any]) -> None:
    for exp in timings.get("experiments") or []:
        run_sec = float(exp.get("run_sec") or 0)
        tune_sec = float(exp.get("tune_sec") or 0)
        plot_sec = float(exp.get("plot_sec") or 0)
        exp["e2e_sec"] = run_sec + tune_sec + plot_sec


def write_timings(path: Path, timings: Dict[str, Any]) -> None:
    recompute_e2e(timings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(timings, indent=2) + "\n")


def apply_plot_sec(path: Path, plot_sec: float) -> None:
    if not path.exists():
        print(f"timings: no file {path}, skip apply-plot", file=sys.stderr)
        return
    timings = json.loads(path.read_text())
    plot_sec = float(plot_sec)
    timings["plot_sec"] = plot_sec
    for exp in timings.get("experiments") or []:
        exp["plot_sec"] = plot_sec
    write_timings(path, timings)


def _build_total(setup: Dict[str, Any]) -> float:
    build = setup.get("build") or {}
    if isinstance(build, dict):
        return sum(float(v or 0) for v in build.values())
    return float(build or 0)


def _load_bench_timings(run_dir: Path) -> List[Dict[str, Any]]:
    rows = []
    for path in sorted(run_dir.glob("*/exp-*/timings.json")):
        bench = path.parent.parent.name
        timings = json.loads(path.read_text())
        rows.append({"name": bench, "path": str(path), **timings})
    return rows


def print_summary(
    run_dir: Path,
    remote_clean_sec: Optional[float] = None,
    merge_plots_sec: Optional[float] = None,
) -> Dict[str, Any]:
    benches = _load_bench_timings(run_dir)
    campaign: Dict[str, Any] = {
        "benches": [],
        "experiments": [],
    }
    if remote_clean_sec is not None:
        campaign["remote_clean_sec"] = float(remote_clean_sec)
    if merge_plots_sec is not None:
        campaign["merge_plot_pdfs_sec"] = float(merge_plots_sec)

    print("Setup (not in experiment time)")
    if not benches and remote_clean_sec is None and merge_plots_sec is None:
        print("  (none)")
    for b in benches:
        setup = b.get("setup") or {}
        prov = float(setup.get("provision_sec") or 0)
        k8s = float(setup.get("k8s_sec") or 0)
        build = _build_total(setup)
        campaign["benches"].append({
            "name": b["name"],
            "setup": setup,
            "tuning": b.get("tuning") or {},
            "plot_sec": float(b.get("plot_sec") or 0),
            "total_sec": float(b.get("total_sec") or 0),
        })
        print(f"  {b['name']}  provision {format_dur(prov)}  k8s {format_dur(k8s)}  build {format_dur(build)}")
    if remote_clean_sec is not None:
        print(f"  remote-clean {format_dur(remote_clean_sec)}")
    if merge_plots_sec is not None:
        print(f"  merge_plot_pdfs {format_dur(merge_plots_sec)}")

    print("")
    print("Experiments (e2e = tune + run + plot)")
    any_exp = False
    for b in benches:
        for exp in b.get("experiments") or []:
            any_exp = True
            name = exp.get("name") or "?"
            e2e = float(exp.get("e2e_sec") or 0)
            run_sec = float(exp.get("run_sec") or 0)
            tune_sec = float(exp.get("tune_sec") or 0)
            plot_sec = float(exp.get("plot_sec") or 0)
            row = {
                "bench": b["name"],
                "name": name,
                "system": exp.get("system"),
                "run_sec": run_sec,
                "tune_sec": tune_sec,
                "plot_sec": plot_sec,
                "e2e_sec": e2e,
            }
            campaign["experiments"].append(row)
            print(
                f"  {b['name']}  {name}  e2e {format_dur(e2e)}"
                f"  (run {format_dur(run_sec)}, tune {format_dur(tune_sec)}, plot {format_dur(plot_sec)})"
            )
    if not any_exp:
        print("  (none)")

    out = run_dir / "timings.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(campaign, indent=2) + "\n")
    print(f"\nWrote {out}")
    return campaign


def log_executor_summary(timings: Dict[str, Any]) -> None:
    import logging

    setup = timings.get("setup") or {}
    logging.info(
        "Timings setup: provision=%s k8s=%s build=%s",
        format_dur(setup.get("provision_sec") or 0),
        format_dur(setup.get("k8s_sec") or 0),
        format_dur(_build_total(setup)),
    )
    for exp in timings.get("experiments") or []:
        logging.info(
            "Timings experiment %s: e2e=%s (run %s, tune %s, plot %s)",
            exp.get("name"),
            format_dur(exp.get("e2e_sec") or 0),
            format_dur(exp.get("run_sec") or 0),
            format_dur(exp.get("tune_sec") or 0),
            format_dur(exp.get("plot_sec") or 0),
        )


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Experiment timing helpers")
    sub = p.add_subparsers(dest="cmd", required=True)
    ap = sub.add_parser("apply-plot", help="Set plot_sec on a bench timings.json and recompute e2e")
    ap.add_argument("--file", required=True)
    ap.add_argument("--plot-sec", required=True, type=float)
    sm = sub.add_parser("summary", help="Print campaign timing table and write timings.json")
    sm.add_argument("--run-dir", required=True)
    sm.add_argument("--remote-clean-sec", type=float, default=None)
    sm.add_argument("--merge-plots-sec", type=float, default=None)
    return p.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    ns = parse_args(argv or sys.argv[1:])
    if ns.cmd == "apply-plot":
        apply_plot_sec(Path(ns.file), ns.plot_sec)
        return 0
    if ns.cmd == "summary":
        print_summary(
            Path(ns.run_dir),
            remote_clean_sec=ns.remote_clean_sec,
            merge_plots_sec=ns.merge_plots_sec,
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
