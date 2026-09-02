#!/bin/bash
# Pre-setup for experiments. Sourced by run_tests.sh; also runnable as ./init_env.sh

_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$_ROOT"
export REPO_ROOT
export KUBECONFIG="$REPO_ROOT/benchmarks/k8s/kubeconfig"

# shellcheck source=/dev/null
source "$_ROOT/scripts/config_env.sh"
apply_git_protocol

if [[ ! -x "$_ROOT/.venv/bin/python" || ! -x "$_ROOT/.venv/bin/pip" ]]; then
    rm -rf "$_ROOT/.venv"
    if ! python3 -c "import ensurepip" 2>/dev/null; then
        _pyver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        echo "Installing python${_pyver}-venv..."
        sudo apt-get update -qq
        sudo apt-get install -y "python${_pyver}-venv" || { echo "error: install python${_pyver}-venv"; exit 1; }
        unset _pyver
    fi
    python3 -m venv "$_ROOT/.venv" || { echo "error: could not create $_ROOT/.venv"; exit 1; }
    "$_ROOT/.venv/bin/pip" install -r "$_ROOT/requirements.txt" || { echo "error: pip install failed"; exit 1; }
fi

# shellcheck source=/dev/null
source "$_ROOT/.venv/bin/activate"

"$_ROOT/scripts/ensure_rwg.sh" || { echo "error: failed to build local rwg"; exit 1; }

unset _ROOT
