"""E.164 phone normalization.

Schema constraint: `contacts.phone_e164` matches `^\\+[1-9][0-9]{6,14}$`.
Keep this helper permissive on input (numeric/spaces/dashes/parens) and
strict on output (fully qualified E.164 or None).
"""

from __future__ import annotations

import re
from typing import Optional

_DIGITS = re.compile(r"\d+")
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


def normalize_phone(value: Optional[str], *, default_country_code: str = "1") -> Optional[str]:
    if not value:
        return None
    raw = value.strip()
    if _E164.match(raw):
        return raw

    digits = "".join(_DIGITS.findall(raw))
    if not digits:
        return None

    # US-centric default: 10 digits → prepend default country code.
    if len(digits) == 10:
        digits = f"{default_country_code}{digits}"
    elif len(digits) == 11 and digits.startswith(default_country_code):
        pass
    elif len(digits) < 7 or len(digits) > 15:
        return None

    candidate = f"+{digits}"
    return candidate if _E164.match(candidate) else None
