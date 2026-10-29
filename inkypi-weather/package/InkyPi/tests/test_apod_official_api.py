"""Migration regressions for NASA's September 2026 official APOD endpoint."""

from dataclasses import replace
import hashlib
from types import SimpleNamespace

import pytest

from plugins.apod import apod
from runtime.refresh_contracts import TaskCancelled, TaskDeadlineExceeded
from utils.http_client import (
    HttpClientError, HttpDecodeError, HttpStatusError, ResponseTooLarge,
)


ARTICLE = (
    "https://science.nasa.gov/image-article/"
    "apod-2026-september-30-arp-78-peculiar-galaxy-in-aries/"
)
MEDIA = (
    "https://assets.science.nasa.gov/dynamicimage/assets/science/cds/apod/apod/"
    "2026/october/NGC772_Robert_Eder.jpg?w=1772&h=1182&fit=clip&crop=faces%2Cfocalpoint"
)


def _payload(**changes):
    # Field shape, date, title, credit and URLs captured from the real 260930 response.
    payload = {
        "date": "2026-09-30",
        "post_id": 1424625,
        "title": "Arp 78: Peculiar Galaxy in Aries",
        "media_type": "image",
        "permalink": ARTICLE,
        "url": ARTICLE,
        "hdurl": MEDIA,
        "explanation": "<strong>Explanation:</strong> <a href='https://example.test'>Peculiar</a> spiral galaxy.",
        "copyright": '<a href="https://www.astrobin.com/users/Robsi/">Robert Eder</a>',
        "basic_html": "<img src='https://untrusted.example.test/not-the-image.jpg'>",
    }
    payload.update(changes)
    return payload


class _Http:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def request_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return SimpleNamespace(data=self.outcome)


def _fetch(payload, *, requested_date="2026-09-30", context=None):
    http = _Http(payload)
    record = apod._fetch_apod_record(
        http=http, api_key="private-nasa-key", requested_date=requested_date,
        context=context,
    )
    return record, http


def test_real_wordpress_shape_uses_exact_date_and_hd_media_without_article_or_key():
    record, http = _fetch(_payload())

    assert record.date == "2026-09-30"  # Asset directory says October; record date wins.
    assert record.title_en == "Arp 78: Peculiar Galaxy in Aries"
    assert record.url is None
    assert record.hdurl == MEDIA
    assert record.copyright == "Robert Eder"
    assert record.explanation == "Explanation: Peculiar spiral galaxy."
    assert record.source_state == "live"
    assert http.calls == [("GET", f"{apod.APOD_ENDPOINT}/260930", {
        "context": None, "timeout": 20, "max_bytes": 512 * 1024,
        "allow_redirects": False,
    })]
    assert "private-nasa-key" not in str(http.calls)


def test_wordpress_text_removes_hidden_markup_preserves_entities_and_boundaries():
    record, _ = _fetch(_payload(
        title="<b>Star</b><i>&amp; Moon</i>",
        explanation="<p>A&nbsp;star</p><p>Next<br>line</p><script>secret()</script><style>hidden</style>",
        copyright=None,
        credit="<span>NASA</span><span>&amp; Team</span>",
    ))
    assert record.title_en == "Star & Moon"
    assert record.explanation == "A star Next line"
    assert record.copyright == "NASA & Team"


def test_text_limits_apply_to_visible_text_without_counting_html_link_attributes():
    record, _ = _fetch(_payload(copyright=f'<a href="https://example.test/{"x" * 800}">NASA</a>'))
    assert record.copyright == "NASA"
    with pytest.raises(RuntimeError, match=r"\[invalid_fields\]"):
        _fetch(_payload(copyright=f'<b>{"x" * 501}</b>'))


@pytest.mark.parametrize("changes, reason", [
    ({"date": "2026-09-29"}, "date_mismatch"),
    ({"date": "not-a-date"}, "invalid_date"),
    ({"hdurl": None}, "missing_safe_media"),
    ({"hdurl": ARTICLE}, "missing_safe_media"),
    ({"hdurl": f"{ARTICLE}?anything=1"}, "missing_safe_media"),
    ({"hdurl": "https://127.0.0.1/private.jpg"}, "missing_safe_media"),
    ({"hdurl": "https://assets.science.nasa.gov/image.jpg?api_key=secret"}, "missing_safe_media"),
])
def test_wrong_date_or_missing_trusted_image_is_not_an_admissible_record(changes, reason):
    with pytest.raises(RuntimeError, match=rf"\[{reason}\]"):
        _fetch(_payload(**changes))


