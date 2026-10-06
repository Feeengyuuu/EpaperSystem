"""Run one Telegram account fetch in a short-lived child process.

Telethon and the dialog/entity graph it loads raised the service's peak RSS by up to
116 MB on its first run in a process and left about 45 MB that never returned to the
OS (device journal, 2026-10-06). A child process gives all of it back when it exits.

Protocol: one JSON request on the child's stdin, one JSON reply on its stdout. A
failure exits non-zero; its last stderr line carries the exception message.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2]
WORKER_MODULE = "plugins.telegram_digest.account_worker"
DEFAULT_TIMEOUT_SECONDS = 120.0
POLL_SECONDS = 0.25
_EXCEPTION_PREFIX = re.compile(r"^(?:[A-Za-z_][\w.]*\.)?[A-Za-z_]\w*(?:Error|Exception|Exceeded|Cancelled): ")


def run_account_fetch_in_child(request, *, context=None, timeout=DEFAULT_TIMEOUT_SECONDS):
    """Return the account payload computed by a child process.

    Honors the task context: cancellation or the deadline kills the child and
    raises the task's own exception.
    """

    budget = float(timeout) if context is None else min(float(timeout), context.remaining_seconds())
    deadline = time.monotonic() + budget
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(SRC_ROOT), env.get("PYTHONPATH")]))
    process = subprocess.Popen(
        [sys.executable, "-m", WORKER_MODULE],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    pending = json.dumps(request, ensure_ascii=True).encode("utf-8")
    try:
        while True:
            try:
                stdout, stderr = process.communicate(pending, timeout=POLL_SECONDS)
                break
            except subprocess.TimeoutExpired:
                pending = None
                if context is not None:
                    context.raise_if_cancelled()
                if time.monotonic() >= deadline:
                    raise RuntimeError("Telegram account fetch timed out") from None
    except BaseException:
        process.kill()
        process.communicate()
        raise
    if process.returncode != 0:
        raise RuntimeError(_failure_detail(stderr, process.returncode))
    try:
        reply = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError("Telegram account worker returned an invalid reply") from exc
    payload = reply.get("payload") if isinstance(reply, dict) else None
    if not isinstance(payload, dict):
        raise RuntimeError("Telegram account worker returned no payload")
    return payload


def _failure_detail(stderr, returncode):
    lines = [line.strip() for line in stderr.decode("utf-8", "replace").splitlines() if line.strip()]
    if not lines:
        return f"Telegram account worker exited with status {returncode}"
    return _EXCEPTION_PREFIX.sub("", lines[-1])[:300]


def main():
    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    reply_stream = sys.stdout
    sys.stdout = sys.stderr  # Library output must never corrupt the reply channel.

    from plugins.telegram_digest.telegram_digest import TelegramDigest

    plugin = TelegramDigest({"id": "telegram_digest"})
    cache_dir = Path(request["cache_dir"])
    plugin._cache_dir = lambda: cache_dir
    payload = asyncio.run(
        plugin._fetch_account_payload_async(
            request["settings"],
            request["cache"],
            datetime.fromisoformat(request["now"]),
            int(request["max_messages"]),
            request["config"],
        )
    )
    reply_stream.write(json.dumps({"payload": payload}, ensure_ascii=True))
    reply_stream.flush()


if __name__ == "__main__":
    main()
