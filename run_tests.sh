#!/bin/bash
# Run all test benchmarks under configs/tests/
# Each run: data under ./exp_runs_test/<run_id>/<test_name>/; run_id is YYYYMMDD_HHMMSS
# optionally with _<comment>; plots under ./exp_runs_test/<run_id>/plots/<test_name>/;
# combined PDF at plots/all_tests_plots.pdf
# Use --namespace to pick config-<ns>.json / experiments-<ns>.json (default: config.json).

cd "$(dirname "$0")"

if [[ -z "$KUBECONFIG" || "$KUBECONFIG" != *"benchmarks/k8s/kubeconfig"* ]]; then
  echo "Error: KUBECONFIG is not set by direnv."
  echo "Install direnv and allow the .envrc in this repo:"
  echo "  sudo apt install direnv"
  echo "  echo 'eval \"\$(direnv hook zsh)\"' >> ~/.zshrc"
  echo "  source ~/.zshrc && direnv allow"
  exit 1
fi

# Filesystem-safe suffix for run folder; empty input -> empty output
sanitize_run_comment() {
  local s="$1" c out=""
  local i
  for (( i=0; i<${#s}; i++ )); do
    c="${s:i:1}"
    if [[ "$c" =~ [a-zA-Z0-9._-] ]]; then
      out+="$c"
    else
      out+="_"
    fi
  done
  while [[ "$out" == *__* ]]; do out="${out//__/_}"; done
  while [[ "$out" == _* ]]; do out="${out#_}"; done
  while [[ "$out" == *_ ]]; do out="${out%_}"; done
  if [[ ${#out} -gt 56 ]]; then
    out="${out:0:56}"
    while [[ "$out" == *_ ]]; do out="${out%_}"; done
  fi
  printf '%s' "$out"
}

usage() {
  echo "Usage: $0 [OPTIONS]"
  echo ""
  echo "Run test benchmarks under configs/tests/. Each test dir with config.json and"
  echo "experiments.json is executed; run data under exp_runs_test/<run_id>/<test_name>/,"
  echo "plots under exp_runs_test/<run_id>/plots/<test_name>/, merged PDF at .../plots/all_tests_plots.pdf."
  echo "<run_id> is YYYYMMDD_HHMMSS, or that plus _<comment> if --comment is set."
  echo ""
  echo "Options:"
  echo "  -h, --help       Show this help and exit"
  echo "  --bench NAME    Run only benchmarks matching NAME (comma-separated). Names are"
  echo "                  configs/tests/<name> dirs (e.g. multi-api); with --also-hotel-social,"
  echo "                  hotel and social are also valid; with --also-alibaba, alibaba-large is valid."
  echo "  --type TYPES    Run only experiments whose JSON \"type\" matches (comma-separated)"
  echo "  --system SYS    Run only experiments with system SYS (plain, sidecar; comma-separated)"
  echo "  --num-apis N    Run only experiments with N APIs (comma-separated, e.g. 1,3)"
  echo "  --shared-generator  Allow fewer generators than APIs (assign round-robin)"
  echo "  --remote          Use CloudLab manifest for hosts (requires --cloudlab-manifest, --num-generators)"
  echo "  --cloudlab-manifest PATH   Experiment manifest XML from CloudLab portal"
  echo "  --cloudlab-ssh-user USER   Default SSH user if manifest has bare hostnames"
  echo "  --num-generators N   Override config num_generators (local). With --remote/--remote-clean,"
  echo "                       required; passed to executor with manifest hosts."
  echo "  --remote-clean       With manifest + num-generators: rm .roshanfer_provisioned on all nodes,"
  echo "                       tear down K8s on deployment nodes (first line of deploy list = server)."
  echo "                       Use with --remote to clean then run tests; alone = clean only and exit."
  echo "  --also-hotel-social  After tests, run configs/hotel and configs/social; --bench"
  echo "                       filters these too when set (e.g. --bench hotel runs hotel only)."
  echo "  --also-alibaba       After tests, run configs/alibaba-large (benchmark alibaba-large);"
  echo "                       --bench filters this too when set (e.g. --bench alibaba-large)."
  echo "  --nanolog-debug      Build sidecar with NanoLog M# metrics; for sidecar runs, collect"
  echo "                       compressed logs, decompress, plot repeat_<n>/nanolog/metrics-<sidecar-stem>.pdf."
  echo "  --comment TEXT       Append sanitized TEXT to run folder name after the timestamp"
  echo "                       (e.g. exp_runs_test/20260403_120000_my-label/)."
  echo "  --namespace NS       Use namespace-specific configs (config-<ns>.json,"
  echo "                       experiments-<ns>.json). Default namespace uses config.json."
  echo ""
  echo "Examples:"
  echo "  $0"
  echo "  $0 --bench multi-api"
  echo "  $0 --num-generators 2 --bench leaf-diverse"
  echo "  $0 --remote --cloudlab-manifest ~/manifest.xml --num-generators 3 --cloudlab-ssh-user ubuntu"
  echo "  $0 --remote-clean --cloudlab-manifest ~/m.xml --num-generators 3 --cloudlab-ssh-user farzad11"
  echo "  $0 --remote --remote-clean --cloudlab-manifest ~/m.xml --num-generators 3   # clean then run"
  echo "  $0 --also-hotel-social"
  echo "  $0 --also-alibaba"
  echo "  $0 --bench alibaba-large --also-alibaba"
  echo "  $0 --bench chain-2 --comment sidecar-tuning"
  echo "  $0 --namespace newsys --bench leaf-diverse"
}

BENCH_FILTER=""
TYPE_FILTER=""
SYSTEM_FILTER=""
NUM_APIS_FILTER=""
SHARED_GENERATOR=""
REMOTE=""
CLOUDLAB_MANIFEST=""
CLOUDLAB_SSH_USER=""
REMOTE_NUM_GENERATORS=""
REMOTE_CLEAN=""
ALSO_HOTEL_SOCIAL=""
ALSO_ALIBABA=""
NANOLOG_DEBUG=""
RUN_COMMENT=""
NAMESPACE="default"

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
    --remote)
      REMOTE=1
      shift
      ;;
    --cloudlab-manifest)
      [[ -z "${2:-}" ]] && { echo "Missing value for --cloudlab-manifest"; usage; exit 1; }
      CLOUDLAB_MANIFEST="$2"
      shift 2
      ;;
    --cloudlab-ssh-user)
      [[ -z "${2:-}" ]] && { echo "Missing value for --cloudlab-ssh-user"; usage; exit 1; }
      CLOUDLAB_SSH_USER="$2"
      shift 2
      ;;
    --num-generators)
      [[ -z "${2:-}" ]] && { echo "Missing value for --num-generators"; usage; exit 1; }
      REMOTE_NUM_GENERATORS="$2"
      shift 2
      ;;
    --remote-clean)
      REMOTE_CLEAN=1
      shift
      ;;
    --also-hotel-social)
      ALSO_HOTEL_SOCIAL=1
      shift
      ;;
    --also-alibaba)
      ALSO_ALIBABA=1
      shift
      ;;
    --nanolog-debug)
      NANOLOG_DEBUG=1
      shift
      ;;
    --comment)
      [[ -z "${2:-}" ]] && { echo "Missing value for --comment"; usage; exit 1; }
      RUN_COMMENT="$2"
      shift 2
      ;;
    --namespace)
      [[ -z "${2:-}" ]] && { echo "Missing value for --namespace"; usage; exit 1; }
      NAMESPACE="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ -n "$REMOTE" || -n "$REMOTE_CLEAN" ]]; then
  [[ -n "$CLOUDLAB_MANIFEST" ]] || { echo "--cloudlab-manifest is required for --remote / --remote-clean"; exit 1; }
  [[ -n "$REMOTE_NUM_GENERATORS" ]] || { echo "--num-generators is required for --remote / --remote-clean"; exit 1; }
