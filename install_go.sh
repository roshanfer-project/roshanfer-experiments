#!/bin/bash

# Go version to install
GO_VERSION="1.25"
OS="linux"
ARCH="amd64"

# URLs
GO_URL="https://go.dev/dl/go${GO_VERSION}.${OS}-${ARCH}.tar.gz"
INSTALL_DIR="/usr/local"
GO_ROOT="${INSTALL_DIR}/go"

echo "Installing Go ${GO_VERSION}..."

# Download Go
echo "Downloading ${GO_URL}..."
wget -q ${GO_URL} -O go.tar.gz

if [ $? -ne 0 ]; then
    echo "Download failed! Please check your internet connection or the Go version."
    exit 1
fi

# Remove previous installation
if [ -d "${GO_ROOT}" ]; then
    echo "Removing previous Go installation in ${GO_ROOT}..."
    sudo rm -rf ${GO_ROOT}
fi

# Extract new installation
echo "Extracting to ${INSTALL_DIR}..."
sudo tar -C ${INSTALL_DIR} -xzf go.tar.gz

# Cleanup
rm go.tar.gz

echo "Go ${GO_VERSION} has been installed to ${GO_ROOT}."

# Configure environment variables
PATH_EXPORT="export PATH=\$PATH:${GO_ROOT}/bin"

configure_shell() {
    local shell_rc="$1"
    if [ -f "$shell_rc" ]; then
        if grep -q "${GO_ROOT}/bin" "$shell_rc"; then
            echo "Go path already set in $shell_rc"
        else
            echo "Adding Go path to $shell_rc"
            echo "" >> "$shell_rc"
            echo "# Go configuration" >> "$shell_rc"
            echo "$PATH_EXPORT" >> "$shell_rc"
        fi
    fi
}

configure_shell "$HOME/.bashrc"
configure_shell "$HOME/.zshrc"
configure_shell "$HOME/.profile"

echo ""
echo "Installation complete."
echo "To use Go immediately, run the following command corresponding to your shell:"
echo "  source ~/.bashrc  # for bash"
echo "  source ~/.zshrc   # for zsh"
echo ""
echo "Current go version:"
${GO_ROOT}/bin/go version
