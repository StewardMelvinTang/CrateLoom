# CrateLoom — macOS UI Implementation Prompt

## Your task

Design and implement the CrateLoom desktop interface in the existing repository. CrateLoom is an all-in-one DJ preparation toolkit: import/discover music, organize tracks, review metadata/artwork, analyze audio, generate and edit hot cues, preview them, and export to rekordbox.

The agreed stack is Tauri, React, TypeScript, a thin Rust bridge, and a Python sidecar. Preserve working backend services and existing project conventions. This brief specifies the UI and interaction layer; it does not authorize replacing the architecture or removing product features.

Inspect the repository first. Reuse suitable existing components, tokens, dependencies, and typed service contracts. Implement the design in working code, not just a plan or screenshot. Make routine decisions autonomously and document meaningful assumptions. Keep the application runnable throughout.

If Impeccable or another frontend-design skill is installed, use it for design, critique, and refinement. The specific requirements in this brief take priority over generic advice to use unusual fonts, oversized typography, bold visual effects, or elaborate animations. Do not install skills without the user's instruction.

## Design direction

Create a quiet, precise music-preparation workspace that belongs on macOS. Use familiar desktop navigation, a compact library table, a persistent player, and a dedicated cue editor. The interface should be approachable to a beginner while supporting efficient repeated work.

Reference categories: the organization of a desktop music library, the clarity of a file browser, and the precise timeline controls of an audio editor. Create an original design rather than copying a particular product's assets or exact layout.

Priorities, in order:

1. Clear track preparation and review workflow.
2. Readable waveform and cue positioning.
3. Keyboard and pointer efficiency.
4. Consistent macOS-style controls and spacing.
5. Restrained visual personality through artwork, teal accents, and cue colors.

Use real window chrome. Never draw fake macOS traffic lights inside the page. Configure the actual Tauri title bar where supported; keep native controls usable. A browser preview should omit simulated OS controls. Use native menus/dialogs where available and retain standard Windows chrome on Windows.

## Visual system

Define semantic CSS variables/tokens before styling individual screens. Provide dark, light, and system appearance settings; default to system. Build and inspect both themes, not an inverted approximation.

Starting palette for dark mode: warm graphite canvas around `#17191C`, subtle elevated surfaces around `#202328`, soft white primary text, muted secondary text, and teal emphasis. These are starting points, not verified accessible color pairs. Choose distinct accessible foreground/background tokens for each theme and validate actual contrast.

Typography: `-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`. Use system fonts without downloading or bundling proprietary font files. Use tabular numerals for BPM, timestamps, and durations. Starting sizes: 13–14 px for library content, 12–13 px secondary text, 18–22 px screen headings. Avoid tiny low-contrast labels. Keep line heights comfortable.

Use a 4 px spacing scale, predominantly 8/12/16/24 px gaps. Controls generally have 6–8 px corner radii; dialogs may use 10–12 px. Use thin dividers and subtle elevation. Reserve stronger shadows for floating menus/dialogs. Prefer one consistent licensed outline icon set. Icon-only controls need accessible names and tooltips.

Use teal for selected/primary actions, semantic colors for status, and distinct labeled colors for cues. Do not make the entire waveform or background rainbow-colored. No neon glow, large gradients, decorative statistics cards, oversized branding, pill-shaped everything, or excessive glass panels. Optional sidebar translucency must have an opaque fallback and must not affect text/waveform clarity.

Use short purposeful transitions, roughly 120–180 ms where appropriate. Respect reduced motion; do not animate waveform positions during precise editing. Readability and responsiveness matter more than effects.

## Window and layout

Target a comfortable default around 1280 × 820 logical pixels. Validate 1024 × 700, 1280 × 800, and 1440 × 900. Layout must adapt without page-level horizontal overflow. Below comfortable widths, collapse secondary panels before shrinking the waveform or essential controls.

- Unified toolbar/title area: native controls, sidebar toggle, contextual title, search, Add Music, inspector toggle.
- Resizable left sidebar: approximately 210 px initially, with a sensible bounded range.
- Flexible central content region.
- Optional right inspector: approximately 300 px, collapsible; open as a drawer if space is insufficient.
- Persistent bottom player in library mode: approximately 72–88 px high.

Persist sidebar/inspector visibility and sizes. Only approved empty toolbar areas are window drag regions; buttons, text inputs, sliders, and selections remain interactive. Verify actual drag behavior in Tauri.

## Screen 1: Library

Sidebar sections:

- Library: All Tracks, Needs Review, Ready to Export.
- Playlists: user-created playlists with a clear creation action.
- Imports & Jobs: running count only when relevant.
- Settings at the bottom.

Main toolbar includes the active collection name, search, relevant filter/sort controls, and one Add Music action. Add Music offers local files/folder, paste link, and search by title/artist. Dragging files over the library shows a clear drop target and supported-input guidance.

