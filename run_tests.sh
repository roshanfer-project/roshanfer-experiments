#!/bin/bash
# Run all test benchmarks under configs/tests/
# Each run: data under ./exp_runs_test/<run_id>/<test_name>/; run_id is YYYYMMDD_HHMMSS
# optionally with _<comment>; plots under ./exp_runs_test/<run_id>/plots/<test_name>/;
# combined PDF at plots/all_tests_plots.pdf
# Use --namespace to pick config-<ns>.json / experiments-<ns>.json (default: config.json).

cd "$(dirname "$0")"

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
  echo "Only one instance may run at a time (lock /tmp/roshanfer-run_tests.lock)."
  echo ""
  echo "Options:"
  echo "  -h, --help       Show this help and exit"
  echo "  --bench NAME    Run only benchmarks matching NAME (comma-separated). Names are"
  echo "                  configs/tests/<name> dirs (e.g. multi-api); with --also-hotel-social,"
  echo "                  hotel and social are also valid; with --also-alibaba, alibaba-large is valid."
  echo "  --type TYPES    Run only experiments whose JSON \"type\" matches (comma-separated)"
  echo "  --system SYS    Run only experiments with system SYS (plain, roshanfer, approx, envoy; comma-separated)"
  echo "  --num-apis N    Run only experiments with N APIs (comma-separated, e.g. 1,3)"
  echo "  --shared-generator  Allow fewer generators than APIs (assign round-robin)"
  echo "  --remote          Use CloudLab manifest for hosts (requires manifest, --num-generators,"
  echo "                    and CLOUDLAB_USER from --cloudlab-user or config.env)"
  echo "  --cloudlab-manifest PATH   Experiment manifest XML (or CLOUDLAB_MANIFEST in config.env)"
  echo "  --cloudlab-user USER       CloudLab username (or CLOUDLAB_USER in config.env)"
  echo "  --num-generators N   Override config num_generators (local). With --remote/--remote-clean,"
  echo "                       required; passed to executor with manifest hosts."
  echo "  --remote-clean       With manifest + num-generators: rm .roshanfer_provisioned on all nodes,"
  echo "                       tear down K8s on deployment nodes (first line of deploy list = server)."
  echo "                       Use with --remote to clean then run tests; alone = clean only and exit."
  echo "  --also-hotel-social  After tests, run configs/hotel and configs/social; --bench"
  echo "                       filters these too when set (e.g. --bench hotel runs hotel only)."
  echo "  --also-alibaba       After tests, run configs/alibaba-large (benchmark alibaba-large);"
  echo "                       --bench filters this too when set (e.g. --bench alibaba-large)."
  echo "  --nanolog-debug      Build sidecar with NanoLog M# metrics; for roshanfer runs, collect"
  echo "                       compressed logs, decompress, plot repeat_<n>/nanolog/metrics-<sidecar-stem>.pdf."
  echo "  --debug              Deploy sidecar/approx* with deploy.sh debug (glog via"
  echo "                       k8s/sidecar-debug-glog.env, debug restart behavior)."
  echo "  --comment TEXT       Append sanitized TEXT to run folder name after the timestamp"
  echo "                       (e.g. exp_runs_test/20260403_120000_my-label/)."
  echo "  --namespace NS       Use namespace-specific configs (config-<ns>.json,"
  echo "                       experiments-<ns>.json). Default namespace uses config.json."
  echo "  --branch NAME        Branch to provision on remotes for both roshanfer-experments"
  echo "                       and benchmarks (same name required). Default: local active"
  echo "                       branch. Aborts if local parent and benchmarks branches differ,"
  echo "                       or if --branch does not match that local pair (exec not started)."
  echo ""
  echo "Examples:"
  echo "  $0"
  echo "  $0 --bench multi-api"
  echo "  $0 --num-generators 3 --bench leaf-1-2"
  echo "  $0 --remote --cloudlab-manifest ./manifest.xml --num-generators 3"
  echo "  $0 --remote --branch lb-explore --cloudlab-manifest ./manifest.xml --num-generators 3"
  echo "  $0 --remote-clean --num-generators 3"
  echo "  $0 --remote --remote-clean --num-generators 3   # clean then run"
  echo "  $0 --also-hotel-social"
  echo "  $0 --also-alibaba"
  echo "  $0 --bench alibaba-large --also-alibaba"
  echo "  $0 --bench chain-2 --comment roshanfer-tuning"
  echo "  $0 --bench chain-2 --system approx --debug --comment approx-test"
  echo "  $0 --namespace newsys --bench leaf-1-2"
}

