from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def parse_months(values: list[str]) -> tuple[str, ...]:
    if not values or any(
        not isinstance(value, str) or not re.fullmatch(r"(?!0000)[0-9]{4}/(0[1-9]|1[0-2])", value)
        for value in values
    ):
        raise ValueError("Wybierz co najmniej jeden poprawny miesiąc.")
    return tuple(sorted(set(values)))


@dataclass(frozen=True)
class DateRange:
    start: str
    end: str
    timezone: str

    def utc_bounds(self) -> tuple[datetime, datetime]:
        try:
            start = date.fromisoformat(self.start)
            end = date.fromisoformat(self.end)
            if start.isoformat() != self.start or end.isoformat() != self.end:
                raise ValueError
            zone = ZoneInfo(self.timezone)
        except (TypeError, ValueError, ZoneInfoNotFoundError) as error:
            raise ValueError("Podaj poprawne daty i strefę czasową.") from error
        if start > end:
            raise ValueError("Data końcowa musi być równa lub późniejsza od początkowej.")
        try:
            first = datetime.combine(start, time.min, zone).astimezone(timezone.utc)
            next_day = datetime.combine(end + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
        except OverflowError as error:
            raise ValueError("Podaj poprawne daty i strefę czasową.") from error
        if first >= next_day:
            raise ValueError("Podany zakres nie obejmuje żadnej godziny w tej strefie czasowej.")
        # icloudpd's upper bound is inclusive, so exclude midnight of the next day.
        return first, next_day - timedelta(microseconds=1)
