# M2 development baseline

## Setup and use

On Apple Silicon macOS, install the optional, pinned Python 3.12 analysis environment once:

```sh
./scripts/setup-analysis-macos.sh
./scripts/dev-macos.sh
```

To enable the separate experimental section-model provider, run `./scripts/setup-structure-macos.sh` before starting the app. The launcher discovers `~/Library/Caches/CrateLoom/structure-env`; `CRATELOOM_STRUCTURE_PYTHON` overrides it. First use may download approximately 95 MB of model weights. The model is optional.

`uv`, Node, Rust and FFmpeg/ffprobe must be installed. The development launcher discovers the analysis environment at `~/Library/Caches/CrateLoom/analysis-env`; set `CRATELOOM_ANALYSIS_PYTHON` to override it. The local library, manual cues and the original simple waveform remain available without that environment. This is not a packaged release. The lock file is for macOS arm64; Windows is unverified.

Import a track to queue **BPM & RGB** analysis automatically. Open **Edit Cues** to monitor progress. Progress is stage-based and indeterminate; **Cancel analysis** stops the worker. While it runs, playback and cue editing remain available. Only one heavy analysis can run at a time. A successful result is stored in a scoped local cache; choosing **Analyze again** forces a new run. Reanalysis does not replace saved manual cues or sections.

The top waveform shows the whole track; click it to seek. The lower waveform follows the playhead at the selected zoom. RGB encodes relative low-, mid-, and high-frequency energy, not song structure or isolated instruments. Markers have editable colors. Detected beat lines are estimates. To show bar numbers and use bar snapping, first review the grid and explicitly set a 4/4 bar-one beat; this confirms phase and meter. Grid shift moves all estimated beats by a user-set amount (up to ±2000 ms); it does not fix tempo drift or half/double tempo errors. Snap only changes a current operation and applies within 100 ms.

Sections can be entered and edited manually. **Suggest sections** uses the optional local model; results are added automatically, while existing manual and reviewed sections are preserved; overlapping generated ranges are trimmed. Edit or remove them in **Edit sections**. Labels and boundaries require human review. The one-button hot-cue action fills available slots from the first detected beat and labeled section starts, snapping nearby boundaries to beats. It skips occupied slots and positions within 250 ms of an existing cue. If no sections are available, it may only add the first beat. These cues are estimates to audition. **Check** starts a chosen number of detected beats before a cue, with a five-second fallback. Loop preview uses seconds and webview media seeking; it is not sample-accurate or guaranteed seamless.

## Tested

On an Apple M5 Mac with generated 120 BPM WAV audio, the native development window displayed an estimated 120.2 BPM, loaded the scoped RGB cache, showed a manual section and confirmed bar numbers, applied a beat-based cue suggestion, and repeated/exited a seconds-based loop. The cue, section, anchor and analysis cache were visible again after restarting. The automated job tests cover cancellation, successful beat and structure analysis, cache reuse, forced-run cancellation, edits during analysis, preservation of manual cues and sections, section/grid persistence, global grid shift, rejection of audio changed mid-analysis, graceful shutdown and cue-color XML serialization. Pure rule tests cover 64-beat offsets, confirmed 4/4, proximity and missing evidence. These tests do not establish musical accuracy or audible alignment.

## Structure-model evaluation and remaining limits

An isolated installation attempt on 2026-11-03 installed `allin1==1.1.0`, `torch==2.15.0`, `natten==0.21.7` and madmom from commit `27f032e8947204902c675e5e341a3faf5dc86dae` in Python 3.11. Importing All-In-One failed because `natten.functional` no longer exports `natten1dav`. An additional attempt to build NATTEN 0.14.6 failed during native extension compilation even with the installed PyTorch available outside build isolation. The project's [NATTEN incompatibility issue](https://github.com/mir-aidj/all-in-one/issues/30) describes an API mismatch. An isolated inference-only fork, [all-in-one-infer](https://github.com/openmirlab/all-in-one-infer/tree/566f96c3f007c783ab5eb6db0123bf3335af8f31), avoids that extension. Version 3.1.0 at commit `566f96c3f007c783ab5eb6db0123bf3335af8f31` with PyTorch 2.15.0 completed CPU inference on a generated 15-second click track in 24.25 seconds on first run; it labeled the synthetic signal Intro, which is not evidence of semantic accuracy. The fork is integrated as an optional, cancellable section-suggestion provider. Its engine-level flow has been exercised on the synthetic track, including an edit made while analysis ran; the automatic section application and section editor have not yet been manually verified in the native window. It is currently limited to tracks of at most five minutes.

