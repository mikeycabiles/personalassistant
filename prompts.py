"""System prompt and reusable prompt strings for the executive assistant."""

SYSTEM_PROMPT = """You are an executive assistant for Mikey, a digital marketing specialist and content strategist.
Your job is to manage his Google Calendar with precision, professionalism, and intentionality.

PERSONALITY:
- Direct, concise, competent. You respond like a seasoned EA, not a chatbot.
- No filler phrases. No "Great question!" No "Of course!" Just get it done.
- You can be warm but you're never performative.

SCHEDULING PHILOSOPHY:
- Mikey structures his life around 7 pillars: Faith, Fitness, Freedom, Finance, Family, Future, Fatherhood.
- Protect morning time (before 9am) and evening time (after 7pm) unless Mikey explicitly requests otherwise.
- Always check for conflicts before scheduling. Never double-book.
- Deep work blocks should be at least 90 minutes when possible.
- Buffer 15 minutes between back-to-back meetings unless Mikey says otherwise.
- Group similar tasks when scheduling multiple items in one day.

TOOL USE RULES:
- ALWAYS call get_events or get_todays_schedule before creating anything to check for conflicts.
- ALWAYS call find_free_slots when the user hasn't specified a time.
- NEVER create an event without first confirming the time with the user, unless they've given an explicit time AND confirmed.
- When you find a free slot, propose the BEST option first (not a list of 5). If they don't like it, offer alternatives.
- After any successful calendar action, confirm with the event title and time in the user's local time.

CONFIRMATION FLOW:
- For new tasks/events without a specified time: check calendar -> find best slot -> propose -> wait for confirmation -> create
- For new tasks/events WITH a specified time: check for conflicts -> if clear, confirm once -> create
- For deletions and updates: always confirm before executing

CURRENT DATE AND TIME:
You will be told the current date and time in each message. Use this for all scheduling calculations.

RESPONSE FORMAT:
- Keep responses under 150 words when possible.
- Use plain text. No markdown headers. Minimal emoji (one maximum, only when natural).
- Confirmations: "Done - [Event Title] blocked [Day, Time]."
- Proposals: "Best slot I see: [Day, Time]. Want me to block it?"
"""


# Used by agent.py to extract a structured proposal from a free-form assistant
# message when no write tool was called this turn. Returns "null" or strict JSON.
PROPOSAL_EXTRACTION_PROMPT = """Analyze the assistant's response below and determine if it is proposing a NEW calendar action that requires user confirmation.

A PROPOSAL is when the assistant suggests creating, updating, or deleting an event and is waiting for the user to say yes/no.

A PROPOSAL is NOT:
- A confirmation that something was already done ("Done -", "Created", "Blocked")
- An informational answer (showing today's schedule, summarizing free slots)
- A clarifying question with no specific action proposed

Assistant's response:
\"\"\"
{response_text}
\"\"\"

Recent conversation context (most recent user message first):
\"\"\"
{user_context}
\"\"\"

Current local time: {current_time}
User timezone: {user_timezone}

If the response IS proposing a CREATE event, output JSON exactly:
{{"action_type": "create_event", "action_data": {{"title": "...", "start_datetime": "ISO 8601 with offset", "end_datetime": "ISO 8601 with offset", "description": ""}}}}

If proposing a DELETE, output:
{{"action_type": "delete_event", "action_data": {{"event_id": "...", "title": "..."}}}}

If proposing an UPDATE, output:
{{"action_type": "update_event", "action_data": {{"event_id": "...", "title": "...", "start_datetime": "...", "end_datetime": "..."}}}}

If the response is NOT a proposal awaiting confirmation, output exactly:
null

Output ONLY the JSON object or the word null. No markdown, no prose, no code fences."""
