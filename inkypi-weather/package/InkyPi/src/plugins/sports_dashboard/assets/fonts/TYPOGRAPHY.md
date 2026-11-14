# Sports typography

Sports have separate **display**, **body**, and **score** roles. These choices
reference official websites where verified; they do not claim to reproduce
every broadcaster's graphics. A supported private face is not proof that it is
installed on a particular device.

## Rendering contract

`sport_type.py` scopes text to a standalone panel, or to one game on a mixed live
wall. It never changes the application's base font. The esports scope has
priority and retains its existing game/organizer choices (`ESPORTS.md`). Both
scopes restore the caller after normal returns and exceptions. UFC retains its
separate, already fitted `ufc_font` path (`UFC.md`).

For text covered here, Noto Sans SC uses weight 500 for ordinary Chinese and 600
for emphasized Chinese. Latin and CJK share a baseline. Brand display faces are
used for bold labels at 14px or larger; dense body labels use the text face below.
The smaller text face is not forced to look like a league logo. Existing local
wordmarks remain images.

`score_fonts.py` continues to own the score and stat roles, including digit-height
matching and complete numeric fitting. Scores and timing values are never
ellipsized. Body text may use the existing bounded name/description fitting.
Optional brand fonts remain original private files under `INKYPI_DATA_DIR/fonts`
and do not ship in Git or release archives. Missing/invalid files use the stated
fallbacks. No new private font or dependency is required for the text changes.

Font objects use the existing weak `render_font_scope`; only bounded loader
functions and numeric tennis measurements are cached by the new helpers.
`utils.fonts` still owns its existing shared glyph facts and system-font probes.

## Maintained mapping

| Sport | Supported private display/score | Body and Chinese | Missing private face |
| --- | --- | --- | --- |
| NBA | Knockout 49 Liteweight, `sports-nba-score.*` | Inter SemiBold/Bold + Noto SC | Barlow Condensed score/display |
| WNBA | WNBA Condensed Bold, `sports-wnba-score.*` | Inter SemiBold/Bold + Noto SC | Barlow Condensed score/display |
| NFL, NCAA | None registered as official | Barlow SemiCondensed SemiBold/Bold + Noto SC | Barlow SemiCondensed score; sport-style substitute |
| NHL | None registered as official | Barlow SemiCondensed text + Noto SC | Barlow Condensed score; sport-style substitute |
| MLB | None registered as official | Inter SemiBold/Bold + Noto SC | Barlow SemiCondensed score; sport-style substitute |
| PGA | None registered as official | Inter SemiBold/Bold + Noto SC, including restrained large labels | Barlow SemiCondensed score; sport-style substitute |
| F1 | Formula1 Display Bold, `sports-f1-score.*`; existing precise metric fitting retained | Inter SemiBold/Bold + Noto SC for metadata and Chinese | Existing base UI score; no unrelated branded substitute |
| MotoGP | MGP Display Bold + MGP Text Bold, separate existing paths | MGP Text for rider names/gaps; Noto SC for Chinese and units | Existing Barlow SemiCondensed metric fallback |
| ATP | Creato Display Medium/Bold, `tennis-atp-{medium,bold}.ttf` | Existing tour face for names, times, rounds and set scores + Noto SC | Noto SC; no claim of another official tour font |
| WTA | General Sans Medium/Semibold, `tennis-wta-{medium,semibold}.ttf` | Existing tour face for names, times, rounds and set scores + Noto SC | Noto SC |
| UFC | UFC Sans Condensed Medium/Bold | Existing UFC Sans/CJK pairing; unchanged by sport text scope | Existing UFC panel UI fallback |
| Football PL | Premier League Bold, `sports-epl-score.*` | Inter + Noto SC; private face for large bold display | Barlow SemiCondensed display/score |
| Football PD | LALIGA Headline Std Bold, `sports-laliga-score.*` | Inter + Noto SC; Headline is not used as small body text | Barlow SemiCondensed display/score |
| Football BL1 | DFL Web Bold, `sports-bundesliga-score.*` | Inter + Noto SC; private face for large bold display | Barlow SemiCondensed display/score |
| Football MLS | MLS Tifo Bold, `sports-mls-score.*` | Inter + Noto SC; available Bold is not assumed to be the Micro cut | Barlow SemiCondensed display/score |
| Football SA | Bai Jamjuree Bold, `sports-seriea-score.*` | Inter + Noto SC | Barlow SemiCondensed; Bai Jamjuree is a style/website reference, not a verified league-wide brand claim |
| Football FL1 | Existing Insatiable Display Bold Condensed, `sports-ligue1-score.*` | Inter + Noto SC; Display is kept out of small body | Barlow SemiCondensed; manifest identity remains `unverified` pending exact file/cut verification |
| World Cup, CSL, unknown football | No private league face selected | Inter + Noto SC | Barlow SemiCondensed display/score |