Use a compact table with small artwork, title, artist, version, BPM, duration, and preparation status. Keep title/artist prominent; let less important columns hide when space is limited. Support sortable headers, multi-selection, keyboard navigation, context menus, and truncated text with a way to inspect the full value. Preserve scroll and selection when returning from cue editing.

Single-click selects a track without automatically playing it. Double-click or Edit Cues opens its editor. Space toggles the loaded player's playback when focus is not in an input. Clearly distinguish the selected track from the currently playing track.

For multi-selection, show contextual actions such as Analyze, Review Metadata, Export, and Remove from Library. Removal must not imply source-file deletion. Use confirm dialogs only for actions with meaningful loss or overwrite consequences.

Status labels: Imported, Analyzing, Needs Review, Ready. Track failures/availability separately so a missing file is not misleadingly displayed as simply Ready. Derive status from actual persisted state. Analysis completion does not automatically mean human review is complete.

The bottom player contains artwork, title/artist, play/pause, seek/progress, elapsed/duration, volume, and Edit Cues. Do not add fake mixer knobs or performance decks. Keep playback state in a shared service so navigating the UI does not accidentally restart or duplicate playback.

## Screen 2: Cue editor

Open within the same application window. Use a focused central workspace with Back to Library, compact artwork/title/artist, BPM and analysis state, and an optional inspector. Keep the library sidebar collapsed if needed for space.

Vertical arrangement:

1. Compact track header.
2. Whole-track waveform overview with section bands and labeled cue markers.
3. Zoomed waveform with playhead, time ruler, beats/downbeats, and draggable cues.
4. Small editing toolbar: snap Off/Beat/Bar, zoom, pre-roll audition, loop length/toggle.
5. A–H cue controls with slot, label, timestamp, and suggested/edited state.
6. Review actions: Generate Suggestions, apply/compare proposals, Mark Reviewed, and Review Next when another track is available.

Use eight cue controls in a single row when spacious; use two rows of four at narrower widths. They should read as useful labeled buttons, not huge glowing performance pads. Empty slots show an add action. Unavailable actions explain why.

Cue interactions:

- Trigger: seek to and play from the selected cue.
- Check: play a configurable number of beats before the cue and continue through it; use a clearly labeled seconds fallback without reliable beats.
- Edit: drag, rename, recolor, change slot, or remove. Support a numerical time input as a precise accessible alternative to dragging.
- Snap: changes only the current edit operation, not unrelated stored cues.
- Loop: set/review start and end and toggle playback; provide an obvious exit action.
- Undo/redo: restore actual edit state, not only marker visuals.

Keep model proposals separate from accepted/manual cues. Make proposed markers visually distinguishable with labels and styles. Applying a new set of suggestions must preserve manual edits unless explicitly replaced. Show traceable explanations, such as “16 bars before Cue C.” Do not invent confidence percentages or section labels.

Mark Reviewed persists review state; edits can autosave with a clear saved/saving/error indicator. Prevent accidental loss during navigation. Review Next advances to the next unresolved track in the current context, preserving filters/order.

Waveform data and playback must correspond to the real selected audio. Use WaveSurfer or the project's existing abstraction. Keep audio playback timing out of React render loops. Do not reload audio on every marker change. Respect the engine's canonical timeline, exact positions, and supported capabilities. Expose a clear baseline editor when semantic analysis is unavailable.

## Screen 3: Metadata inspector

Tabs or clearly separated groups: Details, Metadata Review, Source/Analysis.

Details include title, artist, version, album, artwork, duration, BPM and optional supported key information. Distinguish editable tags from technical file properties. Preserve remix/extended/clean/explicit labels.

Metadata Review shows current versus proposed fields with source and evidence. Users can accept selected fields and choose among candidate releases. Unknown stays unknown. Include a visible action for writing tags to the audio file and an understandable change summary. Ordinary local edits should not silently modify original files.

Source/Analysis includes filename/location, acquisition source, codec, source versus export quality, analysis status, and actionable warnings. Put technical details behind disclosure controls; ordinary users should not need to understand model or IPC internals.

## Screen 4: Add Music, jobs, and export

Add Music is a compact dialog or sheet with Files, Link, and Search modes. Search/link inputs produce candidate tracks with title, artist, version, duration, source, and actual acquisition availability. Show unsupported links honestly. Keep required choices obvious and advanced options collapsed.

Jobs display title, operation, stage, measured progress or indeterminate activity, Cancel, and Retry where supported. Use progress events from the engine. Persist meaningful failures with a recovery action; do not rely on a disappearing toast as the only error record.

Export presents selected track count, destination, rekordbox XML options, and optional audio-copy behavior. Show missing-file/invalid-cue issues before exporting. After success, show Open Folder and import instructions. Do not claim rekordbox import succeeded just because XML generation succeeded.

## Keyboard, accessibility, and platform behavior

