"""Input validation utilities shared across modules."""

from __future__ import annotations

import re

_SHUTDOWN_TIME_RE = re.compile(r"^([01]\d|2[0-3])([0-5]\d)$")


def validate_shutdown_time(time: str) -> None:
    """Validate that a shutdown time string is in HHMM format (00-23, 00-59).

    Args:
        time: Time string to validate, e.g. "1900".

    Raises:
        ValueError: If the time string is not valid HHMM.
    """
    if not _SHUTDOWN_TIME_RE.match(time):
        raise ValueError(f"Invalid shutdown time {time!r} — expected HHMM (e.g. 1900)")
