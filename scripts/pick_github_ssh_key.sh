# Sourced. pick_github_ssh_key prints an OpenSSH private-key path usable with GitHub.
# Uses `ssh -G github.com` (IdentityFile / defaults) so macOS, Linux, and Windows OpenSSH agree.
# Skips hardware-backed *_sk keys (they cannot be copied to CloudLab workers).

pick_github_ssh_key() {
  local p
  _pick_github_ssh_key_ok() {
    p="$1"
    p="${p/#\~/$HOME}"
    p="${p//\\//}"
    case "$p" in *_sk) return 1 ;; esac
    [[ -f "$p" && -f "${p}.pub" ]] || return 1
    printf '%s\n' "$p"
  }
  while read -r p; do
    [[ -z "$p" ]] && continue
    _pick_github_ssh_key_ok "$p" && return 0
  done < <(ssh -G github.com 2>/dev/null | awk 'tolower($1)=="identityfile" { $1=""; sub(/^ /,""); print }')
  for p in "$HOME/.ssh/id_ed25519" "$HOME/.ssh/id_ecdsa" "$HOME/.ssh/id_rsa"; do
    _pick_github_ssh_key_ok "$p" && return 0
  done
  return 1
}
