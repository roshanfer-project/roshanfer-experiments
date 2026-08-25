#!/bin/bash
# Ensure ./rwg/rwg exists on this machine (used by collector parse).
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/scripts/elapsed.sh"
RWG_DIR="$ROOT/rwg"
RWG_BIN="$RWG_DIR/rwg"
INSTALL_GO="$ROOT/benchmarks/provisioning/install_go.sh"
# Keep in sync with benchmarks/provisioning/install_go.sh
GO_VERSION="1.25.5"
USER_GO_ROOT="${HOME}/.local/go"

if [[ -x "$RWG_BIN" ]]; then
    exit 0
fi

find_go() {
    if command -v go >/dev/null 2>&1; then
        command -v go
        return 0
    fi
    if [[ -x /usr/local/go/bin/go ]]; then
        echo /usr/local/go/bin/go
        return 0
    fi
    if [[ -x "${USER_GO_ROOT}/bin/go" ]]; then
        echo "${USER_GO_ROOT}/bin/go"
        return 0
    fi
    return 1
}

install_go_user_local() {
    local url="https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz"
    local tmp
    tmp="$(mktemp)"
    echo "Installing Go ${GO_VERSION} to ${USER_GO_ROOT} (user-local)..."
    wget -q "$url" -O "$tmp"
    mkdir -p "$(dirname "$USER_GO_ROOT")"
    rm -rf "$USER_GO_ROOT"
    tar -C "$(dirname "$USER_GO_ROOT")" -xzf "$tmp"
    rm -f "$tmp"
}

GO=""
if ! GO="$(find_go)"; then
    if [[ -f "$INSTALL_GO" ]] && bash "$INSTALL_GO" && GO="$(find_go)"; then
        :
    else
        echo "System Go install unavailable; falling back to user-local."
        install_go_user_local
        GO="$(find_go)" || { echo "error: Go install failed"; exit 1; }
    fi
fi

if [[ ! -d "$RWG_DIR" ]]; then
    echo "error: rwg directory missing at $RWG_DIR (init the submodule)"
    exit 1
fi

echo "Building rwg with $GO..."
(cd "$RWG_DIR" && "$GO" build -o rwg .)
echo "Built $RWG_BIN"
