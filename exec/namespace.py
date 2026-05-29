"""Resolve config/experiments paths per namespace."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

DEFAULT_NAMESPACE = "default"
_NS_RE = re.compile(r"^[a-zA-Z0-9._-]+$")

# suite_kind -> (config stem, experiments stem, merged stem)
_SUITE_PATTERNS: dict[str, tuple[str, str, str]] = {
    "tests": ("config", "experiments", "merged"),
    "hotel": ("config.hotel", "hotel_experiments", "merged"),
    "social": ("config.social", "social_experiments", "merged_social"),
    "alibaba": ("config.alibaba", "experiments", "merged"),
}

_OPTIONAL_SUITES: dict[str, tuple[str, str]] = {
    "hotel": ("configs/hotel", "hotel"),
    "social": ("configs/social", "social"),
    "alibaba-large": ("configs/alibaba-large", "alibaba"),
}


def normalize_namespace(namespace: str) -> str:
    ns = (namespace or DEFAULT_NAMESPACE).strip()
    if not ns:
        ns = DEFAULT_NAMESPACE
    if not _NS_RE.match(ns):
        raise ValueError(f"Invalid namespace {namespace!r}; use [a-zA-Z0-9._-]+")
    return ns


def _suffix(namespace: str) -> str:
    return "" if normalize_namespace(namespace) == DEFAULT_NAMESPACE else f"-{normalize_namespace(namespace)}"


def _filenames(suite_kind: str, namespace: str) -> tuple[str, str, str]:
    cfg_stem, exp_stem, merged_stem = _SUITE_PATTERNS[suite_kind]
    sfx = _suffix(namespace)
    return (
        f"{cfg_stem}{sfx}.json",
        f"{exp_stem}{sfx}.json",
        f"{merged_stem}{sfx}.yaml",
    )


@dataclass(frozen=True)
class SuiteFiles:
    config: Path
    experiments: Path
    merged: Optional[Path]

    def as_dict(self) -> dict[str, Optional[str]]:
        return {
            "config": str(self.config),
            "experiments": str(self.experiments),
            "merged": str(self.merged) if self.merged else None,
        }


def resolve_suite_files(
    suite_dir: Path,
    suite_kind: str,
    namespace: str,
) -> Optional[SuiteFiles]:
    if suite_kind not in _SUITE_PATTERNS:
        raise ValueError(f"Unknown suite kind {suite_kind!r}")
    cfg_name, exp_name, merged_name = _filenames(suite_kind, namespace)
    cfg = suite_dir / cfg_name
    exp = suite_dir / exp_name
    if not cfg.is_file() or not exp.is_file():
        return None
    merged = suite_dir / merged_name
    return SuiteFiles(config=cfg, experiments=exp, merged=merged if merged.is_file() else None)


def iter_test_suites(tests_root: Path, namespace: str) -> Iterator[tuple[str, SuiteFiles]]:
    if not tests_root.is_dir():
        return
    for d in sorted(tests_root.iterdir()):
        if not d.is_dir():
            continue
        files = resolve_suite_files(d, "tests", namespace)
        if files:
            yield d.name, files


def known_optional_suites(namespace: str) -> dict[str, SuiteFiles]:
    out: dict[str, SuiteFiles] = {}
    for name, (rel_dir, kind) in _OPTIONAL_SUITES.items():
        files = resolve_suite_files(Path(rel_dir), kind, namespace)
        if files:
            out[name] = files
    return out


def suite_kind_for_name(name: str) -> Optional[str]:
    if name in _OPTIONAL_SUITES:
        return _OPTIONAL_SUITES[name][1]
    return "tests"


def resolve_suite_by_name(
    name: str,
    namespace: str,
    tests_root: Path = Path("configs/tests"),
) -> Optional[SuiteFiles]:
    if name in _OPTIONAL_SUITES:
        rel_dir, kind = _OPTIONAL_SUITES[name]
        return resolve_suite_files(Path(rel_dir), kind, namespace)
    return resolve_suite_files(tests_root / name, "tests", namespace)


def read_run_namespace(run_root: Path) -> str:
    p = run_root / ".namespace"
    if p.is_file():
        ns = p.read_text().strip()
        if ns:
            return normalize_namespace(ns)
    return DEFAULT_NAMESPACE


def _cmd_resolve(args: argparse.Namespace) -> int:
    files = resolve_suite_files(Path(args.dir), args.kind, args.namespace)
    if not files:
        print(f"namespace: no config+experiments for kind={args.kind} dir={args.dir} ns={args.namespace}", file=sys.stderr)
        return 1
    print(json.dumps(files.as_dict()))
    return 0


def _cmd_list_tests(args: argparse.Namespace) -> int:
    for name, _ in iter_test_suites(Path(args.root), args.namespace):
        print(name)
    return 0


def _cmd_list_optional(args: argparse.Namespace) -> int:
    for name in sorted(known_optional_suites(args.namespace)):
        print(name)
    return 0


def _cmd_resolve_by_name(args: argparse.Namespace) -> int:
    files = resolve_suite_by_name(args.name, args.namespace, Path(args.tests_root))
    if not files:
        print(f"namespace: no config+experiments for suite={args.name} ns={args.namespace}", file=sys.stderr)
        return 1
    print(json.dumps(files.as_dict()))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Resolve config namespace file paths")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("resolve", help="Resolve paths for a suite directory")
    r.add_argument("--kind", required=True, choices=sorted(_SUITE_PATTERNS))
    r.add_argument("--dir", required=True)
    r.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    r.set_defaults(func=_cmd_resolve)

    lt = sub.add_parser("list-tests", help="List test suite names with config+experiments")
    lt.add_argument("--root", default="configs/tests")
    lt.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    lt.set_defaults(func=_cmd_list_tests)

    lo = sub.add_parser("list-optional", help="List hotel/social/alibaba-large with config+experiments")
    lo.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    lo.set_defaults(func=_cmd_list_optional)

    rn = sub.add_parser("resolve-by-name", help="Resolve paths by suite name")
    rn.add_argument("--name", required=True)
    rn.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    rn.add_argument("--tests-root", default="configs/tests")
    rn.set_defaults(func=_cmd_resolve_by_name)

    args = p.parse_args(argv)
    try:
        args.namespace = normalize_namespace(args.namespace)
    except ValueError as e:
        print(f"namespace: {e}", file=sys.stderr)
        return 1
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
