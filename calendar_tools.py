"""Google Calendar integration.

Every function returns a structured dict the agent can communicate to the user:
    {"success": bool, ..., "error": str | None}

Errors are caught and returned, never raised — the agent layer should never
have to deal with exceptions from this module.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any

import pytz
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config


logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Module-level cached service. Built lazily on first call so missing token.json
# at import time doesn't crash the bot — the user gets a clean error instead.
_service = None


# --- Authentication --------------------------------------------------------

def _get_service():
    """Return a Google Calendar service object, refreshing the token if needed."""
    global _service
    if _service is not None:
        return _service

    if not config.GOOGLE_TOKEN_PATH:
        raise RuntimeError(
            "token.json is missing. Run generate_token.py locally to create it, "
            "then base64-encode it into GOOGLE_TOKEN_JSON for production."
        )

    creds = Credentials.from_authorized_user_file(config.GOOGLE_TOKEN_PATH, SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Persist the refreshed token so the next call doesn't refetch.
            with open(config.GOOGLE_TOKEN_PATH, "w") as f:
                f.write(creds.to_json())
        else:
            raise RuntimeError(
                "Google OAuth credentials are invalid and cannot be refreshed. "
                "Re-run generate_token.py locally and update GOOGLE_TOKEN_JSON."
            )

    _service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    return _service


def _auth_error_payload(exc: Exception) -> str:
    return (
        f"Calendar authentication failed: {exc}. "
        "Re-run generate_token.py locally and update GOOGLE_TOKEN_JSON."
    )


# --- Internal helpers ------------------------------------------------------

def _now_local() -> datetime:
    return datetime.now(config.USER_TZ)


def _parse_iso(dt_str: str) -> datetime:
    """Parse an ISO 8601 datetime string. If naive, localize to USER_TZ."""
    parsed = datetime.fromisoformat(dt_str)
    if parsed.tzinfo is None:
        parsed = config.USER_TZ.localize(parsed)
    return parsed


def _to_rfc3339(dt: datetime) -> str:
    """Serialize a timezone-aware datetime to RFC 3339 for Google Calendar."""
    if dt.tzinfo is None:
        dt = config.USER_TZ.localize(dt)
    return dt.isoformat()


def _format_event_time(dt_str: str) -> str:
    """Format an event start/end string in the user's local timezone."""
    parsed = datetime.fromisoformat(dt_str)
    if parsed.tzinfo is None:
        parsed = pytz.UTC.localize(parsed)
    local = parsed.astimezone(config.USER_TZ)
    return local.strftime("%Y-%m-%d %H:%M %Z")