fi

PYTHON=python
[[ -x .venv/bin/python ]] && PYTHON=.venv/bin/python

TESTS_ROOT="configs/tests"
OUTPUT_BASE="./exp_runs_test"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RUN_DIR_ID="$TIMESTAMP"
if [[ -n "$RUN_COMMENT" ]]; then
  _san=$(sanitize_run_comment "$RUN_COMMENT")
  if [[ -n "$_san" ]]; then
    RUN_DIR_ID="${TIMESTAMP}_${_san}"
  else
    echo "Warning: --comment became empty after sanitization; using timestamp-only folder name."
  fi
fi
echo "Run directory: $OUTPUT_BASE/$RUN_DIR_ID"
PLOTS_ROOT="$OUTPUT_BASE/${RUN_DIR_ID}/plots"
mkdir -p "$OUTPUT_BASE/${RUN_DIR_ID}"
echo "$NAMESPACE" > "$OUTPUT_BASE/${RUN_DIR_ID}/.namespace"
echo "Namespace: $NAMESPACE"
failed=0

REMOTE_ARGS=()
HOSTS_OUT=""
if [[ -n "$REMOTE" || -n "$REMOTE_CLEAN" ]]; then
  if [[ -n "$REMOTE" ]]; then
    HOSTS_OUT="$OUTPUT_BASE/${RUN_DIR_ID}/cloudlab_hosts.txt"
    mkdir -p "$(dirname "$HOSTS_OUT")"
  else
    HOSTS_OUT=$(mktemp)
    trap 'rm -f "$HOSTS_OUT"' EXIT
  fi
  CL=( "$PYTHON" -m exec.cloudlab_hosts --manifest "$CLOUDLAB_MANIFEST" -o "$HOSTS_OUT" )
  [[ -n "$CLOUDLAB_SSH_USER" ]] && CL+=( --ssh-user "$CLOUDLAB_SSH_USER" )
  "${CL[@]}" || exit 1
  [[ -n "$REMOTE" ]] && REMOTE_ARGS=( --hosts-file "$HOSTS_OUT" --num-generators "$REMOTE_NUM_GENERATORS" )
