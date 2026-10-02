"""Facts about the Indian market that the data sources do not tell us.

The holiday calendar is NOT included, so on an exchange holiday `is_open` is still True during trading hours
by this rule. Callers should treat it as a hint and prefer a source's own session information when present.
"""
import datetime
from typing import Optional

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)
CURRENCY_BY_EXCHANGE = {"NSE": "INR", "BSE": "INR", "MCX": "INR", "NCDEX": "INR", "NSEIX": "INR"}


def now_ist() -> datetime.datetime:
    return datetime.datetime.now(IST)


def is_trading_hours(at: Optional[datetime.datetime] = None) -> bool:
    """Monday to Friday, 09:15 to 15:30 India time (equity cash market). Holidays are not checked."""
    at = (at or now_ist()).astimezone(IST)
    return at.weekday() < 5 and OPEN <= at.time() <= CLOSE


def last_trading_day(at: Optional[datetime.datetime] = None) -> datetime.date:
    """The most recent weekday that is not in the future (today after the open, otherwise the previous one)."""
    at = (at or now_ist()).astimezone(IST)
    day = at.date()
    if at.time() < OPEN or at.weekday() >= 5:
        day -= datetime.timedelta(days=1)
    while day.weekday() >= 5:
        day -= datetime.timedelta(days=1)
    return day


def parse_ist(text: str) -> Optional[int]:
    """'2026-10-01 16:00:28' (India time) -> epoch seconds."""
    try:
        return int(datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST).timestamp())
    except (TypeError, ValueError):
        return None


def day_start_epoch(date_text: str) -> Optional[int]:
    """'2026-10-01' -> epoch of that day's 09:15 India time (the same stamp a daily candle carries elsewhere)."""
    try:
        d = datetime.datetime.strptime(date_text, "%Y-%m-%d").replace(hour=9, minute=15, tzinfo=IST)
        return int(d.timestamp())
    except (TypeError, ValueError):
        return None


def describe_time(epoch: Optional[int]) -> str:
    """'1 Oct, 4:00 pm IST' for messages."""
    if not epoch:
        return ""
    d = datetime.datetime.fromtimestamp(epoch, IST)
    return f"{d.day} {d.strftime('%b')}, {d.strftime('%I:%M %p').lstrip('0').lower()} IST"
