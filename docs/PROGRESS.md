# Progress and verification

## Current state

M0 feasibility checks have been exercised with the boundaries in [M0_VERIFICATION.md](M0_VERIFICATION.md); The M1 local workflow is implemented as a development build. The M3 development baseline includes batch import, duplicate detection, relinking, ordered playlists, metadata/artwork candidates, staged edits and explicit tag writing; see [M3 verification](M3_VERIFICATION.md). The M2 development baseline is implemented and the owner reports the current workflow works well; see [M2_VERIFICATION.md](M2_VERIFICATION.md). The local Python engine imports individual files or batches using ffprobe, stores track records and manual cues in SQLite, derives a real mono waveform envelope using FFmpeg, and exports a one-track rekordbox XML playlist and a separate JSON cue backup. The UI connects local import, automatic waveform extraction, playback and seeking, waveform zoom, manual A–H cue creation, rename, precise time editing, marker dragging, undo/redo, persistence and export. Import and analysis leave originals unchanged; explicit tag writes use verified copies and backups without re-encoding audio.

Automated integration uses generated 2-second WAV audio: protocol handshake, import and same-path duplicate, waveform decode, cue persistence across process restarts, XML escaped Unicode/file URL and 1-microsecond serialization, and JSON backup. This checks mechanics, not musical analysis or audible alignment.

The native development app has been exercised with a generated WAV through import, playhead movement, seeking, waveform extraction, cue creation, restart and export. An independent parser read the exported track, playlist and cue. A frozen Python sidecar has been smoke-tested alone and through the Tauri development window; it is not bundled with FFmpeg or a distributable app. Beat estimation is integrated through the optional pinned analysis environment. See [M0_VERIFICATION.md](M0_VERIFICATION.md) for measurements and important verification limits. The shared SSD creates AppleDouble files that break Cargo/Tauri parsing; the README uses a native-storage copy.

## Important limitations

- Local beat/tempo estimates, RGB waveform cache, manual section bands, cue colors, conservative suggestions, pre-roll and seconds-based loop preview are available. Automatic downbeats, flexible beat-warp editing, cue slot reassignment and portable export audio copying are not implemented. Online acquisition currently supports direct public HTTPS audio URLs only.
- Import queues beat/RGB analysis; the legacy overview extraction remains synchronous when used. M2 beat/RGB analysis runs in a cancellable separate worker and stores its larger arrays in a scoped cache, but still decodes in memory and is limited to 15-minute tracks. A whole-file hash and audio-stream signature now protect changed assets. Existing schema-v1 tracks must first be opened unchanged to establish their stream signature; replacement or ambiguous changes are blocked. Detection is tested on generated WAV replacement and a single MP3 tag-only rewrite, not every container or transformation.
- Development shell normally starts Python from the source tree; a frozen sidecar can be selected for testing, but no bundled sidecar, FFmpeg, signing, notarization, clean-machine test or Windows test exists. Python/FFmpeg startup failure is not yet gracefully recovered. No release artifact has been produced.
- Tauri asset access is restricted at runtime to selected imported track paths, but local webview playback position and a click-to-seek were exercised; range behavior, audible playback and decoder alignment require further measurement. JSONL has no timeout, crash recovery, queue, or robust maximum-response guard. Do not treat this as hardened IPC.
- The canonical stored cue unit is integer microseconds on the decoded media timeline. MP3 delay/padding, VBR, browser/FFmpeg/rekordbox decoder differences, and real playback alignment remain unmeasured. No offset is guessed.
- Export writes each artifact atomically but the two writes are not one transaction. XML destination overwrite is delegated to the native save dialog; the sidecar refuses to overwrite an existing JSON backup. Existing tracks in rekordbox may need a separate collection update; behavior is not verified here.
- The original All-In-One package failed to import with the resolved NATTEN API. A separately installed optional inference fork ran on a synthetic fixture and is available for automatically applied section labels that can be edited. Its manifest and the upstream model card disagree about the weights license; see M2_VERIFICATION.md. No representative music dataset, musical accuracy metrics, or real rekordbox import test has been completed. FFmpeg and ffprobe are locally installed external dependencies, not bundled; their build configuration and redistribution rights require inventory before packaging.

## Next work

Review M3 with copied music folders, playlists, metadata matches and backup-protected tag writes. M4 now has source review and a verified direct-download route; owner testing remains pending. Multi-track playlist export is the next implementation milestone, M5. Carry forward the unverified rekordbox import/alignment, structure-model licensing, and packaging boundaries; these do not turn XML generation into a successful rekordbox import.

## M1 native verification (2026-11-03)

On a generated 20-second WAV, the development window imported and automatically displayed the waveform. A cue was added, renamed, set to 5.25 seconds, undone and redone, dragged to 9.286432 seconds, exported, and restored after restart. Triggering the restored cue advanced playback from approximately 9.29 seconds. The XML and JSON contained the same cue time and label. This does not prove audible alignment or actual rekordbox import. Automated tests cover eight slots, reanalysis preserving cues, rejected invalid cue edits, replacement detection, and one MP3 tag-only change. The owner has tested the current development build; detailed playback and rekordbox alignment remain to be verified.


