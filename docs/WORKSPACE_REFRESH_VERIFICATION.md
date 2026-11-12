# Workspace refresh verification

Verified on macOS / Apple Silicon, 2026-11-12. Tests and native launch used isolated libraries.

## Implemented behavior

- Set reports fetch and cache album artwork in the background from playlist images or a matching iTunes recording. Artwork remains attached by stable set-entry identity and is staged on prepared/imported tracks, including artwork that arrives after an audio download. Original audio is unchanged.
- YouTube search collects up to 15 results, supplements original-version searches with an extended-mix search where needed, and exposes at most ten deduplicated suggestions with durations. Recommendations prefer a compatible extended version, then audio/appropriate duration over a music video, while preserving named remix credits and excluding unrelated variants from automatic selection.
- A sole compatible downloadable result is accepted automatically during preparation and when opening an older prepared report. Reference-only service results still require an actual downloadable source. Authenticated SoundCloud uses its existing creator-download route.
- Source choices use a bounded background pool and row-local progress; other rows remain usable. The report offers Apply recommended sources. Review sources and Links share animated, keyboard-accessible disclosures with reduced-motion support and locally bundled Simple Icons service marks.
- Set and playlist rows support handle dragging and keyboard reordering. Visible numbers change; internal identities remain stable. Set reorder updates playlist positions and reserves gaps so later downloads attach in the requested order.
- The sidebar supports persisted width, collapse to a 64px rail, hide/show, pointer and keyboard resizing, and reset by double-clicking its separator. Sidebar branding is removed. The custom title area retains native macOS traffic lights with Tauri Overlay and hiddenTitle. The bottom player has pencil + Edit.
- Playback selection avoids whole-file verification on the critical path. Editing/export retain verification. Unsupported webview formats use cached PCM WAV playback copies, with loading, cancellation and stale-request handling.
- Automatic section analysis uses local energy/texture estimates, preserving manual/reviewed sections and cues. Detailed sections remain an explicit optional action. Fast estimates do not identify vocals, verses or choruses and do not reproduce rekordbox's proprietary analysis.
- The supplied Crate mark, palette masters and app icon are copied into the repository. Application chrome is monochrome in both themes; waveform RGB, musical cue/section annotations and the subtle green recommendation outline retain their informational colors.

## Automated verification

- Python: 85 tests, 84 passed, one optional detailed-structure-model test skipped. Optional analysis and metadata runtimes were enabled. Existing SQLite resource warnings remain.
- Frontend utility tests: 13 passed; TypeScript and production Vite build passed.
- Rust: two tests passed; debug and release app builds passed.
- Ten new backend tests cover recommendation limits and named extended versions, sole compatible YouTube and authenticated SoundCloud sources, queue concurrency, stable ordering during source lookup and later attachment, artwork staging, compatible playback caching, and fast-section boundaries/manual-edit preservation.
- Browser interactions: ten rendered suggestions; two simultaneous manual source checks; three automatic legacy sole-source queues; multiple-option review preserved; reorder/renumbering; sidebar drag to 266px and persisted width; 64px collapse and hide/show; reduced motion; playback/pause; cancellation during loading; successful compatibility playback with one preparation; no page errors or horizontal overflow at 1280px and the 900px desktop minimum.
- Frozen bundled engine: fast sections and AIFF-to-PCM playback preparation passed with a temporary library and no development PYTHONPATH.
- macOS bundle built with the supplied CrateLoom.icns and passed codesign verification.

## Live and visual checks

- A live YouTube search for CHRYSTAL - The Days (Notion Remix) returned 14 videos and ten matching suggestions. Ranking accepts titles such as NOTION Extended Remix while retaining the named remixer. This check did not download the videos.
- A matching Nicki Minaj - Starships album cover was fetched, validated and cached in about 1.03 seconds in a temporary directory.
- Fast sections processed a four-minute synthetic tone in about 0.209 seconds. This measures pipeline latency on synthetic audio, not musical boundary accuracy.
- Browser fixture captures: `.impeccable/review/desktop-dark.png`, `.impeccable/review/desktop-light.png`, `.impeccable/review/compact-light.png`, `.impeccable/review/editor-light.png`. Interaction evidence is in `.impeccable/review/interaction-checks.json`.
- The independent Impeccable finish review scored its sole-source and design-documentation fixes resolved. Its ship verdict covers those scored fixes within browser evidence.

## Boundaries

The native app compiled and opened against an isolated library. macOS denied assistive access, and native window capture failed. Native traffic-light placement, window dragging, and audible playback on representative library files remain manual checks. Windows, detailed-model musical accuracy, and live authenticated SoundCloud were not reverified here. Artwork and remote source availability depend on their providers; unmatched artwork keeps the local placeholder.

Built app: `~/Library/Caches/CrateLoom/m5-app-build/target/release/bundle/macos/CrateLoom.app`. The running user app and production library were not replaced.
