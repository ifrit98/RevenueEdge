"""Business hours helper.

`businesses.hours` is free-form JSON; for the MVP we honor a simple shape:

    {
      "timezone": "America/New_York",          # optional — falls back to businesses.timezone
      "weekly": {
        "mon": [["09:00", "17:00"]],
        "tue": [["09:00", "17:00"]],
        ...
        "sat": [],
        "sun": []
      },
      "holidays": ["2026-12-25", ...]           # optional ISO dates treated as closed
    }

If `hours` is empty or malformed, we treat the business as 24/7 (i.e. always
within hours) so the agent never silently gags itself on bad config.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

_DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _parse_hhmm(value: str) -> Optional[time]:
    try:
        hh, mm = value.split(":", 1)
        return time(int(hh), int(mm))
    except Exception:
        return None


def is_within_business_hours(business: Optional[dict], now: Optional[datetime] = None) -> bool:
    if not business:
        return True
    hours = business.get("hours") or {}
    if not isinstance(hours, dict) or not hours.get("weekly"):
        return True

    tz_name = hours.get("timezone") or business.get("timezone") or "America/New_York"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("America/New_York")

    current = (now or datetime.utcnow()).astimezone(tz) if (now and now.tzinfo) else datetime.now(tz)

    iso_date = current.date().isoformat()
    if iso_date in set(hours.get("holidays") or []):
        return False

    day_key = _DAY_KEYS[current.weekday()]
    windows = (hours.get("weekly") or {}).get(day_key) or []
    if not windows:
        return False

    now_t = current.time()
    for win in windows:
        if not isinstance(win, (list, tuple)) or len(win) != 2:
            continue
        start = _parse_hhmm(str(win[0]))
        end = _parse_hhmm(str(win[1]))
        if not start or not end:
            continue
        if start <= now_t <= end:
            return True
    return False


def next_business_open(business: Optional[dict], now: Optional[datetime] = None) -> Optional[datetime]:
    """Return the earliest UTC datetime when the business next opens.

    Scans up to 8 days forward (handles weekends + a holiday).
    Returns ``None`` when it cannot be determined (e.g. 24/7 business).
    """
    if not business:
        return None
    hours = business.get("hours") or {}
    weekly = hours.get("weekly") or {}
    if not weekly:
        return None

    tz_name = hours.get("timezone") or business.get("timezone") or "America/New_York"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("America/New_York")

    current = (now or datetime.utcnow()).astimezone(tz) if (now and now.tzinfo) else datetime.now(tz)
    holidays = set(hours.get("holidays") or [])

    for offset_days in range(0, 8):
        candidate = current + timedelta(days=offset_days)
        iso_date = candidate.date().isoformat()
        if iso_date in holidays:
            continue
        day_key = _DAY_KEYS[candidate.weekday()]
        windows = weekly.get(day_key) or []
        for win in windows:
            if not isinstance(win, (list, tuple)) or len(win) != 2:
                continue
            start = _parse_hhmm(str(win[0]))
            if not start:
                continue
            open_dt = candidate.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
            if open_dt > current:
                return open_dt.astimezone(ZoneInfo("UTC"))

    return None


_QUIET_START = time(21, 0)
_QUIET_END = time(8, 0)


def is_quiet_hours(business: Optional[dict], now: Optional[datetime] = None) -> bool:
    """Return True if the current local time is in the quiet-hours window (9pm–8am).

    Uses the business timezone. Override window via ``business.settings.quiet_start``
    and ``business.settings.quiet_end`` (HH:MM strings).
    """
    if not business:
        return False
    tz_name = (business.get("hours") or {}).get("timezone") or business.get("timezone") or "America/New_York"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("America/New_York")

    current = (now or datetime.utcnow()).astimezone(tz) if (now and now.tzinfo) else datetime.now(tz)
    now_t = current.time()

    settings = business.get("settings") or {}
    q_start = _parse_hhmm(str(settings.get("quiet_start") or "")) or _QUIET_START
    q_end = _parse_hhmm(str(settings.get("quiet_end") or "")) or _QUIET_END

    if q_start > q_end:
        return now_t >= q_start or now_t < q_end
    return q_start <= now_t < q_end
