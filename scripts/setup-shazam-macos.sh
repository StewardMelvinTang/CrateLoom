#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
ENV_DIR="${CRATELOOM_SHAZAM_ENV:-$HOME/Library/Caches/CrateLoom/shazam-env}"
if [ ! -x "$ENV_DIR/bin/python" ]; then uv venv --python 3.12 "$ENV_DIR"; fi
uv pip sync --python "$ENV_DIR/bin/python" "$ROOT/engine/requirements/shazam.lock"
printf 'ShazamIO Python: %s\n' "$ENV_DIR/bin/python"
