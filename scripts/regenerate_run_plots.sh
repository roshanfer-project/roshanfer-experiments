#!/usr/bin/env bash
# Regenerate plot_runner + merged_plot_runner + merge_plot_pdfs for one exp_runs_test run.
# Suites: configs/tests/<name>/ plus hotel, social, alibaba-large (same layout as run_tests.sh).
# Usage: ./scripts/regenerate_run_plots.sh 20260410_123116_dynamic-large
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

usage() {
  echo "Usage: $0 <run_id>" >&2
  echo "  run_id: folder name under exp_runs_test/ (e.g. 20260410_123116_dynamic-large)" >&2
  exit 1
}

[[ $# -eq 1 ]] || usage
RUN_ID="$1"
RUN_ROOT="exp_runs_test/${RUN_ID}"
[[ -d "$RUN_ROOT" ]] || { echo "Not a directory: $RUN_ROOT" >&2; exit 1; }

PYTHON=python3
[[ -x .venv/bin/python ]] && PYTHON=.venv/bin/python

resolve_suite_configs() {
  local name="$1"
  cfg=""
  exp=""
  merged=""
  if [[ -f "configs/tests/${name}/config.json" ]]; then
    cfg="configs/tests/${name}/config.json"
    exp="configs/tests/${name}/experiments.json"
    merged="configs/tests/${name}/merged.yaml"
  elif [[ "$name" == "alibaba-large" ]]; then
    cfg="configs/alibaba-large/config.alibaba.json"
    exp="configs/alibaba-large/experiments.json"
    merged="configs/alibaba-large/merged.yaml"
  elif [[ "$name" == "hotel" ]]; then
    cfg="configs/hotel/config.hotel.json"
    exp="configs/hotel/hotel_experiments.json"
    merged="configs/hotel/merged.yaml"
  elif [[ "$name" == "social" ]]; then
    cfg="configs/social/config.social.json"
    exp="configs/social/social_experiments.json"
    merged="configs/social/merged_social.yaml"
  fi
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
  echo "[$RUN_ID] $name (experiment_index=$idx)"
  "$PYTHON" -m exec.plot_runner \
    --experiment-index "$idx" \
    --experiments-root "$RUN_ROOT/$name" \
    --config-file "$cfg" \
    --output-dir "$out"
  if [[ -f "$merged" ]]; then
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
  echo "No suite dirs under $RUN_ROOT matched configs/tests/<name> or hotel/social/alibaba-large configs" >&2
  exit 1
fi

mkdir -p "$RUN_ROOT/plots"
"$PYTHON" -m exec.merge_plot_pdfs "$RUN_ROOT/plots"
echo "Done: $RUN_ROOT/plots/all_tests_plots.pdf"
