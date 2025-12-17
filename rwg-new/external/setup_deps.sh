#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${SCRIPT_DIR}/.."

# --- glog ---
if [ ! -f "${SCRIPT_DIR}/glog_install/lib/libglog.a" ]; then
    if [ ! -d "${SCRIPT_DIR}/glog" ]; then
        echo "Downloading glog..."
        wget -q https://github.com/google/glog/archive/refs/tags/v0.7.1.tar.gz -O glog.tar.gz
        tar -xf glog.tar.gz
        mv glog-0.7.1 "${SCRIPT_DIR}/glog"
        rm glog.tar.gz
    fi

    echo "Building glog..."
    cd "${SCRIPT_DIR}/glog"
    # We need -fPIC because it will be linked into a position-independent executable (usually) or just good practice
    cmake -S . -B build \
        -DCMAKE_C_COMPILER=clang \
        -DCMAKE_CXX_COMPILER=clang++ \
        -DCMAKE_CXX_FLAGS="-stdlib=libc++ -fPIC" \
        -DCMAKE_BUILD_TYPE=Release \
        -DBUILD_SHARED_LIBS=OFF \
        -DWITH_GFLAGS=OFF \
        -DWITH_UNWIND=OFF \
        -DBUILD_TESTING=OFF \
        -DCMAKE_INSTALL_LIBDIR=lib \
        -DCMAKE_INSTALL_PREFIX="${SCRIPT_DIR}/glog_install"
    cmake --build build -j$(nproc)
    cmake --install build
else
    echo "glog already built. Skipping."
fi