BENCH_FILTER=""
TYPE_FILTER=""
SYSTEM_FILTER=""
NUM_APIS_FILTER=""
SHARED_GENERATOR=""
REMOTE=""
CLOUDLAB_MANIFEST=""
CLOUDLAB_USER=""
REMOTE_NUM_GENERATORS=""
REMOTE_CLEAN=""
ALSO_HOTEL_SOCIAL=""
ALSO_ALIBABA=""
NANOLOG_DEBUG=""
SIDECAR_DEPLOY_DEBUG=""
RUN_COMMENT=""
NAMESPACE="default"
BRANCH_ARG=""

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
    --cloudlab-user)
      [[ -z "${2:-}" ]] && { echo "Missing value for --cloudlab-user"; usage; exit 1; }
      CLOUDLAB_USER="$2"
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
    --debug)
      SIDECAR_DEPLOY_DEBUG=1
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
    --branch)
      [[ -z "${2:-}" ]] && { echo "Missing value for --branch"; usage; exit 1; }
      BRANCH_ARG="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

# Local preflight: parent and benchmarks must be on the same named branch.
PARENT_BRANCH="$(git branch --show-current 2>/dev/null || true)"
BENCH_BRANCH="$(git -C benchmarks branch --show-current 2>/dev/null || true)"
if [[ -z "$PARENT_BRANCH" || -z "$BENCH_BRANCH" ]]; then
  echo "Error: local branches must be checked out by name (not detached)."
  echo "  roshanfer-experments: '${PARENT_BRANCH:-detached/empty}'"
  echo "  benchmarks:           '${BENCH_BRANCH:-detached/empty}'"
  exit 1
fi
if [[ "$PARENT_BRANCH" != "$BENCH_BRANCH" ]]; then
  echo "Error: local branch mismatch; both repos must use the same branch name."
  echo "  roshanfer-experments: $PARENT_BRANCH"
  echo "  benchmarks:           $BENCH_BRANCH"
  echo "Align them (e.g. git checkout <branch> in both) before running. Not starting exec."
  exit 1
fi
if [[ -n "$BRANCH_ARG" && "$BRANCH_ARG" != "$PARENT_BRANCH" ]]; then
  echo "Error: --branch ($BRANCH_ARG) does not match local branch ($PARENT_BRANCH)."
  echo "Checkout that branch locally in both repos, or omit --branch to use the local pair."
  exit 1
fi
BRANCH="$PARENT_BRANCH"
echo "Provision branch: $BRANCH (roshanfer-experments + benchmarks)"

LOCKFILE=/tmp/roshanfer-run_tests.lock
# First creator: 0666 so other Unix users can flock the same file.
(umask 000; set -o noclobber; : > "$LOCKFILE") 2>/dev/null || true
exec 9>>"$LOCKFILE" || { echo "error: cannot open lock $LOCKFILE"; exit 1; }
if ! flock -n 9; then
  echo "error: run_tests.sh already running (lock $LOCKFILE)"
  cat "$LOCKFILE" 2>/dev/null || true
  exit 1
fi
printf 'pid=%s user=%s started=%s\n' "$$" "$(id -un)" "$(date -Iseconds)" > "$LOCKFILE"

CLI_CLOUDLAB_MANIFEST="$CLOUDLAB_MANIFEST"
CLI_CLOUDLAB_USER="$CLOUDLAB_USER"

# shellcheck source=/dev/null
source ./init_env.sh
PYTHON=python

[[ -n "$CLI_CLOUDLAB_MANIFEST" ]] && CLOUDLAB_MANIFEST="$CLI_CLOUDLAB_MANIFEST"
[[ -n "$CLI_CLOUDLAB_USER" ]] && CLOUDLAB_USER="$CLI_CLOUDLAB_USER"