**Weight-license discrepancy:** the fork’s [checkpoint manifest](https://github.com/openmirlab/all-in-one-infer/blob/566f96c3f007c783ab5eb6db0123bf3335af8f31/src/allin1_infer/config/checkpoints.toml) marks Harmonix weights `CC-BY-NC-SA-4.0`, while the original [model card](https://huggingface.co/taejunkim/allinone) lists MIT. These sources conflict; rights for redistribution and commercial use have not been resolved. No weights are bundled, and the setup script prints the discrepancy. The model downloads eight Harmonix checkpoints (about 11.2 MB total) and one Demucs checkpoint (about 84.1 MB) into the user cache; the provider records SHA-256 values in its manifests. Dependency installation is additional. A representative-music evaluation, offline reuse, individual weight rights and redistribution are unverified. The project's [MP3 guidance](https://github.com/mir-aidj/all-in-one#concerning-mp3-files) also warns of decoder timing differences. No global offset is guessed.

The worker currently decodes the selected file in memory and limits beat analysis to 15 minutes; it is not a streaming, large-library analysis pipeline. Beat tracking has no automatic downbeat/meter detection and needs musical review; no automatic tempo correction or individual-beat editing is implemented; the global shift is manual. The local cache key incorporates audio-stream signature, codec, sample rate, duration, analyzer version and FFmpeg version. A crashed engine marks an unfinished job interrupted when next inspected; an abrupt OS kill can leave a child process until it exits. The optional structure provider uses CPU source separation; its memory and latency on representative music have not been measured. Windows, clean-machine packaging, actual rekordbox import, cue-color compatibility and audible MP3/rekordbox alignment remain unverified.

## Development ports and test libraries

Before reopening an existing test library with this version, quit any older CrateLoom window using that same library. Schema upgrades create a SQLite backup alongside the library. The development server uses port 1420 by default. To run another copy while a test window is already open, set `CRATELOOM_DEV_PORT=1421` before `./scripts/dev-macos.sh`; each running instance must use a different port and preferably a separate library. The earlier agent-run M2 test window uses `~/Library/Caches/CrateLoom/m2-ui-test-data`; the normal launcher uses `~/.crateloom` unless `CRATELOOM_DATA` is set. Those libraries are separate and are not automatically merged.

## Waveform interaction update

The detail view is centered on the playhead. Drag it horizontally to scrub; use the mouse wheel or trackpad pinch to zoom, or the +/- buttons. Clicking the overview seeks. Playback updates are scheduled with requestAnimationFrame, but a sustained 60 FPS has not been measured. The high-resolution analysis stores about 1,378 normalized RMS windows per second; select **Analyze again** on older tracks to generate it. When upgrading the analysis cache for the same verified audio, the app retains the fixed grid and shift, and retains a detected bar-one anchor if it still matches a beat; review alignment after analysis. Manual cues and sections remain saved.

Scrubbing attempts to decode the track into a Web Audio buffer on first interaction and plays short forward/reverse grains while dragging. The first drag can be silent while decoding, long files use substantial memory, and this is an approximation rather than a low-latency DJ scratch engine. Physical trackpad pinch, scratch output, frame rate under load, and real-music visual quality have not been validated. The native test window confirmed overview seeking, centered detail and zoom buttons on a synthetic WAV.

## Grid, sections, and transport update

Imported files queue BPM/RGB analysis within the running app; jobs are processed one at a time. The queue itself does not survive an application restart. The optional section model runs only when requested and fills available ranges automatically, preserving existing manually edited sections. Automatic labels remain estimates, not verified song structure. Editing a generated section turns it into a protected manual section.

The editable BPM switches to a constant-tempo grid anchored at the existing bar-one marker when available. The grid panel moves the nearest beat to the playhead, sets bar one at the playhead, nudges the grid by 1 ms, halves/doubles BPM and resets shift. Cue proposals and snapping use this same grid. Tap tempo, 0.1 BPM adjustments, and a session-only grid undo/redo are available. This does not implement flexible beat-warp markers or grid lock; tracks with tempo changes need more capable grid editing. Review grid alignment after reanalysis.

CUE is a temporary, unsaved transport cue: set while paused at a new position; press while playing to return and pause; hold at the cue to preview, release to return. These actions use webview playback and have not been validated for sample accuracy or controller-like latency.

Research: [rekordbox 7.2.14 manual, grid and cue controls](https://cdn.rekordbox.com/files/20260409151936/rekordbox7.214_manual_EN.pdf), [Serato DJ Pro beatgrids](https://support.serato.com/hc/en-us/articles/360001274936-Beatgrids), and [Serato temporary cue](https://support.serato.com/hc/en-us/articles/233435527-Temporary-Cue). Rekordbox distinguishes grid shifts, BPM spacing changes and bar numbering. Serato also supports beat-warp markers and stretching, which are not yet implemented here.


## Waveform and hot-cue update

The display uses a per-track normalized 16-sample RMS envelope at 22.05 kHz (approximately 1,378 windows/second), stored separately from its three low/mid/high spectral-color bands. Normalization affects the drawing only, not playback or audio files. The colors are broad frequency bands, not a full-resolution spectrogram or isolated instruments. Existing caches remain readable; use Analyze again for the new representation. A generated 4:40 sine fixture completed analysis in 1.95 seconds with a 2.86 MB cache and about 804 MB peak resident memory on this Mac; this is not a representative music or UI frame-rate benchmark. Performance at the 15-minute limit and visual similarity to DJ software have not been benchmarked. The one-button cue action fills free slots from available beats/sections, keeps existing cues, and is limited to eight slots; it does not analyze sections itself.

## Native bridge and signed waveform correction

The desktop allowlist omitted `cues.auto`, `grid.fixed` and `grid.restore`; Python-only tests did not exercise that boundary. They are now exposed and covered by a Rust regression test. In the rebuilt native window, Auto cues added four cues to a generated track with sections, and grid placement plus undo completed successfully.

Analyzer v5 adds normalized signed minimum/maximum sample pairs every eight decoded samples (22.05 kHz), encoded as packed int16 data. The renderer aggregates the signed ranges to physical screen columns, rather than drawing a symmetric envelope. The display uses headroom and a saturated green/blue/violet frequency palette. Frequency color still represents three broad bands; the fine lines are actual signal oscillations, not individual frequency components. Older caches automatically queue an upgrade when selected. Original audio is unchanged.

The native window was visually inspected with a generated signal and the local 4:46 Work (Remix) MP3, including 128x zoom. This establishes cache loading, signal detail and appearance for those examples, not exact Rekordbox equivalence, semantic cue accuracy, or measured sustained frame rate. The previous per-track RMS display remains a fallback for legacy caches while upgrading. Analysis information is now collapsed rather than occupying the main editor.

## Cue indicators and gesture audio

Temporary CUE has a yellow arrow on the overview and, when visible, the detail waveform. Beat/bar lines have small red triangles at both ends. Cue undo/redo use labeled icon buttons. The frequency palette maps low/mid/high energy to red/green/blue.

Pixel-mode wheel events now scrub (horizontal, or vertical when horizontal delta is absent). Pinch events, Ctrl/Alt+wheel and line-mode mouse wheels zoom. Physical two-finger and pinch behavior still needs hardware verification; synthetic native accessibility scrolling did not establish it.

The previous short-grain scratch preview is replaced by an AudioWorklet that interpolates forward or backward through decoded audio at a gesture-dependent rate. It smooths rate/gain changes, fades when idle, and stops on release. Two Node tests exercise forward/reverse motion, release, idle fade and zero-speed silence. The production build emits the worklet as a separate same-origin asset to satisfy CSP. The native synthetic-track window loaded the preview without an error notice. Listening quality, device latency and equality to Rekordbox remain unverified. First use may be silent until decoding finishes; decoded tracks occupy memory.


## Mouse and trackpad input

Auto mode treats line/page wheels and coarse vertical pixel detents as mouse zoom; small/fractional or horizontal pixel events identify a trackpad gesture. Once identified, trackpad acceleration remains scrubbing until the gesture ends. Ctrl/Alt+wheel and pinch zoom. Since WheelEvent has no standard device identifier, smooth mouse wheels and unusual first-event trackpad deltas can be ambiguous: Analysis info provides persisted Mouse wheel and Trackpad overrides. Five Node tests cover scrub DSP plus mouse detents, trackpad acceleration, horizontal scroll, pinch modifiers and overrides. Run `npm test`.
