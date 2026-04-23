"""Shared experiment derived-name logic for executor and merged_plot_runner."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .models import ExperimentConfig


_TAG_ALLOWED = re.compile(r"[^a-zA-Z0-9._-]+")


def slugify_tag(raw: Optional[str]) -> str:
    """Normalize optional user `tag` for use inside experiment_name (filesystem-friendly)."""
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    s = _TAG_ALLOWED.sub("_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if len(s) > 48:
        s = s[:48].rstrip("_")
    return s


def api_slug(apis: List[str]) -> str:
    """API ids in JSON list order, joined with '-'. Empty list -> 'none'."""
    if not apis:
        return "none"
    return "-".join(str(a) for a in apis)


def derive_experiment_base_name(
    exp_type: str,
    bench: str,
    apis: List[str],
    system: str,
    tag_raw: Optional[str] = None,
) -> str:
    """
    Base name before duplicate suffix. Format:
    {type}-{bench_basename}-{api_slug}[-{tag}]-{system}
    """
    bench_slug = bench.split("/")[-1] if bench else ""
    apis_s = api_slug(apis)
    tag_s = slugify_tag(tag_raw)
    if tag_s:
        return f"{exp_type}-{bench_slug}-{apis_s}-{tag_s}-{system}"
    return f"{exp_type}-{bench_slug}-{apis_s}-{system}"


def derive_experiment_base_from_dict(exp: Dict[str, Any], bench: str) -> str:
    """Base name when JSON has no explicit `name`. Callers must skip when `name` is set."""
    exp_type = str(exp.get("type", ""))
    system = str(exp.get("system", ""))
    apis = list(exp.get("apis") or [])
    tag = exp.get("tag")
    tag_val: Optional[str] = None
    if isinstance(tag, str) and tag.strip():
        tag_val = tag
    return derive_experiment_base_name(exp_type, bench, apis, system, tag_val)


def derive_experiment_base_from_config(exp: "ExperimentConfig", bench: str) -> str:
    """Base when no explicit name. Callers must skip when `exp.name` is set."""
    tag_val: Optional[str] = exp.tag if (exp.tag and str(exp.tag).strip()) else None
    return derive_experiment_base_name(exp.type, bench, exp.apis, exp.system, tag_val)


def assign_final_names_from_experiment_list(
    experiments: List[Dict[str, Any]], bench: str
) -> List[str]:
    """Match executor / merged load_experiment_configs: explicit name or derived + duplicate suffix."""
    seen: Dict[str, int] = {}
    out: List[str] = []
    for exp in experiments:
        if str(exp.get("name", "")).strip():
            out.append(str(exp["name"]).strip())
            continue
        base = derive_experiment_base_from_dict(exp, bench)
        if base in seen:
            seen[base] += 1
            out.append(f"{base}-{seen[base]}")
        else:
            seen[base] = 0
            out.append(base)
    return out
