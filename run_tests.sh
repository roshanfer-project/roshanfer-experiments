#!/bin/bash
# Run all test benchmarks under configs/tests/
# Each run gets a timestamped dir; each test is a subdir: ./exp_runs_test/<timestamp>/<test_name>

cd "$(dirname "$0")"

usage() {
  echo "Usage: $0 [OPTIONS]"
  echo ""
  echo "Run test benchmarks under configs/tests/. Each test dir with config.json and"
  echo "experiments.json is executed; results go to exp_runs_test/<timestamp>/<test_name>."
  echo ""
  echo "Options:"
  echo "  -h, --help       Show this help and exit"
  echo "  --bench NAME    Run only tests matching NAME (comma-separated for multiple)"
  echo "  --type TYPES    Run only experiments whose JSON \"type\" matches (comma-separated)"
  echo "  --system SYS    Run only experiments with system SYS (plain, sidecar; comma-separated)"
  echo "  --num-apis N    Run only experiments with N APIs (comma-separated, e.g. 1,3)"
  echo "  --shared-generator  Allow fewer generators than APIs (assign round-robin)"
  echo ""
  echo "Examples:"
  echo "  $0                           # run all"
  echo "  $0 --bench multi-api         # only multi-api test"
  echo "  $0 --system plain            # only plain experiments"
  echo "  $0 --num-apis 3              # only experiments with 3 APIs"
  echo "  $0 --bench multi-api --system sidecar --num-apis 3"
  echo "  $0 --bench chain-2 --type latency-vs-throughput"
  echo "  $0 --bench multi-api --shared-generator   # run with 1 gen + 1 deploy when hosts limited"
}

BENCH_FILTER=""
TYPE_FILTER=""
SYSTEM_FILTER=""
NUM_APIS_FILTER=""
SHARED_GENERATOR=""
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
    --type)
      [[ -z "${2:-}" ]] && { echo "Missing value for --type"; usage; exit 1; }
      TYPE_FILTER="$2"
      shift 2
      ;;
    --system)
      [[ -z "${2:-}" ]] && { echo "Missing value for --system"; usage; exit 1; }
      SYSTEM_FILTER="$2"
      shift 2
      ;;
    --num-apis)
      [[ -z "${2:-}" ]] && { echo "Missing value for --num-apis"; usage; exit 1; }
      NUM_APIS_FILTER="$2"
      shift 2
      ;;
    --shared-generator)
      SHARED_GENERATOR=1
      shift
      ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

# Use venv if present (plot_runner needs pandas/matplotlib)
PYTHON=python
[[ -x .venv/bin/python ]] && PYTHON=.venv/bin/python

TESTS_ROOT="configs/tests"
OUTPUT_BASE="./exp_runs_test"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
failed=0

EXTRA_ARGS=()
[[ -n "$TYPE_FILTER" ]] && EXTRA_ARGS+=(--only-types "$TYPE_FILTER")
[[ -n "$SYSTEM_FILTER" ]] && EXTRA_ARGS+=(--only-system "$SYSTEM_FILTER")
[[ -n "$NUM_APIS_FILTER" ]] && EXTRA_ARGS+=(--only-num-apis "$NUM_APIS_FILTER")
[[ -n "$SHARED_GENERATOR" ]] && EXTRA_ARGS+=(--shared-generator)

for dir in "$TESTS_ROOT"/*/; do
  test_name=$(basename "$dir")
  config="$dir/config.json"
  experiments="$dir/experiments.json"
  if [[ -n "$BENCH_FILTER" ]]; then
    if [[ ",$BENCH_FILTER," != *",$test_name,"* ]]; then
      continue
    fi
  fi
  if [[ -f "$config" && -f "$experiments" ]]; then
    out_dir="$OUTPUT_BASE/${TIMESTAMP}/${test_name}"
    echo "Running $test_name -> $out_dir"
    if ! $PYTHON -m exec.executor --experiments-file "$experiments" --config "$config" --output-base-dir "$out_dir" "${EXTRA_ARGS[@]}"; then
      ((failed++))
    else
      experiment_index=$($PYTHON -c "import json,sys; c=json.load(open('$config')); print(c.get('experiment_index','$test_name'))")
      run_summary="$out_dir/exp-${experiment_index}/run_summary.jsonl"
      if [[ ! -f "$run_summary" ]]; then
        echo "Skipping plots for $test_name (no run summary — filters may have excluded all experiments)"
      else
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
  fi
done

if [[ $failed -gt 0 ]]; then
  echo "Failed: $failed test(s)"
  exit $failed
fi
echo "All tests passed"
