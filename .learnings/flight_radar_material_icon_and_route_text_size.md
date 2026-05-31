2026-05-31

For FlightRadar/SkyRadar, use a proven 24px vector source for aircraft map
markers instead of hand-drawn arrows. The current marker is adapted from Google
Material Design Icons `flight`, rendered with paper halo, black outline, and
warm fill. Keep right-card route/city labels at 13px bold Chinese font and
high-contrast ink; the earlier 11px muted text was too small on the e-paper UI.
Route label cleaning must preserve CJK characters because preview-only Chinese
labels can otherwise disappear and fall back to aircraft identity text.
