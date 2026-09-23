"""Exercise the public esports renderer with official-schedule-shaped inputs."""

from datetime import datetime, timezone

from PIL import ImageDraw
import pytest

from plugins.base_plugin.render_provenance import SourceProvenance
import plugins.sports_dashboard.sports_dashboard as sports_module
from tests.test_sports_dashboard import FakeDeviceConfig, _plugin
from tests.test_sports_cs2_follow import FeedSession, cs_match


def render_schedule(monkeypatch, tmp_path, *, start="2026-09-19T09:00:00Z",
                    state="completed", wins=(1, 3), other_match=False):
    now = datetime(2026, 9, 23, 4, 0, tzinfo=timezone.utc)
    payload = {
        "data": {"schedule": {"events": [{
            "startTime": start,
            "state": state,
            "type": "match",
            "blockName": "Regional Qualifier",
            "league": {"name": "LPL", "slug": "lpl"},
            "match": {
                "id": "116957100120526836",
                "strategy": {"type": "bestOf", "count": 5},
                "teams": [
                    {"code": "JDG", "image": "", "result": {"gameWins": wins[0]}},
                    {"code": "IG", "image": "", "result": {"gameWins": wins[1]}},
                ],
            },
        }]}}
    }

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    class Session(FeedSession):
        def get(self, url, **kwargs):
            assert "getSchedule" in url
            return Response()

    match = cs_match(now, offset=6, team_a="fnatic", team_b="Phantom")
    match["league"]["image_url"] = ""
    session = Session({"upcoming": [match]})
    monkeypatch.setattr(sports_module, "get_http_session", lambda: session)
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path / "data"))
    drawn = []
    original_text = ImageDraw.ImageDraw.text

    def record_text(draw, xy, text, *args, **kwargs):
        drawn.append((xy, str(text)))
        return original_text(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record_text)
    device = FakeDeviceConfig(timezone="America/Los_Angeles")
    device.env["PANDASCORE_API_KEY"] = "test-token"
    image, provenance = _plugin().render_isolated_region(
        {
            "localTimezone": "America/Los_Angeles" if other_match else "UTC",
            "lplLiveEndpointEnabled": False,
            "lplOddsEnabled": False,
            "lckEnabled": False,
            "msiEnabled": False,
            "valveEsportsEnabled": other_match,
            "valveEsportsTiOfficialEnabled": False,
            "pandaScoreCs2HltvLogos": False,
        },
        device,
        region="esports",
        now=now,
    )

    header = [text for (x, y), text in drawn if x > 550 and y < 66]
    focus = [text for (x, y), text in drawn if x > 550 and 78 <= y < 232]
    assert image.size == (800, 480)
    assert provenance is SourceProvenance.LIVE
    return header, focus


def test_finished_lpl_yields_to_next_cs2_match_before_local_midnight(monkeypatch, tmp_path):
    # At 21:00 PDT, tomorrow's 03:00 CS2 game must replace a three-day-old
    # LPL final immediately, without waiting for the local date to change.
    header, focus = render_schedule(monkeypatch, tmp_path, other_match=True)
    assert "PANDASCORE DATA" in header
    assert "NEXT" in header
    assert "FINAL RESULT" not in focus


def test_live_lpl_keeps_priority_over_future_cs2(monkeypatch, tmp_path):
    header, focus = render_schedule(
        monkeypatch, tmp_path, start="2026-09-23T03:00:00Z",
        state="inProgress", wins=(1, 0), other_match=True,
    )
    assert "LIVE" in header
    assert "NOW PLAYING" in focus
    assert "1-0" in focus


def test_completed_lpl_schedule_renders_final_score_instead_of_next_match(
    monkeypatch, tmp_path
):
    # The official last match on 2026-09-19 was JDG 1-3 IG. The league
    # published no upcoming games; repeating this result must not promise
    # that this three-day-old match is still waiting to start.
    header, focus = render_schedule(monkeypatch, tmp_path)
    assert "RECENT" in header
    assert "FINAL RESULT" in focus
    assert "1-3" in focus
    assert "NEXT" not in header
    assert "NEXT MATCH" not in focus
    assert "VS" not in focus


@pytest.mark.parametrize(
    "start,state,wins,status,tag,headline,score",
    [
        ("2026-09-24T09:00:00Z", "unstarted", (0, 0),
         "NEXT", "NEXT MATCH", "9:00 AM", "VS"),
        ("2026-09-23T03:00:00Z", "inProgress", (1, 0),
         "LIVE", "NOW PLAYING", "IN PROGRESS", "1-0"),
        ("2026-09-19T09:00:00Z", "completed", (0, 0),
         "RECENT", "LAST MATCH", "RESULT PENDING", "0-0"),
        ("2026-09-19T09:00:00Z", "completed", (1, 0),
         "RECENT", "LAST MATCH", "RESULT PENDING", "1-0"),
        ("2026-09-19T09:00:00Z", "unstarted", (0, 0),
         "RECENT", "LAST MATCH", "RESULT PENDING", "0-0"),
    ],
)
def test_lpl_refresh_preserves_live_and_future_matches_without_inventing_results(
    monkeypatch, tmp_path, start, state, wins, status, tag, headline, score
):
    header, focus = render_schedule(
        monkeypatch, tmp_path, start=start, state=state, wins=wins
    )
    assert status in header
    assert tag in focus
    assert headline in focus
    assert score in focus
    assert "FINAL RESULT" not in focus