def test_non_ascii_media_path_is_admitted_as_its_percent_encoded_uri():
    # 2026-10-14's real hdurl carries the photographer's "č" in the asset path.
    folder = "https://assets.science.nasa.gov/dynamicimage/assets/science/cds/apod/apod/2026/october/"
    query = "?w=1500&h=1000&fit=clip&crop=faces%2Cfocalpoint"
    iri = f"{folder}2026_10_10_PZ_Jesli{chr(0x10D)}ky_01_1500px.png{query}"
    record, _ = _fetch(_payload(hdurl=iri))
    assert record.hdurl == f"{folder}2026_10_10_PZ_Jesli%C4%8Dky_01_1500px.png{query}"
    assert apod._public_http_url(record.hdurl) == record.hdurl


@pytest.mark.parametrize("hdurl", [
    f"https://assets.science.nasa.gov/a{chr(0xA0)}b.jpg",  # non-ASCII whitespace
    f"https://assets.science.nasa.gov/a{chr(0x85)}b.jpg",  # C1 control
    f"https://assets.science.nasa.gov/a{chr(0x7F)}b.jpg",  # DEL
    f"https://a{chr(0x10D)}.nasa.gov/image.jpg",  # non-ASCII host
])
def test_non_ascii_whitespace_controls_and_hosts_stay_refused(hdurl):
    with pytest.raises(RuntimeError, match=r"\[missing_safe_media\]"):
        _fetch(_payload(hdurl=hdurl))


def test_current_day_without_trusted_media_is_returned_without_media_for_fallback():
    http = _Http(_payload(hdurl="https://127.0.0.1/private.jpg"))
    record = apod._fetch_apod_record(
        http=http, api_key="private-nasa-key", requested_date="2026-09-30",
        context=None, allow_missing_media=True,
    )
    assert record.media_type == "image"
    assert record.url is None and record.hdurl is None
    assert record.title_en == "Arp 78: Peculiar Galaxy in Aries"


@pytest.mark.parametrize("kind", ["video", "iframe"])
def test_non_image_records_keep_existing_video_fallback_contract(kind):
    # Rendering falls back to the newest prior image (test_network_failure_regression).
    record, _ = _fetch(_payload(media_type=kind, hdurl="https://www.youtube.com/embed/video"))
    assert record.media_type == "video"
    assert record.url is None and record.hdurl is None


@pytest.mark.parametrize("error, reason", [
    (HttpStatusError("GET", "https://example.test/?api_key=secret", 404), "http_status_404"),
    (ResponseTooLarge(123), "response_too_large"),
    (HttpDecodeError("secret URL query"), "invalid_json"),
    (HttpClientError("secret URL query"), "transport_error"),
])
def test_errors_have_static_category_without_leaking_exception_or_secret(error, reason):
    with pytest.raises(RuntimeError) as raised:
        _fetch(error)
    assert str(raised.value) == f"NASA APOD request failed for 2026-09-30 [{reason}]"


@pytest.mark.parametrize("error_type", [TaskCancelled, TaskDeadlineExceeded])
def test_provider_cancellation_and_deadline_propagate_unchanged(error_type):
    error = error_type("bounded task ended")
    with pytest.raises(error_type) as raised:
        _fetch(error)
    assert raised.value is error


@pytest.mark.parametrize("day, suffix", [
    ("1995-06-16", "950616"), ("2000-02-29", "000229"), ("2026-01-01", "260101"),
])
def test_custom_and_random_dates_use_the_official_yymmdd_path(day, suffix):
    record, http = _fetch(_payload(date=day), requested_date=day)
    assert record.date == day
    assert http.calls[0][1] == f"{apod.APOD_ENDPOINT}/{suffix}"


def test_same_short_date_from_wrong_century_is_rejected():
    with pytest.raises(RuntimeError, match=r"\[date_mismatch\]"):
        _fetch(_payload(date="2095-06-16"), requested_date="1995-06-16")


def test_cancellation_after_response_precedes_payload_normalization(monkeypatch):
    class Context:
        def __init__(self):
            self.checkpoints = 0

        def raise_if_cancelled(self):
            self.checkpoints += 1
            if self.checkpoints == 2:
                raise TaskCancelled("cancelled while receiving JSON")

    monkeypatch.setattr(apod, "normalize_official_payload", lambda _raw: pytest.fail("must not parse after cancellation"))
    with pytest.raises(TaskCancelled, match="while receiving"):
        _fetch(_payload(), context=Context())


