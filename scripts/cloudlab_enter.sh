#!/bin/bash
# SSH to CloudLab node0, clone this repo, attach tmux session "roshanfer".
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION="roshanfer"
BRANCH="artifact-evaluation"
DEST_REL="roshanfer-experiments"
DEFAULT_URL="wisc.cloudlab.us"

usage() {
  echo "Usage: $0 --name NAME --project PROJECT --user USER [--url URL]"
  echo ""
  echo "SSH to the CloudLab control node (node0), clone this repo (branch"
  echo "${BRANCH}, with submodules) into ~/${DEST_REL}, and attach tmux"
  echo "session ${SESSION}."
  echo ""
  echo "Options:"
  echo "  --name NAME       Experiment Name from the CloudLab experiment page (required)"
  echo "  --project PROJECT Experiment Project from the CloudLab experiment page (required)"
  echo "  --user USER       CloudLab username (required)"
  echo "  --url URL         Cluster hostname (default: ${DEFAULT_URL})"
  echo "  -h, --help        Show this help and exit"
  echo ""
  echo "SSH target: USER@node0.NAME.PROJECT-pg0.URL"
  echo ""
  echo "Examples:"
  echo "  $0 --name myexp --project MyProject --user alice"
}

NAME=""
PROJECT=""
CL_USER=""
URL="$DEFAULT_URL"

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

origin_to_ssh() {
  local url="$1"
  if [[ "$url" =~ ^https://([^/]+)/(.+)$ ]]; then
    printf 'git@%s:%s' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
  else
    printf '%s' "$url"
  fi
}

CLONE_URL="$(origin_to_ssh "$(git -C "$ROOT" remote get-url origin)")"
HOST="${CL_USER}@node0.${NAME}.${PROJECT}-pg0.${URL}"

# Forward local GitHub keys: CloudLab auth uses a key file, which -A cannot offer.
if [[ -z "${SSH_AUTH_SOCK:-}" ]]; then
  eval "$(ssh-agent -s)" >/dev/null
fi
if ! ssh-add -l >/dev/null 2>&1; then
  ssh-add || { echo "error: add your GitHub SSH key to the agent (e.g. ssh-add ~/.ssh/id_ed25519)"; exit 1; }
fi

remote=$(cat <<EOF
set -euo pipefail
DEST="\$HOME/${DEST_REL}"
SESSION=$(printf '%q' "$SESSION")
BRANCH=$(printf '%q' "$BRANCH")
CLONE_URL=$(printf '%q' "$CLONE_URL")

if ! command -v tmux >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y tmux
fi

if [[ ! -d "\$DEST/.git" ]]; then
  git clone --recurse-submodules -b "\$BRANCH" "\$CLONE_URL" "\$DEST"
else
  git -C "\$DEST" submodule update --init --recursive
fi

if ! tmux has-session -t "\$SESSION" 2>/dev/null; then
  tmux new-session -d -s "\$SESSION" -c "\$DEST"
fi
exec tmux attach-session -t "\$SESSION"
EOF
)

SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null}"
# shellcheck disable=SC2086
exec ssh -A -o AddKeysToAgent=yes $SSH_OPTS -t "$HOST" "bash -c $(printf '%q' "$remote")"
