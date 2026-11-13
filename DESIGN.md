---
name: CrateLoom
description: A monochrome, compact desktop workspace for preparing local music.
colors:
  light-bg: "#f4f4f4"
  light-sidebar: "#ebebeb"
  light-surface: "#ffffff"
  light-text: "#111111"
  light-muted: "#5e5e5e"
  light-line: "#d4d4d4"
  light-selection: "#e2e2e2"
  light-control-line: "#949494"
  light-recommended: "#43835a"
  dark-bg: "#181818"
  dark-sidebar: "#1f1f1f"
  dark-surface: "#262626"
  dark-text: "#f2f2f2"
  dark-muted: "#aaaaaa"
  dark-line: "#363636"
  dark-selection: "#333333"
  dark-control-line: "#737373"
  dark-recommended: "#73ab86"
typography:
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "20px"
    fontWeight: 700
    letterSpacing: "-0.02em"
  section:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "16px"
    fontWeight: 700
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "14px"
    fontWeight: 400
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "12px"
    fontWeight: 400
rounded:
  artwork: "5px"
  field: "6px"
  control: "7px"
  panel: "8px"
  dialog: "10px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "20px"
  xxl: "24px"
  workspace: "28px"
components:
  button-primary-light:
    backgroundColor: "{colors.light-text}"
    textColor: "{colors.light-bg}"
    rounded: "{rounded.control}"
    padding: "8px 12px"
  button-primary-dark:
    backgroundColor: "{colors.dark-text}"
    textColor: "{colors.dark-bg}"
    rounded: "{rounded.control}"
    padding: "8px 12px"
  button-secondary-light:
    backgroundColor: "{colors.light-surface}"
    textColor: "{colors.light-text}"
    rounded: "{rounded.control}"
    padding: "8px 12px"
  button-secondary-dark:
    backgroundColor: "{colors.dark-surface}"
    textColor: "{colors.dark-text}"
    rounded: "{rounded.control}"
    padding: "8px 12px"
  field:
    rounded: "{rounded.field}"
    padding: "7px"
  source-option:
    rounded: "{rounded.control}"
    padding: "7px 9px"
---

# Design System: CrateLoom

## Overview

**Creative North Star: "Quiet, compact desktop workspace"**

CrateLoom uses the supplied original monochrome brand assets in a compact workspace for preparing local music. System typography, thin dividers and neutral tonal layers keep track information, source provenance and editable cue positions prominent. The light and dark themes have separate palettes.

The interface supports the local, offline work and human review required by `PRODUCT.md`. Status includes text, controls show keyboard focus, and errors should suggest a next action. Native window controls belong to the operating system.

Visual evidence in `.impeccable/review/metadata-ordering/` includes Chromium and WebKit fixtures and an actual macOS window capture. Native traffic-light and sidebar-control alignment is visually verified; native window dragging still requires a manual check.

**Key Characteristics:**
- Monochrome application chrome with deliberate musical data colors.
- Compact rows, stable columns and concise action labels.
- Native controls, visible focus and reviewable analysis.

## Colors

The frontmatter records the effective palette from `apps/desktop/src/workspace.css`, loaded after the base stylesheet. The supplied brand palette is an identity reference; the effective application tokens take precedence where values differ.

### Primary

Primary actions and active navigation use the theme's text tone against its background tone. Selection and hover surfaces use the softer neutral selection tone. There is no teal interface accent.

### Neutral

Window, sidebar and content surfaces form three quiet tonal layers. Muted text remains readable in each theme. Fine dividers use the line tone; editable fields use the stronger control outline.

**The Data Color Rule.** Keep application chrome monochrome. Preserve waveform RGB, hot-cue and musical-section colors because they encode musical information. Source recommendations use a restrained green outline together with a written recommendation and reason.

## Typography

Use the platform system stack recorded in the frontmatter; there is no display face. Workspace titles use the title role, section headings use the section role, and track names and controls use the body role. Supporting labels use the label role, with smaller metadata where existing components require it. Durations and track positions use tabular numerals.

**The Track First Rule.** Keep track names and editable cue positions more prominent than auxiliary metadata. Use short, direct labels and sentence case for actions.

