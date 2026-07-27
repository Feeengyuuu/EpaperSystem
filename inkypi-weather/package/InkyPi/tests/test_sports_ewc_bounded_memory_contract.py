import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import plugins.sports_dashboard.sports_dashboard as sports_dashboard_module
from plugins.sports_dashboard.common import (
    EWC_DETAIL_LOW_MEMORY_SCAN_MAX_BYTES,
    EWC_DETAIL_LOW_MEMORY_VALUE_MAX_BYTES,
)
from plugins.sports_dashboard.sports_dashboard import SportsDashboard


def _initial_structures(*, padding=""):
    return [
        {
            "phase": {
                "id": "phase-groups",
                "name": "Group Stage",
                "slug": "group-stage",
            },
            "groups": [
                {
                    "id": "group-a",
                    "name": "Group A",
                    "slug": "group-a",
                }
            ],
            "series": [
                {
                    "id": "series-streamed-detail",
                    "phase_id": "phase-groups",
                    "state": "SCHEDULED",
                    "scheduled_start": "2026-07-27T20:00:00Z",
                    "format": {"type": "BEST_OF", "best_of": 3},
                    "structure": {
                        "kind": "BRACKET_SERIES",
                        "group_ids": ["group-a"],
                        "label": "Group A - Opening Match",
                    },
                    "slots": [
                        {
                            "slot": 1,
                            "competitor": {
                                "club": {"id": "club-t1", "name": "T1"},
                                "team": {"id": "team-t1", "name": "T1"},
                            },
                        },
                        {
                            "slot": 2,
                            "competitor": {
                                "club": {
                                    "id": "club-g2",
                                    "name": "G2 Esports",
                                },
                                "team": {
                                    "id": "team-g2",
                                    "name": "G2 Esports",
                                },
                            },
                        },
                    ],
                    "games": [],
                    "streams": [],
                }
            ],
            "state_machine_probe": 'quote " slash \\ bracket ] nested [[',
            "padding": padding,
        }
    ]


def _rsc_value(*, padding=""):
    return json.dumps(
        _initial_structures(padding=padding),
        separators=(",", ":"),
    ).encode("utf-8")


class _ChunkedResponse:
    encoding = "utf-8"

    def __init__(self, chunks, *, declared_length=None):
        self._chunks = list(chunks)
        self.headers = (
            {}
            if declared_length is None
            else {"Content-Length": str(declared_length)}
        )
        self.closed = 0
        self.yielded_chunks = 0
        self.yielded_bytes = 0
        self.requested_chunk_size = None

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        self.requested_chunk_size = chunk_size
        for chunk in self._chunks:
            self.yielded_chunks += 1
            self.yielded_bytes += len(chunk)
            yield chunk

    def close(self):
        self.closed += 1


class _Session:
    def __init__(self, response):
        self.response = response

    def get(self, _url, *, headers, timeout, stream):
        assert headers["Accept"] == "text/x-component"
        assert headers["RSC"] == "1"
        assert timeout == 25
        assert stream is True
        return self.response


def _event():
    return {
        "slug": "league-of-legends",
        "game": "League of Legends",
        "year": "2026",
        "source_url": (
            "https://esportsworldcup.com/en/competitions/2026/"
            "league-of-legends"
        ),
    }


def _fetch_detail(plugin):
    return plugin._fetch_ewc_detail_page(
        _event(),
        ZoneInfo("America/Los_Angeles"),
        datetime(2026, 7, 27, tzinfo=timezone.utc),
        use_rsc_initial_structures=True,
        max_value_bytes=EWC_DETAIL_LOW_MEMORY_VALUE_MAX_BYTES,
    )


def test_large_rsc_response_stops_after_initial_structures_value(monkeypatch):
    marker = b'0:{"initialStructures":'
    target = marker + _rsc_value()
    assert len(_rsc_value()) < EWC_DETAIL_LOW_MEMORY_VALUE_MAX_BYTES

    split_at = marker.find(b"initialStructures") + 5
    unread_tail = b"x" * (EWC_DETAIL_LOW_MEMORY_SCAN_MAX_BYTES + 1)
    chunks = [target[:split_at], target[split_at:], unread_tail]
    total_response_bytes = sum(len(chunk) for chunk in chunks)
    assert total_response_bytes > EWC_DETAIL_LOW_MEMORY_SCAN_MAX_BYTES

    response = _ChunkedResponse(chunks, declared_length=total_response_bytes)
    monkeypatch.setattr(
        sports_dashboard_module,
        "get_http_session",
        lambda: _Session(response),
    )

    page = _fetch_detail(SportsDashboard({"id": "sports_dashboard"}))

    assert [match["event_id"] for match in page["matches"]] == [
        "series-streamed-detail"
    ]
    assert response.yielded_chunks == 2
    assert response.yielded_bytes == len(target)
    assert response.closed == 1


def test_oversized_initial_structures_value_is_bounded_and_closed(monkeypatch):
    chunk_size = 32 * 1024
    oversized_value = _rsc_value(
        padding=(
            "x"
            * (
                EWC_DETAIL_LOW_MEMORY_VALUE_MAX_BYTES
                + (4 * chunk_size)
            )
        )
    )
    assert len(oversized_value) > EWC_DETAIL_LOW_MEMORY_VALUE_MAX_BYTES
    assert len(oversized_value) < EWC_DETAIL_LOW_MEMORY_SCAN_MAX_BYTES

    body = b'0:{"initialStructures":' + oversized_value
    chunks = [
        body[offset:offset + chunk_size]
        for offset in range(0, len(body), chunk_size)
    ]
    response = _ChunkedResponse(chunks)
    monkeypatch.setattr(
        sports_dashboard_module,
        "get_http_session",
        lambda: _Session(response),
    )

    with pytest.raises(Exception, match="value exceeds limit"):
        _fetch_detail(SportsDashboard({"id": "sports_dashboard"}))

    assert (
        response.yielded_bytes
        <= EWC_DETAIL_LOW_MEMORY_VALUE_MAX_BYTES + (2 * chunk_size)
    )
    assert response.yielded_chunks < len(chunks)
    assert response.closed == 1


def test_targetless_rsc_scan_is_bounded_and_response_is_closed(monkeypatch):
    chunk_size = 32 * 1024
    body = b"x" * (EWC_DETAIL_LOW_MEMORY_SCAN_MAX_BYTES + (4 * chunk_size))
    chunks = [
        body[offset:offset + chunk_size]
        for offset in range(0, len(body), chunk_size)
    ]
    response = _ChunkedResponse(chunks)
    monkeypatch.setattr(
        sports_dashboard_module,
        "get_http_session",
        lambda: _Session(response),
    )

    with pytest.raises(Exception, match="scan exceeds limit"):
        _fetch_detail(SportsDashboard({"id": "sports_dashboard"}))

    assert (
        response.yielded_bytes
        <= EWC_DETAIL_LOW_MEMORY_SCAN_MAX_BYTES + chunk_size
    )
    assert response.yielded_chunks < len(chunks)
    assert response.closed == 1