req="${REQUIRE_REMOTE:-0}"
req="${req,,}"
if [[ "$req" == "1" || "$req" == "true" || "$req" == "yes" ]]; then
  if [[ -z "$REMOTE" && -z "$REMOTE_CLEAN" ]]; then
    echo "REQUIRE_REMOTE is set. Pass --remote or --remote-clean, or set REQUIRE_REMOTE=0 in config.env."
    exit 1
  fi
fi

if [[ -n "$REMOTE" || -n "$REMOTE_CLEAN" ]]; then
  [[ -n "$CLOUDLAB_MANIFEST" ]] || { echo "CLOUDLAB_MANIFEST or --cloudlab-manifest is required for --remote / --remote-clean"; exit 1; }
  if [[ ! -f "$CLOUDLAB_MANIFEST" ]]; then
    echo "Manifest not found: $CLOUDLAB_MANIFEST"
    echo "Place the CloudLab experiment XML at that path yourself (this is not fetched automatically)."
    echo "If you used ./scripts/cloudlab_enter.sh and are on the control node, you can run ./scripts/fetch_manifest.sh."
    exit 1
  fi
  [[ -n "$CLOUDLAB_USER" ]] || { echo "CLOUDLAB_USER or --cloudlab-user is required for --remote / --remote-clean"; exit 1; }
  [[ -n "$REMOTE_NUM_GENERATORS" ]] || { echo "--num-generators is required for --remote / --remote-clean"; exit 1; }
fi

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
  CL=( "$PYTHON" -m exec.cloudlab_hosts --manifest "$CLOUDLAB_MANIFEST" -o "$HOSTS_OUT" --user "$CLOUDLAB_USER" )
  "${CL[@]}" || exit 1
  [[ -n "$REMOTE" ]] && REMOTE_ARGS=( --hosts-file "$HOSTS_OUT" --num-generators "$REMOTE_NUM_GENERATORS" )
fi

LOCAL_HOSTS_ARGS=()
if [[ -z "$REMOTE" && -z "$REMOTE_CLEAN" ]]; then
  if [[ ! -f hosts.txt ]]; then
    echo "hosts.txt not found. Copy hosts.txt.example to hosts.txt and set user@host lines."
    exit 1
  fi
  LOCAL_HOSTS_ARGS=( --hosts-file hosts.txt )
fi

remote_clean_hosts() {
  local hf="$1" ng="$2"
  local kcfg="$PWD/benchmarks/k8s/config.env"
  # shellcheck source=/dev/null
  [[ -f "$kcfg" ]] && source "$kcfg"
  local ssh_o="${SSH_OPTS:--o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null}"
  echo "Removing .roshanfer_provisioned on all manifest hosts..."
  while IFS= read -r entry; do
    local u h
    if [[ "$entry" != *"@"* ]]; then
      echo "Host line must be user@host: $entry"
      return 1
    fi
    u="${entry%%@*}"
    h="${entry#*@}"
    ssh $ssh_o "$u@$h" "rm -f .roshanfer_provisioned" && echo "  cleared provision marker $u@$h" || echo "  warn: could not clear $u@$h"
  done < <(grep -vE '^\s*#|^\s*$' "$hf")
  echo "Running benchmarks/k8s/delete.sh (NUM_GENERATORS=$ng)..."
  HOSTS_FILE="$hf" NUM_GENERATORS="$ng" "$PWD/benchmarks/k8s/delete.sh"
}

REMOTE_CLEAN_SEC=""
if [[ -n "$REMOTE_CLEAN" ]]; then
  _rc_start=$(date +%s)
  remote_clean_hosts "$HOSTS_OUT" "$REMOTE_NUM_GENERATORS" || exit 1
  REMOTE_CLEAN_SEC=$(( $(date +%s) - _rc_start ))
  echo "remote-clean ${REMOTE_CLEAN_SEC}s"
  if [[ -z "$REMOTE" ]]; then
    mkdir -p "$OUTPUT_BASE/${RUN_DIR_ID}"
    $PYTHON -m exec.timings summary --run-dir "$OUTPUT_BASE/${RUN_DIR_ID}" --remote-clean-sec "$REMOTE_CLEAN_SEC"
    echo "Remote clean finished."
    exit 0
  fi
fi

