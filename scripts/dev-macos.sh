#!/bin/sh
set -eu
REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
DEV_SOURCE="${CRATELOOM_DEV_SOURCE:-$HOME/Library/Caches/CrateLoom/source}"
mkdir -p "$DEV_SOURCE"
rsync -a --exclude='._*' --exclude='.git' --exclude='node_modules' --exclude='target' --exclude='dist' "$REPO_ROOT/" "$DEV_SOURCE/"
cd "$DEV_SOURCE"
npm ci
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/Library/Caches/CrateLoom/target}"
export COPYFILE_DISABLE=1
if [ -z "${CRATELOOM_ANALYSIS_PYTHON:-}" ] && [ -x "$HOME/Library/Caches/CrateLoom/analysis-env/bin/python" ]; then
  export CRATELOOM_ANALYSIS_PYTHON="$HOME/Library/Caches/CrateLoom/analysis-env/bin/python"
fi
if [ -z "${CRATELOOM_STRUCTURE_PYTHON:-}" ] && [ -x "$HOME/Library/Caches/CrateLoom/structure-env/bin/python" ]; then
  export CRATELOOM_STRUCTURE_PYTHON="$HOME/Library/Caches/CrateLoom/structure-env/bin/python"
fi
DEV_PORT="${CRATELOOM_DEV_PORT:-1420}"
case "$DEV_PORT" in ''|*[!0-9]*) echo 'CRATELOOM_DEV_PORT must be a numeric port.' >&2; exit 1;; esac
if [ "$DEV_PORT" -lt 1024 ] || [ "$DEV_PORT" -gt 65535 ]; then echo 'CRATELOOM_DEV_PORT must be between 1024 and 65535.' >&2; exit 1; fi
export CRATELOOM_DEV_PORT="$DEV_PORT"
cd apps/desktop
exec ../../node_modules/.bin/tauri dev --config "{\"build\":{\"devUrl\":\"http://localhost:$DEV_PORT\"}}"
