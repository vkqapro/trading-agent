"""America/New_York support that remains available in minimal Windows venvs."""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo

try:  # Python's normal IANA-zone path on full installations.
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - Python 3.9+ supplies zoneinfo.
    ZoneInfo = None  # type: ignore[assignment,misc]
    ZoneInfoNotFoundError = Exception  # type: ignore[assignment,misc]


_HOUR = timedelta(hours=1)
_STANDARD_OFFSET = timedelta(hours=-5)


class USNewYorkFallback(tzinfo):
    """US Eastern timezone for modern US DST rules when IANA tzdata is absent.

    The analyst only needs the trading system's modern timestamps. The fallback
    covers the post-2007 US rule: 02:00 on the second Sunday in March through
    02:00 on the first Sunday in November.
    """

    @staticmethod
    def _boundaries(year: int) -> tuple[datetime, datetime]:
        march_eighth = datetime(year, 3, 8)
        dst_start = march_eighth + timedelta(days=(6 - march_eighth.weekday()) % 7, hours=2)
        november_first = datetime(year, 11, 1)
        dst_end = november_first + timedelta(days=(6 - november_first.weekday()) % 7, hours=2)
        return dst_start, dst_end

    def dst(self, value: datetime | None) -> timedelta:
        if value is None:
            return timedelta(0)
        start, end = self._boundaries(value.year)
        local = value.replace(tzinfo=None)
        return _HOUR if start <= local < end else timedelta(0)

    def utcoffset(self, value: datetime | None) -> timedelta:
        return _STANDARD_OFFSET + self.dst(value)

    def tzname(self, value: datetime | None) -> str:
        return "EDT" if self.dst(value) else "EST"

    def fromutc(self, value: datetime) -> datetime:
        if value.tzinfo is not self:
            raise ValueError("fromutc requires this timezone")
        start, end = self._boundaries(value.year)
        utc = value.replace(tzinfo=None)
        start_utc = start - _STANDARD_OFFSET
        end_utc = end - (_STANDARD_OFFSET + _HOUR)
        offset = _STANDARD_OFFSET + _HOUR if start_utc <= utc < end_utc else _STANDARD_OFFSET
        return (utc + offset).replace(tzinfo=self)


def america_new_york() -> tzinfo:
    """Return IANA Eastern time where present, otherwise the modern DST fallback."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo("America/New_York")
        except ZoneInfoNotFoundError:
            pass
    return USNewYorkFallback()


EASTERN = america_new_york()
