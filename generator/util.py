import calendar
import random
from datetime import date, datetime, time, timedelta


def add_months(d: date, months: int) -> date:
    """Add (or subtract) calendar months, clamping the day (31 Jan + 1m = 28 Feb)."""
    year_shift, month0 = divmod(d.month - 1 + months, 12)
    year, month = d.year + year_shift, month0 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def random_date(rng: random.Random, start: date, end: date) -> date:
    """Random date between start and end, both inclusive."""
    return start + timedelta(days=rng.randint(0, (end - start).days))


def working_hours(rng: random.Random, day: date) -> datetime:
    """A timestamp between 09:00 and 17:59 on the given day."""
    return datetime.combine(day, time(rng.randint(9, 17), rng.randint(0, 59)))