Football identity comes from normalized provider `league_code`, or a club
presentation's league code. Team names and arbitrary display labels never select
a brand. Mixed walls select each game's sport, including feature, tile and row
layouts. NCAA's ranked-matchup main score uses the same complete-score route as
its scoreboard rows.

Tennis uses the tightest nominal size with the required visible `8H` height,
without shrinking the original capitals. This accounts for hinting plateaus:
General Sans at adjacent nominal sizes can have the same visible height but
different advances. CJK stays at the requested size; no artificial negative
tracking or horizontal distortion is applied. The pre-match clock is measured
and drawn in the same tour face, preserving its minutes.

## Evidence and future optional faces

Checked 2026-11-14. Current website CSS paths may change when websites deploy.

- [NBA Stats](https://www.nba.com/stats) uses Knockout display families alongside
  Roboto/Roboto Condensed text. This does not establish the same Knockout cut for
  every label. Inter is the already bundled open body substitute here.
- [NFL All-Pro Sans](https://positype.com/custom/all-pro-sans) is a verified NFL
  digital typeface, designed for screen readability. It is not installed or
  registered by this change; Barlow is explicitly a substitute.
- [F1's font definitions](https://www.formula1.com/s/fonts.css) and
  [official guidelines](https://www.formula1.com/en/information/guidelines.4EOKE9RRqevL4niTK9kWyt)
  distinguish Formula1 faces from Titillium Web. Titillium is a possible future
  body choice, not an implied additional asset in this release.
- [WTA CSS](https://www.wtatennis.com/resources/v7.55.11/styles/screen.css)
  declares General Sans and Marsden Slim. The existing General Sans is retained
  for readable names. Its [Fontshare license](https://www.fontshare.com/fonts/general-sans)
  is not described as OFL. ATP's existing Creato mapping is retained; this audit
  could not independently read the current ATP website (403).
- [Premier Sans](https://enterprise.monotype.com/resources/case-studies/premier-league-a-brand-identity-that-works-hard-plays-hard)
  supports compact data/long names; football shirt lettering is a separate use.
- [LALIGA's designer](https://arillatype.studio/laliga-custom-brand-font)
  distinguishes Headline, Text, and Players. Text is a future private option;
  the existing Headline should not replace every small label.
- [MLS Tifo](https://f37.com/custom-fonts/major-league-soccer) has Headline,
  Standard and Micro optical roles. Only the already supported private Bold is
  assumed here.
- [DFL Sans](https://dev.rosettatype.com/services/Branding-fonts-for-DFL-Bundesliga)
  was designed with open counters for medium and small text.
- [Ligue 1's designer](https://www.elliottamblard.com/Ligue-1-Custom-type)
  distinguishes Display, TV and Jersey. TV is the better future small-data cut;
  this does not by itself validate the installed Display file's metadata.

Bundled Barlow sources, licenses and hashes remain in `sources.json`. Inter is
reused from `plugins/flight_radar/assets/fonts`; Noto Sans SC is reused from
`static/fonts`. These are open font choices, not relabeled official brands.
