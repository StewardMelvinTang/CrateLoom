# Direct YouTube link import

In **Find Music**, paste a single YouTube or YouTube Music link, click **Find source**, review the editable track details, and choose **Download & import**. The selected audio-only M4A is imported in its supplied format. There is no intermediate URL to copy or MP3 conversion.

The app uses the installed [yt-dlp](https://github.com/yt-dlp/yt-dlp) to resolve an available public audio format. Install yt-dlp and Deno separately if needed (`brew install yt-dlp deno` on macOS), then restart CrateLoom. The app locates executables on PATH, including common Homebrew locations on macOS. `CRATELOOM_YT_DLP` can point to a trusted yt-dlp executable. The bundle does not include these tools.

The queue retains the public page URL and reviewed format, resolves a fresh CDN address when starting or retrying, and streams it through the existing bounded HTTPS transfer and audio verification. Signed media URLs are not stored in the job or returned to the UI. The extractor ignores user configs and plugin paths and does not read browser cookies or request remote components. Unavailable formats and access restrictions can fall back to a reference with an explanation. Spotify and SoundCloud service links retain the M5 reference behavior.

Verification uses the exact Rocketeer link reported by the user, in a disposable library. No test audio is shipped in the bundle.

On 2026-11-07, the exact link resolved to format 140 and imported 3,440,434 bytes of AAC/M4A (212.532 s) unchanged. The saved-file SHA-256 matched the transfer hash. The initial short test window elapsed while the transfer was still progressing; retry successfully resolved a fresh media URL and completed. The freshly extracted macOS bundle also showed an enabled **Download & import** button for that link without a second URL. 53 Python tests, 13 Node tests, the frontend build, and bundle signature verification passed.
