# Flight radar terrain map style

- For `SkyRadar`, the preferred Google Static Maps style is `terrain` unless the user asks to compare another map type.
- The map should not draw radar-ring decoration over the Google terrain map; keep aircraft markers, route list, legend, and the `20 NM VIEW` range label.
- If the cached plugin image was created by a root-running service, `cp` may fail for a non-root SSH user, but the writable plugin image directory allows replacing the exact cache file via `cp *.new && mv -f *.new target`.
