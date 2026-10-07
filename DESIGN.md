---
name: EpaperSystem Web Portal
description: A private printed-instrument-panel view of configured EpaperSystem playlists.
colors:
  paper: "#f4f4f9"
  surface: "#ffffff"
  ink: "#333333"
  muted-ink: "#555555"
  rule: "#dddddd"
  process-teal: "#1abc9c"
  process-teal-deep: "#16a085"
  process-red: "#c4515b"
  night: "#1a1a1a"
  night-surface: "#2d2d2d"
  night-ink: "#e0e0e0"
typography:
  headline:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "2rem"
    fontWeight: 750
    lineHeight: 1.1
    letterSpacing: "-0.02em"
  title:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 700
    lineHeight: 1.2
  body:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "1rem"
    fontWeight: 500
    lineHeight: 1.45
  label:
    fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 700
    lineHeight: 1.2
rounded:
  sm: "6px"
  md: "10px"
  lg: "14px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.process-teal}"
    textColor: "{colors.surface}"
    rounded: "{rounded.sm}"
    padding: "12px 18px"
  button-primary-hover:
    backgroundColor: "{colors.process-teal-deep}"
    textColor: "{colors.surface}"
    rounded: "{rounded.sm}"
    padding: "12px 18px"
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "16px"
---

# Design System: EpaperSystem Web Portal

## Overview

**Creative North Star: "The Printed Instrument Panel"**

The portal combines the immediacy of a vehicle instrument panel with the
graphic discipline of the existing color e-paper product. Information is
organized by strong type, flat process-color signals, thin rules, and generous
negative space around the current fact. It is compact without looking like an
admin console.

The interface explicitly rejects generic SaaS glass panels, purple gradients,
neon gaming effects, tiny hover-dependent controls, and unlabeled raster output
pretending to be native HTML. Every publication deliberately preserves the
exact frame produced by its plugin renderer inside the portal shell instead of
attempting a lower-fidelity native redraw.

**Key Characteristics:**

- A responsive authenticated shell around first-class original plugin frames.
- Uncropped renderer output with source, freshness, and local-time context kept
  outside the frame.
- Flat print-like surfaces and crisp separators.
- One dominant fact per region, with freshness always visible.
- Familiar controls, large touch targets, and restrained state transitions.
- Automatic light and night themes with identical information hierarchy.

## Colors

The palette inherits the existing InkyPi neutrals and teal accent, then applies
the repository's limited process-color discipline. Accent color is structural,
not decorative.

### Primary

- **Process Teal:** the active playlist, primary action, focus, and fresh-data
  signal.

### Secondary

- **Process Red:** failed, unavailable, or materially expired content only.

### Neutral

- **Cool Paper:** the daylight application background.
- **Clean Surface:** primary reading surfaces and media mats.
- **Process Ink:** primary text and crisp rules.
- **Night Ink and Surface:** the dark-theme equivalents, never a neon palette.

**The Limited Ink Rule.** A screen uses neutral ink plus no more than three
semantic accents. More color is noise.

**The Freshness Rule.** Fresh, stale, and unavailable states always include a
text label and timestamp; color never carries the meaning alone.

## Typography

**Display Font:** system UI sans-serif
**Body Font:** system UI sans-serif

**Character:** familiar, compact, and dependable. One family prevents the
interface from competing with varied plugin artwork and Chinese content.

### Hierarchy

- **Headline** (750, 2rem, 1.1): playlist and full-screen publication titles.
- **Title** (700, 1.25rem, 1.2): instance names and primary region labels.
- **Body** (500, 1rem, 1.45): facts, supporting copy, and useful empty states.
- **Label** (700, 0.875rem, 1.2): timestamps, freshness, and short controls.

**The Windshield Test.** If the current fact or freshness label cannot be read
from normal driving posture while parked, the type is too small or too weak.

## Elevation

The system is flat by default. Depth comes from tonal layers and rules, not
wide ambient shadows. A focused control may use a compact outline; overlays are
reserved for authentication and deliberate full-screen viewing.

