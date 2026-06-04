# ColoredEpaperFrame Night Disconnect Diagnostics

When `ColoredEpaperFrame` disappears from the router and `.188` is unreachable by ping, HTTP, SSH, mDNS, and ARP, treat it as a device/Wi-Fi/power-level outage rather than an InkyPi-only crash.

After a manual power reboot on 2026-06-02, `.188` returned with MAC `88:a2:9e:94:d3:ea`, `inkypi` active, `/playlist` 200, Wi-Fi connected to `Void` on channel 4 with strong signal, gateway/public ping OK, and `vcgencmd get_throttled` reported `0x0`.

The important diagnostic gap is that `journalctl --list-boots` showed only the current boot after reboot, so the pre-reboot outage evidence was unavailable. For future night-disconnect debugging, set up persistent boot/network logging or a small health monitor before waiting for the next reproduction.
