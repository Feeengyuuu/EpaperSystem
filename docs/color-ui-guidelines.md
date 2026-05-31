# Color UI Guidelines

Captured: 2026-05-28

## Purpose

The production EpaperSystem target is a color e-paper product. When the UI is
later upgraded from the current monochrome beta style, use the user-provided
vintage comic process/Pantone color chart as the governing color direction for
both the UI and the matching host/device visual language.

The printed color codes in the chart are the canonical reference. Visual color
sampling from the compressed image is only a preview aid; implementation should
identify each color by its exact `Pantone` or `Process` label and the printed
mix code, then map that identifier to calibrated production-panel values.

This reference is a color-system reference only. Do not reuse the copyrighted
character artwork, logos, costumes, or brand marks from the source image.

## Normative Color Identifiers

Every future color token should keep a source reference using the chart label.
Preferred metadata:

```json
{
  "token": "accent_yellow",
  "source_mix": "100Y-25R",
  "source_code": "PANTONE 123",
  "source_label": "100Y-25R PANTONE 123",
  "calibrated_hex": "set after production panel testing",
  "notes": "Do not replace with sampled image RGB."
}
```

Rules:

- Use the printed `source_label` as the stable color ID.
- Store calibrated hex/RGB values as derivatives of that ID, not replacements.
- If a design needs a new color, choose the closest chart code first and record
  why that code was selected.
- Do not introduce untracked colors into production UI assets.
- Preserve discontinued or hard-to-find Pantone IDs exactly. For example,
  `50Y-100R PANTONE 833` should remain that source ID even if a modern Pantone
  library cannot resolve it directly.

## Reference Chart Code Index

Transcribed from the provided chart so future implementation can locate exact
colors by code:

| A | B | C | D | E |
| --- | --- | --- | --- | --- |
| `25Y PANTONE 100` | `50Y PANTONE 101` | `100Y PROCESS YELLOW-2` | `100Y-25B PANTONE 374` | `100Y-50B PANTONE 375` |
| `25B PANTONE 304` | `50B PANTONE 305` | `100B PROCESS CYAN-2` | `50Y-25B PANTONE 358` | `50Y-50B PANTONE 345` |
| `25R PANTONE 196` | `50R PANTONE 210` | `100R PROCESS MAGENTA-2` | `25Y-25B PANTONE 337` | `25Y-50B PANTONE 338` |
| `100Y-25R PANTONE 123` | `100Y-50R PANTONE ORANGE 021` | `100Y-100R PANTONE RED 032` | `50Y-100B PANTONE 327` | `50Y-25R-100B PANTONE 329` |
| `100B-25R PANTONE 285` | `100B-50R PANTONE 286` | `100R-100B PANTONE 266` | `100Y-100B PANTONE 354` | `100Y-100B-25R PANTONE 336` |
| `25R-25B PANTONE 263` | `25R-50B PANTONE 278` | `100Y-25R-25B PANTONE 132` | `100Y-25R-50B PANTONE 385` | `100Y-100B-50R PANTONE 350` |
| `50R-25B PANTONE 257` | `50R-50B PANTONE 271` | `25Y-25R-50B PANTONE 429` | `50Y-25R-50B PANTONE 443` | `50Y-50R-50B PANTONE 437` |
| `100R-25B PANTONE 233` | `100R-50B PANTONE 241` | `25Y-50R PANTONE 183` | `50Y-50R PANTONE 177` | `50Y-100R PANTONE 833` |
| `50Y-25R PANTONE 156` | `50Y-25R-25B PANTONE 465` | `100Y-50R-50B PANTONE 470` | `100Y-50R-25B PANTONE 167` | `100Y-25R-100B PANTONE 484` |
| `25Y-25R PANTONE 489` | `25Y-25R-25B PANTONE 481` | `50Y-50R-25B PANTONE 479` | `100Y-50B-100R PANTONE 483` | `PROCESS BLACK` |

Before building the first production token file, verify this transcription
against the original image or a higher-resolution source asset.

Auxiliary digital conversion reference: TruColor's "DC Comics 1982 Color
Palette" page lists current Color Bridge-style hex/RGB/CMYK representations and
notes that `PANTONE 833 C` is a discontinued Neon/Day-Glo color:
`https://www.trucolor.net/portfolio/dc-comics-1982-color-palette/`.

## Visual Direction

- The vintage comic process/Pantone chart is the default color law for color
  e-paper UI. New color-screen pages should follow this palette unless the user
  explicitly asks for a different art direction.
- Use a clean comic-print palette: high-contrast black linework, paper white or
  warm off-white backgrounds, and flat CMYK-like accent colors.
- Prefer crisp separations over soft digital effects. Color blocks, thin black
  rules, labels, panels, and halftone/dither texture are appropriate.
- Keep layouts readable after e-paper quantization. Text, key data, icons, and
  panel borders must remain understandable in black ink even if color is muted.
- Treat color as semantic and structural, not decorative. Most screens should
  use black, paper, and 2-4 accents rather than many colors at once.

## Core Palette Families

Use these families when creating future color tokens:

| Family | Use |
| --- | --- |
| Paper / warm tint | Backgrounds, empty space, low-emphasis panels |
| Process black | Text, outlines, dividers, icons, primary contrast |
| Yellow | Highlights, time/date emphasis, attention without danger |
| Cyan / blue | Primary UI accents, active states, informational data |
| Red / magenta | Alerts, high-priority warnings, energetic feature moments |
| Orange | Transitional warning, warmth, callouts |
| Green | Success, healthy status, environment/device state |
| Purple / muted mixes | Secondary categories, rare contrast accents |
| Brown / gray mixes | Neutral metadata, inactive states, low-emphasis surfaces |

## UI Token Rules

- Future color implementation should introduce named tokens first, then map
  those tokens to the actual display-calibrated colors.
- Initial token set should include: `paper`, `ink`, `muted`, `primary_blue`,
  `accent_yellow`, `accent_red`, `accent_green`, `accent_orange`,
  `accent_purple`, `warning`, `danger`, and `success`.
- Never choose arbitrary saturated web colors just because they look good on an
  LCD monitor. Every new UI color should be traceable to a specific chart
  `source_label` or to a calibrated production-panel derivative of it.
- Avoid gradients, glassmorphism, neon glows, and soft pastel-only palettes.
  The intended feel is printed, graphic, bold, and legible.

## E-Paper Constraints

- Calibrate final hex values on the production color e-paper panel instead of
  trusting compressed image samples from the reference image.
- Validate every color screen through a quantized preview before shipping.
- Prefer black text on paper or paper text on black. Avoid colored body text
  unless the contrast survives panel rendering.
- Use color to support the main hierarchy; do not make recognition depend on
  color alone.

## Application Guidance

For future requests such as "make this color", "upgrade to the formal color
version", or "match the host colors", apply this document as the default color
rule. The palette should feel like a modern e-paper product using vintage comic
process color discipline: bold, limited, print-like, and highly readable.
Existing plugin pages updated for the new color screen should also use this
comic process palette, including functional pages such as Stock Tracker.
