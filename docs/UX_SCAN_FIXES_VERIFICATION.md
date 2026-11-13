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

Analysis version was `librosa-0.11.0-grid-6` at this point (now `grid-7`, which adds key detection), reported in the handshake. Existing tracks are re-analysed when opened, or in bulk through Analyze beats in the library. The queue is driven by the window; it slows while the window is hidden.

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

## Faster Import DJ Set (November 13)

### What changed

- **New scan panel.** The Scan from switch (link, file, paste) is kept. Link mode is now one row: the link field, an "Access: Public" toggle (YouTube only), and Scan, with one helper line. The access block opens from the toggle, or by itself when an access error occurs. The recognize checkbox and the Advanced token disclosure are gone. Link scans send `fingerprint:false`; "Recognize audio too" on the report starts recognition for a published list. Spotify and SoundCloud token fields appear only where an error or a reference-only row needs them. Tracking parameters (`si`, `utm_*`, `fbclid` and others) are removed in the field and again in the engine.
- **Plain errors.** Engine failures reach the panel as one sentence, with the raw text only under "Details". A frozen engine whose app file was replaced while it ran now says "CrateLoom was updated while it was open. Restart CrateLoom to continue." The frozen sidecar imports every engine module at startup, so the old lazy-import path no longer breaks after an install.
- **Sources are found while the report is read.** After a report is saved, `set_discovery` searches YouTube for each searchable row: 3 threads, at most 4 yt-dlp searches at once, waiting while a mix download runs. It never resolves, enqueues or downloads, and it stops once a playlist exists. MusicBrainz runs afterwards as background enrichment and never delays a row's result. "Create playlist" reuses fresh results instead of searching again. Rows with no artist but a title of at least two words are searched by title, and can at best be "Choose a version". Nothing is ever picked for them automatically.
- **Overlapped steps.** For a mix without a published list, the audio download starts while the comments are read. The report is saved as soon as the audio is ready, and recognition continues as the automatic follow-up.
- **One continuous recognition pass.** There are no 5-minute batches, and answered samples are saved while the worker runs, so cancel and resume never ask Shazam again for them. The panel lists provisional hits ("Heard so far"). Pacing is adaptive: one request in flight, starting at 2.0 s start-to-start and slowly falling to a floor of 1.2 s. A 429 brings a pause and a persisted backoff. Worker failures are reported as codes; the engine turns them into sentences.
- **Faster yt-dlp start.** The pinned release's unpacked build (`yt-dlp_macos.zip`, SHA-256 pinned) ships as an app resource. On first use the engine verifies it and extracts it to `~/Library/Caches/CrateLoom/tools/yt-dlp-2026.10.26-<first 12 hex digits of the pinned SHA-256>-dir` (named by the pin, so engines pinned to different builds of the release never replace each other's folder); it falls back to the onefile helper silently on any problem, including a folder that stops starting later (the helper is retried once with the onefile build and the folder is extracted again at the next start). `CRATELOOM_YT_DLP` still wins.
- **Integration fix found here.** Cancelling discovery (Create playlist, delete, shutdown) did not stop catalog enrichment once all searches had finished: the report's work entry was already forgotten, so the cancel found nothing to stop. The work entry now stays until enrichment ends. A test was added (`test_cancel_after_searches_stops_catalog_enrichment`).

### Measured before and after

All "after" figures come from the dev engine with fresh `/tmp/crateloom-wf2-verify-*` libraries. Enqueue was patched to record only, and no Shazam request was made. "Before" figures are the baseline timing measurements taken earlier the same day.

