"""Report generation module.

Generates a Markdown summary; extend to include plots by invoking existing plotting
scripts with collected data (left as TODO for you to integrate domain specifics).
"""

from __future__ import annotations

from pathlib import Path
from typing import List
import json
from datetime import datetime

from .models import CollectorResult, ExperimentConfig, RunResult, LoadRange


def generate_report(run_root: Path, experiments: List[ExperimentConfig], run_results: List[RunResult], total_duration: float) -> None:
    report_path = run_root / "report.md"
    lines: List[str] = []
    lines.append(f"# Experiment Run Report\n")
    lines.append(f"Date (UTC): {datetime.utcnow().isoformat()}\n")
    lines.append(f"Total duration: {total_duration:.1f} sec\n")
    lines.append("## Experiments\n")
    for exp in experiments:
        lines.append(f"- {exp.name} (type={exp.type})")
    lines.append("\n## Run Units Summary\n")
    for idx, rr in enumerate(run_results):
        dur = rr.details.get('duration_sec', '?')
        lines.append(f"- {rr.group_name or rr.unit_name} (repeat {rr.repeat_index+1}/{rr.total_repeats}): status={rr.status}, duration={dur:.2f}s")
    # Placeholder: integrate plotting. For each recognized experiment type you can call
    # existing plot scripts and embed image links. (User to implement.)
    lines.append("\n## Plots (TODO)\n")
    lines.append("Add logic to generate & embed plots here.\n")
    report_path.write_text("\n".join(lines))

    # Also emit a machine-readable summary JSON.
    summary_json = run_root / "report_summary.json"
    def _exp_to_dict(exp: ExperimentConfig):
        d = dict(exp.__dict__)
        lr = d.get("loads")
        if isinstance(lr, LoadRange):
            d["loads"] = {"start": lr.start, "end": lr.end, "step": lr.step}
        return d

    summary_json.write_text(json.dumps({
        "total_duration_sec": total_duration,
        "experiments": [_exp_to_dict(exp) for exp in experiments],
        "run_results": [rr.to_dict() for rr in run_results],
    }, indent=2))
