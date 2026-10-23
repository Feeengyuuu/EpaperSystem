# Vehicle art source

Both car images show the owner's car as the Tesla app showed it on 2026-10-23:
a 2025 Model Y Performance under a custom white police-livery wrap. Tesla's
vehicle data reports only the base paint (StealthGrey), never a wrap, so these
are refreshed by hand when the wrap changes.

`vehicle.png` (3/4 view) is cut from the owner's Tesla app screenshot: the car
was cropped (SHA-256 `7601d5d507abe235a25b2d18d58670545916db47658c2db19b6d53b36640fb64`), its
background removed with Creative Claw's AI background removal (SHA-256
`c07bbf5c6a71113ed6d914f977205b4814194c0a0edc8d9739ff7e79775386ba`), cropped to its visible alpha,
scaled to 560 px wide and padded by 2 px. Final SHA-256
`982418dcd6403a6bdcc2f3e6f3c5c29a56f0e5a744a88bfd553cf9da573f08ca`.

`vehicle_top.png` (top-down view for the tyre map) was generated with GPT Image
2.5 from that crop as the reference, asking for the same car and wrap seen from
directly above on a transparent background (SHA-256
`b9db80f77ce217054d4ac2927fb7fedbc4684de9e72b937763d99530db6ddb1e`), then cropped to its
visible alpha, scaled to 300 px tall and padded by 2 px. Final SHA-256
`be0e76a16a182be21031192154c5211578dbb09a51734bc9c01fecee28bd1e79`.

At runtime each is fitted into its box and, on night paper, outlined in the
muted ink.
