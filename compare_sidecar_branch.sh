#!/bin/bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <branch-a> <branch-b>" >&2
  exit 1
fi

branch_a=$1
branch_b=$2

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

SIDECAR="$ROOT/benchmarks/sidecar"
if [[ ! -d "$SIDECAR" ]]; then
  echo "error: missing $SIDECAR" >&2
  exit 1
fi
if [[ -n "$(git -C "$SIDECAR" status --porcelain)" ]]; then
  echo "error: benchmarks/sidecar has uncommitted or untracked changes; commit or clean first" >&2
  exit 1
fi

verify_ref() {
  local r=$1
  if ! git -C "$SIDECAR" rev-parse -q --verify "${r}^{commit}" >/dev/null; then
    echo "error: ref not found in benchmarks/sidecar: $r (try: cd benchmarks/sidecar && git fetch)" >&2
    exit 1
  fi
}
verify_ref "$branch_a"
verify_ref "$branch_b"

failed=0
RUN_TESTS_ARGS=(--num-generators 1 --shared-generator --bench fan-out --type max-queue,latency-and-rate-vs-time)

run_for_branch() {
  local b=$1
  echo "=== sidecar checkout: $b ==="
  git -C "$SIDECAR" checkout "$b"
  echo "=== run_tests.sh (sidecar at $b) ==="
  if ! "$ROOT/run_tests.sh" "${RUN_TESTS_ARGS[@]}"; then
    failed=$((failed + 1))
  fi
}

run_for_branch "$branch_a"
run_for_branch "$branch_b"

if [[ $failed -gt 0 ]]; then
  echo "error: $failed run(s) failed" >&2
  exit 1
fi
echo "All runs finished OK"
