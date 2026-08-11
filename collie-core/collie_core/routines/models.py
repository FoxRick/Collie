"""Validated models for routine schedules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ScheduleKind = Literal["once", "daily", "weekdays", "weekly", "monthly"]
_DAYS = frozenset({"MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"})


@dataclass(frozen=True, slots=True)
class Schedule:
    kind: ScheduleKind
    time: time
    timezone: str
    days: tuple[str, ...] = ()
    day: int | None = None
    date: date | None = None

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {self.timezone}") from exc
        if self.kind == "weekly" and (not self.days or not set(self.days) <= _DAYS):
            raise ValueError("weekly schedules need valid selected days")
        if self.kind == "monthly" and not (self.day and 1 <= self.day <= 31):
            raise ValueError("monthly schedules need a day from 1 to 31")
        if self.kind == "once" and self.date is None:
            raise ValueError("once schedules need a date")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "kind": self.kind,
            "time": self.time.strftime("%H:%M"),
            "timezone": self.timezone,
        }
        if self.days:
            data["days"] = list(self.days)
        if self.day is not None:
            data["day"] = self.day
        if self.date is not None:
            data["date"] = self.date.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Schedule:
        clock = time.fromisoformat(str(data["time"]))
        once_date = date.fromisoformat(str(data["date"])) if data.get("date") else None
        return cls(
            kind=str(data["kind"]),  # type: ignore[arg-type]
            time=clock,
            timezone=str(data["timezone"]),
            days=tuple(str(day).upper() for day in data.get("days", [])),
            day=int(data["day"]) if data.get("day") is not None else None,
            date=once_date,
        )
