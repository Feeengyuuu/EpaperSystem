"""One deadline for mainland source data and optional enrichment."""
from contextvars import ContextVar
from datetime import timedelta, timezone
import json
import re

import requests

from runtime.long_task_executor import current_task_context, task_context_or_default
from runtime.refresh_contracts import TaskContext, TaskCancelled, TaskDeadlineExceeded
from utils.http_client import HttpClientError, HttpStatusError
from plugins.box_office_top_movies.china_http import IsolatedChinaHttpClient


ACTIVE_BUDGET = ContextVar("china_box_office_budget", default=None)
SHANGHAI = timezone(timedelta(hours=8))
METRIC_SCOPE = "comprehensive_including_service_fee"


class ChinaFetchBudget:
    """Own responses and permit at most one transient retry across all requests.

    Production transport uses killable governed children, including for DNS and
    socket stalls. Earlier valid chart data remains in the parent during extras.
    """
    def __init__(self, *, client=None, parent=None):
        self.parent = parent or current_task_context()
        base = self.parent or task_context_or_default(20)
        self.context = TaskContext(base.cancel_event, min(base.deadline_monotonic, base.clock() + 20), base.clock)
        self.client = client if client is not None else IsolatedChinaHttpClient()
        self.retry_used = False

    def __enter__(self):
        self.token = ACTIVE_BUDGET.set(self)
        return self

    def __exit__(self, exc_type, error, traceback):
        ACTIVE_BUDGET.reset(self.token)
        self.client.close()
        if self.parent is not None:
            self.parent.raise_if_cancelled()
        if isinstance(error, TaskDeadlineExceeded):
            raise RuntimeError("China box office source budget expired") from error

    def remaining_seconds(self):
        if self.parent is not None:
            self.parent.raise_if_cancelled()
        return self.context.remaining_seconds()

    def get(self, url, *, timeout=None, stream=False, **kwargs):
        """Adapt bounded fully-owned responses to existing enrichment helpers."""
        while True:
            self.context.raise_if_cancelled()
            remaining = self.context.remaining_seconds()
            try:
                result = self.client.request_bytes(
                    "GET", url, context=self.context,
                    timeout=(min(3, remaining / 2), remaining / 2),
                    max_bytes=8 * 1024 * 1024 if stream else 2 * 1024 * 1024,
                    **kwargs,
                )
                response = requests.Response()
                response.status_code = result.status
                response._content = result.data
                response._content_consumed = True
                response.encoding = "utf-8"
                response.headers.update(result.headers)
                response.url = result.url
                return response
            except TaskCancelled:
                raise
            except HttpClientError as error:
                transient = (
                    isinstance(error, HttpStatusError) and error.status in {408, 429, 500, 502, 503, 504}
                ) or isinstance(error.__cause__, (requests.ConnectionError, requests.Timeout))
                if not transient or self.retry_used or self.remaining_seconds() <= 0.5:
                    raise
                self.retry_used = True
                if self.context.cancel_event.wait(0.25):
                    self.context.raise_if_cancelled()


def validate_comprehensive_metrics(gross, share):
    gross, share = str(gross or "").strip(), str(share or "").strip()
    if not re.fullmatch(r"<?\d+(?:\.\d+)?", gross):
        raise RuntimeError("missing comprehensive gross")
    if not re.fullmatch(r"\d+(?:\.\d+)?%", share) or float(share[:-1]) > 100:
        raise RuntimeError("invalid comprehensive share")


def official_metadata(html_text, now):
    """Require current-day comprehensive gross, never substitute split gross."""
    match = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;</script>", html_text or "", re.DOTALL)
    try:
        state = json.loads(match.group(1)) if match else None
        data = state.get("boxData") if isinstance(state, dict) else None
        stamp = data.get("updateTimestamp") if isinstance(data, dict) else None
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or stamp < 946684800000:
            raise ValueError("missing source time")
        updated = now.fromtimestamp(stamp / 1000, timezone.utc)
        if updated > now + timedelta(minutes=2) or updated.astimezone(SHANGHAI).date() != now.astimezone(SHANGHAI).date():
            raise ValueError("source is not today's Beijing statistics")
        rows = data.get("list")
        if not isinstance(rows, list) or not rows:
            raise ValueError("empty movie list")
        for row in rows[:5]:
            if not isinstance(row, dict) or not str(row.get("name") or "").strip():
                raise ValueError("missing movie title")
            validate_comprehensive_metrics(row.get("salesInWanDesc"), row.get("salesRateDesc"))
    except (ValueError, TypeError, OverflowError, OSError) as error:
        raise RuntimeError("Official China chart has no valid current comprehensive data") from error
    return {
        "statistic_date": updated.astimezone(SHANGHAI).date().isoformat(),
        "source_updated_at": updated.isoformat(), "fetched_at": now.isoformat(),
        "metric_scope": METRIC_SCOPE, "source": "zgdypw_realtime", "timezone": "Asia/Shanghai",
    }
