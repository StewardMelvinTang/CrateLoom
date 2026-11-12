#!/bin/sh
set -eu
# Build on native macOS storage to avoid AppleDouble files on shared volumes.
REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
BUILD_DIR="${CRATELOOM_SIDECAR_BUILD_DIR:-$HOME/Library/Caches/CrateLoom/sidecar-build}"
VENV_DIR="${CRATELOOM_PACKAGING_VENV:-$HOME/Library/Caches/CrateLoom/packaging-env}"
mkdir -p "$BUILD_DIR/worker-source"
# The optional system Python runtimes need physical .py modules outside the frozen archive.
cp "$REPO_ROOT"/engine/crateloom/*.py "$BUILD_DIR/worker-source/"
if [ ! -x "$VENV_DIR/bin/pyinstaller" ]; then
  uv venv --python python3 "$VENV_DIR"
fi
# Ensure a pre-existing environment uses the tested PyInstaller version.
uv pip install --python "$VENV_DIR/bin/python" 'pyinstaller==6.22.0' 'certifi==2026.10.23'
COPYFILE_DISABLE=1 "$VENV_DIR/bin/pyinstaller" --noconfirm --clean --onefile --name crateloom-engine --paths "$REPO_ROOT/engine" --add-data "$BUILD_DIR/worker-source:crateloom" --distpath "$BUILD_DIR/dist" --workpath "$BUILD_DIR/work" --specpath "$BUILD_DIR" "$REPO_ROOT/scripts/sidecar_entry.py"
printf 'Standalone sidecar: %s\n' "$BUILD_DIR/dist/crateloom-engine"
