# M5 export, service references, and local macOS bundle

Verified on 2026-11-07 (Asia/Taipei), on Apple Silicon macOS.

## Export

Open a saved playlist and choose **Export playlist**, or select rows in All Tracks and choose **Export selected**. The playlist uses its saved order; selected tracks use library order. The editor still supports a single-track export.

- Export creates XML, a cue/settings JSON backup and a manifest with file hashes, track keys and paths. Files already at those destinations are kept. Choose a new XML name to export again.
- **Copy audio** creates a sibling audio folder, checks each copy against the source hash, and points the XML at those copies. Leave it off to reference existing files. The XML uses absolute paths; keep that folder in place for import. Relative paths in the manifest are for recovery, not instructions rekordbox follows.
- Missing or replaced audio stops the export with the track name. Staged files are published with exclusive destinations; XML is published last. A normal failure rolls back files created by the job. A sudden exit or unplug can leave staged files; export again to a fresh destination. There is no cross-file transaction or automatic resume.
- Hot cues A–H preserve slots, integer microseconds, labels, and RGB fields. No beat grid, memory cues or active loop state is written. Analyze the grid in rekordbox and check cue alignment.

A native, freshly extracted CrateLoom bundle exported a two-track playlist of generated WAV and VBR MP3 fixtures. The emitted XML had two tracks in saved order and 16 A–H markers. Both copied-audio SHA-256 values and the XML hash matched the manifest; the JSON backup retained the two track records. The automated suite tests order, escaped names, all relevant cue fields, copy hashes, missing/changed assets, failure rollback, destination collision, filesystems without hard links and POSIX, Windows-drive and UNC file URLs.

rekordbox **7.2.19.0342** accepted the generated two-track XML in **Preferences → Advanced → Database → Imported Library**. A first warning was traced to the file picker selecting an unrelated WAV; after verifying the selected filename, the original generated XML was accepted. The prior XML source and two-deck layout were restored. The XML browser's custom controls were inaccessible to native automation, so collection import, existing-track update behavior, cue-pad playback and audible alignment were not verified. No test tracks were added to the owner’s rekordbox collection.

For manual verification: enable the **rekordbox xml** browser source, open the fixture playlist, and import it into a test collection. Verify both tracks appear in order, then load each deck and check A–H against the generated pulses. For an existing-track update, back up the collection and test the update prompt on a disposable track; do not assume adding a playlist updates cues. The [official XML schema](https://cdn.rekordbox.com/files/20200410160904/xml_format_list.pdf) and [developer import instructions](https://rekordbox.com/en/support/developer/) describe the interchange format.

## Service links

This section records the M5 baseline. [Direct YouTube and YouTube Music import](YOUTUBE_DOWNLOAD_VERIFICATION.md) was added afterward.

**Find Music** resolves public YouTube, YouTube Music, Spotify and SoundCloud links as references. It shows the title and uploader when returned, offers **Open on [service]** and **Add local file**, and requires a separate direct audio URL for **Download & import**. Spotify's oEmbed response can omit the artist; uploader/channel names are not assumed to be artists. URLs are normalized to remove share tracking or private query parameters. Embedded HTML is ignored.

| Input | Verified live title | Audio route |
| --- | --- | --- |
| YouTube short watch link | Kevin MacLeod ~ Monkeys Spinning Monkeys | Independently supplied audio file |
| YouTube Music watch link | Kevin MacLeod ~ Monkeys Spinning Monkeys | Independently supplied audio file |
| Spotify track link | Blinding Lights | Independently supplied audio file |
| SoundCloud public permalink | Flickermood | Creator-enabled **Download file** on SoundCloud when offered, then **Add local file**; or a direct audio URL |

All four went through the real engine's resolve/job methods. Spotify and YouTube Music were also verified through the bundled native UI, including the disabled download action without a direct file. Only the public link forms in this table were live-tested; provider outages, private/removed or region-limited items may return an error. This is not bulk playlist acquisition, account integration or stream extraction. SoundCloud's creator-download step can require sign-in on SoundCloud; it is not performed by the app. No account was signed in during testing.

Primary sources: [Spotify oEmbed](https://developer.spotify.com/documentation/embeds/reference/oembed) and [Developer Terms](https://developer.spotify.com/terms), [SoundCloud oEmbed](https://developers.soundcloud.com/docs/oembed) and [creator-enabled downloads](https://help.soundcloud.com/hc/en-us/articles/115003448787-Downloading-tracks), [YouTube API Developer Policies](https://developers.google.com/youtube/terms/developer-policies). Direct-file verification is recorded in [M4](M4_VERIFICATION.md).

## Bundle and release status

Build locally with `./scripts/build-app-macos.sh`. It copies source to native storage, builds/tests the frozen engine, builds an app bundle with Tauri, applies an ad-hoc signature and verifies it. The sidecar uses the bundled CA store for HTTPS. The app discovers separately installed analysis, section and tag runtimes under `~/Library/Caches/CrateLoom`; their interpreters can be overridden by the existing environment variables. FFmpeg and ffprobe remain separate requirements; the native launcher includes common Homebrew paths even when launched from Finder.

The delivered Apple Silicon ZIP was freshly extracted, signature-checked, and opened with an isolated library and system-only initial PATH. Native checks covered playlist export, provider reference lookup and successful BPM/RGB analysis using the separately installed runtime. The bundled engine smoke test imports and analyzes generated audio without PYTHONPATH or a separately running Python host. The full suite passed 52 Python tests, 13 Node tests and two Rust tests; frontend type checking and the production build passed.

The app defaults to `~/.crateloom`. Launch the bundle with `CRATELOOM_DATA` if you want it to use a different development library. Keep only one CrateLoom instance open against a given library.

- [x] Local ARM64 app bundle, sealed ZIP and fresh-extract check.
- [x] Ordered playlist export, selected-track export, optional byte-identical audio copies and integrity manifest.
- [x] Native rekordbox XML-source acceptance; generated artifact and path contract checks.
- [ ] Collection import and cue alignment for new and existing tracks in rekordbox.
- [ ] Windows executable/installer and physical Windows verification; Intel macOS build also untested.
- [ ] Clean-machine dependency setup, Developer ID signing, notarization and full redistribution review.

See [dependency inventory](DEPENDENCIES.md). This is a local test build, not a signed/notarized public release.
