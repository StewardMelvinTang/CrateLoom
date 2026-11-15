# Development and bundle dependency inventory

This inventory describes the 2026-11-07 local M5 build. Exact JavaScript/Rust dependencies are in the lockfiles; optional Python runtimes have separate locks under `engine/requirements`.

| Dependency | In the local app ZIP? | Source and release consideration |
| --- | --- | --- |
| React, lucide-react, Tauri frontend libraries | Built frontend | See `package-lock.json` and each package's notices. Vite and TypeScript are build tooling. |
| Tauri, dialog plugin, `open` 5.4.5 and Rust libraries | Compiled | See `apps/desktop/src-tauri/Cargo.lock`; produce a complete third-party notices list before distribution. WebKit is provided by macOS. |
| CPython 3.13.13 and frozen sidecar | Yes | PyInstaller 6.22.0 with bootloader distribution exception; inventory embedded Python and native libraries before publishing. |
| certifi 2026.10.23 | Yes | Mozilla trust bundle, MPL-2.0; see [certifi](https://pypi.org/project/certifi/). |
| FFmpeg / ffprobe | No | Tested Homebrew FFmpeg 9.0.1 with `--enable-gpl --enable-version3`, shared libraries, x264/x265 and other codecs. Obtain a separate installation; assess the exact build and its obligations if ever bundling it. |
| librosa and beat/RGB runtime | No | Optional pinned analysis environment; installed by `scripts/setup-analysis-macos.sh`. |
| Structure model, runtime and weights | No | Optional separate model setup. The documented weight-license discrepancy remains unresolved; see [M2 verification](M2_VERIFICATION.md). |
| Mutagen 1.48.1 | No | Optional tag writer environment, GPL-2.0-or-later; see [M3 verification](M3_VERIFICATION.md). |
| Catalogs, source links and artwork | No fixture media in ZIP | Provider metadata and artwork are fetched on request. Review applicable provider data/artwork terms and obtain audio from authorized sources. |

The ZIP is ad-hoc signed for local testing. Developer ID, notarization, a complete license/notice review and clean-machine verification are release requirements. Test fixture music, model weights, FFmpeg, account credentials and owner library data are not included.