EXTRA_ARGS=(--branch "$BRANCH")
[[ -n "$TYPE_FILTER" ]] && EXTRA_ARGS+=(--only-types "$TYPE_FILTER")
[[ -n "$SYSTEM_FILTER" ]] && EXTRA_ARGS+=(--only-system "$SYSTEM_FILTER")
[[ -n "$NUM_APIS_FILTER" ]] && EXTRA_ARGS+=(--only-num-apis "$NUM_APIS_FILTER")
[[ -n "$SHARED_GENERATOR" ]] && EXTRA_ARGS+=(--shared-generator)
[[ -z "$REMOTE" && -n "$REMOTE_NUM_GENERATORS" ]] && EXTRA_ARGS+=(--num-generators "$REMOTE_NUM_GENERATORS")
[[ -n "$NANOLOG_DEBUG" ]] && EXTRA_ARGS+=(--nanolog-debug)
[[ -n "$SIDECAR_DEPLOY_DEBUG" ]] && EXTRA_ARGS+=(--debug)

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
  local exec_rc=0
  if ! $PYTHON -m exec.executor --experiments-file "$experiments" --config "$config" \
      --output-base-dir "$out_dir" "${REMOTE_ARGS[@]}" "${LOCAL_HOSTS_ARGS[@]}" "${EXTRA_ARGS[@]}"; then
    failed=$((failed + 1))
    exec_rc=1
  fi
  experiment_index=$($PYTHON -c "import json; print(json.load(open('$config')).get('experiment_index','$name'))")
  local timings_file="$out_dir/exp-${experiment_index}/timings.json"
  local plot_sec=0
  if [[ $exec_rc -eq 0 ]]; then
    local run_summary="$out_dir/exp-${experiment_index}/run_summary.jsonl"
    if [[ ! -f "$run_summary" ]]; then
      echo "Skipping plots for $name (no run summary — filters may have excluded all experiments)"
    else
      echo "Plotting $name -> $PLOTS_ROOT/$name"
      local plot_start
      plot_start=$(date +%s)
      $PYTHON -m exec.plot_runner --experiment-index "$experiment_index" \
        --experiments-root "$out_dir" --config-file "$config" --output-dir "$PLOTS_ROOT/$name" || echo "Warning: plot failed for $name"
      if [[ -n "$merged" && -f "$merged" ]]; then
        echo "Merged plots $name -> $PLOTS_ROOT/$name/merged"
        $PYTHON -m exec.merged_plot_runner --merged-config "$merged" \
          --experiments-file "$experiments" --experiments-root "$out_dir" \
          --output-dir "$PLOTS_ROOT/$name/merged" --experiment-index "$experiment_index" \
          --config "$config" || echo "Warning: merged plot failed for $name"
      fi
      plot_sec=$(( $(date +%s) - plot_start ))
    fi
  fi
  if [[ -f "$timings_file" ]]; then
    $PYTHON -m exec.timings apply-plot --file "$timings_file" --plot-sec "$plot_sec"
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

MERGE_PLOTS_SEC=""
if [[ -d "$PLOTS_ROOT" ]]; then
  echo "Merging all plot PDFs -> $PLOTS_ROOT/all_tests_plots.pdf"
  _mp_start=$(date +%s)
  $PYTHON -m exec.merge_plot_pdfs "$PLOTS_ROOT" || echo "Warning: merge_plot_pdfs failed"
  MERGE_PLOTS_SEC=$(( $(date +%s) - _mp_start ))
fi

echo ""
echo "=== Run timings ==="
SUMMARY_ARGS=( --run-dir "$OUTPUT_BASE/${RUN_DIR_ID}" )
[[ -n "$REMOTE_CLEAN_SEC" ]] && SUMMARY_ARGS+=(--remote-clean-sec "$REMOTE_CLEAN_SEC")
[[ -n "$MERGE_PLOTS_SEC" ]] && SUMMARY_ARGS+=(--merge-plots-sec "$MERGE_PLOTS_SEC")
$PYTHON -m exec.timings summary "${SUMMARY_ARGS[@]}" || echo "Warning: timings summary failed"

if [[ $failed -gt 0 ]]; then
  echo "Failed: $failed test(s)"
  exit $failed
fi
echo "All tests passed"
