# Metadata, Ordering And Titlebar Verification

Verified on macOS Apple Silicon on 2026-11-12.

## Result

- The sole collapse/expand control is in the 40px titlebar, after the native macOS traffic lights. Sidebar width, resizing and collapse persistence remain supported. The control restores a sidebar hidden by an earlier preference.
- Native controls retain macOS behavior and colors. Their visible circles and the sidebar icon are centered together, checked from an actual window capture.
- Metadata searches Apple Music and MusicBrainz independently. One unavailable catalog no longer prevents results from the other. Early results can be selected and saved while the remaining catalog is working.
- Catalog retrieval handles credits such as `Flo Rida,T-Pain` and `Low (feat. T-Pain)`. Filename and set/source matching keep their original featured-artist evidence.
- Named remixes, live/studio conflicts, instrumental/acoustic/extended/radio/dub conflicts and large duration discrepancies remain excluded. Candidate provenance includes the actual catalog. Apple metadata and release covers can be saved to the library; source audio changes only through explicit tag writing.
- Both playlists and set reports use captured pointer events for ordering. Dragging includes edge scrolling; arrow keys and move buttons use the same persistence path. Display numbers follow saved order. An immediate lock prevents overlapping order writes, and revision checks reject stale background refreshes. Failed writes restore the previous order.

## Live Metadata Check

The owner's `Low (feat. T-Pain)` row was read from SQLite in read-only mode: artist `Flo Rida,T-Pain`, duration 229.900771s. All lookups, cover downloads and staging checks used separate temporary libraries.

Apple returned `Low (feat. T-Pain)` by Flo Rida, `Mail On Sunday`, release date `2007-10-09`, genre `Hip-Hop/Rap`, duration 231.4s. The 1.50s difference passes the strong text/duration checks. Its catalog IDs are recording `275496141` and release `275496110`.

First selectable results arrived in 0.699s; both catalogs finished in 2.349s and returned 35 usable release candidates. Repeating the search from cache took 0.005s. An earlier independent MusicBrainz probe returned HTTP 503 while Apple answered in 0.74s, demonstrating why independent providers matter. These measurements describe this track and connection, not an accuracy or latency guarantee.

The frozen engine independently searched the song, saved the selected Apple source and fields, fetched and validated the release image, saved its artwork path, and recovered the fields after restart. The final verification exited successfully with engine status 0.

A separate read-only check of four existing library rows returned usable catalog matches for all four. First usable results took 1.060s for `Let Me Blow Ya Mind` (`Eve,Gwen Stefani`), 2.602s for `Starships`, 0.843s for `Low`, and 1.945s for `Where Them Girls At` (`David Guetta,Flo Rida,Nicki Minaj`). Inputs, leading candidates and provider results are saved in `live-catalog-checks.json`. These checks support the featured-credit fix; they do not identify which of several releases originally supplied a local file.

## Automated Checks

- Python: 90 tests, 89 passed and one optional detailed structure-model test skipped. Analysis and metadata worker environments were enabled. New regression cases cover featured credits, independent incremental providers, cache expiry and stale fallback, version exclusions, selected-cover validation and unchanged shared set/source behavior.
- Frontend: production TypeScript/Vite build passed; all 13 Node tests passed.
- Rust: both bridge tests passed; the release app built and its ad-hoc signature verified.
- Chromium and WebKit 26.0: real mouse dragging, keyboard ordering, move buttons, filtered playlist moves, persistence after reload, failed-save rollback and stale-refresh races passed. Early metadata selection and Apple source saving passed. Dark/light desktop and 900x650 layouts were captured, and reduced-motion behavior passed. No page errors occurred.
- Existing backend tests also cover stable set-entry IDs, playlist synchronization and downloads/source jobs finishing after a reorder.

The reusable fixture interaction check is `scripts/verify-workspace-ui.cjs`. Start Vite or serve `apps/desktop/dist` first, provide Playwright through `CRATELOOM_PLAYWRIGHT` if it is outside this repository, and select `CRATELOOM_UI_ENGINE=chromium` or `webkit`. `CRATELOOM_UI_URL` and `CRATELOOM_UI_OUTPUT` override the server and artifact directory. This test replaces IPC with fixtures and never opens the owner's library.

## Native Evidence And Installation

Artifacts are in `.impeccable/review/metadata-ordering/`: Chromium/WebKit checks and screenshots, the native full-window capture, its titlebar detail and the installation receipt.

Screen Recording permission is enabled and native window capture succeeded. macOS Accessibility permission remains disabled for this agent, so OS-level injected mouse/keyboard checks were unavailable. Pointer interactions were verified in Chromium and WebKit; native window dragging, resizing and fullscreen transitions remain manual verification boundaries.

The verified bundle is installed at `/Applications/CrateLoom.app`. The preceding installed bundle is preserved at `/Users/melvin/Library/Caches/CrateLoom/installed-backups/CrateLoom-20261112-154015.app`. The installed executable and engine hashes match the built bundle. Existing user processes were left running; they must be quit and reopened to load the update.

## Provider Boundaries

This is catalog matching, not acoustic identification. Catalog outages, missing releases, regional coverage and unpublished bootlegs can still prevent a reliable match. Manual Find matches leaves application to the user. The separate opt-in Auto metadata action now applies eligible matches with verified covers; see [automatic metadata verification](AUTO_METADATA_VERIFICATION.md). Incompatible and weak matches remain for review. Successful responses are cached for seven days, empty responses for five minutes, and Refresh forces another attempt with saved results available during an outage.

References: [Apple Search API](https://performance-partners.apple.com/search-api), [MusicBrainz search fields](https://musicbrainz.org/doc/MusicBrainz_API/Search), [Tauri window configuration](https://v2.tauri.app/reference/config/). Apple documents approximately 20 Search API calls per minute; the metadata provider gate spaces starts by 3.1 seconds.
