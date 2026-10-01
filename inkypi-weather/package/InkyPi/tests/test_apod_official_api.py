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


@pytest.mark.parametrize("kind", ["video", "iframe"])
def test_non_image_records_keep_existing_video_fallback_contract(kind):
    record, _ = _fetch(_payload(media_type=kind, hdurl="https://www.youtube.com/embed/video"))
    assert record.media_type == "video"
    assert record.url is None and record.hdurl is None
    dates = []

    def prior(day):
        dates.append(day)
        return _fetch(_payload(date=day), requested_date=day)[0]

    selected, fallback = apod._resolve_image_record(requested=record, fetch_for_date=prior)
    assert fallback is True
    assert dates == ["2026-09-29"]
    assert selected.date == "2026-09-29"
    assert selected.requested_device_date == "2026-09-30"
    assert selected.warning


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
