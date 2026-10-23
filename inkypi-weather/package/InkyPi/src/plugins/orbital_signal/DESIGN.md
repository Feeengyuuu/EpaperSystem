# Orbital Signal img-2 design record

## UI mockup prompt

```text
Use case: ui-mockup
Primary request: Create a production-ready landscape UI mockup for an 800x480 color e-paper information display that combines a global rocket launch countdown and a prediction-market thermometer on one page.
Asset type: single full-screen 800x480 dashboard screenshot, straight-on, edge-to-edge, no device frame.
Style/medium: realistic product UI, editorial mission-control dashboard, practical implementation reference rather than concept art. Optimized for a 7-color e-paper display: flat solid colors only, no gradients, no transparency effects, no shadows, no tiny light-gray text.
Palette: warm ivory paper background, near-black navy ink, vivid launch orange, cobalt blue, signal green, small restrained red accents. High contrast and thick dark rules. Use large bold geometric sans-serif typography.
Layout hierarchy: a compact full-width header about 54px high. Below it, left side about 55 percent width for launches, right side about 45 percent for market heat. Use a 2px dark vertical divider. Outer padding 16px.
Header exact text: "ORBITAL SIGNAL" as the main title. Small subtitle "GLOBAL LAUNCHES × CROWD ODDS". Right-aligned status chip "LIVE DATA" and small timestamp "17 JUL · 21:20 UTC".
Left launch area: label "NEXT LAUNCH" in a small orange tab. A very large countdown "T− 08H 40M". Main mission name "VIKRAM-I" and subtitle "DEMO FLIGHT". A green outlined status chip "GO". Metadata in compact bold rows: "18 JUL · 06:00 UTC", "SRIHARIKOTA · INDIA", "SKYROOT · LEO". Add a simple flat graphic of a slender rocket and orbital arc in orange/navy at the far left or behind a bounded visual column, without using any real company logo. Bottom section titled "UP NEXT" with two clean rows: "20 JUL  ·  FALCON 9  ·  STARLINK 17-39" and "20 JUL  ·  STARSHIP  ·  FLIGHT 13".
Right market area: title "MARKET HEAT" with a compact vertical thermometer icon and the explainer "VOLUME + 24H MOVE". Show three stacked market cards, each with a category tag, bold question, a thick probability bar, large percentage, and 24h delta. Card 1: tag "SPORT", question "WORLD CUP WINNER", leading outcome "SPAIN", "59%", delta "+0.8%", orange hot indicator. Card 2: tag "MATCH", question "FRANCE vs ENGLAND", leading outcome "FRANCE", "52%", delta "+2.0%", red hottest indicator. Card 3: tag "POLITICS", question "NEXT PRIME MINISTER", leading outcome "LEADING PICK", "41%", delta "−1.2%", blue warm indicator. Use distinct solid heat levels, not speculative charts.
Footer exact text: "SOURCES  THE SPACE DEVS · POLYMARKET" on the left and "CROWD PROBABILITY · NOT ADVICE" on the right.
Composition/framing: every element aligned to a strict grid, no overlaps, no clipped text, readable from several feet away, suitable as a direct coding blueprint.
Constraints: exact 5:3 aspect ratio; make the screenshot look fully populated but calm; practical flat UI; legible English text; no logos or trademarks; no watermark; no decorative outer background.
Avoid: photorealistic space scene, glossy sci-fi HUD, neon glow, gradients, 3D glass cards, illegible microtext, fake browser chrome, phone frame, device mockup, blurry or garbled typography.
```

Saved workspace copy: `.tmp/imagegen/orbital-signal-ui-mockup-img2.png`

## Plugin icon prompt

```text
Use case: icon-illustration
Primary request: Create a clean square app/plugin icon for a dashboard named Orbital Signal, combining a rocket launch countdown and a prediction-market thermometer.
Asset type: single centered square icon, no text, no letters, no numbers, no border outside the square.
Style/medium: flat vector-like editorial icon for a 7-color e-paper interface, minimal geometric shapes, crisp hard edges, no gradients, no shadows, no texture.
Composition: warm ivory background. A simple near-black navy rocket rising vertically through a vivid orange orbital arc. Integrate a small upright thermometer or signal gauge on the right side of the orbit, filled orange-red. Add one small cobalt blue probability bar segment near the bottom, keeping the rocket dominant. Strong silhouette and generous padding so it remains legible at 64x64.
Palette: warm ivory, near-black navy, launch orange, signal red, cobalt blue, a very small green accent.
Constraints: perfectly centered, practical plugin icon, high contrast, no logos or trademarks, no watermark, no text, no transparency needed.
Avoid: photorealism, detailed space background, planets, stars clutter, glow, neon, gradients, 3D, tiny lines, UI screenshot, device frame.
```

Saved plugin asset: `icon.png`

## Mission-control redesign (2026-10-23)

The owner picked direction A of three GPT Image 2.5 mockups (mission-control
timeline, poster hero, departure board), with the launch photo kept and more
real data. The page now has:

- Left: the launch photo column, a NEXT LAUNCH tab (plus WEBCAST LIVE when The
  Space Devs reports one), a split-flap countdown (hours and minutes, or days
  and hours beyond a day), the status badge, rocket, mission, and the launch
  time in UTC and local time with the launch site.
- A fact grid: WINDOW, PAD (short pad name and country), ORBIT, PROVIDER (with
  its launches this year), MISSION type, and WEATHER go-probability or, when
  there is no forecast, the world's orbital launch count this year.
- NEXT LAUNCHES: the next four launches as evenly spaced stops, each with its
  local time, rocket and mission, and the real time between stops written on
  the line, so launches minutes or days apart read alike.
- Right: MARKET HEAT as three gauge columns (question, category, 24h move,
  dial, leader, probability, 24h volume, end date, heat), and the generated
  `assets/market-console.png` strip in the room below them.
