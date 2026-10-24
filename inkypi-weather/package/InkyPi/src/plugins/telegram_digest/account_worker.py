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
import logging
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)
SRC_ROOT = Path(__file__).resolve().parents[2]
WORKER_MODULE = "plugins.telegram_digest.account_worker"
DEFAULT_TIMEOUT_SECONDS = 120.0
POLL_SECONDS = 0.25
# The child is told to finish this much before the parent gives up on it, so it can
# still reply (and the parent still render) when its optional steps are cut short.
CHILD_REPLY_RESERVE_SECONDS = 6.0
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
    request = {**request, "budget_seconds": max(5.0, budget - CHILD_REPLY_RESERVE_SECONDS)}
    pending = json.dumps(request, ensure_ascii=True).encode("utf-8")
    # The request is fed and the replies drained on threads. Polling communicate(input) does not
    # work on Linux: only the call that is handed the input writes it, so a request larger than
    # the pipe buffer (64 KB) was never finished and the child waited on stdin until the deadline.
    out, err = [], []
    threads = [threading.Thread(target=_feed, args=(process.stdin, pending), daemon=True),
               threading.Thread(target=_drain, args=(process.stdout, out), daemon=True),
               threading.Thread(target=_drain, args=(process.stderr, err), daemon=True)]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        while process.poll() is None:
            if context is not None:
                context.raise_if_cancelled()
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(POLL_SECONDS)
    except BaseException:
        _stop(process, threads)
        raise
    if timed_out:
        _stop(process, threads)
        # The child's last log line says which step it was still on.
        last = _last_line(b"".join(err))
        raise RuntimeError("Telegram account fetch timed out" + (f" during: {last}" if last else ""))
    for thread in threads:
        thread.join(timeout=5)
    stdout, stderr = b"".join(out), b"".join(err)
    if process.returncode != 0:
        raise RuntimeError(_failure_detail(stderr, process.returncode))
    # The child's own progress lines (how many chats, how long) go to the service journal.
    for line in (stderr or b"").decode("utf-8", "replace").splitlines():
        if line.startswith("Telegram Digest "):
            logger.info("Telegram account worker: %s", line.strip()[:200])
    try:
        reply = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError("Telegram account worker returned an invalid reply") from exc
    payload = reply.get("payload") if isinstance(reply, dict) else None
    if not isinstance(payload, dict):
        raise RuntimeError("Telegram account worker returned no payload")
    return payload


def _feed(stream, data):
    try:
        stream.write(data)
    except (OSError, ValueError):
        pass  # the child exited or was killed before it read everything
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _drain(stream, sink):
    try:
        for chunk in iter(lambda: stream.read(65536), b""):
            sink.append(chunk)
    except (OSError, ValueError):
        pass


def _stop(process, threads):
    process.kill()
    process.wait()
    for thread in threads:
        thread.join(timeout=5)


def _last_line(stderr):
    lines = [line.strip() for line in (stderr or b"").decode("utf-8", "replace").splitlines() if line.strip()]
    return lines[-1][:200] if lines else ""


def _failure_detail(stderr, returncode):
    last = _last_line(stderr)
    if not last:
        return f"Telegram account worker exited with status {returncode}"
    return _EXCEPTION_PREFIX.sub("", last)[:300]


def main():
    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    reply_stream = sys.stdout
    sys.stdout = sys.stderr  # Library output must never corrupt the reply channel.
    # Progress goes to stderr: the parent quotes the last line when the child runs out of time.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    logger.info("Telegram Digest worker started")

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
            {**request["config"], "budget_seconds": request.get("budget_seconds")},
        )
    )
    reply_stream.write(json.dumps({"payload": payload}, ensure_ascii=True))
    reply_stream.flush()


if __name__ == "__main__":
    main()
