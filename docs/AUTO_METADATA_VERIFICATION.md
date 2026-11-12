# Automatic Metadata And Album Covers

Implemented on 2026-11-12 for macOS Apple Silicon.

## Behavior

The library toolbar's **Auto metadata** button processes selected tracks. With no selection, it processes the current library or playlist view, including its search/filter scope. The number of tracks is shown on the button. Users can keep working while the batch runs and cancel remaining work.

Two workers use the existing independent Apple Music and MusicBrainz lookups and rate limits. A match must have an identified input artist, at least 0.9 title and artist similarity, a rank of at least 85, a release album, and no version/duration rejection. Strong matches take priority, followed by rank. These are ranking rules rather than acoustic-identification probabilities.

For each track, the worker tries the highest-priority eligible release first. If its cover is unavailable, it tries up to five distinct releases within eight rank points of the best candidate. Metadata is applied only after a JPEG/PNG cover has been downloaded and validated. Apple artwork URLs already returned by search are reused to avoid another catalog request. Weak matches, unknown artists, missing files and releases with no usable cover remain for review.

Automatic changes update the library and pending metadata/artwork, preserving source audio and cues. A snapshot of metadata and file identity prevents the job from overwriting edits saved after the batch started. Cancellation and the final apply transaction share a lock, preventing further applies after cancellation is acknowledged.

**Review** and **Review results** reopen saved fields, artwork, the chosen source, match score and cached candidate list. Tracks that could not be applied show a reason. Saving a review marks the automatic match reviewed. A completed batch remains available after reopening the app, even with no selection. **Write tags** is still required to modify original files.

## Persistence And Architecture

- `engine/crateloom/metadata_batch.py` owns bounded workers, ranking eligibility, concurrent-edit checks, cancellation and batch persistence.
- `metadata-batch/latest.json` stores the latest job and per-track results. Interrupted work is marked interrupted on restart; completed changes remain in SQLite.
- `metadata-review/<track-id>.json` stores up to 50 candidates per processed track, with an identity token so changed tracks do not display unrelated saved results. This data is loaded on Review rather than sent with every library row.
- `preparation.fetch_cover` supplies shared synchronous cover retrieval to both manual and automatic workflows.
- `MetadataBatch.tsx` manages progress, scope actions and review. The existing metadata editor displays the persisted automatic result and candidate list.
- Four restricted bridge methods expose start/get/latest/cancel. No database-schema migration is required.

## Verification

The Python suite runs 100 tests: 99 pass and one optional detailed structure-model test is skipped. Ten new batch tests cover selection validation, highest compatible match, cover fallback, no application without a cover, unknown/weak matches, concurrent manual edits, cancellation, per-track failure isolation, journal recovery, cached review, missing files and avoiding redundant Apple lookups. Audio bytes and cue positions remain unchanged in the application test.

All 13 frontend utility tests and both Rust bridge tests pass. Production TypeScript/Vite and macOS bundle builds pass.

The frozen engine was tested against `Starships` in a separate temporary library. It chose the Apple Music release `Pink Friday ... Roman Reloaded (Deluxe Edition)`, rank 100, downloaded and validated its cover, applied the fields/artwork, and returned 50 cached candidates for Review. The batch completed in about 5.2 seconds and exited with status 0. The original audio's SHA-256 was unchanged. This is a live behavior check rather than a general accuracy or speed benchmark.

Interactive browser checks and native captures are recorded under `.impeccable/review/auto-metadata/`. The existing reusable `scripts/verify-workspace-ui.cjs` includes automatic metadata scope, progress, Review, restart persistence and cancellation checks alongside the ordering and sidebar regression checks.

Both Chromium and WebKit passed the full interaction check with no page errors. Dark/light and 900x650 captures confirm the progress panel and review dialog fit the workspace. A native macOS capture confirms the new button, restored batch summary, enabled Review action and real downloaded album cover render in the packaged app. Native OS-level input automation remains unavailable because macOS Accessibility permission is disabled.

The verified build is installed at `/Applications/CrateLoom.app`; the preceding version is preserved at `/Users/melvin/Library/Caches/CrateLoom/installed-backups/CrateLoom-20261112-162257.app`. Installed executable and engine hashes match the tested bundle. Existing user app processes were left running and must be restarted to use the update.
