# M3 library increment

## Available

Add Music accepts multiple files. The folder icon scans recursively, skipping hidden names and symlinks. Supported extensions: MP3, WAV, AIFF/AIF, M4A, FLAC and OGG. Each job is limited to 5,000 audio files; scanning errors fail the job without importing a partial discovery list. Import and hashing run in a background host thread so the desktop can continue processing library and playback actions. The import report shows counts and per-file outcomes; malformed files do not stop later files. Cancelling retains completed imports and stops an in-flight hash/probe. Reports survive restart; unfinished jobs are marked interrupted and can be retried by choosing the folder again. There is no automatic resume.

Duplicates match the stored audio-stream hash, codec, sample rate and duration. A repeated path is verified before being reused. An identical copy returns the existing library record rather than making another cue set. This is exact stream identity, not fuzzy song matching: remixes, transcodes and alternate encodings may remain separate. A duplicate whose original path is missing stays attached to its original record until explicitly relinked.

Refresh updates file availability. Missing files can be filtered and relinked from the library or editor. Relink checks audio identity and technical timing before changing the path; it preserves track ID, cues, sections and analysis. It rejects paths already assigned to another record and mismatched audio. Legacy tracks without a stored stream signature require byte-identical files. No audio or tags are changed.

Imported tracks enter the existing session analysis queue. Reports persist, but analysis queue state does not survive app closure; opening a track with an outdated/missing analysis queues it again. Analysis remains limited to 15 minutes, and optional section modeling to five minutes.

## Verified on macOS

Automated tests import a nested generated WAV folder with a duplicate and a malformed MP3, verify 2 added / 1 duplicate / 1 failure, cursor pagination, repeated-import deduplication, cancellation, correct and rejected relinks, and cue/path/report persistence after restart. The native desktop repeated folder import with the same counts, queued waveform analysis, displayed results, saved a cue, detected a moved file via Refresh, and relinked it through the native picker with the cue and cached waveform still visible. Rust regression coverage includes the new bridge methods.

## Playlists and metadata

Playlists store ordered track references. Select rows and create a playlist, then use Add selected / Remove selected, the playlist-name field and up/down controls to organize it. Track removal from a playlist does not remove library records or audio. Playlist-wide XML export is not implemented yet.

Review selected tracks sequentially. Title, artist, album, genre and release date can be staged without writing originals. MusicBrainz search is explicit (cleaned title, artist, version and duration are sent), runs in background threads, includes a project User-Agent, spaces requests by at least 1.1 seconds and caches candidates locally. Filename noise is removed while remix names are retained. Up to three bounded queries prefer the requested version, compatible duration and single release before relaxing constraints. Artist/title/version conflicts and duration differences above max(12 seconds, 8%) are excluded; rejected candidates are disabled and hidden behind an explicit toggle. Release type, country/format when available and duration differences are displayed. A strong text match is not audio identity or calibrated confidence. Raw provider responses are cached separately from the older unranked cache and re-ranked against the current duration. Cached candidates can be refreshed. Local JPEG/PNG covers are validated and copied to the artwork cache; selected release covers can be fetched at up to 1200px from Cover Art Archive. Choosing another release clears the preceding candidate artwork. Release dates come from the selected release; recording first-release dates are not substituted into unknown release dates. Embedded covers can be displayed from existing files. Online candidates are never applied automatically.

Write tags to file is a separate action. Selected tracks also have a batch-write review showing saved changes. Each file is processed independently; Stop after current preserves completed writes. Review revisions include pending fields and file identity, preventing a stale batch review from silently writing a different edit. Reload file tags discards staged library metadata and reads the original again.

The optional tag worker uses pinned Mutagen 1.48.1, installed by scripts/setup-metadata-macos.sh. It edits a sibling temporary copy, reads back the changed tags/artwork, verifies audio-stream hash/codec/sample rate/duration, backs up the original bytes, rechecks the source hash, and atomically replaces the file. Backups are under CRATELOOM_DATA/tag-backups and recipes/results under tag-receipts. Source tags change only when explicitly written. A failed pre-replacement check keeps the original. A process/disk failure between file replacement and receipt/database completion is not a cross-filesystem transaction; inspect receipts and restore the verified backup manually if recovery is needed. Backup restoration has no app button; Reload file tags can resynchronize library metadata afterward.

Generated MP3, FLAC, M4A, WAV, AIFF and OGG fixtures passed text-tag readback and unchanged audio/comment/cue checks. MP3 artwork insertion was tested separately; the native MP3/FLAC batch write also completed. Embedded cover writing is enabled for ID3/FLAC/M4A, and is unavailable for OGG in this build. Unsupported formats, read-only files and hard-linked files are rejected. Unusual vendor/private tag structures, multi-artist conventions, non-front artwork collections and ID3 revisions beyond the tested fixtures need further evaluation. Mutagen may rewrite the tag container even when audio packets are unchanged.

Native verification also exercised playlist creation/reordering, sequential metadata review, live MusicBrainz lookup and candidate selection, live Cover Art Archive retrieval/display, and two-file tag writing with backups on disposable generated fixtures. Automated provider tests cover cache reuse, candidate duration/version evidence, and offline failure. This does not establish matching accuracy on real music.

## Remaining limits

Large-library performance, unavailable network volumes, abrupt power loss, physical Windows behavior, and real rekordbox import/alignment remain unverified. Do not run different app instances against the same development library. Schema v10 creates a SQLite backup before upgrading an older nonempty schema.

## Owner test

Use a small copied music folder first. Import it twice and check the duplicate report; include an unsupported/damaged audio file to inspect failure reporting. Move one imported file, Refresh, filter Missing files, and Relink it. Confirm its cues remain and it plays from the new location. Repeat with the wrong song and confirm rejection. Select tracks, create/reorder a playlist, then open Review. Stage a title/artwork change with Save to library and confirm the copied source is unchanged. Use Write tags to review/apply the saved changes and check the backup. Test a MusicBrainz lookup and verify the release/version and duration before accepting a candidate.


## Sources and dependency boundary

Provider behavior follows the official [MusicBrainz API](https://musicbrainz.org/doc/MusicBrainz_API), [search](https://musicbrainz.org/doc/MusicBrainz_API/Search), [rate-limiting guidance](https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting), and [Cover Art Archive API](https://musicbrainz.org/doc/Cover_Art_Archive/API). Tag behavior uses the [Mutagen documentation](https://mutagen.readthedocs.io/en/latest/) and pinned [Mutagen 1.48.1 package](https://pypi.org/project/mutagen/1.48.1/). Mutagen declares GPL-2.0-or-later; it is a separately installed optional runtime here, not a bundled release. Provider data/artwork rights, dependency distribution obligations and the optional section-weight license conflict still require review before distribution. No legal clearance is claimed.

See [metadata matching verification](METADATA_MATCHING_VERIFICATION.md) for the four owner filenames, UI checks and ShazamKit pilot blocker.