| Step | Before | After | Target |
| --- | --- | --- | --- |
| HHloSWHKdvw: report saved | 0.95–1.05 s (M) | 0.90 s and 1.17 s (M) | ≤ 1.5 s, met |
| First source search after the report | only on Create playlist (M) | already running when the report returned (M) | ≤ 2 s, met |
| First 12 searchable rows final | 90.1 s on a 4-thread pool during Create playlist; 254.5 s serial (M) | 7.4 s after the report (M) | ≤ 45 s, met |
| Rows with a ready source | 4 of 12 (M) | 4 ready, 5 to choose, 3 none (M) | ≥ 4, met |
| Enqueues during discovery | — | 0 (M) | 0, met |
| Create playlist, whole job (same 12 rows) | 90.1 s (M) | 21.7 s; 29.9 s before the integration fix (M) | ≤ 20 s, **missed** |
| Create playlist, until the 4 downloads are queued | — | 2.7–3.0 s (M) | — |
| yt-dlp `--version` | 3.70–4.17 s, onefile (M) | 0.19–0.26 s, onedir (M) | — |
| One `ytsearch15` | 4.78–4.82 s (M) | 1.17–1.18 s (M); discovery median 1.29 s over 16 searches | — |
| First use of the onedir build | — | about 4.2 s once per machine (M, by the engine implementer) | — |
| tGjZrfLJsMc: report visible | recognition started at 24.5 s; first rows after the first 5-minute batch, about 50 s (M / E) | 13.2 s, recognizing with a stub at once (M) | ≤ 20 s, met |
| Comment check vs audio download | serial: 5.5 s comment check, then download (M) | both started at 1.02 s; download ran through the 2.0 s comment check (M) | gap < 0.5 s, met |
| Recognition pacing (stub) | 2 s pacing plus about 3.5 s idle at each batch boundary (read in code) | one worker per pass; start-to-start 1.8–2.0 s, never above 2.0 s (M) | ≤ interval + 0.5 s, met |
| First provisional hit | none until the batch ended | 1.0 s after the report (M) | visible during scan, met |
| Cancel, then resume | samples of an unfinished batch were lost (read in code) | 26 samples kept; resume asked 20 new ones and repeated none (M) | met |
| Whole 37-minute mix (stub, no throttling) | about 6 min for 60 min (E) | 46 requests, about 90 s of recognition (M, stub); the transition positions in this run predate the offset fix below and were up to the stub's 20 s cue-in early | — |
| Frozen engine, binary replaced, then a YouTube scan | reply "Error -3 while decompressing data: incorrect header check" (M) | scan completed with 52 rows (M) | — |
| Forced lazy import after replacement (onefile probe using `errors.py`) | raw zlib text | restart sentence; detail ends "(the app file changed)" (M) | — |
| Frozen engine start to handshake (3 warm runs) | 1.69–1.88 s (M) | 1.77–1.94 s (M), about 0.1 s for the eager imports | — |

M = measured, E = estimated.

**Why Create playlist missed 20 s.** The four rows with a found source were queued within 3 s. The rest of the job was MusicBrainz lookups for the three rows where discovery found nothing. Their background catalog check had not finished when Create playlist ran: it was started immediately after discovery, and enrichment runs one request a second. Prepare then ran the full lookup for them, about 14–17 s serialized on the MusicBrainz pacer. When the owner reads the report for a while first, enrichment usually finishes and those rows are skipped (estimated, not measured). Two ways to meet the target are left for the owner to decide:

- enrich rows with no source first;
- skip MusicBrainz in Create playlist for rows whose search found nothing.

**How it was limited.** To stay within the YouTube request budget, the HHloSWHKdvw run searched only the first 12 rows with an artist or a link, and Create playlist ran on those 12 rows, the same count as the baseline. The engine implementer's earlier unlimited run of all 52 rows finished in 24 s, title-only rows included.

The frozen-engine checks used builds made with `scripts/build-sidecar-macos.sh` in `/tmp`. The bundled-archive lookup was checked by placing the frozen engine and `yt-dlp_macos.zip` in a hand-made `CrateLoom.app/Contents/{MacOS,Resources}` layout, with a temporary HOME. The archive was verified and extracted, and two discovery searches succeeded.

### Fixes after review

An adversarial review confirmed these defects, and each is now fixed:

