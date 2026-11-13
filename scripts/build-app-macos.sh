#!/bin/sh
set -eu
REPO_ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
BUILD_ROOT="${CRATELOOM_APP_BUILD_DIR:-$HOME/Library/Caches/CrateLoom/m5-app-build}"
export CRATELOOM_SIDECAR_BUILD_DIR="$BUILD_ROOT/sidecar"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$BUILD_ROOT/target}"
export COPYFILE_DISABLE=1
case "$(uname -s):$(uname -m)" in
  Darwin:arm64) triplet=aarch64-apple-darwin ;;
  Darwin:x86_64) triplet=x86_64-apple-darwin ;;
  *) echo 'Build this bundle on macOS.' >&2; exit 1 ;;
esac
mkdir -p "$BUILD_ROOT/source"
rsync -a --exclude='._*' --exclude='.git' --exclude='node_modules' --exclude='target' --exclude='dist' "$REPO_ROOT/" "$BUILD_ROOT/source/"
cd "$BUILD_ROOT/source"
./scripts/build-sidecar-macos.sh
python3 scripts/test-sidecar.py "$CRATELOOM_SIDECAR_BUILD_DIR/dist/crateloom-engine"
mkdir -p apps/desktop/src-tauri/packaging
cp "$CRATELOOM_SIDECAR_BUILD_DIR/dist/crateloom-engine" "apps/desktop/src-tauri/packaging/crateloom-engine-$triplet"
youtube_helper=$(python3 scripts/setup-youtube-macos.py)
cp "$youtube_helper" "apps/desktop/src-tauri/packaging/yt-dlp-$triplet"
# The unpacked build of the same release ships as data (Contents/Resources), so the
# app seal is unchanged; the engine verifies and extracts it on first use.
youtube_onedir=$(python3 scripts/setup-youtube-macos.py --onedir)
cp "$youtube_onedir" apps/desktop/src-tauri/packaging/yt-dlp_macos.zip
cp docs/YOUTUBE_HELPER_LICENSES.txt apps/desktop/src-tauri/packaging/youtube-licenses.txt
npm ci
cd apps/desktop
../../node_modules/.bin/tauri build --bundles app --config '{"bundle":{"active":true,"externalBin":["packaging/crateloom-engine","packaging/yt-dlp"],"resources":{"packaging/youtube-licenses.txt":"youtube-licenses.txt","packaging/yt-dlp_macos.zip":"yt-dlp_macos.zip"}}}'
bundle="$CARGO_TARGET_DIR/release/bundle/macos/CrateLoom.app"
# This local build has an ad-hoc seal. A distributable release requires Developer ID and notarization.
# Notarization also inspects nested archives: yt-dlp_macos.zip holds upstream's ad-hoc-signed
# executables and libraries (no hardened runtime, no timestamp), which the notary service rejects.
# A release must re-sign the unpacked tree with the Developer ID identity and hardened runtime,
# repack it, and pin the repacked archive's SHA-256 in engine/crateloom/youtube_audio.py, or leave
# the archive out (the engine then keeps using the onefile helper).
codesign --force --sign - "$bundle"
codesign --verify --deep --strict "$bundle"
printf 'macOS bundle: %s\n' "$bundle"