@pytest.mark.parametrize("outcome", [
    _payload(date="2026-09-29"),
    HttpStatusError("GET", "https://science.nasa.gov/", 404),
    HttpDecodeError("not JSON"),
])
def test_failed_provider_does_not_replace_existing_state(tmp_path, outcome):
    state_path = tmp_path / "apod-state.json"
    previous = b'{"schema": 0, "previous-day-state": true}'
    state_path.write_bytes(previous)
    selection = apod.ApodSelection(
        device_day="2026-09-30", mode="today", requested_date="2026-09-30",
        fingerprint="a" * 64, resolved_record_date="2026-09-30",
    )
    with pytest.raises(RuntimeError, match="NASA APOD request failed"):
        apod._load_or_fetch_apod_state(
            http=_Http(outcome), api_key="private-key", selection=selection,
            paths=SimpleNamespace(cache=tmp_path), context=None,
        )
    assert state_path.read_bytes() == previous


def test_iframe_fallback_round_trips_through_existing_state_schema(tmp_path):
    selection = apod.ApodSelection(
        device_day="2026-09-30", mode="today", requested_date="2026-09-30",
        fingerprint="a" * 64, resolved_record_date="2026-09-30",
    )
    requested = apod._bind_record_to_selection(
        _fetch(_payload(media_type="iframe", hdurl=None))[0], selection=selection,
    )
    displayed = replace(
        _fetch(_payload(date="2026-09-29"), requested_date="2026-09-29")[0],
        selection_key=selection.fingerprint, requested_device_date=selection.device_day,
        image_url=MEDIA, image_cache_key=hashlib.sha256(MEDIA.encode()).hexdigest(),
        warning=apod._fallback_warning("2026-09-29"),
    )
    state = apod.ApodDisplayState(
        selection_fingerprint=selection.fingerprint, device_day=selection.device_day,
        requested_date="2026-09-30", requested_record=requested,
        display_record=displayed, fallback_reason="video", provisional_media=False,
    )
    apod._persist_apod_state(SimpleNamespace(cache=tmp_path), state)
    loaded = apod._read_apod_state(tmp_path / "apod-state.json", selection=selection)
    assert loaded is not None
    assert loaded.requested_record.media_type == "video"
    assert loaded.display_record.date == "2026-09-29"


def test_a_video_day_with_a_still_frame_is_shown_by_that_still():
    # 2026-10-28: "The Mutual Occultation of Ganymede and Io", a video whose hdurl is its still.
    still = ("https://assets.science.nasa.gov/dynamicimage/assets/science/cds/apod/apod/"
             "2026/october/occultation.jpeg?w=1600")
    record, _ = _fetch(_payload(media_type="video", hdurl=still))
    assert record.media_type == "image" and record.video_still is True
    assert record.hdurl == still and record.warning is None
    # It survives the state cache, and a plain image is never marked as a still.
    assert apod._record_from_document(apod._record_document(record)).video_still is True
    plain, _ = _fetch(_payload())
    assert plain.video_still is False
    assert apod._record_from_document(apod._record_document(plain)).video_still is False
    # A still named by the clip itself is not a picture: the day keeps the video fallback.
    clip = still.replace("occultation.jpeg?w=1600", "occultation.mp4")
    record, _ = _fetch(_payload(media_type="video", hdurl=clip))
    assert record.media_type == "video" and record.video_still is False


def test_the_page_marks_a_video_still_in_its_kicker():
    from PIL import Image, ImageDraw

    from plugins.apod import apod_page

    record, _ = _fetch(_payload(media_type="video", hdurl=(
        "https://assets.science.nasa.gov/dynamicimage/assets/science/cds/apod/apod/2026/october/occultation.jpeg")))
    draw = apod_page.MixedDraw(ImageDraw.Draw(Image.new("RGB", (1, 1))))
    layout = apod_page._layout_caption(
        draw=draw, title_en=record.title_en, title_zh="木卫三与木卫一的互掩", translation_unavailable=False,
        copyright=record.copyright, apod_date=record.date,
        warning=record.warning or (apod_page.VIDEO_STILL_KICKER if record.video_still else None))
    assert layout.kicker_copy == apod_page.VIDEO_STILL_KICKER
