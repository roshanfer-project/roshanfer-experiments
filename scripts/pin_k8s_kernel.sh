#!/bin/bash
# Pin Ubuntu kernel ABI on all CloudLab generator + workload hosts from the experiment manifest.
set -euo pipefail

_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$_ROOT"
REPO_ROOT="$_ROOT"
export REPO_ROOT
# shellcheck source=/dev/null
source "$_ROOT/scripts/elapsed.sh"

KVER="6.8.0-134-generic"
CHECK_ONLY=0
CLI_CLOUDLAB_MANIFEST=""
CLI_CLOUDLAB_USER=""
REBOOT_WAIT_SEC=360

GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

usage() {
  echo "Usage: $0 [--kernel ABI] [--check-only] [--cloudlab-manifest PATH] [--cloudlab-user USER]"
  echo ""
  echo "Install, GRUB-pin, and reboot every generator and workload host from the CloudLab"
  echo "manifest onto KERNEL (default: ${KVER}). CONTROL_ON_CLUSTER=1 drops node0."
  echo "Does not use Kubernetes. Then checks uname -r on each host."
  echo ""
  echo "Options:"
  echo "  --kernel ABI                 e.g. 6.8.0-134-generic (default: ${KVER})"
  echo "  --check-only                 skip install/reboot; only verify"
  echo "  --cloudlab-manifest PATH     manifest XML (or CLOUDLAB_MANIFEST in config.env)"
  echo "  --cloudlab-user USER         CloudLab username (or CLOUDLAB_USER in config.env)"
  echo "  -h, --help                   show this help and exit"
  echo ""
  echo "Example:"
  echo "  $0 --kernel 6.8.0-134-generic"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --kernel)
      [[ -z "${2:-}" ]] && { echo "Missing value for --kernel"; usage; exit 1; }
      KVER="$2"
      shift 2
      ;;
    --check-only)
      CHECK_ONLY=1
      shift
      ;;
    --cloudlab-manifest)
      [[ -z "${2:-}" ]] && { echo "Missing value for --cloudlab-manifest"; usage; exit 1; }
      CLI_CLOUDLAB_MANIFEST="$2"
      shift 2
      ;;
    --cloudlab-user)
      [[ -z "${2:-}" ]] && { echo "Missing value for --cloudlab-user"; usage; exit 1; }
      CLI_CLOUDLAB_USER="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      usage
      exit 1
      ;;
  esac
done

# shellcheck source=/dev/null
source "$_ROOT/scripts/config_env.sh"
[[ -n "$CLI_CLOUDLAB_MANIFEST" ]] && CLOUDLAB_MANIFEST="$CLI_CLOUDLAB_MANIFEST"
[[ -n "$CLI_CLOUDLAB_USER" ]] && CLOUDLAB_USER="$CLI_CLOUDLAB_USER"

SSH_OPTS="${SSH_OPTS:--o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null}"
SSH_OPTS="$SSH_OPTS -o BatchMode=yes -o LogLevel=ERROR"
WAIT_SSH_OPTS="$SSH_OPTS -o ConnectTimeout=5"

PYTHON="$_ROOT/.venv/bin/python"
[[ -x "$PYTHON" ]] || PYTHON=python3

