# Sourced. Sets defaults, loads $REPO_ROOT/config.env, derives git URLs.
# shellcheck disable=SC2034

REQUIRE_REMOTE="${REQUIRE_REMOTE:-0}"
CONTROL_ON_CLUSTER="${CONTROL_ON_CLUSTER:-1}"
CLOUDLAB_USER="${CLOUDLAB_USER:-}"
CLOUDLAB_MANIFEST="${CLOUDLAB_MANIFEST:-./manifest.xml}"
REGISTRY="${REGISTRY:-farzad1132}"
IMAGE_TAG="${IMAGE_TAG:-}"
SKIP_BUILD="${SKIP_BUILD:-0}"
GIT_PROTOCOL="${GIT_PROTOCOL:-ssh}"
GIT_HOST="${GIT_HOST:-github.com}"
GIT_ORG="${GIT_ORG:-farzad1132}"
REPO_EXPERIMENTS="${REPO_EXPERIMENTS:-roshanfer-experiments}"
REPO_BENCHMARKS="${REPO_BENCHMARKS:-benchmarks}"
REPO_RWG="${REPO_RWG:-rwg}"
REPO_SIDECAR="${REPO_SIDECAR:-roshanfer-sidecar}"
REPO_FORMAL="${REPO_FORMAL:-roshanfer-formal}"

_cfg="${REPO_ROOT:-}/config.env"
if [[ -n "${REPO_ROOT:-}" && -f "$_cfg" ]]; then
  # shellcheck source=/dev/null
  source "$_cfg"
fi
unset _cfg

_git_url() {
  local repo="$1"
  if [[ "${GIT_PROTOCOL}" == "https" ]]; then
    printf 'https://%s/%s/%s.git' "$GIT_HOST" "$GIT_ORG" "$repo"
  else
    printf 'git@%s:%s/%s.git' "$GIT_HOST" "$GIT_ORG" "$repo"
  fi
}

REPO_URL="$(_git_url "$REPO_EXPERIMENTS")"
REPO_BENCHMARKS_URL="$(_git_url "$REPO_BENCHMARKS")"
REPO_RWG_URL="$(_git_url "$REPO_RWG")"
REPO_SIDECAR_URL="$(_git_url "$REPO_SIDECAR")"
REPO_FORMAL_URL="$(_git_url "$REPO_FORMAL")"
export REQUIRE_REMOTE CONTROL_ON_CLUSTER CLOUDLAB_USER CLOUDLAB_MANIFEST
export REGISTRY IMAGE_TAG SKIP_BUILD
export GIT_PROTOCOL GIT_HOST GIT_ORG
export REPO_EXPERIMENTS REPO_BENCHMARKS REPO_RWG REPO_SIDECAR REPO_FORMAL
export REPO_URL REPO_BENCHMARKS_URL REPO_RWG_URL REPO_SIDECAR_URL REPO_FORMAL_URL

_set_submodule_url() {
  local gitdir="$1" name="$2" url="$3"
  [[ -d "$gitdir/.git" || -f "$gitdir/.git" ]] || return 0
  local cur
  cur=$(git -C "$gitdir" config --file .gitmodules --get "submodule.${name}.url" 2>/dev/null || true)
  [[ "$cur" == "$url" ]] && return 0
  git -C "$gitdir" submodule set-url "$name" "$url" >/dev/null 2>&1 || true
}

apply_git_protocol() {
  [[ -n "${REPO_ROOT:-}" && -d "${REPO_ROOT}/.git" ]] || return 0
  _set_submodule_url "$REPO_ROOT" rwg "$REPO_RWG_URL"
  _set_submodule_url "$REPO_ROOT" benchmarks "$REPO_BENCHMARKS_URL"
  _set_submodule_url "$REPO_ROOT" formal "$REPO_FORMAL_URL"
  _set_submodule_url "${REPO_ROOT}/benchmarks" sidecar "$REPO_SIDECAR_URL"
}