fi

remote_clean_hosts() {
  local hf="$1" ng="$2"
  local kcfg="$PWD/benchmarks/k8s/config.env"
  # shellcheck source=/dev/null
  [[ -f "$kcfg" ]] && source "$kcfg"
  local ssh_o="${SSH_OPTS:--o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null}"
  local def_u="${SSH_USER:-ubuntu}"
  echo "Removing .roshanfer_provisioned on all manifest hosts..."
  while IFS= read -r entry; do
    local u h
    if [[ "$entry" == *"@"* ]]; then
      u="${entry%%@*}"
      h="${entry#*@}"
    else
      u="$def_u"
      h="$entry"
    fi
    ssh $ssh_o "$u@$h" "rm -f .roshanfer_provisioned" && echo "  cleared provision marker $u@$h" || echo "  warn: could not clear $u@$h"
  done < <(grep -vE '^\s*#|^\s*$' "$hf")
  local deploy_tmp
  deploy_tmp=$(mktemp)
  tail -n "+$((ng + 1))" "$hf" | grep -vE '^\s*#|^\s*$' > "$deploy_tmp" || true
  if [[ ! -s "$deploy_tmp" ]]; then
    rm -f "$deploy_tmp"
    echo "No deployment hosts (need more hosts than num_generators). Skipping K8s delete."
    return 0
  fi
  echo "Running benchmarks/k8s/delete.sh with deployment hosts ($(wc -l < "$deploy_tmp") nodes)..."
  HOSTS_FILE="$deploy_tmp" "$PWD/benchmarks/k8s/delete.sh" || { rm -f "$deploy_tmp"; return 1; }
  rm -f "$deploy_tmp"
}

if [[ -n "$REMOTE_CLEAN" ]]; then
  remote_clean_hosts "$HOSTS_OUT" "$REMOTE_NUM_GENERATORS" || exit 1
  [[ -z "$REMOTE" ]] && { echo "Remote clean finished."; exit 0; }
fi

EXTRA_ARGS=()
[[ -n "$TYPE_FILTER" ]] && EXTRA_ARGS+=(--only-types "$TYPE_FILTER")
[[ -n "$SYSTEM_FILTER" ]] && EXTRA_ARGS+=(--only-system "$SYSTEM_FILTER")
[[ -n "$NUM_APIS_FILTER" ]] && EXTRA_ARGS+=(--only-num-apis "$NUM_APIS_FILTER")
[[ -n "$SHARED_GENERATOR" ]] && EXTRA_ARGS+=(--shared-generator)
[[ -z "$REMOTE" && -n "$REMOTE_NUM_GENERATORS" ]] && EXTRA_ARGS+=(--num-generators "$REMOTE_NUM_GENERATORS")
[[ -n "$NANOLOG_DEBUG" ]] && EXTRA_ARGS+=(--nanolog-debug)

resolve_suite_paths() {
  local name="$1"
  local json
  json=$($PYTHON -m exec.namespace resolve-by-name --name "$name" --namespace "$NAMESPACE" --tests-root "$TESTS_ROOT" 2>/dev/null) || return 1
  RESOLVED_CONFIG=$(echo "$json" | $PYTHON -c "import json,sys; d=json.load(sys.stdin); print(d['config'])")
  RESOLVED_EXPERIMENTS=$(echo "$json" | $PYTHON -c "import json,sys; d=json.load(sys.stdin); print(d['experiments'])")
  RESOLVED_MERGED=$(echo "$json" | $PYTHON -c "import json,sys; d=json.load(sys.stdin); m=d.get('merged'); print(m or '')")
}