## Layout

The desktop shell has a fixed titlebar (40px), a sidebar and a flexible main workspace, with playback controls along the bottom. Use the existing 4px spacing rhythm as a baseline, preserving local optical adjustments in compact controls.

The sidebar defaults to an expanded width (216px), resizes within its expanded range (180-340px), and collapses to an icon rail (64px). The collapse/expand control lives in the titlebar immediately after the native traffic lights, with no repeated control inside the sidebar or workspace heading. Width and collapse state persist. Its separator supports dragging and keyboard resizing. Keep identity in the centered titlebar: the Crate Dig mark (16px) beside CrateLoom; do not repeat the logo in the sidebar.

Set rows reserve columns for order, time, artwork, track information and actions. Artwork is normally (52px), reduces at the compact breakpoint (46px), and reduces again in the narrow layout (40px). Source options reserve fixed duration and action columns beside a flexible title. At most ten candidates are shown in a bounded scrolling list.

At (1100px), row columns tighten. At (850px), library columns and cue grids simplify. At (760px), the expanded sidebar overlays the workspace, source actions wrap beneath their title, and the transport removes secondary details. The native window permits 600x520 layouts for smaller desktop tiles. These are adaptive desktop layouts; Windows is planned later.

## Elevation & Depth

Use tonal surfaces and thin borders for ordinary separation. Shadows are reserved for floating work, dialogs and the narrow overlay sidebar: floating status uses `0 4px 18px var(--shadow)`, dialogs use `0 10px 45px var(--shadow)`, and the overlay sidebar uses `8px 0 24px var(--shadow)`. The shadow tone adapts to the theme.

## Shapes

Use modest rounded rectangles: artwork and waveform frames use the artwork radius, fields use the field radius, buttons and source options use the control radius, and dialogs use the dialog radius. Playback buttons may be circular. Dense table rows remain square and divide with thin rules.

## Components