- Cmd+F focuses library search on macOS; use the platform equivalent on Windows.
- Cmd+O opens import; Cmd+, opens settings where supported.
- Cmd+Z and Shift+Cmd+Z handle editor undo/redo without breaking native text-input undo.
- Space controls playback only outside typing contexts; use displayed number keys 1–8 for cue triggering when the cue editor owns focus.
- Escape closes the topmost overlay and restores focus without discarding saved work.
- Menus/dialogs require appropriate focus management; native application shortcuts must not be intercepted indiscriminately.
- All controls must be keyboard reachable. Provide visible focus, accessible names, and disabled-state explanations.
- Provide a textual cue list equivalent to canvas/waveform information. Color cannot be the only cue/status identifier.
- Target WCAG AA text contrast, usable pointer targets, and 200% text scaling without inaccessible critical controls.
- Right-click menus supplement visible actions; essential actions must not require right-clicking.

## State and implementation quality

Use existing typed service contracts. Separate view state, playback state, persisted track/cue state, and job state. Handle asynchronous selection changes so stale results cannot overwrite the active track. Cancel or ignore outdated requests correctly.

Use accessible component primitives if already present. If starting fresh, select a small maintained primitive library or implement the necessary controls carefully; avoid a second overlapping design system. Styling can use the existing CSS/Tailwind approach, but semantic tokens remain authoritative.

Create reusable components such as AppShell, Sidebar, LibraryTable, PlayerBar, WaveformEditor, CueButton, CueList, Inspector, MetadataDiff, JobList, and ExportDialog. Keep boundaries pragmatic. Virtualize large lists when warranted by measurements; avoid rerendering the entire library for playhead updates.

Required UI states: empty library, loading, first import, mixed batch success/failure, offline lookup, unsupported source, missing file, unavailable model, analysis failure, no detected sections, save failure, export failure, no next review item. Each must contain a useful next action.

If the backend is unfinished, use a separate development fixture adapter, explicitly marked Demo Data and disabled in production. Do not fabricate successful analysis/acquisition in the normal app. Document exactly which controls are wired and which require missing services. Continue implementing independent screens without pretending integration is complete.

## Verification and delivery

1. Inspect the repository and identify integration boundaries.
2. Establish tokens, layout, shared controls, and the library/player.
3. Implement cue editor and inspector against real services or clearly isolated fixtures.
4. Wire import, jobs, metadata review, and export.
5. Run the app and inspect rendered screens in both themes and target sizes.
6. Critique hierarchy, density, keyboard flow, contrast, and macOS fit; fix findings before finishing.

Capture representative screenshots for review when the environment supports it. Browser screenshots verify web layout only; test real Tauri title-bar behavior, file dialogs, and audio separately. Never claim a screenshot or native verification was performed if the tools were unavailable.

Verify keyboard-only import/review flow, long titles and Unicode, missing artwork, eight cue slots at minimum size, sidebar resizing, inspector toggling, saved edits across navigation, player continuity, and responsive controls during analysis. Run relevant lint/type/build checks and meaningful interaction tests. Preserve existing backend tests.

Deliver working code plus concise run instructions, screenshots when available, test/build results, platform limitations, and a list of unfinished backend integrations. Write the final design tokens and component conventions into a short repository design document for future consistency.

## Skill recommendations and reference links

Checked 2026-11-03. These are external optional coding-agent skills; none is required at runtime and none has been installed by this document.

### Recommended: Impeccable

Repository: https://github.com/pbakaus/impeccable

Use for frontend design review/refinement. Start here instead of installing several overlapping design packages.

From your project directory, the repository's current installer is:

```bash
npx impeccable install
```

Choose your coding tool and project scope in the installer. Review any offered hooks before enabling them; they are separate from reading skill instructions. Reload the coding tool as directed. Installation behavior may change, so consult the README if the command differs.

### Alternative: Anthropic frontend-design

Skill: https://github.com/anthropics/skills/tree/main/skills/frontend-design

Install using the Vercel skills CLI, selecting your coding tool interactively:

```bash
npx skills add anthropics/skills --skill frontend-design
```

Installer reference: https://github.com/vercel-labs/skills

The current skills CLI README requires Node.js 22.20 or later. Use this as an alternative design foundation; installing both it and Impeccable is not necessary for this brief. Native-style system typography and restraint remain explicit requirements.

### Optional: UI UX Pro Max

Repository: https://github.com/nextlevelbuilder/ui-ux-pro-max-skill

Useful as an additional design-system/style reference if you want broader exploration. Treat this project's established visual direction as the constraint rather than selecting a new style every iteration.

Current documented CLI installation:

```bash
npm install -g ui-ux-pro-max-cli
uipro init --ai codex
```

For Claude Code, replace the second command with `uipro init --ai claude`. Run initialization from the project folder. The repository also documents Python 3 as a prerequisite for its search script.

### Platform reference, not an installable skill

Apple Human Interface Guidelines: https://developer.apple.com/design/human-interface-guidelines/designing-for-macos

Consult this for platform conventions. General frontend skills do not guarantee native macOS behavior, Tauri correctness, or accurate audio playback.
