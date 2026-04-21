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
from datetime import datetime, time
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