- **Buttons and fields:** Neutral filled or outlined controls; primary actions invert foreground and background. Hover strengthens the outline; primary hover reduces opacity. Disabled controls reduce opacity and remain labeled. Focus uses a visible outline (2px) with an offset (3px).
- **Navigation:** Active items invert the theme's text and background tones. Collapsed navigation retains labeled icon buttons. Retain native macOS traffic lights with the overlay titlebar and hidden native title. Center their visible circles vertically in the 40px titlebar, beside the sidebar control.
- **Source options:** Compact title, service icon, duration and explicit action. Recommendation uses the green outline and written reason. Source checks and downloads show progress on the affected track row, allowing independent rows to remain useful.
- **Disclosures:** A shared chevron and expanding content region reveal sources and links. Content expands over (240ms) with `cubic-bezier(.16,1,.3,1)`; opacity fades over (180ms), and the chevron rotates over (220ms). Closed content is hidden from interaction. Respect reduced motion.
- **Track ordering:** Pointer-based drag handles support stable track identities, drop feedback, edge scrolling and keyboard movement with Up and Down. Move buttons provide a click alternative. Renumber visible positions after reordering, save the order, restore it if saving fails, and prevent stale background results from replacing a newer order.
- **Library actions:** The header carries a labelled Downloads button and one primary Add music menu (Files, Folder); no icon-only header actions. The action row holds Auto metadata, Analyze beats (only while tracks lack a current analysis) and the selection actions. Selection actions stay visible and disabled until rows are ticked, so ticking a row never moves the list; they apply only to ticked rows that are currently shown. Playlist rename and delete live in a Playlist menu. Removing tracks and deleting playlists confirm first and state that audio files are never deleted. Export options retains a directly connected checkbox label.
- **Metadata review and panels:** The metadata window is a fixed frame (`min(1040px, 100vw - 48px)` by `min(720px, 100dvh - 88px)`) that never resizes: cover and fields on the left, catalog matches on the right, each scrolling on its own, with skeleton rows and a reserved status line while results load; under (760px) it is one scrolling column. The artwork is the cover control (choose a file, use the release cover, remove). The header keeps save-and-previous/next arrows and track position; the footer has one status line, Reload file tags, Save to library and the primary Write tags to file. Panels open with one fade/scale and retain their contents until closing motion ends. Move the player artwork/title/artist into Cue Editor through a shared transition, with a reduced-motion fallback.
- **Automatic metadata:** The Auto metadata button states its scope in words (all tracks, tracks shown, or tracks selected) and never changes the selection; its first use explains in one line what it does. Beside it, the remembered checkbox "Write tags to the files automatically" (off by default) also writes each track the run applies into its file after a backup, never tracks that need review; the results strip then says how many were written and lists any file that could not be written, with its reason and a way into Write tags. Review of results starts from the results strip. Use a compact progress strip for completed/total tracks, applied covers, items needing review, current work and cancellation. Keep results available after completion, with Review reopening saved fields, artwork and candidate matches. Use ordinary monochrome surfaces and text; show uncertain or unavailable results explicitly.
- **DJ-set scan progress:** Center the initial scan icon, stage, measured progress and Cancel action in the space below the retained form. Keep History and Current set available, use a compact status strip while browsing, and preserve that navigation when scanning completes. A published tracklist opens as a report at once; audio recognition (of a mix without a tracklist, or "Recognize audio too" on a report) continues inside the report while entries stay usable, with recordings heard so far listed muted and without actions until they are placed. Use bytes, checked time or sample counts according to the actual phase, with an indeterminate bar for unknown totals; while Shazam asks to wait, show "Waiting for Shazam" with a countdown, and after an attempt that failed for another reason show "Waiting before the next attempt" instead. Preserve inputs and completed results on cancellation, and keep Cancel and Pause scan visible above the player at the minimum window height (under 720px high the report's scan card becomes a strip with Pause beside its heading). Before a playlist exists, each entry says where its background source search stands (Waiting to search, Finding sources…, Source ready, Choose a version, No source found yet, Search failed) and the header counts ready sources.
- **Waveforms and cues:** Preserve musical data colors and editable positions. Describe fast section analysis as local energy and texture estimates, and keep automatic results open to human review.

## Do's and Don'ts

### Do:
- Do use the supplied original CrateLoom mark and application icon.
- Do preserve compact native conventions, readable themes and visible keyboard focus.
- Do show status text, useful errors and source provenance beside the affected work.
- Do keep musical data colors distinct from monochrome interface chrome.

### Don't:
- Don't reintroduce teal as an interface accent or add a sidebar wordmark.
- Don't replace native window controls with simulated traffic lights.
- Don't communicate recommendation, progress or errors through color alone.
- Don't present fast section estimates as proprietary semantic analysis or imply an export was imported into rekordbox.

## Clarity rules added with the November 13 rework

- Every control has a visible label or, for a compact icon, a tooltip and an accessible name. A control whose purpose cannot be stated in a few words is removed.
- Status is worded for the task, not the mechanism: a row says "File missing", "Tags not written", "Downloading 42%", "Needs a source", never a bare dash or an internal state name. Raw engine or network text stays available on demand under a details disclosure.
- An error appears beside the thing that failed and stays until dismissed. YouTube access problems appear in the YouTube access block, next to the browser choice.
- Opening or closing the Cue Editor animates the content area only. The titlebar, sidebar and player never fade or move with a workspace change.
- The Cue Editor groups by purpose: one Analysis menu and one status line; labelled Jump, Loop, Zoom and Scrolling controls; a Beat grid panel that says whether the grid is a straight grid or detected beats and what confirming bar 1 does; compact cue pads with a single edit panel for the selected cue.
- Import DJ Set starts from one of three inputs (a link, an audio file, a pasted tracklist), each with one line about what will happen. A link is one row: the link field, an "Access: Public" toggle for YouTube links that opens the YouTube access block underneath (it also opens by itself on an access error), and Scan. Share and tracking parameters are removed from a pasted link. There is no recognition checkbox: audio is recognized when a mix has no tracklist, or later from the report with "Recognize audio too". Token fields appear only where needed: Spotify beside a Spotify authorization error or on a report read from Spotify's public page, SoundCloud on a report whose sources are reference-only. Scan errors are one plain sentence; engine text sits under Details.
