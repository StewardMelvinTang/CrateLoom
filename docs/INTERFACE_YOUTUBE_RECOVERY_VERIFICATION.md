# Interface And YouTube Mix Recovery

Implemented and checked on macOS Apple Silicon, 2026-11-12.

## Interface

The library separates playlist naming, count, filtering and refresh from metadata and export actions. Selection operations live in a dropdown, and export has a compact settings dropdown. The previous Copy audio checkbox inherited a 140px text-input width; the replacement Include audio files checkbox has an explicit size and a connected label.

Metadata review has previous and next arrows in its fixed header. Both save the current track to the library before navigating. A failed save keeps the same track and its edited fields. Saving is serialized, and loading disables navigation. Source files still change only through Write tags. The panel surface morphs independently of its form text when its dimensions change. Dialogs retain their content during exit, while disclosures use rotating arrows, animated expansion and inert closed content. Native selection controls retain native keyboard behavior and use a consistent arrow.

The whole artwork/title/artist region of the bottom player opens Cue Editor. Shared artwork and text transition into the editor header on browsers supporting View Transitions, with a fade fallback and reduced-motion support.

DJ-set scans keep their input and navigation available. Progress is centered in the space below the form. History and Current set remain usable; a compact status strip leads back to the active scan. Completion preserves the user's chosen History/report view. Access errors expand Options so the YouTube access setting is immediately available.

The custom titlebar no longer adds a second maximize handler alongside Tauri's own double-click behavior. The minimum window size is 600x520, allowing smaller macOS tiles. An isolated native probe verified stable positions and sizes at 900x650, 720x600 and 600x540, and one maximize followed by an exact size restoration on successive simulated titlebar double-clicks. Manual OS snapping/drag gestures remain outside that probe.

## YouTube Recovery

The macOS bundle includes yt-dlp 2026.10.26, installed from the official release and verified against a pinned SHA-256. It uses the locally installed Deno runtime. Builds include the upstream licenses. CRATELOOM_YT_DLP remains an explicit override for another extractor.

Mix acquisition now prefers yt-dlp's transfer handling. Direct WebM/M4A connections and another public client remain fallbacks. This avoids treating a resolved signed CDN URL as evidence that a custom HTTP transfer will succeed. If only a supported low-resolution stream containing video and audio is available, FFmpeg copies its audio stream into a separate analysis file without reencoding. The cache still verifies identity, duration, file size and hash, and retains the existing three-hour/512 MB input bounds.

Published metadata is read independently of audio extraction. Full page descriptions, chapters, numbered/prefix/suffix timestamps, and coherent published comment tracklists are supported. Comment evidence is labeled. Named chapters without artist credits survive audio failures and recognition results. A successful audio scan with no identified recordings retains the unidentified range and is not reported as a fetch failure.

Errors distinguish access restrictions, age verification, region restrictions, challenges and connection failures. One client's age warning does not stop the remaining connections. Diagnostics store only video identity and attempt outcome categories, excluding signed CDN URLs, headers and cookies.

Options includes Public access and an explicit signed-in browser choice for Safari, Chrome, Firefox or Edge. Public access is the default. Only an explicitly selected browser is passed to yt-dlp for that scan; a saved report remembers the browser name for continuation. CrateLoom does not store a cookie export. Browser/session availability and YouTube's account eligibility can still prevent access.

## Live Evidence

- HHloSWHKdvw: recovered 52 description/chapter entries. A complete 3,783-second WebM downloaded and validated at 28,737,378 bytes. Direct CDN connections returned HTTP 403; native extractor transfer succeeded. After changing the preferred path, a fresh complete transfer took about 17.25 seconds and used the first connection.
- uVR76AQGfdE: complete 1,785-second WebM downloaded and validated at 14,337,225 bytes in about 16.49 seconds. A real recognition run returned two recordings and a saved-through position of 30 seconds before Shazam rate-limited further requests. This establishes acquisition and recognition handoff, not complete set identification.
- RSxaogPCjD8: the updated extractor resolved its exact video identity and an audio format. A complete download was not counted for this video.
- User-supplied LTFCuhofnN8: YouTube identifies this as "Siangyoo |High-Energy| Hip-Hop, R&B, Edits 014", 1,867 seconds. No published description/chapter/comment tracklist was recovered. All public audio attempts required age verification. The user chose to configure the browser session in the app, so no browser cookies were accessed for live testing and authenticated acquisition is not claimed as verified.

Live requests and recognition used temporary libraries. Native rendering used a SQLite backup with automatic scans and acquisitions disabled; the original library was untouched. Native captures under a second copy of the same application identity were blank. A separately identified review bundle rendered the production interface successfully. The installed copy must be quit before opening the update.

## Checks

125 Python tests ran: 124 passed and one optional detailed-model test was skipped. The 25 focused scan tests also pass after strengthening the alternate-client and browser-session continuation cases. All 13 frontend utility tests and both Rust bridge tests pass. Production frontend and macOS builds pass.

Chromium and WebKit interaction checks cover save-and-previous/next, failed-save retention, closing presence, persisted review, player-to-editor navigation, export checkbox sizing, light/dark and 600px layouts, ordering, cancellation, navigation during scans, and retaining History on completion. The signed-in browser flow is tested with fixture IPC, without reading a real session. Evidence is under `.impeccable/review/interface-youtube-recovery/`.

References: [yt-dlp release](https://github.com/yt-dlp/yt-dlp/releases/tag/2026.10.26), [YouTube extractor guidance](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#youtube), [upstream licenses](YOUTUBE_HELPER_LICENSES.txt).

## Installation

Installed and signature-verified at `/Applications/CrateLoom.app`. The previous copy is preserved at `/Users/melvin/Library/Caches/CrateLoom/installed-backups/CrateLoom-20261112-195215.app`. Main executable, frozen engine and bundled YouTube helper hashes match the tested build; the bundled helper reports 2026.10.26. Existing user app processes were left running. Quit those copies before opening the update.