- **Kept mix download could not be stopped.** A mix with a titles-only list keeps its audio download running for the recognition pass after the scan job ends. That download is now registered under the saved report. Pause scan, deleting the report, and every error raised after the list was read (too many entries, nothing inside the video length) now stop it. Before, it kept the download counter above zero, and that held back source discovery for every report.
- **Create playlist overwrote concurrent writes.** Prepare wrote back its snapshot of the row. Links are now merged by URL into the latest row, and a changed credit is kept: the lookup's artist and title are written only while the row still has the credit the lookup started from. Prepare also builds a fresh library index, so a credit edited within the last 5 s is never attached. The report's 5 s index cache is still used for display only.
- **Unreadable Shazam replies.** During an outage, samples are no longer recorded as misses; they stay unanswered for a later pass. A sample counts as a miss only after two unreadable replies while Shazam answers other samples, or when the pass ends below the throttling streak. A stop caused by three unreadable replies in a row reports `rate_limited` (the detail is kept), so it gets the rate-limit sentence and is not counted as a stalled attempt.
- **Pacing never relaxed.** A slowdown saved after a 429 is now forgotten together with its episode, after an hour without throttling, and the next scan starts at 2.0 s again. A faster pace learned from clean runs is kept.
- **Cooldown lived only in memory.** The end of a rate-limit cooldown is saved in `shazam-pacing.json` (`limited_until`, capped at 15 minutes). Other reports and a restarted engine wait for it too.
- **Offset shortcut placed boundaries at the recording start.** Shazam's offset gives the earliest point the next track can enter, not where the DJ brings it in. When that start is more than 20 s before the sample, one lookup 10 s after it checks that the track is already playing. If it is not, the rest of the interval is bisected. Simulated with the test fake: 16 transitions per hour, mean of 5 mixes:

  | Track brought in | Requests per hour, before → after | Mean / worst boundary error, before → after |
  | --- | --- | --- |
  | from its start | 61.0 → 72.0 | 0 / 0 s → 0 / 0 s |
  | 20 s into the recording | 73.4 → 92.8 | 13.8 / 20 s → 3.4 / 20 s |
  | 30 s into the recording | 79.4 → 95.4 | 15.3 / 30 s → 4.1 / 10 s |

  The fix adds about 11 requests per hour of mix (about 22 s at 2 s pacing) when tracks start from the beginning. A start estimate within 20 s of the sample is still taken as it is, so a cue-in of up to 20 s can still place the boundary that much early.
- **First use of the unpacked yt-dlp blocked the request loop.** Hashing and extraction now run once in a background thread. Until they finish, every caller gets the onefile helper at once; this includes `providers.capabilities`, which runs on the request loop. Leftover `.yt-dlp-extract-*` and replaced-build folders from an engine stopped part-way are removed under the extraction lock, without the earlier one-hour age limit, including on the ready path.
- **Notarization.** `scripts/build-app-macos.sh` now states that the bundled zip holds upstream's ad-hoc-signed code, which the notary service rejects. A release must re-sign and repack it and pin the new hash, or leave it out.
- **Panel.** Scan warnings ("Last attempt", the report note, a failed recognition) go through `plainError`, so raw `[Errno …]` or tool text sits under Details. The engine names the wait: "Waiting for Shazam" only for Shazam's cooldown, otherwise "Waiting before the next attempt". The searching indicator shows whenever discovery runs. The playlist hint counts title-only rows, which are searched by title and are never chosen automatically. The access toggle's accessible name starts with its visible text ("Access: Chrome, change YouTube access"). Spotify and SoundCloud token errors show only in their own field.

### Checks run on this tree

| Check | Result |
| --- | --- |
| Python suite with beat and tag runtimes | 249 tests OK, 1 skipped (after both review rounds) |
| Frontend utility tests | 14 passed |
| TypeScript check | clean |
| Rust bridge tests (native-storage copy; `sets.recognize` now also in the bridge test) | 2 passed |
| Interaction harness, Chromium and WebKit | exit 0, no failures, no unhandled engine calls; now also checks the stall-backoff wait and a raw OSError warning |

### Not verified

- **Real Shazam:** pacing and throughput. The ≥ 25 samples per minute target was not run, the 429 handling ran only in unit tests, and `matches[].offset` from the real service is still unverified. The stub returned canned offsets.
- **Real browser sessions:** cookies for YouTube access. No browser profile was read.
- **The app as shipped:** the full `.app` build (`scripts/build-app-macos.sh`) and the Tauri window. A downloaded app's quarantine and Gatekeeper treatment of the extracted onedir helper was not checked; the engine runs `--version` before adopting it and falls back otherwise.
- **The new panel against the real engine:** checked once afterwards in the browser preview (dev engine, disposable library copy, public access). Pasting `https://youtu.be/HHloSWHKdvw?si=…` dropped the `si` parameter as typed. The report for all 52 entries was on screen within 4 s, with rows already searching. Within about 34 s every row had a result and the header read "13 sources ready". No engine or console errors. Create playlist was not pressed, so nothing was downloaded.

