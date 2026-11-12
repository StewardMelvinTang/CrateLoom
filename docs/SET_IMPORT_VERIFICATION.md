# DJ-set import and reviewed beatgrid export

Open **Import DJ Set** and paste a YouTube mix, a YouTube or YouTube Music playlist, a public Spotify playlist, or a SoundCloud set. You can also expand **Paste tracklist**. A timestamped entry looks like `08:16 Artist - Track (Remix)`. A direct audio URL on the same line becomes a download candidate. Choose **Scan**, then **Create & prepare playlist**. The saved report keeps the original timestamps, recording sources, and unresolved entries. The playlist contains playable library tracks in set order.

Tracklist and audio scans now show centered progress and **Cancel scan**. Cancelling preserves completed entries and samples; an explicitly cancelled audio scan stays paused after restarting, with **Resume audio scan** available in the report. YouTube recovery tries published-page metadata, alternate audio formats, segmented transfers and verified cached mix audio. See [scan progress verification](SET_SCAN_PROGRESS_VERIFICATION.md) for behavior, live checks and limits.

CrateLoom tries an exact title, artist and version match in the existing library, then checks MusicBrainz recording links for free downloads. Spotify and YouTube Music links open as recording references or searches. Creator-hosted audio files go through the existing verification queue. If a source requires a browser step, use **Add source** to enter a direct URL or choose an acquired local file. New files receive the normal BPM and RGB analysis and a first-beat cue when beat detection succeeds. Existing tracks keep their cues.

The report preserves source order, repeated entries, and unavailable entries. The local playlist keeps one entry per imported track and is ordered by the first occurrence. A Spotify link supplies recording metadata. CrateLoom looks for matching files through its existing acquisition sources. A public Spotify embed can expose only part of a playlist. Spotify authorization enables paging for playlists accessible to the token. YouTube and SoundCloud expose the entries available to the current client. Playlist import accepts up to 1,000 entries.

The **Downloads** toolbar button opens current and completed transfers. **Clear** hides completed history while preserving the download file, library track, and report status.

## Optional provider connections

- For personal use, install the scanner with `./scripts/setup-shazam-macos.sh`. CrateLoom scans a single YouTube video automatically when there is no usable tracklist. **Scan audio with Shazam** forces scanning even when a tracklist exists. **Choose audio** scans a local mix. The app decodes overlapping audio windows locally and ShazamIO sends their fingerprints to Shazam. Timestamps are approximate, single-window hits need review, and longer gaps are marked unidentified. The scanner supports mixes up to three hours. For audio-only reports, CrateLoom saves each batch, waits out rate limits, and resumes in the background while the app is running. The queue survives restarts. After repeated attempts without progress, the report pauses with its matches intact.
- A SoundCloud token entered under **Connections**, or `CRATELOOM_SOUNDCLOUD_TOKEN`, enables the official SoundCloud API route for a recording with an exact catalog link, a unique artist/title/version search result, or a manually supplied track link. It needs access to the creator-enabled original download. A SoundCloud search link alone does not establish a match or a download. API credentials and live access are required to verify the adapter against a real track.

## Rekordbox grid workflow

In the Cue Editor, enter the confirmed BPM, place the playhead on the musical downbeat, and press **1.1**. Check a later bar before exporting. The XML writes `AverageBpm` and one `TEMPO` element with the bar-1 position including any grid nudge, BPM, meter `4/4`, and beat number `1`. The export notification counts the included reviewed grids. Hot cues remain separate timestamped markers. Detected or variable grids are left to rekordbox.

## Validation

- Live native YouTube scan of [Ben Böhmer above Cappadocia for Cercle](https://www.youtube.com/watch?v=RvRhUHTV_8k), 1:24:28, extracted 14 published entries. This measures tracklist extraction. A live source pass returned two MusicBrainz references and no downloadable files for this set. Several MusicBrainz lookups reported intermittent errors.
- Live direct download of Kevin MacLeod’s [Monkeys Spinning Monkeys](https://incompetech.com/music/royalty-free/?isrc=USUAN1400011) in an isolated library completed verification, import, playlist placement, BPM/RGB analysis and first-beat cue generation. An unidentified entry stayed in the report.
- A copied Rocketeer audio file exported at 96 BPM with a test downbeat and all eight original cue positions. rekordbox accepted the test XML source in Preferences. The browser’s custom controls could not be activated by the automation, so deck-level alignment and drift remain unverified. The original XML source was restored.
- Shazam fingerprint windowing, timestamp reports and partial scans were verified with controlled responses. The live Shazam endpoint returned HTTP 429 for the upstream sample on this Mac, so live catalog recognition and coverage for long sets remain unverified. The scanner stops on a provider error and retains matches already found. Live SoundCloud downloads remain unverified.

References: [rekordbox XML specification](https://cdn.rekordbox.com/files/20200410160904/xml_format_list.pdf), [ShazamIO](https://github.com/shazamio/ShazamIO), [MusicBrainz free download relation](https://musicbrainz.org/relationship/45d0cbc5-d65b-4e77-bdfd-8a75207cb5c5), [SoundCloud creator downloads](https://help.soundcloud.com/hc/en-us/articles/115003448787-Downloading-tracks).
