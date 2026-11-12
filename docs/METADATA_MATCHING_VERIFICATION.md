# Metadata matching verification — 2026-11-04

## Behavior

Filename hints replace underscores and strip known download marks (`KLICKAUD`), file extensions and presentation-only labels such as `(Lyrics)` and `(Official Audio)`. Artist/title separation is tentative; meaningful remix/edit names are retained. The title-first Annyeonghaseyo example keeps ambiguous credits out of the artist field. Parsing never changes library fields or source tags by itself.

MusicBrainz lookup uses up to three progressively relaxed queries, a 50-recording limit per response, the existing background jobs and one-request-per-1.1-second start rate. The precise query includes version information, duration and a same-title release preference. Ranking uses title, artist, named version, duration and release evidence. Official singles are preferred over bootleg compilations. Different artist/title/remix/live versions and duration discrepancies above max(12 seconds, 8%) are excluded. Unknown duration or unknown artist cannot become a strong text match. The thresholds are conservative product rules, not calibrated match probabilities or musical accuracy measurements.

Search caches store provider responses under a versioned namespace; each lookup re-ranks them using the current duration. Old candidate-only caches are not reused. A failed later query can return partial results with a retry message. Release dates belong to the selected release; an unknown release date is not replaced by the recording's earliest release date.

The editor keeps the existing metadata fields, explicit selection, Save to library and separate file writes. It shows parsed search hints, source/release/version/duration evidence, strong text matches versus versions needing review, and disabled excluded results behind a toggle. Manual title/artist edits clear stale match provenance. Switching between selected releases clears preceding candidate artwork. Covers request `front-1200` under a new cache key; source images can be smaller than 1200px. No artwork is synthesized or guessed from the filename.

## Automated and live checks

- Python: 27 tests run, 25 passed; two optional analysis/model integration tests skipped because their environment variables were unset. The configured tag writer was exercised. Nine new matching tests cover the owner's filenames, a 308.52-second discrepancy, a wrong remixer, named-remix preservation, original/live conflicts, missing durations, release/date ordering, escaped bounded queries, cache reuse and duration re-ranking, no automatic staging, and high-resolution cover requests. Existing staging tests also verify explicit source clearing and unchanged original bytes.
- Frontend: TypeScript and production Vite build passed; five existing Node transport/wheel tests and the Rust bridge allowlist test passed.
- Native development app compiled and launched from native storage. The final metadata interaction checks used the actual React editor and Python engine through a temporary localhost bridge and isolated library. This does not establish native WebKit behavior or Windows behavior for the revised dialog.
- Browser interaction exercised filename search, candidate selection, live release-cover retrieval, clearing artwork when selecting another release, Save & next in the isolated library and the niche-remix no-match state, and all 62 displayed excluded candidates remaining disabled. The light 1280 × 820 and dark 900 × 650 layouts were visually inspected; the minimum-size dialog had no horizontal overflow and retained vertical scrolling. The owner library and source tags were not changed. Artwork returned by the selected Faithless release was a 600 × 464 image despite the 1200px request; the app does not upscale it.

The four user files were probed read-only for duration and searched by their original filenames:

| Filename | Result |
| --- | --- |
| `Faithless - Insomnia (Lyrics).mp3` | Parsed Faithless / Insomnia. A leading candidate was the official Insomnia single, Monster mix radio edit, 214s versus the file's ~213s. It stays marked Check version. This is a plausible text/duration candidate, not a verified audio identification. |
| `Work REMIX (Official Audio).mp3` | Parsed Work / REMIX without inventing an artist. A leading candidate was A$AP Ferg feat. A$AP Rocky, French Montana, Trinidad James & ScHoolboy Q, Work (remix), ~283s versus ~286.8s. Human review remains necessary. |
| `Call_Me_Maybe_Refilled_Remix_KLICKAUD.mp3` | Preserved Refilled Remix. No reliable catalog match; original recordings and other versions were excluded. |
| `Annyeonghaseyo_Jersey_-_Yuki_Chiba_Lil_Moshpit_Wichaya_Jersey_Club_Remix_KLICKAUD.mp3` | Preserved Wichaya Jersey Club Remix; no reliable catalog result. Ambiguous artist credits were not written. |

These four examples are not a representative accuracy benchmark. No audio identity or exact remix identity is claimed.

## Experimental ShazamKit probe

`scripts/shazam-pilot.swift` is a read-only experimental command-line probe, separate from the desktop. It reads a 1–15-second mono PCM excerpt at a supported sample rate and emits JSON. `--self-test` adds the same signature to a local custom catalog; the default requests a Shazam catalog match. It has a 20-second timeout. Recognition output always leaves `version_verified` false.

A 12-second excerpt starting at 45s in the owner's Refilled remix passed the local custom-catalog self-match. Two catalog attempts failed with `com.apple.ShazamKit` code 202; the second exposed underlying `AMSErrorDomain` code 306. The SDK labels 202 as match-attempt failure. The failure does not establish no-match, catalog coverage or a specific authentication cause. The development executable has no ShazamKit entitlement configured in this repository. Apple documents enabling ShazamKit for an App ID; a configured/signed application and a successful catalog control test are prerequisites for a useful recognition benchmark. No developer-account changes were attempted.

Compile on internal storage, then run on an explicitly chosen excerpt:

```sh
xcrun swiftc scripts/shazam-pilot.swift -o /tmp/crateloom-shazam-pilot
ffmpeg -v error -ss 45 -i '/path/to/copied-track.mp3' -t 12 -ac 1 -ar 16000 -c:a pcm_s16le /tmp/crateloom-excerpt.wav
/tmp/crateloom-shazam-pilot /tmp/crateloom-excerpt.wav --self-test
/tmp/crateloom-shazam-pilot /tmp/crateloom-excerpt.wav
```

Next: resolve catalog access and run a 20–30-track pilot with official releases, remixes and ambiguous edits; record exact-version correctness and wrong-match rate separately from base-song recognition. Creator-source URL enrichment remains future provider work. It is not implemented by this increment.

## Sources

[MusicBrainz search fields](https://musicbrainz.org/doc/MusicBrainz_API/Search), [Cover Art Archive image API](https://musicbrainz.org/doc/Cover_Art_Archive/API), [ShazamKit](https://developer.apple.com/shazamkit/) and [Apple App ID setup](https://developer.apple.com/help/account/services/shazamkit). API behavior was also checked against the installed macOS SDK headers.
