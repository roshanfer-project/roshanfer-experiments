#!/bin/bash
# Run all test benchmarks under configs/tests/
# Each test gets a dedicated output dir: ./exp_runs_test/<test_name>_<timestamp>

cd "$(dirname "$0")"

TESTS_ROOT="configs/tests"
OUTPUT_BASE="./exp_runs_test"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
failed=0

for dir in "$TESTS_ROOT"/*/; do
  test_name=$(basename "$dir")
  config="$dir/config.json"
  experiments="$dir/experiments.json"
  if [[ -f "$config" && -f "$experiments" ]]; then
    out_dir="$OUTPUT_BASE/${test_name}_${TIMESTAMP}"
    echo "Running $test_name -> $out_dir"
    if ! python -m exec.executor --experiments-file "$experiments" --config "$config" --output-base-dir "$out_dir"; then
      ((failed++))
    fi
  fi
done

if [[ $failed -gt 0 ]]; then
  echo "Failed: $failed test(s)"
  exit $failed
fi
echo "All tests passed"