def _normalize_event(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a Google Calendar event resource into the shape we expose."""
    start = raw.get("start", {})
    end = raw.get("end", {})

    # All-day events use 'date' (YYYY-MM-DD); timed events use 'dateTime'.
    start_str = start.get("dateTime") or start.get("date")
    end_str = end.get("dateTime") or end.get("date")

    return {
        "id": raw.get("id"),
        "title": raw.get("summary", "(no title)"),
        "start": _format_event_time(start_str) if start.get("dateTime") else start_str,
        "end": _format_event_time(end_str) if end.get("dateTime") else end_str,
        "all_day": "date" in start and "dateTime" not in start,
        "description": raw.get("description", ""),
        "html_link": raw.get("htmlLink", ""),
    }


def _day_bounds_rfc3339(d: date) -> tuple[str, str]:
    """Start of `d` and start of `d + 1 day`, both in user TZ as RFC 3339."""
    start_local = config.USER_TZ.localize(datetime.combine(d, time.min))
    end_local = config.USER_TZ.localize(datetime.combine(d + timedelta(days=1), time.min))
    return start_local.isoformat(), end_local.isoformat()


def _list_events(time_min: str, time_max: str, query: str | None = None) -> list[dict]:
    """Wrapper around events().list with single_events=True for stable ordering."""
    service = _get_service()
    kwargs = {
        "calendarId": config.GOOGLE_CALENDAR_ID,
        "timeMin": time_min,
        "timeMax": time_max,
        "singleEvents": True,
        "orderBy": "startTime",
        "maxResults": 250,
    }
    if query:
        kwargs["q"] = query
    result = service.events().list(**kwargs).execute()
    return result.get("items", [])


# --- Public tool functions -------------------------------------------------

def get_events(start_date: str, end_date: str) -> dict[str, Any]:
    """List events between two dates (inclusive), returned in user's local time.

    Args:
        start_date: ISO date string YYYY-MM-DD.
        end_date: ISO date string YYYY-MM-DD.
    """
    try:
        sd = date.fromisoformat(start_date)
        ed = date.fromisoformat(end_date)
        if ed < sd:
            return {"success": False, "events": [], "error": "end_date is before start_date"}
        time_min = _day_bounds_rfc3339(sd)[0]
        time_max = _day_bounds_rfc3339(ed)[1]
        raw_events = _list_events(time_min, time_max)
        return {
            "success": True,
            "events": [_normalize_event(e) for e in raw_events],
            "error": None,
        }
    except (RefreshError, RuntimeError) as exc:
        return {"success": False, "events": [], "error": _auth_error_payload(exc)}
    except HttpError as exc:
        logger.exception("Google Calendar API error in get_events")
        return {"success": False, "events": [], "error": f"Calendar API error: {exc}"}
    except Exception as exc:
        logger.exception("Unexpected error in get_events")
        return {"success": False, "events": [], "error": f"Unexpected error: {exc}"}


def find_free_slots(
    date: str,
    duration_minutes: int,
    working_hours_start: str = "08:00",
    working_hours_end: str = "20:00",
) -> dict[str, Any]:
    """Find gaps of at least `duration_minutes` within working hours on a date."""
    try:
        from datetime import date as date_cls  # local alias to avoid shadowing

        target_date = date_cls.fromisoformat(date)

        wh_start_h, wh_start_m = map(int, working_hours_start.split(":"))
        wh_end_h, wh_end_m = map(int, working_hours_end.split(":"))

        window_start = config.USER_TZ.localize(
            datetime.combine(target_date, time(wh_start_h, wh_start_m))
        )
        window_end = config.USER_TZ.localize(
            datetime.combine(target_date, time(wh_end_h, wh_end_m))
        )

        if window_end <= window_start:
            return {
                "success": False,
                "free_slots": [],
                "error": "working_hours_end must be after working_hours_start",
            }

        time_min, time_max = _day_bounds_rfc3339(target_date)
        raw_events = _list_events(time_min, time_max)

        # Build busy intervals in user TZ. Skip all-day events (they don't block
        # specific timed slots in Mikey's schedule).
        busy: list[tuple[datetime, datetime]] = []
        for raw in raw_events:
            start_dt_str = raw.get("start", {}).get("dateTime")
            end_dt_str = raw.get("end", {}).get("dateTime")
            if not start_dt_str or not end_dt_str:
                continue
            s = datetime.fromisoformat(start_dt_str).astimezone(config.USER_TZ)
            e = datetime.fromisoformat(end_dt_str).astimezone(config.USER_TZ)
            # Clamp to working window so events outside don't poison gaps.
            s = max(s, window_start)
            e = min(e, window_end)
            if e > s:
                busy.append((s, e))

        busy.sort()

        # Merge overlapping busy intervals.
        merged: list[tuple[datetime, datetime]] = []
        for s, e in busy:
            if merged and s <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))

        # Free = window minus merged busy.
        free: list[tuple[datetime, datetime]] = []
        cursor = window_start
        for s, e in merged:
            if s > cursor:
                free.append((cursor, s))
            cursor = max(cursor, e)
        if cursor < window_end:
            free.append((cursor, window_end))

        min_delta = timedelta(minutes=duration_minutes)
        slots = []
        for s, e in free:
            if e - s >= min_delta:
                slots.append(
                    {
                        "start": s.strftime("%H:%M"),
                        "end": e.strftime("%H:%M"),
                        "duration_minutes": int((e - s).total_seconds() // 60),
                    }
                )

        return {"success": True, "free_slots": slots, "error": None}
    except (RefreshError, RuntimeError) as exc:
        return {"success": False, "free_slots": [], "error": _auth_error_payload(exc)}
    except HttpError as exc:
        logger.exception("Google Calendar API error in find_free_slots")
        return {"success": False, "free_slots": [], "error": f"Calendar API error: {exc}"}
    except Exception as exc:
        logger.exception("Unexpected error in find_free_slots")
        return {"success": False, "free_slots": [], "error": f"Unexpected error: {exc}"}


def create_event(
    title: str,
    start_datetime: str,
    end_datetime: str,
    description: str = "",
) -> dict[str, Any]:
    """Create a calendar event. Datetime strings are ISO 8601 in user's local time."""
    try:
        start_dt = _parse_iso(start_datetime)
        end_dt = _parse_iso(end_datetime)
        if end_dt <= start_dt:
            return {
                "success": False,
                "event_id": None,
                "event_link": None,
                "error": "end_datetime must be after start_datetime",
            }

        body = {
            "summary": title,
            "description": description or "",
            "start": {"dateTime": _to_rfc3339(start_dt), "timeZone": config.USER_TIMEZONE},
            "end": {"dateTime": _to_rfc3339(end_dt), "timeZone": config.USER_TIMEZONE},
        }

        service = _get_service()
        created = (
            service.events()
            .insert(calendarId=config.GOOGLE_CALENDAR_ID, body=body)
            .execute()
        )
        return {
            "success": True,
            "event_id": created["id"],
            "event_link": created.get("htmlLink", ""),
            "error": None,
        }
    except (RefreshError, RuntimeError) as exc:
        return {
            "success": False,
            "event_id": None,
            "event_link": None,
            "error": _auth_error_payload(exc),
        }
    except HttpError as exc:
        logger.exception("Google Calendar API error in create_event")
        return {
            "success": False,
            "event_id": None,
            "event_link": None,
            "error": f"Calendar API error: {exc}",
        }
    except Exception as exc:
        logger.exception("Unexpected error in create_event")
        return {
            "success": False,
            "event_id": None,
            "event_link": None,
            "error": f"Unexpected error: {exc}",
        }


def update_event(
    event_id: str,
    title: str | None = None,
    start_datetime: str | None = None,
    end_datetime: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Patch only the provided fields on an existing event."""
    try:
        body: dict[str, Any] = {}
        if title is not None:
            body["summary"] = title
        if description is not None:
            body["description"] = description
        if start_datetime is not None:
            start_dt = _parse_iso(start_datetime)
            body["start"] = {
                "dateTime": _to_rfc3339(start_dt),
                "timeZone": config.USER_TIMEZONE,
            }
        if end_datetime is not None:
            end_dt = _parse_iso(end_datetime)
            body["end"] = {
                "dateTime": _to_rfc3339(end_dt),
                "timeZone": config.USER_TIMEZONE,
            }

        if not body:
            return {"success": False, "error": "No fields to update."}

        # Cross-validate start/end if both supplied.
        if start_datetime and end_datetime:
            if _parse_iso(end_datetime) <= _parse_iso(start_datetime):
                return {
                    "success": False,
                    "error": "end_datetime must be after start_datetime",
                }

        service = _get_service()
        service.events().patch(
            calendarId=config.GOOGLE_CALENDAR_ID, eventId=event_id, body=body
        ).execute()
        return {"success": True, "error": None}
    except (RefreshError, RuntimeError) as exc:
        return {"success": False, "error": _auth_error_payload(exc)}
    except HttpError as exc:
        logger.exception("Google Calendar API error in update_event")
        return {"success": False, "error": f"Calendar API error: {exc}"}
    except Exception as exc:
        logger.exception("Unexpected error in update_event")
        return {"success": False, "error": f"Unexpected error: {exc}"}


def delete_event(event_id: str) -> dict[str, Any]:
    try:
        service = _get_service()
        service.events().delete(
            calendarId=config.GOOGLE_CALENDAR_ID, eventId=event_id
        ).execute()
        return {"success": True, "error": None}
    except (RefreshError, RuntimeError) as exc:
        return {"success": False, "error": _auth_error_payload(exc)}
    except HttpError as exc:
        logger.exception("Google Calendar API error in delete_event")
        return {"success": False, "error": f"Calendar API error: {exc}"}
    except Exception as exc:
        logger.exception("Unexpected error in delete_event")
        return {"success": False, "error": f"Unexpected error: {exc}"}


def get_todays_schedule() -> dict[str, Any]:
    """Return today's events in user's local time, plus a one-line summary."""
    try:
        today = _now_local().date()
        result = get_events(today.isoformat(), today.isoformat())
        if not result["success"]:
            return {"success": False, "events": [], "summary": "", "error": result["error"]}
        events = result["events"]
        if not events:
            summary = f"No events scheduled for {today.isoformat()}."
        else:
            lines = [f"{today.isoformat()} ({len(events)} event{'s' if len(events) != 1 else ''}):"]
            for ev in events:
                if ev["all_day"]:
                    lines.append(f"  - All day: {ev['title']}")
                else:
                    lines.append(f"  - {ev['start']} -> {ev['end']}: {ev['title']}")
            summary = "\n".join(lines)
        return {"success": True, "events": events, "summary": summary, "error": None}
    except Exception as exc:
        logger.exception("Unexpected error in get_todays_schedule")
        return {
            "success": False,
            "events": [],
            "summary": "",
            "error": f"Unexpected error: {exc}",
        }


def get_weeks_schedule() -> dict[str, Any]:
    """Return events for the current week (Monday-Sunday) grouped by day."""
    try:
        today = _now_local().date()
        # Monday=0, Sunday=6.
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        result = get_events(monday.isoformat(), sunday.isoformat())
        if not result["success"]:
            return {"success": False, "days": {}, "error": result["error"]}

        days: dict[str, list[dict[str, Any]]] = {
            (monday + timedelta(days=i)).isoformat(): [] for i in range(7)
        }
        for ev in result["events"]:
            # Bucket by date in user's local timezone.
            if ev["all_day"]:
                day_key = ev["start"]  # already YYYY-MM-DD for all-day events
            else:
                # ev["start"] looks like "2025-01-15 14:00 EST"; the first 10 chars are the date.
                day_key = ev["start"][:10]
            if day_key in days:
                days[day_key].append(ev)
        return {"success": True, "days": days, "error": None}
    except Exception as exc:
        logger.exception("Unexpected error in get_weeks_schedule")
        return {"success": False, "days": {}, "error": f"Unexpected error: {exc}"}


def search_events(query: str, days_ahead: int = 14) -> dict[str, Any]:
    """Full-text search across event titles and descriptions in the next N days."""
    try:
        if days_ahead <= 0:
            return {"success": False, "matches": [], "error": "days_ahead must be positive"}
        now = _now_local()
        time_min = now.isoformat()
        time_max = (now + timedelta(days=days_ahead)).isoformat()
        raw_events = _list_events(time_min, time_max, query=query)
        return {
            "success": True,
            "matches": [_normalize_event(e) for e in raw_events],
            "error": None,
        }
    except (RefreshError, RuntimeError) as exc:
        return {"success": False, "matches": [], "error": _auth_error_payload(exc)}
    except HttpError as exc:
        logger.exception("Google Calendar API error in search_events")
        return {"success": False, "matches": [], "error": f"Calendar API error: {exc}"}
    except Exception as exc:
        logger.exception("Unexpected error in search_events")
        return {"success": False, "matches": [], "error": f"Unexpected error: {exc}"}
