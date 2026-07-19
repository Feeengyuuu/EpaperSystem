# Orbital Signal implementation plan

1. Normalize Launch Library and Polymarket payloads into display-safe rows.
2. Cache each source independently and preserve one live panel when the other fails.
3. Recreate the approved img-2 hierarchy as an 800x480 flat, high-contrast e-paper layout.
4. Verify parser, cache, day/night, provenance, and render behavior with focused tests.
5. Render fixture and live desktop previews. Do not deploy until explicitly requested.
