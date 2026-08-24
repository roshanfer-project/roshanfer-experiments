#!/bin/bash
# Detach this tmux client. If you attached via cloudlab_enter.sh, SSH exits too.
set -euo pipefail

if [[ -z "${TMUX:-}" ]]; then
  echo "error: not inside a tmux session; run this on the control node after cloudlab_enter.sh" >&2
  exit 1
fi

tmux detach-client
