# Sports Dashboard EWC LoL Priority and World Cup Pixel Strip Design

## Goal and scope

This change makes two narrow adjustments to the Sports Dashboard:

1. League of Legends takes display priority when EWC matches from multiple
   games are live at the same time.
2. The existing World Cup pixel-pitch strip fills the intentional gap between
   the `UPCOMING` and `RECENT` sections when that gap can contain it.

No other competition ordering, phase ordering, rotation cadence, match data,
or panel geometry changes.

## EWC live-game priority

The priority applies only to the EWC live phase. After collecting live EWC
matches, the selector identifies each match's normalized game group using the
existing EWC grouping rules.

- If more than one game group is live and one group is League of Legends, only
  the live LoL group participates in the current display choice.
- If two or more LoL matches are live, the existing rotation logic continues
  to rotate among those LoL matches.
- If only one game group is live, selection is unchanged.
- If multiple non-LoL game groups are live, their existing rotation is
  unchanged.
- A merely upcoming LoL match does not displace a currently live match from
  another game.
- Upcoming and recent EWC selection remains unchanged.

LoL recognition uses normalized game identifiers such as
`league-of-legends` and `lol`, rather than presentation text. The returned EWC
model continues to expose the full `all_live_matches`, `all_upcoming_matches`,
and `all_recent_matches` collections. The priority only narrows the group used
to choose what is rendered, so no source data is discarded.

## World Cup pixel-strip placement

The renderer reuses the existing 248x13 black-and-white pixel-art asset at
`assets/decor/worldcup_pitch_strip.png`. It is not replaced or regenerated.

When both `UPCOMING` and `RECENT` are visible, the renderer calculates the
unused vertical interval after the final upcoming row and before the fixed
recent-section boundary. If the original strip fits, it is horizontally and
vertically centered in that interval at its native size. Nearest-neighbor
rendering preserves the hard pixel edges.

The strip is decorative and must never displace sports information:

- upcoming rows keep their current coordinates and capacity;
- the recent section remains bottom-anchored at its current coordinate;
- the asset is not stretched or distorted;
- if the native strip cannot fit in the available gap, it is omitted;
- layouts without a recent section retain their existing pitch-strip
  behavior.

For the default 556x208 World Cup panel shown in the approved mockup, the
available gap is about 34 pixels high. The 248x13 strip therefore fits at its
native size and is centered in the right-column content area between the two
sections.

## Failure and compatibility behavior

The changes require no settings migration and no network or provider changes.
An unrecognized EWC game identifier follows the existing non-LoL path. A
missing or unreadable decorative asset follows the renderer's existing safe
fallback and does not prevent match data from rendering.

## Verification

Automated tests must prove:

- simultaneous live LoL and Dota/other EWC matches always select LoL across
  rotation seeds;
- multiple simultaneous LoL matches still rotate within the LoL group;
- the full live-match collection still contains every game;
- non-LoL live rotation and all upcoming/recent behavior remain unchanged;
- a live non-LoL match is not displaced by an upcoming LoL match;
- with one upcoming and one recent World Cup item, the strip is drawn strictly
  inside the computed gap at 248x13;
- upcoming and recent coordinates are unchanged by the decoration;
- insufficient space omits the strip without clipping text or rows;
- a rendered-image smoke test confirms the expected black-and-white pixel
  region.

After focused tests and the complete Sports Dashboard regression suite pass, a
clean release is deployed to the live device. Acceptance proof includes service
readiness, the active release identity, an EWC fixture showing LoL priority,
and the physical World Cup panel showing the pitch strip in the approved gap.