### Remaining gaps

- Create playlist exceeds 20 s when rows with no found source still need their MusicBrainz lookup (see above).
- A catalog request already in flight finishes after a cancel. It takes up to about 8 s, and nothing new starts.
- When the frozen engine hits an ImportError, it always reports "updated", even for a genuinely missing optional module.
- The sidecar is not restarted automatically after an install; the owner restarts CrateLoom.
- `watch?v=…&list=…` still reads the playlist.
- Swapped artist and title are not detected.
- Dead rules for the removed disclosures remain in `workspace.css`.
- Two gaps listed under "Known gaps left open" are now closed:
  - a list combined with recognition has a resumable checkpoint (`sets.recognize`);
  - metadata lookup and set import share one MusicBrainz pacer.

## Playback end, key analysis and the track table (November 13)

### Tracks did not move on and the time ran past the end

Every track in the library is a YouTube download saved exactly as YouTube serves it: a fragmented MP4 (`major_brand=dash`, `moof` fragments). WebKit, the app's web view, reports these files as twice their real length (measured in WebKit: a 188.76 s file reported as 377.46 s; in the preview, "NOKIA", 4:01, reported as 482.09 s). Positions inside the file are correct (audio energy at 30, 100 and 150 s matches a repacked copy); after the music the element keeps "playing" silence until the doubled length, so `ended` fires only then.

- The player now treats the library's measured duration (ffprobe) as the end: on `timeupdate` (which keeps firing while the window is hidden) and on each animation frame, playback past that point is handled as the end of the track, once per track. The clock is clamped to it, and Play after the end starts from the beginning.
- New YouTube downloads are repacked into a regular M4A after verification (`ffmpeg -c copy -movflags +faststart`, audio packets unchanged; the job records `repacked: true` and the new size and SHA-256). Existing files are not changed.
- Verified with the real engine and a real track in WebKit: after seeking to 3 s before the end, the clock stopped at 4:00 and the next track started 3.5 s later. The harness checks the same with an 8 s file listed as 2 s.

### Key detection

Beat analysis now also detects the key (`librosa-0.11.0-grid-7`). The constant-Q spectrum (36 bins per octave, 7 octaves, tuning estimated) is median-filtered to remove drums (harmonic/percussive separation on the spectrum: about 1 s per track instead of 5-19 s for separating the waveform, same answers on 16 library tracks), summed into a pitch-class profile and correlated with the Krumhansl-Kessler major and minor profiles. A flat profile (white noise measured 0.04 standard deviation over mean, songs 0.27-0.65) or a best correlation under 0.2 gives no key. Results are stored with the analysis as Camelot code, name and rekordbox short name; the rekordbox export writes `Tonality`.

Not verified against a labelled dataset (none is available offline). On 16 library songs the detected keys match the documented key or its relative major/minor (same Camelot number) for the songs where the key is well established (for example "If I Ain't Got You" G major 9B, "Levels" E major 12B for C♯ minor 12A, "Feel So Close" G major 9B). Existing tracks get a key when re-analysed: "Analyze tempo & key" in the library, or by opening a track.

### Track table

- Every column header sorts: first click ascending, then descending, then back to the library or playlist order. Empty values (no tempo, no key, no artist) stay last. Key sorts around the Camelot wheel (1A, 1B, 2A…). The sort is remembered; Next and Previous follow the sorted order; a sorted playlist cannot be reordered by hand ("Clear the sort to reorder").
- Columns other than Title have a drag handle on their left edge (Title takes the remaining width), with arrow keys in 8 px steps and double-click to reset. Widths are remembered.
- Right-click a track (or press the menu key) for Copy title, Copy artist, Copy artist and title, and Reveal in Finder. Reveal goes through the engine (`library.reveal`), which only shows a library track's own file.

Checks: engine suite 258 tests OK (1 skipped), tsc clean, frontend tests 14 passed, interaction harness exit 0 on Chromium and WebKit with new sorting, resizing, context menu and track-end checks.