**The Flat-at-Rest Rule.** No card combines a decorative border with a wide
shadow. Use one crisp separator or one tonal change.

## Components

### Buttons

- **Shape:** compact curved rectangle (6px radius), never a giant pill.
- **Primary:** Process Teal with clean white text and a 48px minimum hit area.
- **Hover / Focus:** deepen the teal on hover and use a visible two-tone focus
  outline. Active state moves no more than one pixel.
- **Secondary:** neutral surface with a crisp rule and Process Ink.

### Chips

- **Style:** short freshness or playlist labels with 6px corners and semantic
  text. They are labels first, filters only when explicitly interactive.

### Cards / Containers

- **Corner Style:** restrained (10px radius).
- **Background:** one clean surface per information region; no nested cards.
- **Shadow Strategy:** flat at rest.
- **Border:** a single subtle rule when tonal contrast is insufficient.
- **Internal Padding:** 16px minimum, 24px for primary vehicle regions.

### Inputs / Fields

- **Style:** clean surface, crisp rule, 6px radius, and at least 48px height.
- **Focus:** Process Teal outline with no glow.
- **Error / Disabled:** explicit message and state text, never opacity alone.

### Navigation

Playlist tabs retain source order, use clear selected state, and collapse into
a horizontally scrollable rail on narrow screens. The autoplay route uses large
previous, pause, and next controls in a floating overlay. The overlay may hide
after inactivity and must also provide an explicit show/hide control, so the
user never has to depend on hover behavior to recover navigation.

### Publication Stage

Every edition uses the exact raster emitted by its plugin renderer. The browser
must not reconstruct plugin content as HTML, crop it, or alter its aspect ratio.
The stage sizes itself from the current browser viewport and uses an uncropped
`object-fit: contain` presentation. The portal shell is responsible only for
authentication, playlist navigation, source and freshness labels, and
timestamps labelled `当地时间`.

Weather keeps the location-neutral portal heading `当地天气`; location text inside
the original frame remains authoritative. Other plugins retain their configured
instance titles. All plugin frames receive the same explicit original-frame
treatment rather than being divided into native and fallback presentations.

The autoplay page uses the dynamic viewport available at runtime, with a normal
viewport fallback, and reserves no fixed Tesla-specific CSS dimensions. Its
navigation floats over the stage, can auto-hide after inactivity, and can be
explicitly hidden or restored with a touch control. The Fullscreen API is
progressive enhancement only: rejection, absence, or exit from fullscreen must
leave the same contain-fitted playback page usable.

Tesla's public Model Y documentation notes that touchscreen presentation can
vary with vehicle options, software version, market region, and settings; it
does not define a stable browser CSS viewport contract. Do not encode rumored
Tesla resolutions as layout breakpoints or canvas sizes.

## Do's and Don'ts

### Do:

- **Do** preserve playlist order and show the active time window.
- **Do** keep freshness and last-updated text in the first visual scan path.
- **Do** use 48px minimum touch targets and visible focus states.
- **Do** provide reduced-motion behavior and pause polling in hidden tabs.
- **Do** use same-origin authenticated assets for personal and adult content.
- **Do** derive playback size from the live viewport and keep every renderer
  frame fully visible with `object-fit: contain`.
- **Do** keep playback navigation recoverable by an explicit touch action.

### Don't:

- **Don't** use generic SaaS dashboards with glass panels, purple gradients,
  ornamental metrics, or giant rounded cards.
- **Don't** use neon gaming-dashboard effects or decorative motion.
- **Don't** ship tiny admin tables, hover-only controls, or mouse-dependent
  drag interactions on the vehicle surface.
- **Don't** redraw any plugin raster as a native web renderer; identify every
  preserved renderer frame as the original plugin presentation.
- **Don't** hard-code a rumored Tesla browser viewport or require the Fullscreen
  API for a usable playback layout.
- **Don't** leak settings, paths, provider errors, credentials, or publication
  assets before authentication.
