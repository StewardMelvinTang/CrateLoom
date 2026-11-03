# Progress and verification

## Current state

M0 and M1 are **in progress**, not complete. The local Python engine imports a single file using ffprobe, stores track records and manual cues in SQLite, derives a real mono waveform envelope using FFmpeg, and exports a one-track rekordbox XML playlist and a separate JSON cue backup. The UI connects the import dialog, track selection, media element, waveform, cue creation/seconds editing/removal, undo/redo and export. No source tags or audio are written.

Automated integration uses generated 2-second WAV audio: protocol handshake, import and same-path duplicate, waveform decode, cue persistence across process restarts, XML escaped Unicode/file URL and 1-microsecond serialization, and JSON backup. This checks mechanics, not musical analysis or audible alignment.

The frontend production build and native Rust check passed. The Tauri development executable compiled and launched from a clean native-storage copy. The webview UI, file dialog, audio playback and seeking have not yet been exercised end to end. The browser-only empty state rendered, but the browser does not provide the Tauri bridge. The shared SSD creates AppleDouble files that break Cargo/Tauri parsing; the README uses a native-storage copy. npm audit reported zero vulnerabilities after updating Vite.

## Important limitations

- Tempo, beat/downbeat and structural analysis, automatic suggestions, WaveSurfer detail/dragging, cue labels/colors, pre-roll, loop preview, folder/batch import, relinking, metadata editing, online discovery/acquisition, durable jobs/cancellation, and portable audio copying are not implemented.
- The analysis request is synchronous and blocks other engine requests; it is not yet cancellable. Decoded samples and peak arrays are held in memory and peaks travel over IPC, so this is an initial prototype, not the specified bounded cache architecture. No audio identity/cache invalidation exists; do not replace files in place after analysis.
- Development shell starts Python from the source tree; no bundled sidecar, FFmpeg, signing, notarization, clean-machine test or Windows test. Python/FFmpeg startup failure is not yet gracefully recovered. No release artifact has been produced.
- Tauri asset access is restricted at runtime to selected imported track paths, but local webview seeking, range behavior and decoder alignment need real native verification. JSONL has no timeout, crash recovery, queue, or robust maximum-response guard. Do not treat this as hardened IPC.
- The canonical stored cue unit is integer microseconds on the decoded media timeline. MP3 delay/padding, VBR, browser/FFmpeg/rekordbox decoder differences, and real playback alignment remain unmeasured. No offset is guessed.
- Export writes each artifact atomically but the two writes are not one transaction. XML destination overwrite is delegated to the native save dialog; the sidecar refuses to overwrite an existing JSON backup. Existing tracks in rekordbox may need a separate collection update; behavior is not verified here.
- No actual model, music dataset, model licensing evaluation, musical accuracy metrics, or real rekordbox import test has been completed. FFmpeg and ffprobe are locally installed external dependencies, not bundled; their build configuration and redistribution rights require inventory before packaging.

## Next work

Prove native playback/seek and cue editing on real files; implement bounded, cancellable background analysis and a pinned beat-analysis provider; move peaks out of JSON IPC and key caches to audio identity; then implement robust cue proposals, library/metadata workflows, safe acquisition, packaging and cross-platform verification. Never report XML generation as a successful rekordbox import.
