"""True lunar phases, offline.

The instants of new moon, first quarter, full moon and last quarter follow Meeus,
*Astronomical Algorithms* (2nd ed.), chapter 49: the mean phase plus the periodic
terms for the Sun's and Moon's anomalies, the Moon's argument of latitude, the
ascending node and the fourteen planetary arguments. Against USNO the results agree
to well under a minute for the present era. The Moon's phase angle (and so its lit
fraction) uses the short series of equation 48.4.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

SYNODIC_MONTH_DAYS = 29.530588861
UNIX_EPOCH_JD = 2440587.5
J2000_JD = 2451545.0
# Mean new moon of 2000 January 6 (k = 0), in Julian Ephemeris Days.
K0_JDE = 2451550.09766

NEW_MOON = 0.0
FIRST_QUARTER = 0.25
FULL_MOON = 0.5
LAST_QUARTER = 0.75
PRINCIPAL_PHASES = (NEW_MOON, FIRST_QUARTER, FULL_MOON, LAST_QUARTER)

_NEW_TERMS = (
    (-0.40720, 0, 0, 1, 0, 0), (0.17241, 1, 1, 0, 0, 0), (0.01608, 0, 0, 2, 0, 0),
    (0.01039, 0, 0, 0, 2, 0), (0.00739, 1, -1, 1, 0, 0), (-0.00514, 1, 1, 1, 0, 0),
    (0.00208, 2, 2, 0, 0, 0), (-0.00111, 0, 0, 1, -2, 0), (-0.00057, 0, 0, 1, 2, 0),
    (0.00056, 1, 1, 2, 0, 0), (-0.00042, 0, 0, 3, 0, 0), (0.00042, 1, 1, 0, 2, 0),
    (0.00038, 1, 1, 0, -2, 0), (-0.00024, 1, -1, 2, 0, 0), (-0.00017, 0, 0, 0, 0, 1),
    (-0.00007, 0, 2, 1, 0, 0), (0.00004, 0, 0, 2, -2, 0), (0.00004, 0, 3, 0, 0, 0),
    (0.00003, 0, 1, 1, -2, 0), (0.00003, 0, 0, 2, 2, 0), (-0.00003, 0, 1, 1, 2, 0),
    (0.00003, 0, -1, 1, 2, 0), (-0.00002, 0, -1, 1, -2, 0), (-0.00002, 0, 1, 3, 0, 0),
    (0.00002, 0, 0, 4, 0, 0),
)
_FULL_TERMS = (
    (-0.40614, 0, 0, 1, 0, 0), (0.17302, 1, 1, 0, 0, 0), (0.01614, 0, 0, 2, 0, 0),
    (0.01043, 0, 0, 0, 2, 0), (0.00734, 1, -1, 1, 0, 0), (-0.00515, 1, 1, 1, 0, 0),
    (0.00209, 2, 2, 0, 0, 0), (-0.00111, 0, 0, 1, -2, 0), (-0.00057, 0, 0, 1, 2, 0),
    (0.00056, 1, 1, 2, 0, 0), (-0.00042, 0, 0, 3, 0, 0), (0.00042, 1, 1, 0, 2, 0),
    (0.00038, 1, 1, 0, -2, 0), (-0.00024, 1, -1, 2, 0, 0), (-0.00017, 0, 0, 0, 0, 1),
    (-0.00007, 0, 2, 1, 0, 0), (0.00004, 0, 0, 2, -2, 0), (0.00004, 0, 3, 0, 0, 0),
    (0.00003, 0, 1, 1, -2, 0), (0.00003, 0, 0, 2, 2, 0), (-0.00003, 0, 1, 1, 2, 0),
    (0.00003, 0, -1, 1, 2, 0), (-0.00002, 0, -1, 1, -2, 0), (-0.00002, 0, 1, 3, 0, 0),
    (0.00002, 0, 0, 4, 0, 0),
)
_QUARTER_TERMS = (
    (-0.62801, 0, 0, 1, 0, 0), (0.17172, 1, 1, 0, 0, 0), (-0.01183, 1, 1, 1, 0, 0),
    (0.00862, 0, 0, 2, 0, 0), (0.00804, 0, 0, 0, 2, 0), (0.00454, 1, -1, 1, 0, 0),
    (0.00204, 2, 2, 0, 0, 0), (-0.00180, 0, 0, 1, -2, 0), (-0.00070, 0, 0, 1, 2, 0),
    (-0.00040, 0, 0, 3, 0, 0), (-0.00034, 1, -1, 2, 0, 0), (0.00032, 1, 1, 0, 2, 0),
    (0.00032, 1, 1, 0, -2, 0), (-0.00028, 2, 2, 1, 0, 0), (0.00027, 1, 1, 2, 0, 0),
    (-0.00017, 0, 0, 0, 0, 1), (-0.00005, 0, -1, 1, -2, 0), (0.00004, 0, 0, 2, 2, 0),
    (-0.00004, 0, 1, 1, 2, 0), (0.00004, 0, -2, 1, 0, 0), (0.00003, 0, 1, 1, -2, 0),
    (0.00003, 0, 3, 0, 0, 0), (0.00002, 0, 0, 2, -2, 0), (0.00002, 0, -1, 1, 2, 0),
    (-0.00002, 0, 1, 3, 0, 0),
)
# (coefficient, constant, rate per lunation k, rate per T^2) of the planetary arguments A1..A14.
_PLANETARY = (
    (0.000325, 299.77, 0.107408, -0.009173), (0.000165, 251.88, 0.016321, 0.0),
    (0.000164, 251.83, 26.651886, 0.0), (0.000126, 349.42, 36.412478, 0.0),
    (0.000110, 84.66, 18.206239, 0.0), (0.000062, 141.74, 53.303771, 0.0),
    (0.000060, 207.14, 2.453732, 0.0), (0.000056, 154.84, 7.306860, 0.0),
    (0.000047, 34.52, 27.261239, 0.0), (0.000042, 207.19, 0.121824, 0.0),
    (0.000040, 291.34, 1.844379, 0.0), (0.000037, 161.72, 24.198154, 0.0),
    (0.000035, 239.56, 25.513099, 0.0), (0.000023, 331.55, 3.592518, 0.0),
)


def julian_day(moment: datetime) -> float:
    return UNIX_EPOCH_JD + _as_utc(moment).timestamp() / 86400.0


def from_julian_day(jd: float) -> datetime:
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(days=jd - UNIX_EPOCH_JD)


def delta_t_seconds(year: float) -> float:
    """TT - UT. Espenak & Meeus polynomials around the present; seconds matter little here."""
    if 2005 <= year < 2050:
        t = year - 2000
        return 62.92 + 0.32217 * t + 0.005589 * t * t
    if 1986 <= year < 2005:
        t = year - 2000
        return 63.86 + 0.3345 * t - 0.060374 * t ** 2 + 0.0017275 * t ** 3 + 0.000651814 * t ** 4
    u = (year - 1820) / 100
    return -20 + 32 * u * u


def phase_jde(k: float) -> float:
    """Julian Ephemeris Day of the true phase numbered ``k`` (an integer plus 0, .25, .5 or .75)."""
    fraction = round((k - math.floor(k)) * 4) / 4
    t = k / 1236.85
    jde = (K0_JDE + SYNODIC_MONTH_DAYS * k + 0.00015437 * t ** 2
           - 0.000000150 * t ** 3 + 0.00000000073 * t ** 4)
    e = 1 - 0.002516 * t - 0.0000074 * t ** 2
    m = math.radians(2.5534 + 29.10535670 * k - 0.0000014 * t ** 2 - 0.00000011 * t ** 3)
    mp = math.radians(201.5643 + 385.81693528 * k + 0.0107582 * t ** 2
                      + 0.00001238 * t ** 3 - 0.000000058 * t ** 4)
    f = math.radians(160.7108 + 390.67050284 * k - 0.0016118 * t ** 2
                     - 0.00000227 * t ** 3 + 0.000000011 * t ** 4)
    node = math.radians(124.7746 - 1.56375588 * k + 0.0020672 * t ** 2 + 0.00000215 * t ** 3)
    terms = {NEW_MOON: _NEW_TERMS, FULL_MOON: _FULL_TERMS}.get(fraction, _QUARTER_TERMS)
    for coefficient, e_power, m_mult, mp_mult, f_mult, node_mult in terms:
        argument = m_mult * m + mp_mult * mp + f_mult * f + node_mult * node
        jde += coefficient * e ** e_power * math.sin(argument)
    if fraction in (FIRST_QUARTER, LAST_QUARTER):
        w = (0.00306 - 0.00038 * e * math.cos(m) + 0.00026 * math.cos(mp)
             - 0.00002 * math.cos(mp - m) + 0.00002 * math.cos(mp + m) + 0.00002 * math.cos(2 * f))
        jde += w if fraction == FIRST_QUARTER else -w
    for coefficient, constant, rate, quadratic in _PLANETARY:
        jde += coefficient * math.sin(math.radians(constant + rate * k + quadratic * t ** 2))
    return jde


def phase_time(k: float) -> datetime:
    """The true phase ``k`` as a UTC datetime (Universal Time)."""
    jde = phase_jde(k)
    year = 2000 + (jde - J2000_JD) / 365.25
    return from_julian_day(jde - delta_t_seconds(year) / 86400.0)


def principal_phases_around(moment: datetime, before: int = 1, after: int = 1):
    """[(utc, fraction), ...] for every principal phase from ``before`` lunations back to ``after`` ahead."""
    jd = julian_day(moment)
    k0 = math.floor((jd - K0_JDE) / SYNODIC_MONTH_DAYS)
    phases = []
    for lunation in range(k0 - before, k0 + after + 1):
        for fraction in PRINCIPAL_PHASES:
            phases.append((phase_time(lunation + fraction), fraction))
    phases.sort(key=lambda item: item[0])
    return phases


def next_phase(moment: datetime, fraction: float) -> datetime:
    """The first true phase of the given kind strictly after ``moment``."""
    moment = _as_utc(moment)
    for when, kind in principal_phases_around(moment, before=0, after=2):
        if kind == fraction and when > moment:
            return when
    raise ValueError("no phase found")  # pragma: no cover - two lunations always contain one


def bracketing_phases(moment: datetime):
    """((utc, fraction) at or before ``moment``, (utc, fraction) after it)."""
    moment = _as_utc(moment)
    phases = principal_phases_around(moment)
    previous = max((item for item in phases if item[0] <= moment), key=lambda item: item[0])
    following = min((item for item in phases if item[0] > moment), key=lambda item: item[0])
    return previous, following


def phase_angle_degrees(moment: datetime) -> float:
    """The Moon's phase angle i (0 at full, 180 at new), Meeus eq. 48.4."""
    jde = julian_day(moment) + delta_t_seconds(_as_utc(moment).year) / 86400.0
    t = (jde - J2000_JD) / 36525.0
    d = math.radians(297.8501921 + 445267.1114034 * t - 0.0018819 * t ** 2
                     + t ** 3 / 545868 - t ** 4 / 113065000)
    m = math.radians(357.5291092 + 35999.0502909 * t - 0.0001536 * t ** 2 + t ** 3 / 24490000)
    mp = math.radians(134.9633964 + 477198.8675055 * t + 0.0087414 * t ** 2
                      + t ** 3 / 69699 - t ** 4 / 14712000)
    angle = (180 - math.degrees(d) - 6.289 * math.sin(mp) + 2.100 * math.sin(m)
             - 1.274 * math.sin(2 * d - mp) - 0.658 * math.sin(2 * d)
             - 0.214 * math.sin(2 * mp) - 0.110 * math.sin(d))
    return angle % 360.0


def elongation_fraction(moment: datetime) -> float:
    """Where the Moon is in its cycle, 0 at new, 0.25 first quarter, 0.5 full, 0.75 last quarter."""
    return ((180.0 - phase_angle_degrees(moment)) % 360.0) / 360.0


def illuminated_fraction(moment: datetime) -> float:
    return (1.0 + math.cos(math.radians(phase_angle_degrees(moment)))) / 2.0


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