run_bench() {
  local name="$1" config="$2" experiments="$3" merged="${4:-}"
  local out_dir="$OUTPUT_BASE/${RUN_DIR_ID}/${name}"
  echo "Running $name -> $out_dir"
  if ! $PYTHON -m exec.executor --experiments-file "$experiments" --config "$config" \
      --output-base-dir "$out_dir" "${REMOTE_ARGS[@]}" "${EXTRA_ARGS[@]}"; then
    failed=$((failed + 1))
    return
  fi
  experiment_index=$($PYTHON -c "import json; print(json.load(open('$config')).get('experiment_index','$name'))")
  local run_summary="$out_dir/exp-${experiment_index}/run_summary.jsonl"
  if [[ ! -f "$run_summary" ]]; then
    echo "Skipping plots for $name (no run summary — filters may have excluded all experiments)"
    return
  fi
  echo "Plotting $name -> $PLOTS_ROOT/$name"
  $PYTHON -m exec.plot_runner --experiment-index "$experiment_index" \
    --experiments-root "$out_dir" --config-file "$config" --output-dir "$PLOTS_ROOT/$name" || echo "Warning: plot failed for $name"
  if [[ -n "$merged" && -f "$merged" ]]; then
    echo "Merged plots $name -> $PLOTS_ROOT/$name/merged"
    $PYTHON -m exec.merged_plot_runner --merged-config "$merged" \
      --experiments-file "$experiments" --experiments-root "$out_dir" \
      --output-dir "$PLOTS_ROOT/$name/merged" --experiment-index "$experiment_index" \
      --config "$config" || echo "Warning: merged plot failed for $name"
  fi
}

# Empty BENCH_FILTER = no restriction; else NAME must appear as a comma-separated token.
bench_filter_allows() {
  local n="$1"
  [[ -z "$BENCH_FILTER" ]] && return 0
  [[ ",$BENCH_FILTER," == *",$n,"* ]]
}

if [[ -n "$BENCH_FILTER" ]]; then
  IFS=',' read -ra _bench_names <<< "$BENCH_FILTER"
  for test_name in "${_bench_names[@]}"; do
    test_name="${test_name#"${test_name%%[![:space:]]*}"}"
    test_name="${test_name%"${test_name##*[![:space:]]}"}"
    [[ -z "$test_name" ]] && continue
    case "$test_name" in
      hotel|social|alibaba-large) continue ;;
    esac
    if ! resolve_suite_paths "$test_name"; then
      echo "Error: no config+experiments for bench '$test_name' in namespace '$NAMESPACE'"
      failed=$((failed + 1))
    fi
  done
fi

while IFS= read -r test_name; do
  [[ -z "$test_name" ]] && continue
  bench_filter_allows "$test_name" || continue
  resolve_suite_paths "$test_name" || continue
  run_bench "$test_name" "$RESOLVED_CONFIG" "$RESOLVED_EXPERIMENTS" "$RESOLVED_MERGED"
done < <($PYTHON -m exec.namespace list-tests --root "$TESTS_ROOT" --namespace "$NAMESPACE")

if [[ -n "$ALSO_HOTEL_SOCIAL" ]]; then
  for opt_name in hotel social; do
    bench_filter_allows "$opt_name" || continue
    if resolve_suite_paths "$opt_name"; then
      run_bench "$opt_name" "$RESOLVED_CONFIG" "$RESOLVED_EXPERIMENTS" "$RESOLVED_MERGED"
    elif [[ -n "$BENCH_FILTER" && ",$BENCH_FILTER," == *",$opt_name,"* ]]; then
      echo "Error: no config+experiments for bench '$opt_name' in namespace '$NAMESPACE'"
      failed=$((failed + 1))
    fi
  done
fi

if [[ -n "$ALSO_ALIBABA" ]]; then
  if bench_filter_allows alibaba-large; then
    if resolve_suite_paths "alibaba-large"; then
      run_bench "alibaba-large" "$RESOLVED_CONFIG" "$RESOLVED_EXPERIMENTS" "$RESOLVED_MERGED"
    elif [[ -n "$BENCH_FILTER" && ",$BENCH_FILTER," == *",alibaba-large,"* ]]; then
      echo "Error: no config+experiments for bench 'alibaba-large' in namespace '$NAMESPACE'"
      failed=$((failed + 1))
    fi
  fi
fi

if [[ -d "$PLOTS_ROOT" ]]; then
  echo "Merging all plot PDFs -> $PLOTS_ROOT/all_tests_plots.pdf"
  $PYTHON -m exec.merge_plot_pdfs "$PLOTS_ROOT" || echo "Warning: merge_plot_pdfs failed"
fi

if [[ $failed -gt 0 ]]; then
  echo "Failed: $failed test(s)"
  exit $failed
fi
echo "All tests passed"
