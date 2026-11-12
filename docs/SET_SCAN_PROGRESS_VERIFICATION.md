# DJ Set Scan Progress, Cancellation And YouTube Recovery

Implemented on macOS Apple Silicon, 2026-11-12.

## Scan Controls

Initial tracklist and audio scans display their status, icon, progress and Cancel scan button in the center of the available workspace. Form inputs remain intact when a scan is cancelled. Automatic audio continuation uses a centered status panel inside the saved report, with Cancel scan and Resume audio scan controls. The compact layout keeps Cancel above the player bar.

Progress describes actual work: tracklist entries when available, received bytes during acquisition, checked seconds for sequential recognition, and samples plus current audio position for adaptive recognition. Adaptive totals are unknown because transition refinement adds samples, so that phase uses an indeterminate bar and real counters. Saved-through time is shown separately from the current sample position.

`set_scan_jobs.py` gives scan jobs a cancellation event and progress contract through the existing job endpoint. Extractor and FFmpeg operations share the cancellation context. Recognition processes are checked every 200ms and their process groups are stopped on cancellation. Network transfers check cancellation between bounded reads, including range-worker threads. Cancellation is acknowledged while remaining work is stopping.

Published entries, sequential matches and adaptive sample plans already found are retained. Adaptive samples remain pending until a complete section can establish its approximate timeline. Explicitly cancelled checkpoints store a disabled continuation row, including legacy reports, and remain paused after restart. Closing the app does not turn an earlier explicit cancellation back into an automatic resume. Completed source audio, cues and playlists are preserved.

## Recovery Paths

- Published descriptions and chapters are read through yt-dlp first, with a bounded public YouTube page/embedded-JSON fallback. Video IDs are checked, and page JavaScript is not executed.
- Mix recognition first tries a smaller audio-only WebM/Opus stream, then direct M4A. Recognition does not need the full-quality recording used for library acquisition.
- If direct connections fail, yt-dlp handles audio-only segmented streams, bounded retries and a supported alternate public client. All attempts address the same video; another DJ set is never silently substituted.
- Transfers that stall or cannot finish within their deadline can move to the next connection instead of waiting out a ten-minute transfer.
- A completed mix is probed for audio-only content and matching duration, hashed and stored under `set-audio/<video-id>/`. Later batches and retries reuse verified audio. The cache is bounded to four mixes and approximately 1 GB, with a 512 MB per-file and three-hour mix limit.
- Published tracklists remain useful when audio is unavailable. Exhausted connections produce a retry explanation; they no longer force a local/offline-file workflow. Private, removed, region-limited or otherwise inaccessible videos cannot be guaranteed available through public clients.

Library-track downloads retain their existing format/quality policy. Smaller streams are used only as analysis input.

## Verification

Final checks: 115 Python tests ran, with 114 passed and one optional detailed-model test skipped. All 13 frontend utility tests, both Rust bridge tests, production frontend compilation and the macOS app build passed. The installed bundle's signature verifies, and its executable/engine hashes match the tested build.

The Python suite includes regression cases for initial tracklist cancellation, real recognition-process termination, partial report preservation, automatic continuation cancellation, cancellation before worker registration, paused checkpoints, first cancellation reason, range-thread cancellation, active-job retention, public-page recovery and identity checks, format fallback, verified-cache reuse, segmented command constraints, exhausted sources, and progress emitted by both actual recognition algorithms using controlled provider responses.

Chromium and WebKit interaction checks exercise initial tracklist/audio cancellation, restored inputs, unknown totals, automatic continuation cancellation, sample preservation and Resume. They verify both axes of centering on desktop and at 900x650, with the compact Cancel button above the player. Existing sidebar, ordering and automatic metadata checks run alongside these cases. Screenshots and result JSON are under `.impeccable/review/set-scan-progress/`.

Live source checks used separate temporary data:

- The public-page fallback read metadata and 52 chapters from `HHloSWHKdvw`, the mix associated with the latest saved transfer timeout. The packaged scanner produced 26 credited track entries in about four seconds.
- Both M4A and WebM streams supplied the first 262,144 bytes for that same mix. The observed transfers took about 6.15s and 2.05s respectively; these are small request checks, not full-download benchmarks.
- With direct-stream failures injected to exercise the backup path, the segmented downloader fetched a public 19.021-second clip (`jNQXAC9IVRw`), validated its audio-only WebM file and saved it to the managed cache. An older yt-dlp test video (`BaW_jenozKc`) was unavailable and was not counted as successful recovery.
- The packaged engine cancelled an actual YouTube tracklist lookup and a controlled recognition process, retaining its match/checkpoint. A separate restart check verifies that an explicitly cancelled adaptive scan stays paused with its samples intact.

These checks establish transport, progress and cancellation behavior, not Shazam catalog coverage or musical identification accuracy. Native OS-level mouse automation remains unavailable while macOS Accessibility permission is disabled; UI interaction evidence comes from Chromium and WebKit.

The updated app is installed at `/Applications/CrateLoom.app`. The previous version is preserved at `/Users/melvin/Library/Caches/CrateLoom/installed-backups/CrateLoom-20261112-174107.app`. Existing user app processes were left running; reopening the installed app after quitting those copies loads the update.

References: [yt-dlp options and extractor clients](https://github.com/yt-dlp/yt-dlp#usage-and-options), [ShazamIO](https://github.com/shazamio/ShazamIO).
