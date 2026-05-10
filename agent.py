"""Claude Haiku agent loop with calendar tool orchestration.

Public entrypoint: ``run_agent(user_id, user_message) -> str``.

Responsibilities:
  * Short-circuit yes/no confirmations against any pending action in the DB.
  * Inject current local time into each user message.
  * Drive the tool-use loop until Claude returns a final text response.
  * Detect when the final response is a *proposal* (no write tool called) and
    extract it as a structured pending_action for the next turn.
  * Persist the conversation turn and prune history.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

import anthropic

import calendar_tools
import config
import database as db
from prompts import PROPOSAL_EXTRACTION_PROMPT, SYSTEM_PROMPT


logger = logging.getLogger(__name__)


client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


# --- Tool registry ---------------------------------------------------------

TOOL_FUNCTIONS = {
    "get_events": calendar_tools.get_events,
    "find_free_slots": calendar_tools.find_free_slots,
    "create_event": calendar_tools.create_event,
    "update_event": calendar_tools.update_event,
    "delete_event": calendar_tools.delete_event,
    "get_todays_schedule": calendar_tools.get_todays_schedule,
    "get_weeks_schedule": calendar_tools.get_weeks_schedule,
    "search_events": calendar_tools.search_events,
}

WRITE_TOOL_NAMES = {"create_event", "update_event", "delete_event"}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_events",
        "description": (
            "List calendar events between two dates (inclusive), returned in the user's "
            "local time. Use this to check for conflicts before scheduling, or when Mikey "
            "asks what's on his calendar for a specific date range. For a single day, "
            "pass the same value for start_date and end_date."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD format (user's local timezone).",
                },
                "end_date": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD format (user's local timezone).",
                },
            },
            "required": ["start_date", "end_date"],
        },
    },
    {
        "name": "find_free_slots",
        "description": (
            "Find open time slots of at least `duration_minutes` on a specific date, "
            "within working hours. ALWAYS use this when Mikey asks to schedule something "
            "without giving an exact time. Returns the gaps between existing events."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date in YYYY-MM-DD format.",
                },
                "duration_minutes": {
                    "type": "integer",
                    "description": "Minimum slot duration in minutes.",
                },
                "working_hours_start": {
                    "type": "string",
                    "description": "Earliest time to consider, HH:MM 24-hour. Defaults to 08:00.",
                },
                "working_hours_end": {
                    "type": "string",
                    "description": "Latest time to consider, HH:MM 24-hour. Defaults to 20:00.",
                },
            },
            "required": ["date", "duration_minutes"],
        },
    },
    {
        "name": "create_event",
        "description": (
            "Create a new event on the user's calendar. ONLY call this after the user "
            "has explicitly confirmed the title and time, OR after they've given an "
            "explicit time AND given a clear go-ahead in the same turn. Datetimes must "
            "be ISO 8601 with the user's local timezone offset (e.g. 2025-03-15T14:00:00-05:00)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Event title/summary.",
                },
                "start_datetime": {
                    "type": "string",
                    "description": "Start datetime in ISO 8601 format with timezone offset.",
                },
                "end_datetime": {
                    "type": "string",
                    "description": "End datetime in ISO 8601 format with timezone offset.",
                },
                "description": {
                    "type": "string",
                    "description": "Optional notes/description for the event.",
                },
            },
            "required": ["title", "start_datetime", "end_datetime"],
        },
    },
    {
        "name": "update_event",
        "description": (
            "Update fields on an existing event. Only the fields you provide will change. "
            "ALWAYS confirm with the user before calling this."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "Google Calendar event ID (from get_events or search_events).",
                },
                "title": {"type": "string", "description": "New title (optional)."},
                "start_datetime": {
                    "type": "string",
                    "description": "New start datetime in ISO 8601 with offset (optional).",
                },
                "end_datetime": {
                    "type": "string",
                    "description": "New end datetime in ISO 8601 with offset (optional).",
                },
                "description": {
                    "type": "string",
                    "description": "New description (optional).",
                },
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "delete_event",
        "description": (
            "Delete an event from the calendar. ALWAYS confirm with the user before calling."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "Google Calendar event ID.",
                },
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "get_todays_schedule",
        "description": (
            "Return all events for today in the user's local time. Faster than get_events "
            "when Mikey just asks 'what's today look like?'."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_weeks_schedule",
        "description": (
            "Return all events for the current week (Monday-Sunday), grouped by day."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "search_events",
        "description": (
            "Full-text search event titles and descriptions over the next N days. "
            "Use when Mikey references an event by name and you need its event_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search string (matched against title and description).",
                },
                "days_ahead": {
                    "type": "integer",
                    "description": "How many days from now to search. Default 14.",
                },
            },
            "required": ["query"],
        },
    },
]


# --- Yes/no detection ------------------------------------------------------

_AFFIRMATIVE = {
    "yes", "yeah", "yep", "yup", "y", "ok", "okay", "k", "kk",
    "sure", "confirm", "confirmed", "do it", "go ahead", "go", "do",
    "block it", "schedule it", "lets do it", "let's do it", "make it",
    "sounds good", "perfect", "great",
}
_NEGATIVE = {
    "no", "nope", "n", "cancel", "stop", "scratch that",
    "never mind", "nevermind", "don't", "dont", "abort", "skip",
}


def _normalize(msg: str) -> str:
    return re.sub(r"[^a-z0-9'\s]", "", msg.strip().lower()).strip()


def _is_affirmative(msg: str) -> bool:
    n = _normalize(msg)
    if not n:
        return False
    if len(n.split()) > 4:
        return False
    return n in _AFFIRMATIVE


def _is_negative(msg: str) -> bool:
    n = _normalize(msg)
    if not n:
        return False
    if len(n.split()) > 4:
        return False
    return n in _NEGATIVE


# --- Pending action execution ---------------------------------------------

def _execute_pending(action_type: str, action_data: dict[str, Any]) -> str:
    """Run the staged action against the calendar and format a confirmation."""
    if action_type == "create_event":
        result = calendar_tools.create_event(
            title=action_data["title"],
            start_datetime=action_data["start_datetime"],
            end_datetime=action_data["end_datetime"],
            description=action_data.get("description", ""),
        )
        if not result["success"]:
            return f"Couldn't create that event: {result['error']}"
        # Format the time for the confirmation message.
        try:
            start_dt = datetime.fromisoformat(action_data["start_datetime"]).astimezone(
                config.USER_TZ
            )
            when = start_dt.strftime("%a %b %d, %I:%M %p")
        except Exception:
            when = action_data.get("start_datetime", "")
        return f"Done - {action_data['title']} blocked {when}."

    if action_type == "update_event":
        kwargs = {k: v for k, v in action_data.items() if k != "event_id" and v is not None}
        result = calendar_tools.update_event(action_data["event_id"], **kwargs)
        if not result["success"]:
            return f"Couldn't update that event: {result['error']}"
        title = action_data.get("title", "Event")
        return f"Done - updated {title}."

    if action_type == "delete_event":
        result = calendar_tools.delete_event(action_data["event_id"])
        if not result["success"]:
            return f"Couldn't delete that event: {result['error']}"
        title = action_data.get("title", "event")
        return f"Done - deleted {title}."

    return f"Unknown pending action type: {action_type}"


# --- Tool execution --------------------------------------------------------

def _execute_tool(name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    func = TOOL_FUNCTIONS.get(name)
    if func is None:
        return {"success": False, "error": f"Unknown tool: {name}"}
    try:
        return func(**tool_input)
    except TypeError as exc:
        # Schema/argument mismatch - return as a tool-level error so the agent can recover.
        return {"success": False, "error": f"Invalid tool input for {name}: {exc}"}
    except Exception as exc:
        logger.exception("Tool %s raised", name)
        return {"success": False, "error": f"Tool {name} failed: {exc}"}


# --- Proposal extraction ---------------------------------------------------

def _extract_proposal(
    response_text: str, user_message: str
) -> dict[str, Any] | None:
    """Run a single Haiku call to detect/structure a pending proposal."""
    if not response_text.strip():
        return None
    now_local = datetime.now(config.USER_TZ).strftime("%Y-%m-%d %H:%M %Z")
    prompt = PROPOSAL_EXTRACTION_PROMPT.format(
        response_text=response_text,
        user_context=user_message,
        current_time=now_local,
        user_timezone=config.USER_TIMEZONE,
    )
    try:
        result = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=512,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in result.content if b.type == "text").strip()
    except Exception:
        logger.exception("Proposal extraction call failed")
        return None

    if not text or text.lower() == "null":
        return None

    # Strip accidental code fences.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Proposal extractor returned non-JSON: %r", text[:200])
        return None

    if not isinstance(parsed, dict):
        return None
    if parsed.get("action_type") not in WRITE_TOOL_NAMES:
        return None
    if not isinstance(parsed.get("action_data"), dict):
        return None

    # Light validation per action type.
    data = parsed["action_data"]
    if parsed["action_type"] == "create_event":
        if not all(k in data for k in ("title", "start_datetime", "end_datetime")):
            return None
    elif parsed["action_type"] in ("update_event", "delete_event"):
        if "event_id" not in data:
            return None

    return parsed


# --- Agent entrypoint ------------------------------------------------------

def run_agent(user_id: str, user_message: str) -> str:
    """Top-level: process one user turn and return the assistant's reply text."""
    try:
        # 1. Pending action confirmation short-circuit.
        pending = db.get_pending_action(user_id)
        if pending is not None:
            if _is_affirmative(user_message):
                reply = _execute_pending(pending["action_type"], pending["action_data"])
                db.clear_pending_action(user_id)
                db.save_message(user_id, "user", user_message)
                db.save_message(user_id, "assistant", reply)
                db.clear_old_history(user_id)
                return reply
            if _is_negative(user_message):
                db.clear_pending_action(user_id)
                reply = "Cancelled."
                db.save_message(user_id, "user", user_message)
                db.save_message(user_id, "assistant", reply)
                db.clear_old_history(user_id)
                return reply
            # Otherwise let the agent see the message in context. The pending
            # action stays in place; if the agent re-proposes it'll be replaced.

        # 2. Build the message list with current-time-prefixed user message.
        history = db.get_conversation_history(user_id, limit=15)
        now_local = datetime.now(config.USER_TZ).strftime(
            "%A, %B %d, %Y at %I:%M %p %Z"
        )
        prepended = f"[Current time: {now_local}]\n{user_message}"

        messages: list[dict[str, Any]] = list(history) + [
            {"role": "user", "content": prepended}
        ]

        # 3. Tool-use loop. Cap iterations as a safety net.
        write_tools_called: list[str] = []
        response = None
        for _ in range(10):
            response = client.messages.create(
                model=config.CLAUDE_MODEL,
                max_tokens=1024,
                temperature=0,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=TOOLS,
            )
            if response.stop_reason != "tool_use":
                break

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            tool_results = []
            for tu in tool_use_blocks:
                if tu.name in WRITE_TOOL_NAMES:
                    write_tools_called.append(tu.name)
                result = _execute_tool(tu.name, dict(tu.input))
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": json.dumps(result),
                    }
                )

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
        else:
            # Loop hit the iteration cap.
            logger.warning("Agent loop hit iteration cap for user %s", user_id)

        # 4. Extract final assistant text.
        final_text = ""
        if response is not None:
            final_text = "".join(
                b.text for b in response.content if b.type == "text"
            ).strip()
        if not final_text:
            final_text = "Done."

        # 5. Pending action management:
        #    - If a write tool ran, the action is complete -> clear any prior pending.
        #    - Otherwise, ask the extractor whether the response is a proposal.
        if write_tools_called:
            db.clear_pending_action(user_id)
        else:
            proposal = _extract_proposal(final_text, user_message)
            if proposal is not None:
                db.save_pending_action(
                    user_id, proposal["action_type"], proposal["action_data"]
                )
            else:
                # No new proposal in this turn; clear any stale one too.
                db.clear_pending_action(user_id)

        # 6. Persist conversation turn and prune.
        db.save_message(user_id, "user", user_message)
        db.save_message(user_id, "assistant", final_text)
        db.clear_old_history(user_id)

        return final_text

    except anthropic.APIError as exc:
        logger.exception("Anthropic API error in run_agent")
        return "Something went wrong on my end. Try again in a moment."
    except Exception:
        logger.exception("Unexpected error in run_agent")
        return "Something went wrong on my end. Try again in a moment."
