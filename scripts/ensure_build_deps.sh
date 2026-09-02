#!/bin/bash
# Check/install host deps for scripts/build.sh (sidecar clang-18 + docker bake).
# Sourced by scripts/build.sh; also runnable as ./scripts/ensure_build_deps.sh

_ebd_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v apt-get >/dev/null 2>&1; then
  echo "error: apt-get is required to install build dependencies"
  exit 1
fi

_ebd_clang18_ok() {
  command -v clang-18 >/dev/null 2>&1 || return 1
  command -v clang++-18 >/dev/null 2>&1 || return 1
  clang-18 --version 2>/dev/null | grep -qE 'version 18\.' || return 1
  clang++-18 --version 2>/dev/null | grep -qE 'version 18\.' || return 1
}

_ebd_pkgs=()
_ebd_clang18_ok || _ebd_pkgs+=(clang-18)
[[ -x /usr/bin/llvm-ar-18 && -x /usr/bin/llvm-ranlib-18 ]] || _ebd_pkgs+=(llvm-18)
[[ -d /usr/lib/llvm-18/include/c++/v1 ]] || _ebd_pkgs+=(libc++-18-dev libc++abi-18-dev)
command -v cmake >/dev/null 2>&1 || _ebd_pkgs+=(cmake)
command -v pkg-config >/dev/null 2>&1 || _ebd_pkgs+=(pkg-config)
command -v make >/dev/null 2>&1 || _ebd_pkgs+=(build-essential)
command -v wget >/dev/null 2>&1 || _ebd_pkgs+=(wget)
command -v git >/dev/null 2>&1 || _ebd_pkgs+=(git)
command -v python >/dev/null 2>&1 || _ebd_pkgs+=(python-is-python3)
if command -v pkg-config >/dev/null 2>&1; then
  pkg-config --exists liburing || _ebd_pkgs+=(liburing-dev)
  pkg-config --exists libnghttp2 || _ebd_pkgs+=(libnghttp2-dev)
  pkg-config --exists zlib || _ebd_pkgs+=(zlib1g-dev)
else
  _ebd_pkgs+=(liburing-dev libnghttp2-dev zlib1g-dev)
fi
if ! command -v docker >/dev/null 2>&1; then
  _ebd_pkgs+=(docker.io docker-buildx)
elif ! docker buildx version >/dev/null 2>&1; then
  _ebd_pkgs+=(docker-buildx)
fi

if [[ ${#_ebd_pkgs[@]} -gt 0 ]]; then
  echo "Installing build deps: ${_ebd_pkgs[*]}"
  sudo apt-get update -qq
  sudo apt-get install -y "${_ebd_pkgs[@]}" || { echo "error: apt-get install failed"; exit 1; }
fi

if ! _ebd_clang18_ok; then
  echo "clang-18 not in distro archive; installing via apt.llvm.org..."
  _ebd_tmp="$(mktemp)"
  wget -q https://apt.llvm.org/llvm.sh -O "$_ebd_tmp"
  sudo bash "$_ebd_tmp" 18
  rm -f "$_ebd_tmp"
  sudo apt-get install -y clang-18 llvm-18 libc++-18-dev libc++abi-18-dev \
    || { echo "error: install clang-18/llvm-18/libc++-18 failed"; exit 1; }
fi

_ebd_fail=0
_ebd_clang18_ok || { echo "error: clang-18/clang++-18 missing or not LLVM 18"; _ebd_fail=1; }
[[ -x /usr/bin/llvm-ar-18 && -x /usr/bin/llvm-ranlib-18 ]] || { echo "error: llvm-ar-18/llvm-ranlib-18 missing"; _ebd_fail=1; }
[[ -d /usr/lib/llvm-18/include/c++/v1 ]] || { echo "error: libc++-18 headers missing at /usr/lib/llvm-18/include/c++/v1"; _ebd_fail=1; }
command -v cmake >/dev/null 2>&1 || { echo "error: cmake missing"; _ebd_fail=1; }
command -v pkg-config >/dev/null 2>&1 || { echo "error: pkg-config missing"; _ebd_fail=1; }
command -v make >/dev/null 2>&1 || { echo "error: make missing"; _ebd_fail=1; }
command -v wget >/dev/null 2>&1 || { echo "error: wget missing"; _ebd_fail=1; }
command -v git >/dev/null 2>&1 || { echo "error: git missing"; _ebd_fail=1; }
command -v python >/dev/null 2>&1 || { echo "error: python missing"; _ebd_fail=1; }
pkg-config --exists liburing || { echo "error: liburing missing (pkg-config)"; _ebd_fail=1; }
pkg-config --exists libnghttp2 || { echo "error: libnghttp2 missing (pkg-config)"; _ebd_fail=1; }
pkg-config --exists zlib || { echo "error: zlib missing (pkg-config)"; _ebd_fail=1; }
if ! command -v docker >/dev/null 2>&1; then
  echo "error: docker missing"
  _ebd_fail=1
elif ! docker info >/dev/null 2>&1; then
  echo "error: docker is installed but not usable. Add $USER to the docker group:"
  echo "  sudo usermod -aG docker $USER && newgrp docker"
  _ebd_fail=1
elif ! docker buildx version >/dev/null 2>&1; then
  echo "error: docker buildx missing"
  _ebd_fail=1
fi
[[ "$_ebd_fail" -eq 0 ]] || exit 1

if [[ ! -x "$_ebd_root/benchmarks/sidecar/build.sh" ]]; then
  echo "Initializing sidecar submodule..."
  git -C "$_ebd_root" submodule update --init --recursive benchmarks
fi
if [[ ! -d "$_ebd_root/benchmarks/sidecar/external/NanoLog/runtime" ]]; then
  git -C "$_ebd_root/benchmarks" submodule update --init --recursive sidecar
fi
if [[ ! -x "$_ebd_root/benchmarks/sidecar/build.sh" ]]; then
  echo "error: benchmarks/sidecar/build.sh missing (init the sidecar submodule)"
  exit 1
fi

unset _ebd_root _ebd_pkgs _ebd_tmp _ebd_fail
unset -f _ebd_clang18_ok
