# M0 feasibility and test notes

Tested 2026-11-03 on an Apple M5 Mac with 16 GB RAM and macOS 27.2, using FFmpeg 9.0.1. The synthetic fixtures are generated locally and contain no music.

## Results

- The Rust host now performs and validates a protocol-v1 handshake with the engine at startup. The native Tauri development window started against both the source Python engine and the frozen engine override.
- In the native webview, a generated 20-second WAV was selected through the file dialog, imported, played (the playhead advanced), and sought by clicking the slider to about 10.01 seconds. Waveform analysis completed; a cue was saved at 10.01 seconds, survived navigation and an application restart, and appeared in a saved XML and JSON backup. This checks UI state and media time progression; audible output, seek precision and decoder alignment were not independently measured.
- The resulting XML contained `POSITION_MARK` Num 0 and Start 10.010000. `pyrekordbox` 0.4.4 read one track, playlist entry 1, and cue A at 10.01 seconds. The output was checked against the published rekordbox element list. This is a parser and structural check, not formal XSD validation or an import into rekordbox. The older official reference explicitly documents A–C; A–H behavior must be checked in the target rekordbox version.
- The optional, separate librosa 0.11.0 experiment used a generated 30-second 120 BPM click track decoded with FFmpeg. It estimated 120.185 BPM and returned 59 beats, with a 14.06 ms median distance to the nearest generated click. One cold run took 17.74 seconds including first-use compilation; peak process RSS was 465,600,512 bytes. These numbers are for this synthetic run and machine, not accuracy or performance claims for music. This provider is **not yet integrated into the app or packaged sidecar**; no downbeats or sections were inferred.
- PyInstaller 6.22.0 produced an arm64 single-file Python sidecar of 9,702,704 bytes. With `PYTHONPATH` unset, it completed the handshake, imported a generated WAV and returned waveform peaks. Tauri was also launched with `CRATELOOM_ENGINE_EXEC` pointing to that binary; import and waveform analysis worked in the native window. This is a sidecar feasibility build, not a bundled or signed app. FFmpeg and ffprobe still came from the local PATH; no clean-machine test was performed.

## Reproduce the beat probe

```sh
uv venv --python 3.12 "$HOME/Library/Caches/CrateLoom/analysis-env"
uv pip install --python "$HOME/Library/Caches/CrateLoom/analysis-env/bin/python" 'librosa==0.11.0'
"$HOME/Library/Caches/CrateLoom/analysis-env/bin/python" scripts/probe-beats.py
```

First use may compile code and take longer. Transitive dependencies are resolved by uv rather than locked; this is an experiment, not a production analysis environment.

## Reproduce the sidecar probe

Requires `uv`, Python 3, FFmpeg and ffprobe. The sidecar build runs on native storage and pins PyInstaller 6.22.0. It is for macOS on the build machine's architecture.

```sh
scripts/build-sidecar-macos.sh
python3 scripts/test-sidecar.py "$HOME/Library/Caches/CrateLoom/sidecar-build/dist/crateloom-engine"
```

To test it with the development window, follow the README's copy and setup instructions, then set `CRATELOOM_ENGINE_EXEC` to that binary before `tauri dev`. The frontend is still served by Vite, and FFmpeg is still external. This does not produce a distributable app.

## Your later hands-on test

1. Follow the development setup in README. For an isolated test library, set `CRATELOOM_DATA` to a new folder before starting the app. Avoid replacing an audio file in place: analysis invalidation is not implemented.
2. Add a local WAV or MP3. Play, pause and seek. In Edit Cues, choose **Analyze waveform**, seek, and add a cue. Quit and reopen the app, select the track and check that the cue remains.
3. Export XML using a new filename. Keep the source audio in place. The matching `.crateloom.json` file is a backup, not audio.
4. When ready, import the XML through rekordbox's XML browser and check cue slot and timing. Existing collection tracks may require a separate update; do not overwrite your collection without a backup. Neither import nor cue alignment has been verified by this project yet.

Record the file format, macOS and rekordbox versions, any error, and whether playback and cues line up. M0 does not yet provide automatic cue proposals, beat overlays, semantic structure labels or a packaged installer.
