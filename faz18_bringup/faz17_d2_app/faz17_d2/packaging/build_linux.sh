#!/usr/bin/env bash
# ===================================================================
# build_linux.sh — Build Linux distribution
# Run from faz17_d2/ directory (parent of packaging/)
# ===================================================================
set -euo pipefail

echo "=== Filament Winding — Linux Build ==="

# Check Python
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found"; exit 1; }

# Install deps
echo "--- Checking dependencies ---"
python3 -m pip install --quiet --upgrade pip
python3 -m pip install --quiet "PySide6==6.11.*" "pyqtgraph==0.14.*" PyOpenGL numpy pyinstaller

# Clean
[[ -d build ]] && rm -rf build
[[ -d dist  ]] && rm -rf dist

# Build
echo "--- Running PyInstaller ---"
python3 -m PyInstaller packaging/filament_winding.spec --clean --noconfirm

# Verify
if [[ -x dist/FilamentWinding/FilamentWinding ]]; then
    echo "=== BUILD SUCCESS ==="
    echo "Executable: dist/FilamentWinding/FilamentWinding"
    ls -la dist/FilamentWinding/FilamentWinding
    echo
    echo "Folder size:"
    du -sh dist/FilamentWinding
else
    echo "ERROR: expected dist/FilamentWinding/FilamentWinding not found"
    exit 1
fi

# Optional: create tarball for distribution
TARBALL="FilamentWinding-linux-$(date +%Y%m%d).tar.gz"
echo "--- Creating $TARBALL ---"
(cd dist && tar -czf "../$TARBALL" FilamentWinding)
echo "Created: $TARBALL  ($(du -h "$TARBALL" | cut -f1))"
