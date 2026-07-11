# Global Microsoft YaHei Base Font Design

## Goal and scope

Make Microsoft YaHei the effective base text font across InkyPi, not merely the
declared CSS default. Regular and bold text must resolve to readable YaHei files
on the device. Brand wordmarks, icon fonts, score displays, pixel art fonts, and
other deliberately decorative typefaces remain unchanged.

The proprietary font binaries are device-owned runtime data. They must never be
committed, copied into a release source tree, or included in a deployment
archive.

## Font storage and ownership

The supported runtime location is `${INKYPI_DATA_DIR}/fonts`, which is
`/var/lib/inkypi/data/fonts` on the live device. The directory survives release
swaps, rollback, restart, and normal uninstall. It is owned by `root:inkypi`
with mode `0750`; font files are `root:inkypi` with mode `0640`.

The live device's existing user-provided YaHei files may be installed there as
`msyh.ttf` and `msyhbd.ttf`. This is a local device operation only; repository
and release tooling continue to exclude the binaries.

## Resolution architecture

`utils.app_utils` becomes the single resolver for runtime font candidates:

1. For `msyh.ttf` or `msyhbd.ttf`, check the durable font directory first.
2. Check the existing release-local font path for development compatibility.
3. If a candidate is missing or cannot be opened, continue to the existing
   readable CJK fallback instead of raising `OSError`.

`get_font`, `get_font_path`, and `get_fonts` use the same resolution rule so PIL
renderers and HTML `@font-face` declarations cannot disagree. HTML render paths
must point to a readable local file URI/path supported by the browser renderer.

SportsDashboard and other independent base-font loaders that bypass
`app_utils` are migrated to the shared durable YaHei candidates. Loaders that
explicitly request a decorative font retain that choice. This avoids changing
logos, digital clocks, pixel text, or illustration typography while making
ordinary UI copy consistent.

## Failure behavior

- Missing, unreadable, or corrupt YaHei files do not crash a plugin.
- Regular and bold variants fall back independently.
- Existing Noto Sans CJK or bundled readable CJK fonts remain the final base
  fallback.
- Startup health reports the missing optional YaHei files as a warning, not as
  a fatal readiness failure.

## Verification

Automated tests cover:

- durable regular and bold files taking priority;
- PIL and HTML resolution using the same durable files;
- missing and corrupt files falling back without a render failure;
- SportsDashboard regular/bold selection;
- no font binary entering `git archive` or a release payload;
- full unit, lint, and clean-archive gates.

Live acceptance requires a fresh SportsDashboard render on the physical
800x480 display, service readiness with zero restarts, and inspection of small
English labels, Chinese team names, bold headings, and overflow behavior.
