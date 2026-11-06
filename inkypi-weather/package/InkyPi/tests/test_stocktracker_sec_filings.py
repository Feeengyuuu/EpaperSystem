import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.stocktracker import sec_filings  # noqa: E402
from runtime.refresh_contracts import TaskContext  # noqa: E402

NOW = datetime(2026, 11, 6, 22, tzinfo=timezone.utc)
FILINGS = {"data": {"symbol": "NVDA", "rows": [
    {"formType": "4/A", "filed": "11/05/2026", "reportingOwner": "PARKER NICHOLAS P."},
    {"formType": "8-K", "filed": "11/04/2026", "reportingOwner": ""},
    {"formType": "SC 13G", "filed": "11/03/2026"},
    {"formType": "10-Q", "filed": "11/01/2026"},
    {"formType": "8-K", "filed": "09/01/2026"}]}}


def _context():
    return TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 30)


def test_a_companys_filings_of_the_last_week_are_kept_newest_first():
    filings = sec_filings.recent_filings(FILINGS, NOW)
    # Insider trades (with the insider), current reports and quarterly or annual reports; an
    # amendment as its form; not other forms or older ones.
    assert filings == [{"form": "4", "label": "内部人", "date": "2026-11-05", "owner": "Parker"},
                       {"form": "8-K", "label": "8-K", "date": "2026-11-04"},
                       {"form": "10-Q", "label": "季报", "date": "2026-11-01"}]


def test_each_holding_gets_its_latest_filing_from_a_daily_cache(tmp_path):
    calls = []

    def fetch(symbol, context):
        calls.append(symbol)
        return FILINGS if symbol == "NVDA" else {"data": {"symbol": symbol, "rows": []}}

    rows = [{"symbol": "NVDA"}, {"symbol": "VTI"}, {"symbol": "CASH"}, {"symbol": "BRK.B"}]
    sec_filings.attach(rows, tmp_path, context=_context(), now=NOW, fetch=fetch)
    assert rows[0]["sec_filing"]["form"] == "4" and rows[0]["sec_filing"]["owner"] == "Parker"
    assert "sec_filing" not in rows[1] and "sec_filing" not in rows[2]
    # Nasdaq names a class share with a dash.
    assert calls == ["NVDA", "VTI", "BRK-B"]
    calls.clear()
    rows = [{"symbol": "NVDA"}]
    sec_filings.attach(rows, tmp_path, context=_context(), now=NOW, fetch=fetch)
    assert calls == [] and rows[0]["sec_filing"]["form"] == "4"


def test_the_title_row_lists_the_weeks_filings_beside_the_ex_dividend_dates():
    from plugins.stocktracker.stocktracker import StockTracker

    plugin = StockTracker({"id": "stocktracker"})
    metrics = [{"data": {"symbol": "NVDA", "sec_filing": {"form": "8-K", "label": "8-K", "date": "2026-11-04"}},
                "is_cash": False},
               {"data": {"symbol": "AAPL", "ex_dividend_date": "2026-11-10"}, "is_cash": False}]
    events = plugin._holding_events(metrics, date(2026, 11, 6))
    assert (date(2026, 11, 10), "除息", "malachite", "AAPL") in events
    assert any(event[1] == "公告" and event[3] == "NVDA" for event in events)
