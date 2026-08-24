#!/bin/bash
# Fetch or require the CloudLab/GENI experiment manifest.
#
# When CONTROL_ON_CLUSTER=1, the first <node> is this machine and must not be a
# generator or workload host. exec.cloudlab_hosts drops it only in that case.

set -euo pipefail

_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$_ROOT"
REPO_ROOT="$_ROOT"
export REPO_ROOT

# shellcheck source=/dev/null
source "$_ROOT/scripts/config_env.sh"

on="${CONTROL_ON_CLUSTER:-1}"
on="${on,,}"
if [[ "$on" != "1" && "$on" != "true" && "$on" != "yes" ]]; then
  echo "CONTROL_ON_CLUSTER=0: write the experiment manifest to $CLOUDLAB_MANIFEST yourself (CloudLab portal download)."
  exit 1
fi

if ! command -v geni-get >/dev/null 2>&1; then
  echo "error: geni-get not found (CONTROL_ON_CLUSTER=1)."
  echo "Run this on a CloudLab node, or set CONTROL_ON_CLUSTER=0 and place $CLOUDLAB_MANIFEST yourself."
  exit 1
fi

dest="$CLOUDLAB_MANIFEST"
if [[ "$dest" != /* ]]; then
  dest="$_ROOT/${dest#./}"
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
if ! geni-get manifest > "$tmp"; then
  echo "error: geni-get manifest failed"
  exit 1
fi
if [[ ! -s "$tmp" ]]; then
  echo "error: geni-get manifest wrote an empty file"
  exit 1
fi

mkdir -p "$(dirname "$dest")"
mv "$tmp" "$dest"
trap - EXIT
echo "Wrote $dest"
