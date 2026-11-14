# Tennis event and venue identities

The tournament mark and the venue mark are separate identities. `tennis_events.py`
selects an event within its tour; `tennis.py` falls back to a Grand Slam or tour-tier
mark when no verified event artwork is bundled. A tier tag is not the event logo.
Explicit Challenger, ITF and WTA 125 names never borrow a same-city tour-level mark.

The 2026-11-14 audit adds 20 independent WTA event marks: Hobart, Abu Dhabi,
Doha, Charleston, Linz, Stuttgart, Rouen, Strasbourg, Nottingham, Berlin, Hamburg,
the National Bank Open, Monterrey, Guadalajara, Seoul, Wuhan, Osaka, Tokyo,
Hong Kong and Chennai. Each PNG has an official source URL, source and output
SHA-256, processing notes and ownership information in `logos.source.json`.
No artwork is generated or redrawn. These are identification assets; their
inclusion does not imply an open trademark licence.

Doha's available official artwork explicitly prints 2026. It is selected only
when the provider establishes that edition (start date first, then year);
unknown or different years use the WTA tier mark. Other marks in this batch
are not dated to a current edition. Revisit sources when sponsors change.

The National Bank Open alternates Toronto and Montreal between tours. Its
city-neutral event mark never establishes the host. Explicit provider location
selects Sobeys Stadium or IGA Stadium; conflicting or missing cities select
neither. Both venue assets include their STADIUM / STADE identification.

Fourteen verified venue marks were also added: Domain Tennis Centre, Zayed
Sports City, Credit One Stadium, Design Center Linz, Porsche Arena, Kindarena,
Tennis Club de Strasbourg, Nottingham Tennis Centre, LTTC Rot-Weiß Berlin,
Sobeys Stadium, IGA Stadium, Club Sonoma, Seoul Olympic Park and Optics Valley
Tennis Center. Named clubs and complexes match only their own venue identity;
operator and sponsor marks are not substitutes for venue marks.

For venues without verified suitable artwork, the page prints a generic court
pictogram and a short Chinese venue label from `tennis_places.py`. In particular,
Hong Kong's Victoria Park uses “维园网球场”; the Prudential Hong Kong Tennis Open
mark remains the event identity. No independent Victoria Park venue logo was
verified in this audit. The unrelated Australian municipality mark is not used.
Khalifa, Am Rothenbaum, Panamerican, Utsubo, Ariake and SDAT also use this fallback.
This means unverified, not that no official logo exists. Unknown provider venue
names are kept intact rather than assigned a guessed translation or identity.

All assets are checked at the production header sizes (event 66×20, venue 72×20)
in both themes. Opaque boxed marks preserve their own backgrounds. Transparent
marks adapt faint lettering on the night theme without mutating shared image
caches. The Chinese labels are editorial display labels, not official branding.
