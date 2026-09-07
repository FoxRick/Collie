"""The desktop supplies an IANA zone; stored routine zones remain authoritative."""

import os
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def local_timezone() -> str:
    for candidate in (os.environ.get("COLLIE_TIMEZONE"), os.environ.get("TZ"), "UTC"):
        if candidate:
            try:
                ZoneInfo(candidate)
                return candidate
            except (ZoneInfoNotFoundError, ValueError):
                continue
    return "UTC"
