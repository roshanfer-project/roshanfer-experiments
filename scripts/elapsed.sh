# Print wall-clock duration of the calling script on EXIT.
# Source this file (do not execute).

_elapsed_start=$(date +%s)
_elapsed_name=$(basename "$0")

_elapsed_report() {
  local s=$(( $(date +%s) - _elapsed_start ))
  printf '[elapsed] %s: %dm%02ds (%ds)\n' "$_elapsed_name" "$((s / 60))" "$((s % 60))" "$s"
}

_elapsed_existing=$(trap -p EXIT 2>/dev/null || true)
if [[ -z "$_elapsed_existing" ]]; then
  trap '_elapsed_report' EXIT
else
  _elapsed_cmd="${_elapsed_existing#trap -- \'}"
  _elapsed_cmd="${_elapsed_cmd%\' EXIT}"
  trap "$_elapsed_cmd; _elapsed_report" EXIT
fi
