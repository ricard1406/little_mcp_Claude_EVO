"""The DATE type. Python only: the C++ and Rust versions have no dates yet.

A DATE is a day on the calendar, stored as YYYY-MM-DD (ISO 8601) in 10 bytes.

Why this format: it sorts as text in the same order as in time, so the
comparisons the engine already has (< <= > >=, which compare text when the
values are not numbers) work on dates with no new code. And it is never
ambiguous, while 03/04/2026 is the 3rd of April in Italy and March 4th in the US.

Why a DATE has no time zone: a birthday, a deadline or a publication date is
the same day wherever you read it. A time zone only matters for a moment in
time (a timestamp), and for one question here: which day is TODAY? At 23:30 in
New York it is already tomorrow in Rome. So TODAY is worked out in a time zone
you choose (LITTLE_MCP_TIMEZONE in .env), or this computer's when there is none.
"""

import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .util import SqlError, ascii_upper, trim

DATE_KIND = "D1"
"""What SYSTEM_TABLE records for a DATE column, next to S1 (CHAR) and N1 (NUMBER)."""

DATE_WIDTH = 10
"""YYYY-MM-DD takes 10 bytes."""

TODAY = "TODAY"

_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def today(zone_name: str = "", now: datetime | None = None) -> str:
    """Today's date in a time zone such as "Europe/Rome", or in this computer's zone when empty.

    `now` is a moment in time (it must know its own time zone); tests pass one,
    so they can check what happens near midnight.
    """
    now = now or datetime.now(timezone.utc)
    if not zone_name:
        return now.astimezone().date().isoformat()
    try:
        zone = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise SqlError(f"unknown time zone '{zone_name}': use a name such as Europe/Rome or America/New_York") from None
    return now.astimezone(zone).date().isoformat()


def check_date(value: str, column: str, zone_name: str = "", now: datetime | None = None) -> str:
    """Checks a value for a DATE column and returns it as stored.

    Accepts YYYY-MM-DD for a real day, TODAY, or '' for "no date". Anything else
    is refused with a message that shows the expected form, so a person or an AI
    can correct it.
    """
    value = trim(value)
    if value == "":
        return ""
    if ascii_upper(value) == TODAY:
        return today(zone_name, now)
    match = _ISO_DATE.fullmatch(value)
    if match:
        try:
            return date(*map(int, match.groups())).isoformat()
        except ValueError:
            raise SqlError(f"'{value}' is not a real day (column {column})") from None
    raise SqlError(f"'{value}' is not a date (column {column}): write YYYY-MM-DD, e.g. 2026-11-13, or TODAY")