[[ -n "$CLOUDLAB_MANIFEST" ]] || { log_error "CLOUDLAB_MANIFEST or --cloudlab-manifest is required"; exit 1; }
if [[ "$CLOUDLAB_MANIFEST" != /* ]]; then
  CLOUDLAB_MANIFEST="$_ROOT/${CLOUDLAB_MANIFEST#./}"
fi
if [[ ! -f "$CLOUDLAB_MANIFEST" ]]; then
  log_error "Manifest not found: $CLOUDLAB_MANIFEST"
  echo "Place the CloudLab experiment XML at that path yourself (this is not fetched automatically)."
  echo "If you used ./scripts/cloudlab_enter.sh and are on the control node, you can run ./scripts/fetch_manifest.sh."
  exit 1
fi
[[ -n "$CLOUDLAB_USER" ]] || { log_error "CLOUDLAB_USER or --cloudlab-user is required"; exit 1; }

parse_host_entry() {
  local entry=$1
  if [[ "$entry" != *"@"* ]]; then
    log_error "Host line must be user@host: $entry"
    exit 1
  fi
  CURRENT_USER="${entry%%@*}"
  CURRENT_HOST="${entry#*@}"
}

ssh_exec() {
  local user=$1 host=$2 cmd=$3
  ssh $SSH_OPTS "$user@$host" "$cmd"
}

ssh_exec_wait() {
  local user=$1 host=$2 cmd=$3
  ssh $WAIT_SSH_OPTS "$user@$host" "$cmd"
}

HOSTS_FILE="$(mktemp)"
trap 'rm -f "$HOSTS_FILE"; _elapsed_report' EXIT
"$PYTHON" -m exec.cloudlab_hosts --manifest "$CLOUDLAB_MANIFEST" --user "$CLOUDLAB_USER" -o "$HOSTS_FILE"
mapfile -t HOSTS < <(grep -vE '^\s*#|^\s*$' "$HOSTS_FILE")
if [[ ${#HOSTS[@]} -eq 0 ]]; then
  log_error "No hosts from manifest $CLOUDLAB_MANIFEST"
  exit 1
fi

log_info "Manifest: $CLOUDLAB_MANIFEST"
log_info "Target kernel: $KVER"
log_info "Hosts (${#HOSTS[@]}): ${HOSTS[*]}"

pin_one_host() {
  local entry=$1
  parse_host_entry "$entry"
  local user="$CURRENT_USER" host="$CURRENT_HOST"
  log_info "[$host] installing $KVER..."
  local rc=0
  set +e
  ssh $SSH_OPTS "$user@$host" "sudo bash -s" <<REMOTE 2>&1 | sed "s/^/[${host}] /"
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
KVER='$KVER'
apt-get update -qq
if ! apt-get install -y "linux-image-\${KVER}" "linux-modules-\${KVER}"; then
  echo "error: could not install linux-image-\${KVER} / linux-modules-\${KVER}"
  echo "hint: try --kernel 6.8.0-117-generic"
  exit 1
fi
apt-mark hold linux-generic linux-image-generic linux-headers-generic \
  "linux-image-\${KVER}" "linux-modules-\${KVER}" >/dev/null
mkdir -p /etc/default/grub.d
printf 'GRUB_DEFAULT="Advanced options for Ubuntu>Ubuntu, with Linux %s"\n' "\$KVER" \
  > /etc/default/grub.d/99-pin-kernel.cfg
update-grub
if ! grep -q "with Linux \${KVER}" /boot/grub/grub.cfg; then
  echo "error: GRUB has no menuentry for \${KVER}"
  exit 1
fi
echo "install+grub done; running \$(uname -r)"
REMOTE
  rc=${PIPESTATUS[0]}
  set -e
  if [[ "$rc" -ne 0 ]]; then
    log_error "[$host] pin install failed (exit $rc)"
    return 1
  fi
  log_success "[$host] pin install finished"
}

boot_id_of() {
  local user=$1 host=$2
  ssh_exec_wait "$user" "$host" "cat /proc/sys/kernel/random/boot_id" 2>/dev/null || true
}

uname_of() {
  local user=$1 host=$2
  ssh_exec_wait "$user" "$host" "uname -r" 2>/dev/null || true
}

reboot_one() {
  local entry=$1
  parse_host_entry "$entry"
  local user="$CURRENT_USER" host="$CURRENT_HOST"
  log_info "[$host] rebooting..."
  ssh_exec "$user" "$host" "sudo reboot" 2>/dev/null || true
}

wait_host_new_kernel() {
  local entry=$1 old_boot=$2
  parse_host_entry "$entry"
  local user="$CURRENT_USER" host="$CURRENT_HOST"
  local deadline=$((SECONDS + REBOOT_WAIT_SEC))
  log_info "[$host] waiting for reboot onto $KVER (timeout ${REBOOT_WAIT_SEC}s)..."
  while (( SECONDS < deadline )); do
    local bid kr
    bid="$(boot_id_of "$user" "$host")"
    if [[ -z "$bid" ]]; then
      sleep 5
      continue
    fi
    if [[ -n "$old_boot" && "$bid" == "$old_boot" ]]; then
      sleep 5
      continue
    fi
    kr="$(uname_of "$user" "$host")"
    if [[ "$kr" == "$KVER" ]]; then
      log_success "[$host] up on $kr"
      return 0
    fi
    if [[ -z "$old_boot" ]]; then
      sleep 5
      continue
    fi
    log_error "[$host] rebooted but uname -r is '${kr:-unknown}', want $KVER"
    return 1
  done
  log_error "[$host] timed out waiting for $KVER"
  return 1
}

run_parallel() {
  local fn=$1
  shift
  local entry pids=() fail=0
  for entry in "$@"; do
    "$fn" "$entry" &
    pids+=("$!")
  done
  local pid
  for pid in "${pids[@]}"; do
    if ! wait "$pid"; then
      fail=1
    fi
  done
  return "$fail"
}

if [[ "$CHECK_ONLY" -eq 0 ]]; then
  log_info "Installing kernel on ${#HOSTS[@]} hosts..."
  if ! run_parallel pin_one_host "${HOSTS[@]}"; then
    log_error "Kernel install failed on one or more hosts"
    exit 1
  fi
  log_success "Install+GRUB finished on all hosts"

  declare -A OLD_BOOT=()
  for entry in "${HOSTS[@]}"; do
    parse_host_entry "$entry"
    OLD_BOOT["$entry"]="$(boot_id_of "$CURRENT_USER" "$CURRENT_HOST")"
  done

  log_info "Rebooting ${#HOSTS[@]} host(s)..."
  for entry in "${HOSTS[@]}"; do
    reboot_one "$entry"
  done
  wait_rest() {
    wait_host_new_kernel "$1" "${OLD_BOOT[$1]}"
  }
  if ! run_parallel wait_rest "${HOSTS[@]}"; then
    log_error "One or more hosts did not come back on $KVER"
    exit 1
  fi
  log_success "Pin complete: all hosts rebooted onto $KVER"
else
  log_info "check-only: skipping install and reboot"
fi

# --- full check ---
log_info "Checking uname -r on ${#HOSTS[@]} host(s)..."
fail=0
fail_hosts=()
printf '%-52s %-28s %s\n' "HOST" "UNAME" "OK"
printf '%-52s %-28s %s\n' "----" "-----" "--"
for entry in "${HOSTS[@]}"; do
  parse_host_entry "$entry"
  kr="$(uname_of "$CURRENT_USER" "$CURRENT_HOST")"
  ok="yes"
  if [[ "$kr" != "$KVER" ]]; then
    ok="no"
    fail=1
    fail_hosts+=("$CURRENT_HOST (uname=${kr:-unreachable})")
  fi
  printf '%-52s %-28s %s\n' "$CURRENT_HOST" "${kr:-unreachable}" "$ok"
done

if [[ "$fail" -ne 0 ]]; then
  log_error "Kernel check failed:"
  for h in "${fail_hosts[@]}"; do
    log_error "  - $h"
  done
  exit 1
fi
log_success "All checks passed: $KVER"
exit 0
