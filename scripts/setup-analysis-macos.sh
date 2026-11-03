#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
ENV_DIR="${CRATELOOM_ANALYSIS_ENV:-$HOME/Library/Caches/CrateLoom/analysis-env}"
if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
  echo 'The pinned analysis environment currently supports Apple Silicon macOS.' >&2
  exit 1
fi
if [ ! -x "$ENV_DIR/bin/python" ]; then uv venv --python 3.12 "$ENV_DIR"; fi
if ! "$ENV_DIR/bin/python" -c 'import sys; assert sys.version_info[:2] == (3, 12)' 2>/dev/null; then
  echo "Analysis environment must use Python 3.12: $ENV_DIR" >&2
  exit 1
fi
uv pip sync --python "$ENV_DIR/bin/python" "$ROOT/engine/requirements/analysis-macos-arm64.lock"
printf 'Analysis Python: %s\n' "$ENV_DIR/bin/python"
