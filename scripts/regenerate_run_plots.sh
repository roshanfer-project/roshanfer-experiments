#!/usr/bin/env bash
# Regenerate plot_runner + merged_plot_runner + merge_plot_pdfs for one exp_runs_test run.
# Also regenerates nanolog debug PDFs when *.nanolog.log files are present.
# Suites: configs/tests/<name>/ plus hotel, social, alibaba-large (same layout as run_tests.sh).
# Usage: ./scripts/regenerate_run_plots.sh [--namespace NS] [--merged-only] <run_id>
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

usage() {
  echo "Usage: $0 [--namespace NS] [--merged-only] <run_id>" >&2
  echo "  run_id: folder name under exp_runs_test/ (e.g. 20260410_123116_dynamic-large)" >&2
  echo "  --namespace NS: override namespace (default: read from run dir .namespace)" >&2
  echo "  --merged-only: skip plot_runner and nanolog; only merged_plot_runner + merge_plot_pdfs" >&2
  exit 1
}

NAMESPACE=""
RUN_ID=""
MERGED_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace)
      [[ -z "${2:-}" ]] && usage
      NAMESPACE="$2"
      shift 2
      ;;
    --merged-only)
      MERGED_ONLY=1
      shift
      ;;
    -h|--help)
      usage
      ;;
    *)
      [[ -z "$RUN_ID" ]] || usage
      RUN_ID="$1"
      shift
      ;;
  esac
done

[[ -n "$RUN_ID" ]] || usage
RUN_ROOT="exp_runs_test/${RUN_ID}"
[[ -d "$RUN_ROOT" ]] || { echo "Not a directory: $RUN_ROOT" >&2; exit 1; }

PYTHON=python3
[[ -x .venv/bin/python ]] && PYTHON=.venv/bin/python

if [[ -z "$NAMESPACE" ]]; then
  NAMESPACE=$($PYTHON -c "from exec.namespace import read_run_namespace; from pathlib import Path; print(read_run_namespace(Path('$RUN_ROOT')))")
fi

resolve_suite_configs() {
  local name="$1"
  cfg=""
  exp=""
  merged=""
  local json
  json=$($PYTHON -m exec.namespace resolve-by-name --name "$name" --namespace "$NAMESPACE" 2>/dev/null) || return 0
  cfg=$(echo "$json" | $PYTHON -c "import json,sys; d=json.load(sys.stdin); print(d['config'])")
  exp=$(echo "$json" | $PYTHON -c "import json,sys; d=json.load(sys.stdin); print(d['experiments'])")
  merged=$(echo "$json" | $PYTHON -c "import json,sys; d=json.load(sys.stdin); m=d.get('merged'); print(m or '')")
}

any=0
for dir in "$RUN_ROOT"/*/; do
  [[ -d "$dir" ]] || continue
  name="$(basename "$dir")"
  [[ "$name" == "plots" ]] && continue

  resolve_suite_configs "$name"
  [[ -n "$cfg" && -f "$cfg" && -f "$exp" ]] || continue

  any=1
  idx="$("$PYTHON" -c "import json,sys; c=json.load(open(sys.argv[1])); print(c.get(\"experiment_index\", sys.argv[2]))" "$cfg" "$name")"
  out="$RUN_ROOT/plots/$name"
  echo "[$RUN_ID] $name (namespace=$NAMESPACE, experiment_index=$idx)"
  if [[ "$MERGED_ONLY" -eq 0 ]]; then
    "$PYTHON" -m exec.plot_runner \
      --experiment-index "$idx" \
      --experiments-root "$RUN_ROOT/$name" \
      --config-file "$cfg" \
      --output-dir "$out"
  fi
  if [[ -n "$merged" && -f "$merged" ]]; then
    "$PYTHON" -m exec.merged_plot_runner \
      --merged-config "$merged" \
      --experiments-file "$exp" \
      --experiments-root "$RUN_ROOT/$name" \
      --output-dir "$out/merged" \
      --experiment-index "$idx" \
      --config "$cfg"
  fi
done

if [[ "$any" -eq 0 ]]; then
  echo "No suite dirs under $RUN_ROOT matched configs for namespace '$NAMESPACE'" >&2
  exit 1
fi

# Nanolog debug PDFs: unit/raw/service_logs/<stem>.nanolog.log -> unit/nanolog/metrics-<stem>.pdf
if [[ "$MERGED_ONLY" -eq 0 ]]; then
  mapfile -t NANOLOGS < <(find "$RUN_ROOT" -type f -name '*.nanolog.log' | sort)
  if ((${#NANOLOGS[@]})); then
    echo "[$RUN_ID] regenerating ${#NANOLOGS[@]} nanolog plot(s)"
    for log in "${NANOLOGS[@]}"; do
      stem="$(basename "$log" .nanolog.log)"
      unit_dir="$(cd "$(dirname "$log")/../.." && pwd)"
      out_pdf="$unit_dir/nanolog/metrics-${stem}.pdf"
      mkdir -p "$(dirname "$out_pdf")"
      "$PYTHON" -m exec.nanolog_metrics_plot --files "$log" --output "$out_pdf"
    done
  fi
fi

mkdir -p "$RUN_ROOT/plots"
"$PYTHON" -m exec.merge_plot_pdfs "$RUN_ROOT/plots" --namespace "$NAMESPACE"
echo "Done: $RUN_ROOT/plots/all_tests_plots.pdf"
