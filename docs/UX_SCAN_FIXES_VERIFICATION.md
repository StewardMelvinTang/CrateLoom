# Signed-in scan failure, interface rework, and fault fixes

Date: 2026-11-13, Asia/Taipei. Branch `ux-scan-fixes` (not pushed, not merged; `main` is unchanged). The installed `/Applications/CrateLoom.app` was not rebuilt or replaced by this work.

## What was wrong with the signed-in YouTube scan

The saved attempt log for `LTFCuhofnN8` from the owner's run on November 12 showed four attempts classified `connection`, while an anonymous request for the same video returns the age-verification error (`age`). Reproduced with the pinned helper and an empty fake profile: when a browser session cannot be read the helper prints `ERROR: could not find chrome cookies database in …` (same for Firefox and Edge). The engine had no category for that, so it reported "The public audio connections did not complete" and tried three more routes that cannot use a session.

Changed:

- `youtube_audio.failure_kind` returns `session` for an unreadable browser session, including a denied keychain request that the helper reports only as a warning.
- With a browser selected, only the helper's own transfer is used (two routes), helper warnings are kept, the first byte may take up to 150 s (keychain or privacy prompt), and the first unreadable session stops the attempts.
- `youtube_audio.access_message` builds every access message. It names the browser and the remedy: Full Disk Access for Safari, the keychain prompt for Chrome and Edge, sign-in for Firefox, and a distinct message when the session is readable but the account still fails the age check.
- `set-audio/<video>/attempts.json` records the browser, route, result, and one sanitized error line per attempt (no query strings, home path shortened).
- A set report returns its saved `youtube_browser`; `sets.auto_scan` accepts `youtube_browser` so a paused scan can resume with another session.

Observed:

- Real engine, real YouTube, public access, disposable library: scanning `https://youtu.be/LTFCuhofnN8` ends with "YouTube requires age-verified access to this mix. Choose your signed-in browser under YouTube access, then retry." shown inside the YouTube access block beside the link field. The attempt log lists four routes, each `age`, each with the helper's error line.
- Fixture tests cover the Chrome, Firefox and Safari failure texts, the denied-keychain case, early stop, route selection, and the messages.

Not verified: reading a real browser session. No browser profile or cookie store was accessed during this work. The owner's own signed-in scan of `LTFCuhofnN8` is still the remaining check; the message it produces now identifies which layer failed.

## Engine faults fixed

Set import, downloads, recognition: Find Music text search raised on every call (missing lock in `providers.musicbrainz`); stall backoff blocked an explicit resume; retry refused during a large set preparation; `sets.add_local` raised a raw error; `sets.apply_recommended` lost started tasks when the queue filled; duplicate links accumulated; deleting a report left its scan running; a whole-mix scan discarded its matches at the deadline; undecodable audio produced a traceback; cancel races could leave automatic continuation on or reopen a finished job; an unexpected worker reply could end the continuation thread; development launches loaded the engine module twice.

Library, metadata, export: one bad path failed a whole import; opening metadata during a batch made the batch skip that track; Ogg/Opus tag writes failed when a cover was staged; titles ending in a version word were split ("In Da Club"); remixer phrases were reordered in queries; non-ISO dates aborted ID3 writes; export held the library write lock during the audio copy and under-checked free space without hard links; status files and the playback cache were never cleaned; an unencodable reply stopped the request loop.

Added: `playlists.delete`, `library.remove` (database only; audio files are never deleted), `acquisitions.clear_failed`; `.opus` import; name search without a separator ("daft punk one more time") now finds the artist; identical recordings that differ only by release country are listed once.

## Tempo analysis

The tracker's tempo is quantized to candidates several BPM apart. Analysis now fits one constant tempo through the tracked beats, refines it against the onset envelope, and prefers an integer tempo when the audio supports it equally well. A steady track with no owner-set grid gets that straight grid by default; bar 1 stays unconfirmed, so nothing is exported until the owner confirms it. Tracks whose tempo drifts keep detected beats.

Six tracks from a disposable copy of the owner's library, old value then new: 126.048 → 127.0, 139.675 → 140.0, 135.999 → 135.0, 123.047 → 122.977, 123.047 → 122.694 (agreement 0.998), and one track left on detected beats (agreement 0.323). These are measurements on six files, not an accuracy benchmark; no reference BPM list was used.

Analysis version is `librosa-0.11.0-grid-6`, reported in the handshake. Existing tracks are re-analysed when opened, or in bulk through Analyze beats in the library. The queue is driven by the window; it slows while the window is hidden.

## Interface

See `DESIGN.md` ("Clarity rules added with the November 13 rework") for the conventions. `main.tsx` keeps state and engine calls; views are `LibraryView`, `CueEditor`, `PlayerBar`, and the panels. Opening or closing the Cue Editor animates the content area only.

Checked in a browser preview (`scripts/preview-browser.mjs`: the real frontend against a real engine and a disposable library copy), Chromium only, light and dark, 1280 px and about 700 px. Native file dialogs do not exist there, so import, relink, export, and the local-file set inputs were checked by reading. Nothing was checked in the Tauri window itself. Write tags was never pressed.

## Checks run on the final tree

| Check | Result |
| --- | --- |
| Python suite with beat and tag runtimes | 170 tests, 169 passed, 1 skipped (optional detailed model) |
| Frontend utility tests | 14 passed |
| TypeScript check | clean |
| Rust bridge tests (native-storage copy) | 2 passed |
| Interaction harness, Chromium and WebKit | exit 0, no failures, no unhandled engine calls |

The harness (`scripts/verify-workspace-ui.cjs`) now also checks: the editor transition animates only the content area; a click on a cue marker does not move it while a drag does; an access error appears in the YouTube access block and the browser choice persists; playlist delete and track removal confirm first; leaving the editor returns to the view it was opened from; Pause scan stays above the player at 900x650.

## Known gaps left open

- The engine answers one request at a time behind one bridge mutex, so slow synchronous calls (single-file import, `analysis.run`, tag writes, synchronous export) still block other calls.
- The analysis queue lives in the window, not the engine.
- A set report that combines a published list with recognition has no resumable checkpoint; only its partial matches are kept.
- Metadata lookup and Find Music pace MusicBrainz separately, so together they can exceed one request per second.
- A raw `.aac` file imported singly still gets ffprobe's inaccurate duration.
- At 600x540 with the Auto metadata results strip open, the library toolbars wrap and leave little room for rows.
- Earlier verification documents still contain the stale statements listed in `PROJECT_HANDOFF.md` section 11.
