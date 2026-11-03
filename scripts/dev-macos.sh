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
cd apps/desktop
exec ../../node_modules/.bin/tauri dev
