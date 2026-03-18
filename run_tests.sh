#!/bin/bash
# Run all test benchmarks under configs/tests/
# Each run gets a timestamped dir; each test is a subdir: ./exp_runs_test/<timestamp>/<test_name>

cd "$(dirname "$0")"

# Use venv if present (plot_runner needs pandas/matplotlib)
PYTHON=python
[[ -x .venv/bin/python ]] && PYTHON=.venv/bin/python

TESTS_ROOT="configs/tests"
OUTPUT_BASE="./exp_runs_test"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
failed=0

for dir in "$TESTS_ROOT"/*/; do
  test_name=$(basename "$dir")
  config="$dir/config.json"
  experiments="$dir/experiments.json"
  if [[ -f "$config" && -f "$experiments" ]]; then
    out_dir="$OUTPUT_BASE/${TIMESTAMP}/${test_name}"
    echo "Running $test_name -> $out_dir"
    if ! $PYTHON -m exec.executor --experiments-file "$experiments" --config "$config" --output-base-dir "$out_dir"; then
      ((failed++))
    else
      experiment_index=$($PYTHON -c "import json,sys; c=json.load(open('$config')); print(c.get('experiment_index','$test_name'))")
      echo "Plotting $test_name -> $out_dir/plots"
      $PYTHON -m exec.plot_runner --experiment-index "$experiment_index" \
        --experiments-root "$out_dir" --config-file "$config" --output-dir "$out_dir/plots" || echo "Warning: plot failed for $test_name"
      merged_yaml="$dir/merged.yaml"
      if [[ -f "$merged_yaml" ]]; then
        echo "Merged plots $test_name -> $out_dir/plots/merged"
        $PYTHON -m exec.merged_plot_runner --merged-config "$merged_yaml" \
          --experiments-file "$experiments" --experiments-root "$out_dir" \
          --output-dir "$out_dir/plots/merged" --experiment-index "$experiment_index" \
          --config "$config" || echo "Warning: merged plot failed for $test_name"
      fi
    fi
  fi
done

if [[ $failed -gt 0 ]]; then
  echo "Failed: $failed test(s)"
  exit $failed
fi
echo "All tests passed"
