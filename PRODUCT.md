# Product

## Register

product

## Users

People who run EpaperSystem on a Raspberry Pi and read its 7.3-inch color
e-paper pages at a glance, often from farther away than a phone or desktop
screen. The primary user needs a trustworthy two-second read of current
information; installers also need the system to remain understandable and safe
when an optional data source is unavailable.

## Product Purpose

EpaperSystem turns live and cached data into dependable, glanceable 800x480
dashboard pages. Success means the most important state is immediately legible,
freshness and uncertainty are honest, and a page remains useful without asking
the user to understand the runtime behind it.

## Brand Personality

Calm, precise, and purposeful. The interface should feel polished and
information-rich without becoming decorative, crowded, or theatrical.

## Anti-references

- Phone-app layouts miniaturized onto e-paper.
- Tiny decorative imagery that cannot be recognized at normal viewing distance.
- Overlapping labels, low-contrast secondary text, or status conveyed by color alone.
- Interfaces that imply fresh or safe data when the provider reported uncertainty.
- Ornamental motion, glass effects, or visual noise that competes with information.

## Design Principles

1. Make the primary state understandable within two seconds.
2. Spend scarce pixels on information hierarchy, not decoration.
3. Preserve truthful freshness, uncertainty, and fail-closed behavior.
4. Treat English and Simplified Chinese as equal product surfaces.
5. Keep every page reliable on constrained Raspberry Pi hardware.

## Accessibility & Inclusion

Use high-contrast text and redundant state labels rather than color alone.
Protect minimum readable sizes and spacing at the fixed 800x480 resolution.
Validate all fixed Simplified Chinese glyphs against the shipped fonts, avoid
unsupported punctuation, and provide meaningful fallbacks when imagery or
provider data is unavailable. Motion is not part of the e-paper experience.
