"""Refresh failures the scheduler expects and already backs off.

They keep the ordinary failure path (job status, lane bookkeeping, cooldowns);
only the log severity differs, so error-only health scans stay meaningful.
"""

from __future__ import annotations


class ExpectedRefreshFailure(RuntimeError):
    """A failed refresh that is a normal outcome, logged without a traceback."""


class ThemeCatchupNotCacheable(ExpectedRefreshFailure):
    """A degraded source produced no exact-theme cache; catch-up retries later."""


def log_refresh_command_failure(logger, command, error):
    """Log a failed command; call only from the handler that caught ``error``."""
    if isinstance(error, ExpectedRefreshFailure):
        logger.warning(
            "Refresh command failed. | source: %s | intent: %s | plugin_id: %s | error: %s",
            command.source,
            command.intent,
            command.plugin_id,
            error,
            stacklevel=2,
        )
        return
    logger.exception(
        "Refresh command failed. | source: %s | intent: %s | plugin_id: %s",
        command.source,
        command.intent,
        command.plugin_id,
        stacklevel=2,
    )
