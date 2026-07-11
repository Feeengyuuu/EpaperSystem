# Per-Plugin Day and Night Theme Design

## Goal and scope

Every renderable plugin must own two deliberate color treatments: a daytime
theme and a deep-night theme. The themes are not one global palette applied to
all plugins. Each plugin keeps its own visual identity while exposing the same
three user choices: `auto`, `day`, and `night`.

`auto` selects one of the plugin's two palettes. It is not a third visual style.
The active palette follows local sunrise and sunset when weather timing is
available and falls back to 07:00-19:00 in the configured device timezone.

## Theme contract

All plugins accept the canonical `themeMode` values:

- `auto`: resolve the current day or night palette at render time;
- `day`: always use the plugin's daytime palette;
- `night`: always use the plugin's deep-night palette.

Existing saved values remain compatible. `paper`, `light`, and `comic` map to
`day`; `dark`, `cinema`, `streaming`, and `midnight` map to `night`. Unknown or
missing values resolve to `auto` and must not prevent rendering.

The shared theme utility owns value normalization, timezone handling, and the
day/night decision. Each plugin owns only its two palette definitions and how
those palette roles are used in its layout.

## Plugin-specific visual treatment

UI-led plugins, including Bambu Monitor, Simple Calendar, weather, radar,
sports, finance, knowledge, news, profile, and dashboard layouts, replace the
full canvas palette. Background, panels, rules, labels, states, charts, and
legibility colors all receive plugin-specific daytime and deep-night values.

Media-led plugins, including box-office posters, comic and magazine covers,
art, Pixiv, Steam artwork, and newspaper pages, keep the source media in its
original colors. Their plugin-specific day/night palettes apply to the canvas,
header, framing, separators, captions, metadata, badges, and fallback artwork.
This gives every plugin two complete presentations without corrupting the
photograph, cover, poster, or scanned page itself.

The night palette must be designed for the physical color e-paper display. It
uses strong text weight and sufficient contrast; it must not depend on subtle
near-black gradients or thin low-contrast secondary text.

## Settings and capability model

Plugin manifests declare `supports_day_night_theme`. A shared settings partial
renders the same `Auto day/night`, `Day`, and `Deep night` control for every
capable plugin, avoiding divergent hand-written controls. All shipped
renderable plugins declare the capability once both palettes are implemented.

Saved instances continue to contain one plugin entry. The feature does not
duplicate instances or add separate day/night playlist items. Existing active
instances are migrated to `auto` while preserving every unrelated setting.

## Rendering and cache behavior

Theme resolution happens before a plugin constructs or retrieves a rendered
image. The resolved theme becomes part of every theme-sensitive rendered-image
cache key. Data caches remain reusable across theme changes; only the final
presentation is invalidated.

When the global day/night state changes, theme-aware instances are marked for
regeneration on their next display. The scheduler does not eagerly render all
plugins at once, which protects the Raspberry Pi Zero 2 W from a memory and CPU
spike. The current visible instance may refresh immediately through the
existing safe display path.

Photo, poster, cover, article, weather, and sports data fetches are not repeated
solely because the palette changed. A theme switch must remain possible while
the source is offline by rendering cached data with the other palette.

## Failure and compatibility behavior

- A missing palette role falls back to that plugin's existing readable color,
  then to a shared high-contrast default.
- Legacy saved aliases continue to render and are canonicalized when the user
  next saves the instance.
- A failed theme-sensitive render keeps the last good image and uses the
  existing cooldown behavior; it does not trigger a retry storm.
- Media content is never inverted, recolored, or replaced merely to create a
  night variant.
- Rollback to an older release remains possible because legacy setting values
  are accepted and no second plugin instance is required.

## Delivery sequence

Implementation proceeds in small verified batches on one shared contract:

1. Add canonical normalization, capability metadata, shared settings control,
   and theme-sensitive cache behavior.
2. Convert plugins that already have two palettes and repair false `auto`
   modes, including both box-office plugins and streaming-style layouts.
3. Add individual palettes to the remaining UI-led plugins.
4. Add individual day/night chrome to the remaining media-led plugins.
5. Migrate the live saved instances to `auto`, deploy once the full set passes,
   and force a bounded acceptance rotation on the physical device.

No batch is considered complete while a shipped renderable plugin lacks either
its day or night treatment.

## Verification

Automated coverage must prove:

- all canonical values and legacy aliases resolve correctly;
- every shipped renderable manifest declares the capability and exposes two
  palettes;
- fixed local midday and nighttime inputs select different plugin-owned
  palettes for every plugin;
- cached source data can render both variants without another network fetch;
- rendered-image cache keys differ by resolved theme;
- source media pixels remain unchanged while media-plugin chrome changes;
- theme changes do not bulk-regenerate the full playlist;
- existing failure cooldown and last-good-image behavior remain intact;
- the complete test, lint, archive, and release validation gates pass.

Live acceptance renders both modes for every configured plugin at 800x480,
checks the images for clipping and readable contrast, then restores each live
instance to `auto`. Final proof includes service readiness, zero new restarts,
the current release identity, and a physical-display rotation rather than only
local previews.
