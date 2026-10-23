# Vehicle art source

The car images show the owner's car as the Tesla app showed it on 2026-10-23:
a 2025 Model Y Performance under a custom white police-livery wrap. Tesla's
vehicle data reports only the base paint (StealthGrey), never a wrap, so these
are refreshed by hand when the wrap changes.

`vehicle.png` (a low front three-quarter view, long side on) was generated on
2026-10-23 with GPT Image 2.5 from two references: the owner's Tesla app
screenshot of the wrapped car (crop SHA-256
`7601d5d507abe235a25b2d18d58670545916db47658c2db19b6d53b36640fb64`) for the car and its wrap, and a
crop of a stock dark Model Y render for the camera angle only. The chosen
transparent output (SHA-256
`013cff97716a6fad9c962f1b12ea4e185671dfdc00b9f9f2eec02aeb58a4e0aa`) was cropped to its
visible alpha, its near-opaque alpha set to opaque, scaled to 560 px wide and
padded by 2 px. Final SHA-256
`c7879f4ce2b5e0bcb0798c2c19c78a284c3427d725f10de98af7c75f3499e957`.

`vehicle_charging.png`, shown while the car is charging, is a GPT Image 2.5
edit of that same output: the cable plugged into the rear-left charge port, the
port lit green and an energy line along the sill (SHA-256
`276bc311684eb5987df68d2c869b4981be201b1bd02e0da71fc1797d8f548c25`), prepared the same way.
Final SHA-256 `6fbdffb0bceeef302820b67cf21bcd220802a51d295384afa9332a176ace9ceb`.

`vehicle_top.png` (top-down view for the tyre map) was generated with GPT Image
2.5 from the app screenshot crop as the reference, asking for the same car and wrap seen from
directly above on a transparent background (SHA-256
`b9db80f77ce217054d4ac2927fb7fedbc4684de9e72b937763d99530db6ddb1e`), then cropped to its
visible alpha, scaled to 300 px tall and padded by 2 px. Final SHA-256
`be0e76a16a182be21031192154c5211578dbb09a51734bc9c01fecee28bd1e79`.

At runtime each is fitted into its box as it is, by day and by night.
