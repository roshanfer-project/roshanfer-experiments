"""Merge every PDF under a plots root into one file (e.g. after run_tests.sh)."""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

os.environ.setdefault("MPLBACKEND", "Agg")

OUTPUT_NAME = "all_tests_plots.pdf"
HEADER_PT = 54.0

MERGED_STEMS = (
    "_latency_vs_throughput",
    "_goodput_bar",
    "_rate_vs_time",
    "_latency_vs_time",
    "_combined",
    "_resource_waste_bar",
    "_max_queue",
)


def _configs_tests_dir() -> Path:
    return Path.cwd() / "configs" / "tests"


def _bench_short(suite: str) -> str:
    cfg = _configs_tests_dir() / suite / "config.json"
    if not cfg.is_file():
        return suite
    try:
        data = json.loads(cfg.read_text())
        b = data.get("bench") or ""
        return Path(str(b)).name if b else suite
    except (json.JSONDecodeError, OSError):
        return suite


def _load_run_index(run_ts_root: Path, suite: str) -> Dict[str, Dict[str, Any]]:
    by_name: Dict[str, Dict[str, Any]] = {}
    suite_dir = run_ts_root / suite
    if not suite_dir.is_dir():
        return by_name
    for summary in suite_dir.glob("exp-*/run_summary.jsonl"):
        try:
            for line in summary.read_text().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                en = row.get("experiment_name")
                if not en:
                    continue
                cfg = row.get("config") or {}
                by_name[en] = {
                    "type": row.get("type") or cfg.get("type") or "?",
                    "system": cfg.get("system") or "?",
                }
        except (json.JSONDecodeError, OSError):
            continue
    return by_name


def _merged_figure_type(suite: str, figure_name: str) -> Optional[str]:
    yml = _configs_tests_dir() / suite / "merged.yaml"
    if not yml.is_file():
        return None
    try:
        doc = yaml.safe_load(yml.read_text()) or {}
        figs = doc.get("figures") or {}
        fc = figs.get(figure_name)
        if isinstance(fc, dict):
            return str(fc.get("type") or "")
    except (yaml.YAMLError, OSError):
        pass
    return None


def _split_merged_stem(stem: str) -> Tuple[str, str]:
    for suf in sorted(MERGED_STEMS, key=len, reverse=True):
        if stem.endswith(suf):
            return stem[: -len(suf)], suf.lstrip("_")
    return stem, ""


def _meta_for_pdf(
    plots_root: Path,
    run_ts_root: Path,
    pdf_path: Path,
) -> Tuple[str, str, str]:
    rel = pdf_path.relative_to(plots_root)
    parts = rel.parts
    suite = parts[0] if parts else ""
    bench = _bench_short(suite)
    idx = _load_run_index(run_ts_root, suite)

    if len(parts) >= 2 and parts[1] == "merged":
        figure_name, _kind = _split_merged_stem(pdf_path.stem)
        mtype = _merged_figure_type(suite, figure_name) or "merged"
        return str(mtype), "merged", bench

    if len(parts) >= 2:
        exp_name = parts[1]
        row = idx.get(exp_name)
        if row:
            return str(row["type"]), str(row["system"]), bench
        return "?", "?", bench

    return "?", "?", bench


def _header_pdf_bytes(width_pt: float, height_pt: float, etype: str, system: str, bench: str) -> bytes:
    import matplotlib.pyplot as plt
    from matplotlib.figure import Figure

    # ~4.5pt per char at fontsize 7; keep lines inside figure (matplotlib clips at canvas edge).
    wrap_w = max(24, min(96, int(width_pt / 5.0)))
    blocks = [
        textwrap.fill(f"type: {etype}", width=wrap_w),
        textwrap.fill(f"system: {system}", width=wrap_w),
        textwrap.fill(f"bench: {bench}", width=wrap_w),
    ]
    body = "\n".join(blocks)

    fig = Figure(figsize=(width_pt / 72.0, height_pt / 72.0), dpi=72)
    fig.patch.set_facecolor("white")
    ax = fig.add_axes((0.04, 0.06, 0.92, 0.88))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.set_facecolor("white")
    ax.text(
        0.5,
        0.5,
        body,
        ha="center",
        va="center",
        fontsize=6.5,
        family="sans-serif",
        wrap=False,
    )
    buf = io.BytesIO()
    fig.savefig(buf, format="pdf", bbox_inches=None, pad_inches=0)
    plt.close(fig)
    return buf.getvalue()


def _append_page_with_header(
    writer: Any,
    src_page: Any,
    meta: Tuple[str, str, str],
    header_cache: Dict[Tuple[str, str, str, int], bytes],
) -> None:
    from pypdf import PdfReader, Transformation

    w = float(src_page.mediabox.width)
    h = float(src_page.mediabox.height)
    header = HEADER_PT
    etype, system, bench = meta
    key = (etype, system, bench, int(round(w)))
    if key not in header_cache:
        header_cache[key] = _header_pdf_bytes(w, header, etype, system, bench)
    hreader = PdfReader(io.BytesIO(header_cache[key]))
    hpage = hreader.pages[0]
    hw = float(hpage.mediabox.width)
    hh = float(hpage.mediabox.height)

    # Taller page: plot unchanged at bottom (y=0..h), caption band above (y=h..h+header).
    H = h + header
    dest = writer.add_blank_page(width=w, height=H)
    dest.merge_transformed_page(src_page, Transformation())

    s = min(w / hw if hw > 0 else 1.0, header / hh if hh > 0 else 1.0)
    tw = hw * s
    tx = (w - tw) / 2.0
    ty = h
    dest.merge_transformed_page(hpage, Transformation().scale(s, s).translate(tx, ty))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Merge all PDFs under a directory into one file.")
    p.add_argument("plots_root", type=Path, help="Root directory to scan recursively for *.pdf")
    args = p.parse_args(argv)
    root: Path = args.plots_root.resolve()
    if not root.is_dir():
        print(f"merge_plot_pdfs: not a directory: {root}", file=sys.stderr)
        return 1

    run_ts_root = root.parent
    out_path = root / OUTPUT_NAME
    out_resolved = out_path.resolve()
    pdfs = sorted(p for p in root.rglob("*.pdf") if p.resolve() != out_resolved)
    if not pdfs:
        print("merge_plot_pdfs: no PDFs found, skipping")
        return 0

    try:
        from pypdf import PdfReader, PdfWriter
    except ModuleNotFoundError:
        print("merge_plot_pdfs: install pypdf (pip install pypdf)", file=sys.stderr)
        return 1

    writer = PdfWriter()
    header_cache: Dict[Tuple[str, str, str, int], bytes] = {}
    for path in pdfs:
        meta = _meta_for_pdf(root, run_ts_root, path)
        reader = PdfReader(str(path))
        for page in reader.pages:
            _append_page_with_header(writer, page, meta, header_cache)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        writer.write(f)
    print(f"merge_plot_pdfs: wrote {out_path} ({len(pdfs)} files, {len(writer.pages)} pages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
