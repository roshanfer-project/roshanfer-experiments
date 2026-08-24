#!/bin/bash
# rsync exp_runs_test/ from the CloudLab control node to this laptop clone.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST_REL="roshanfer-experiments"
DEFAULT_URL="wisc.cloudlab.us"

usage() {
  echo "Usage: $0 --name NAME --project PROJECT --user USER [--url URL]"
  echo "          [--run RUN_ID] [--plots-only] [--list] [--dest DIR]"
  echo ""
  echo "From the laptop clone, rsync ~/${DEST_REL}/exp_runs_test/ on the CloudLab"
  echo "control node (node0) to ./exp_runs_test/ here."
  echo ""
  echo "Options:"
  echo "  --name NAME       Experiment Name from the CloudLab experiment page (required)"
  echo "  --project PROJECT Experiment Project from the CloudLab experiment page (required)"
  echo "  --user USER       CloudLab username (required)"
  echo "  --url URL         Cluster hostname (default: ${DEFAULT_URL})"
  echo "  --run RUN_ID      Copy only this folder under exp_runs_test/"
  echo "  --plots-only      Copy only plots/ trees (PDFs); skip raw metrics"
  echo "  --list            Print remote run folder names and exit"
  echo "  --dest DIR        Local destination (default: ${ROOT}/exp_runs_test)"
  echo "  -h, --help        Show this help and exit"
  echo ""
  echo "SSH target: USER@node0.NAME.PROJECT-pg0.URL"
  echo ""
  echo "Examples:"
  echo "  $0 --name myexp --project MyProject --user alice"
  echo "  $0 --name myexp --project MyProject --user alice --plots-only"
  echo "  $0 --name myexp --project MyProject --user alice --run 20260403_120000_tutorial"
  echo "  $0 --name myexp --project MyProject --user alice --list"
}

NAME=""
PROJECT=""
CL_USER=""
URL="$DEFAULT_URL"
RUN_ID=""
PLOTS_ONLY=0
LIST_ONLY=0
DEST=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)
      [[ -z "${2:-}" ]] && { echo "Missing value for --name"; usage; exit 1; }
      NAME="$2"
      shift 2
      ;;
    --project)
      [[ -z "${2:-}" ]] && { echo "Missing value for --project"; usage; exit 1; }
      PROJECT="$2"
      shift 2
      ;;
    --user)
      [[ -z "${2:-}" ]] && { echo "Missing value for --user"; usage; exit 1; }
      CL_USER="$2"
      shift 2
      ;;
    --url)
      [[ -z "${2:-}" ]] && { echo "Missing value for --url"; usage; exit 1; }
      URL="$2"
      shift 2
      ;;
    --run)
      [[ -z "${2:-}" ]] && { echo "Missing value for --run"; usage; exit 1; }
      RUN_ID="$2"
      shift 2
      ;;
    --plots-only)
      PLOTS_ONLY=1
      shift
      ;;
    --list)
      LIST_ONLY=1
      shift
      ;;
    --dest)
      [[ -z "${2:-}" ]] && { echo "Missing value for --dest"; usage; exit 1; }
      DEST="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

[[ -n "$NAME" ]] || { echo "Missing --name"; usage; exit 1; }
[[ -n "$PROJECT" ]] || { echo "Missing --project"; usage; exit 1; }
[[ -n "$CL_USER" ]] || { echo "Missing --user"; usage; exit 1; }

if [[ -n "$RUN_ID" ]]; then
  if [[ "$RUN_ID" == */* || "$RUN_ID" == .* || "$RUN_ID" == *..* ]]; then
    echo "error: --run must be a single folder name under exp_runs_test/" >&2
    exit 1
  fi
fi

if ! command -v rsync >/dev/null 2>&1; then
  echo "error: rsync not found on this laptop (install rsync, e.g. apt install rsync / brew install rsync)." >&2
  exit 1
fi

HOST="${CL_USER}@node0.${NAME}.${PROJECT}-pg0.${URL}"
SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null}"

remote_cmd=$(cat <<EOF
set -euo pipefail
base="\$HOME/${DEST_REL}/exp_runs_test"
if [[ ! -d "\$base" ]]; then
  echo "error: no exp_runs_test on the control node (\$base)." >&2
  echo "Run experiments first, or check that cloudlab_enter.sh cloned the repo." >&2
  exit 1
fi
run=$(printf '%q' "$RUN_ID")
list_only=$(printf '%q' "$LIST_ONLY")
if [[ "\$list_only" == "1" ]]; then
  ls -1 "\$base"
  exit 0
fi
if [[ -n "\$run" ]]; then
  if [[ ! -d "\$base/\$run" ]]; then
    echo "error: run not found: \$base/\$run" >&2
    echo "Remote runs:" >&2
    ls -1 "\$base" >&2 || true
    exit 1
  fi
  printf '%s/%s\n' "\$base" "\$run"
else
  printf '%s\n' "\$base"
fi
EOF
)

# shellcheck disable=SC2086
REMOTE_PATH="$(ssh $SSH_OPTS "$HOST" "bash -c $(printf '%q' "$remote_cmd")")"

if [[ "$LIST_ONLY" -eq 1 ]]; then
  printf '%s\n' "$REMOTE_PATH"
  exit 0
fi

if [[ -z "$DEST" ]]; then
  DEST="${ROOT}/exp_runs_test"
fi
if [[ -n "$RUN_ID" ]]; then
  LOCAL_DEST="${DEST}/${RUN_ID}"
else
  LOCAL_DEST="$DEST"
fi
mkdir -p "$LOCAL_DEST"

RSYNC_RSH="ssh ${SSH_OPTS}"
RSYNC_ARGS=(-avz --progress -e "$RSYNC_RSH")
if [[ "$PLOTS_ONLY" -eq 1 ]]; then
  RSYNC_ARGS+=(
    --prune-empty-dirs
    --include='*/'
    --include='**/plots/***'
    --exclude='*'
  )
fi

echo "Fetching ${HOST}:${REMOTE_PATH}/ -> ${LOCAL_DEST}/"
rsync "${RSYNC_ARGS[@]}" "${HOST}:${REMOTE_PATH}/" "${LOCAL_DEST}/"
echo "Done: ${LOCAL_DEST}/"
