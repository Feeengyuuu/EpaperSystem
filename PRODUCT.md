# Product

## Register

product

## Users

The primary user is the owner of an EpaperSystem installation who wants to
check the same configured information from a Tesla Model Y, a phone, or a
desktop browser. The most important context is a wide vehicle display where
content must be understandable at a glance and must not require repeated input.

## Product Purpose

Provide a private, read-only, cloud-hosted view of every configured
EpaperSystem playlist. The cloud runtime collects and publishes content without
depending on the Raspberry Pi after a one-time import. Success means the owner
can open one trusted HTTPS address, see the exact frame produced by each plugin
renderer, understand freshness immediately, move between playlists, and use an
optional full-screen autoplay mode without exposing the InkyPi control plane or
provider credentials.

## Brand Personality

Clear, restrained, dependable. The interface should feel like a carefully
printed instrument panel: dense enough to be useful, calm enough to scan, and
honest about stale or unavailable data.

## Anti-references

- Generic SaaS dashboards with glass panels, purple gradients, and ornamental
  metrics.
- Neon gaming dashboards whose visual effects compete with the information.
- Tiny admin tables, hover-only controls, and interaction patterns that assume
  a mouse and keyboard.
- A plugin raster stretched, cropped, redrawn as lower-fidelity HTML, or shown
  without provenance. Every plugin edition preserves the renderer's exact
  original frame inside the portal's authenticated structure. The surrounding
  portal supplies only authentication, playlist navigation, source and
  freshness context, and `当地时间` labels. For Weather, the portal heading stays
  location-neutral as `当地天气`; the rendered frame remains the source of truth
  for the actual place name.
- Public status pages that leak settings, internal paths, raw provider errors,
  or personal content before authentication.

## Design Principles

- Put the current fact, its source freshness, and its timestamp in the first
  scan path.
- Keep reading independent from collection: opening a page never triggers a
  provider request, Chromium render, scheduler action, or hardware write.
- Use the same plugin renderer for e-paper and web publication, preserve its
  exact raster bytes, and allow only uniform contain-fit scaling without
  browser-side reconstruction, cropping, or aspect-ratio changes.
- Make degraded states useful: retain the last known good edition, label it
  clearly, and never replace it with a failed update.
- Treat every configured playlist as private, including personal and adult
  content, with the same authenticated asset policy.
- Adapt to the browser's measured viewport instead of assuming a fixed Tesla
  CSS resolution. Fullscreen is optional progressive enhancement, not a layout
  prerequisite.

## Accessibility & Inclusion

Target WCAG 2.2 AA. Use at least 48px touch targets on the vehicle surface,
visible keyboard focus, reduced-motion alternatives, semantic landmarks, and
text labels in addition to color. Default copy is Simplified Chinese and times
are displayed in America/Los_Angeles unless an imported instance explicitly
requires a different interpretation.
