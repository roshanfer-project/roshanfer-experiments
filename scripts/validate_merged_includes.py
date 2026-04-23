"""Check merged YAML `include` keys match resolved experiment names (same logic as load_experiment_configs)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml
from exec.experiment_naming import assign_final_names_from_experiment_list  # noqa: E402


def _bench_from_config(config_path: Path) -> str:
    data = json.loads(config_path.read_text())
    return str(data.get("bench", ""))


def check_test_bench(bench_dir: Path) -> list[str]:
    err: list[str] = []
    exp = bench_dir / "experiments.json"
    mrg = bench_dir / "merged.yaml"
    cfg = bench_dir / "config.json"
    if not exp.is_file() or not mrg.is_file() or not cfg.is_file():
        return err
    bench = _bench_from_config(cfg)
    data = json.loads(exp.read_text())
    known = set(assign_final_names_from_experiment_list(data.get("experiments", []), bench))
    yml = yaml.safe_load(mrg.read_text()) or {}
    for fig, fc in (yml.get("figures") or {}).items():
        if not isinstance(fc, dict):
            continue
        inc = fc.get("include") or {}
        if not isinstance(inc, dict):
            continue
        for k in inc.keys():
            if str(k) not in known:
                err.append(f"{bench_dir.name} figure {fig}: include key {k!r} not in resolved names {sorted(known)[:5]}…")
    return err


def main() -> int:
    tests = ROOT / "configs" / "tests"
    all_err: list[str] = []
    for d in sorted(tests.iterdir()):
        if d.is_dir():
            all_err.extend(check_test_bench(d))
    for err in all_err:
        print(err, file=sys.stderr)
    if all_err:
        return 1
    print("validate_merged_includes: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
