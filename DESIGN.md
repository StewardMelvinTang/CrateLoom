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

The sidebar defaults to an expanded width (216px), resizes within its expanded range (180-340px), and collapses to an icon rail (64px). The collapse/expand control lives in the titlebar immediately after the native traffic lights, with no repeated control inside the sidebar or workspace heading. Width and collapse state persist. The titlebar control also restores a sidebar hidden by an older saved preference. Its separator supports dragging and keyboard resizing. Keep identity in the centered titlebar: the original mark (16px) beside CrateLoom; do not repeat the logo in the sidebar.

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
- **Library actions:** Separate playlist name/count and view controls from the action row. Keep Auto metadata and Review together; put selection operations and audio-copy settings in compact, animated dropdowns. Export options retains a directly connected checkbox label.
- **Metadata review and panels:** Keep save-and-previous/next arrows and track position in the review header. Morph the surface while keeping form text unscaled, and retain dialog contents until closing motion ends. Move the player artwork/title/artist into Cue Editor through a shared transition, with a reduced-motion fallback.
- **Automatic metadata:** Place Auto metadata beside Review in the library toolbar, with the target count. Use a compact progress strip for completed/total tracks, applied covers, items needing review, current work and cancellation. Keep results available after completion, with Review reopening saved fields, artwork and candidate matches. Use ordinary monochrome surfaces and text; show uncertain or unavailable results explicitly.
- **DJ-set scan progress:** Center the initial scan icon, stage, measured progress and Cancel action in the space below the retained form. Keep History and Current set available, use a compact status strip while browsing, and preserve that navigation when scanning completes. Center automatic continuation inside the report while keeping entries usable. Use bytes, checked time or sample counts according to the actual phase, with an indeterminate bar for unknown totals. Preserve inputs and completed results on cancellation, and keep Cancel visible above the player at the minimum window height.
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
