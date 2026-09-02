#!/bin/bash
# Build and push sidecar + selected benchmark images for a stable tag.
# Counterpart to SKIP_BUILD=1: populate REGISTRY images that run_tests.sh will pull.

set -euo pipefail

_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$_ROOT"
REPO_ROOT="$_ROOT"
export REPO_ROOT
# shellcheck source=/dev/null
source "$_ROOT/scripts/elapsed.sh"

# shellcheck source=/dev/null
source "$_ROOT/scripts/config_env.sh"

usage() {
  echo "Usage: $0 [--bench NAME[,NAME...]] [--tag TAG]"
  echo ""
  echo "Build and push sidecar + workload images via each bench's build.sh."
  echo "If --bench is omitted, builds all tests under benchmarks/tests/ plus hotel, social, and alibaba-large."
  echo "Names match run_tests.sh (one-service, hotel, alibaba-large, …)."
  echo "TAG defaults to latest if --tag is omitted (not IMAGE_TAG from config.env)."
  echo ""
  echo "Example:"
  echo "  $0 --tag latest"
  echo "  $0 --tag latest --bench one-service,hotel,social"
}

CLI_TAG=""
BENCH_FILTER=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --bench)
      [[ -z "${2:-}" ]] && { echo "Missing value for --bench"; usage; exit 1; }
      BENCH_FILTER="$2"
      shift 2
      ;;
    --tag)
      [[ -z "${2:-}" ]] && { echo "Missing value for --tag"; usage; exit 1; }
      CLI_TAG="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

TAG="${CLI_TAG:-latest}"

resolve_build_sh() {
  local name="$1"
  if [[ -x "$_ROOT/benchmarks/${name}/build.sh" ]]; then
    echo "$_ROOT/benchmarks/${name}/build.sh"
    return 0
  fi
  if [[ -x "$_ROOT/benchmarks/tests/${name}/build.sh" ]]; then
    echo "$_ROOT/benchmarks/tests/${name}/build.sh"
    return 0
  fi
  return 1
}

NAMES=()
if [[ -z "$BENCH_FILTER" ]]; then
  for script in "$_ROOT/benchmarks/tests"/*/build.sh; do
    [[ -x "$script" ]] || continue
    NAMES+=("$(basename "$(dirname "$script")")")
  done
  NAMES+=(hotel social alibaba-large)
else
  IFS=',' read -ra NAMES <<< "$BENCH_FILTER"
fi

scripts=()
built_names=()
for raw in "${NAMES[@]}"; do
  name="${raw// /}"
  [[ -z "$name" ]] && continue
  if ! script=$(resolve_build_sh "$name"); then
    echo "error: no build.sh for bench '$name' (tried benchmarks/${name} and benchmarks/tests/${name})"
    exit 1
  fi
  scripts+=("$script")
  built_names+=("$name")
done

if [[ ${#scripts[@]} -eq 0 ]]; then
  echo "error: no benches to build"
  exit 1
fi

# shellcheck source=/dev/null
source "$_ROOT/scripts/ensure_build_deps.sh"

echo "Building ${#scripts[@]} bench(es) with tag $TAG (registry ${REGISTRY})"
for script in "${scripts[@]}"; do
  echo "=== $script $TAG ==="
  "$script" "$TAG"
done

echo ""
echo "Build complete. ${#built_names[@]} bench(es), tag $TAG, registry ${REGISTRY}:"
for name in "${built_names[@]}"; do
  echo "  - $name"
done