## M2 owner acceptance (2026-11-04)

The owner reports that the current development workflow works well. Automated verification covers analysis cancellation/cache/preservation, grid persistence and restoration, one-button cue insertion and deduplication, section-model application preserving manual edits, signed waveform data, migration backup, and XML serialization. Desktop checks have exercised cue insertion through the Rust bridge, grid placement/undo, and the signal renderer on synthetic audio and a real MP3. Scrub DSP and wheel gesture classification have separate Node tests. Mouse/trackpad inference is heuristic; an explicit input override is available under Analysis info. Exact Rekordbox audio behavior, measured sustained frame rate, real rekordbox import, Windows, and distributable packaging remain unverified.


## M3 preparation verification (2026-11-04)

Native checks created/reordered a two-track playlist, reviewed selected generated tracks, fetched live MusicBrainz candidates and a release cover, staged metadata, and wrote MP3/FLAC fixture tags through the batch review with backup paths. Automated tests cover ordered membership across restart, staging without source writes, stale-review rejection, provider cache/offline failure, image embedding, audio/comment/cue preservation and verified backups for generated MP3/FLAC/M4A/WAV/AIFF/OGG. These are generated-format checks, not every vendor tag or real DJ library. Owner acceptance of M3 is pending.

## Metadata matching follow-up (2026-11-04)

Download filename cleanup, artist/title separation, named-remix preservation, duration/version rejection, official-single preference, release-aware artwork clearing and 1200px cover requests are implemented. A local test of the four owner filenames found plausible Faithless and A$AP Ferg candidates; the two niche remixes did not yield reliable catalog matches. No match was auto-applied to the owner library or written to source files. Text lookup does not establish an exact audio version. The experimental ShazamKit probe passed a local self-match but catalog recognition failed with SHError 202 / underlying AMSError 306. It is not integrated into the app. See [verification and next steps](METADATA_MATCHING_VERIFICATION.md).

## Waveform grid and transport follow-up (2026-11-04)

The preview grid now covers silent intros, dropped detections and outros without changing detector evidence. Beats and bars have distinct lines/edge ticks; 4/4 bar phase is provisional (`~`) until bar 1 is set. Display-frame scrubbing avoids repeated media seeks, playback interpolates coarse media time, and signed-envelope range queries are cached. Adjustable fractional-to-64-beat jumps preserve phase and playback state. Python/Node/build and isolated rendered-app checks passed; copied GO! analysis confirmed that late first detections caused the visible intro gap. Physical high-refresh and native scratch feel remain owner checks. See [waveform verification](WAVEFORM_GRID_VERIFICATION.md).


## M4 discovery/acquisition increment (2026-11-06)

Find Music supports reviewed direct HTTPS audio downloads and MusicBrainz recording references, with optional release covers. The persistent queue includes progress, cancel/retry, original-source quality, provenance and automatic analysis after import. A real artist-hosted MP3 completed download → verification → import → BPM/RGB analysis in an isolated library while the source screen was closed. UI checks cover reference/source selection, covers and explicit unsupported-provider messaging. YouTube/SoundCloud acquisition and Spotify metadata/acquisition remain unavailable. See [M4 verification](M4_VERIFICATION.md) for test evidence, source attribution and recovery boundaries. No owner acceptance, universal download support or release packaging is claimed.


## M5 development increment (2026-11-07)

Ordered playlist and selection export, optional verified audio copies, cue/settings backup and integrity manifest are implemented. YouTube, YouTube Music, Spotify and SoundCloud public reference links resolved live. Direct HTTPS remains the acquisition adapter. An Apple Silicon app bundle was built, sealed, freshly extracted and exercised; native export and optional BPM/RGB analysis passed. rekordbox 7 accepted the generated XML source, with collection import and audible alignment still pending. Windows and public release signing/notarization remain pending. See [M5 verification](M5_VERIFICATION.md).

## DJ-set import and reviewed beatgrid export

Import DJ Set reads published YouTube tracklists and chapters, accepts pasted timestamped tracklists, and supports optional ACRCloud scans of a public video or a local audio file. It saves the report separately from the playlist. Preparing the playlist reuses exact library matches and queues eligible creator downloads. Open the report to review version conflicts and unresolved entries.

Rekordbox XML now includes the fixed BPM and the adjusted 4/4 downbeat for tracks with a confirmed bar 1. Estimated or unconfirmed grids leave no tempo entry. See [setup and validation](SET_IMPORT_VERIFICATION.md).

## App icon: Crate Dig mark (2026-11-12)

The app icon and title bar mark now use the "Crate Dig" mark: a vinyl record pulled up out of a crate. `src-tauri/icons/CrateLoom.icns` and `icon.png` were replaced; the `.icns` keeps a separately drawn small cut in its 16 and 32 px slots, so don't regenerate it with `tauri icon`. The title bar shows the one-colour small cut (`assets/brand/crateloom-mark-small-black.svg`), which the existing dark-mode rule inverts. The source kit, with lockups, web icons and a usage guide, was produced in the design scratch workspace (`crateloom-brand/crate-dig-kit`). UI colours are unchanged.
