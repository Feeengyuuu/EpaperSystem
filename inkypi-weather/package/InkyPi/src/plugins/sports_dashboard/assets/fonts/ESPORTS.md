# Optional private esports type

The right esports sidebar uses optional fonts in `INKYPI_DATA_DIR/fonts`.
The original files are device-owned data and are excluded from source archives
and Git. Their absence, invalid bytes or conflicting formats retains the normal
UI font; unknown event identities retain the game's body face when available.

These are references to the actual official game/organizer websites verified
on 2026-10-27. They are not a claim of an event-exclusive or future-edition
broadcast typeface. Existing official logos remain images. English and numeric
runs use these faces; unsupported original glyphs use the existing Chinese UI
font on a common baseline.

| Scope | Typeface and use | First-party reference |
| --- | --- | --- |
| CS2 base | Stratum2 Medium/Bold: teams, scores and times | https://www.counter-strike.net/cs2 |
| Dota 2 / TI | Radiance Semibold/Bold: information and scores | https://www.dota2.com/home |
| LPL, LCK, MSI, Worlds | Riot Sans headings; Inter body and scores | https://lolesports.com/en-US/ |
| BLAST | Radion A Black Italic headings; TT Norms Pro body | https://blast.tv/ |
| ESL / IEM | ESL Legendary / ESL Legend event caption | https://pro.eslgaming.com/tour/cs/ and https://intelextrememasters.com/ |
| PGL | DIN Pro event caption; CS2 base scores | https://www.pglesports.com/cs2/pgl-masters-bucharest-2026/ |
| Tesla Cup | Anek Latin event caption | https://relog.gg/tesla-cup-2026 |
| StarLadder, StarSeries, Stake Ranked, Thunderpick | Roboto event caption | https://starladder.com/en/ and https://world-championship.thunderpick.io/ |
| CAC / Perfect World Shanghai | Industry event caption; 2024 Shanghai archive reference only | https://cac.pwesports.cn/ and https://shmajor2024.pwesports.cn/ |
| Esports Nations / World Cup | Gear event caption | https://esportsnationscup.com/en and https://resources.esportsworldcup.com/en/brand |
| CCT | IBM Plex Sans event caption for small-size clarity | https://cctour.gg/ |
| Dust2.dk POWER Ligaen | Blinker event caption | https://liga.dust2.dk/om |
| European Pro League, Svenska Elitserien, GOEXANIMO | Montserrat Bold event caption | https://eplesports.com/, https://secsgo.se/, https://goexanimo.com/projects/ |

LPL and LCK have documented custom brand faces, but those files were not obtained.
Their game-level Riot Sans/Inter selection is an explicit fallback. ESEA is not
silently assigned ESL fonts. Other unverified organizers do not receive guessed
fonts from sponsors, social networks or unrelated event pages.

The fixed private names are enumerated in `esports_fonts.PRIVATE_KEYS` and in the
installer allowlist. Installation reuses the tracked release toolkit's
`private-fonts` step with a verified original file, after a successful source
release. No font is fetched by the renderer and no public redistributability is
implied by its availability on a website.
