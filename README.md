# Mikey's Telegram Executive Assistant - Kiki

A production-grade Telegram bot named **Kiki** that manages a Google Calendar via natural-language conversation, powered by Claude Haiku.

**What Kiki does:**
- Schedules tasks and meetings on demand, finding free slots and confirming before booking.
- Sends a **morning briefing at 8:00 EST** with the day's schedule and asks for adjustments.
- Sends an **evening review at 20:00 EST** asking what went wrong (so it can learn) and what's needed for tomorrow.
- Refuses to book over your **W-2 office days** and other blocked days you mark on your Google Calendar.
- Persists scheduling lessons across conversations so it doesn't repeat mistakes.
- Both 8am and 8pm fire at fixed UTC-5 (EST) regardless of daylight savings.

---

## Prerequisites

Before you start, make sure you have:

- [ ] A Google account with Google Calendar
- [ ] A Telegram account
- [ ] An [Anthropic API key](https://console.anthropic.com/settings/keys)
- [ ] A Telegram bot token (from `@BotFather` on Telegram, `/newbot`)
- [ ] Your numeric Telegram user ID (message `@userinfobot` to get it)
- [ ] A [Google Cloud Console](https://console.cloud.google.com) account
- [ ] A [Railway](https://railway.app) account (for deployment)
- [ ] Python 3.11+ installed locally
- [ ] `git` installed locally

---

## 1. Google Cloud Setup

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and sign in.
2. Create a new project (any name — e.g. `mikey-assistant`).
3. **Enable the Google Calendar API:** APIs & Services -> Library -> search "Google Calendar API" -> Enable.
4. **Create OAuth 2.0 credentials:**
   - APIs & Services -> Credentials -> Create Credentials -> OAuth Client ID.
   - If prompted, configure the OAuth consent screen first (see step 5).
   - Application type: **Desktop app**. Name it anything.
   - Click Create. A dialog appears with your Client ID — click **Download JSON**.
   - Rename the downloaded file to `credentials.json` and place it in the project root.
5. **Configure the OAuth consent screen:**
   - APIs & Services -> OAuth consent screen.
   - User Type: **External**. Click Create.
   - App name, user support email, developer email — fill in.
   - Scopes — add `auth/calendar` (full Calendar access).
   - Test users — add your Gmail address. (Required while the app is in Testing mode; otherwise OAuth will refuse.)

---

## 2. Generate `token.json` Locally (one-time)

This step authenticates you with Google and produces `token.json`, which the bot needs to call the Calendar API on your behalf.

```bash
git clone <your-repo-url>
cd personalassistant
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Drop credentials.json (from step 1) into this directory, then:
python generate_token.py
```

A browser window opens for Google sign-in. After you approve, `token.json` is written to the project root and the script confirms success.

---

## 3. Base64-encode credentials for Railway

Railway env vars don't accept multi-line JSON, so we encode both files as base64 strings.

**macOS / Linux:**
```bash
base64 -i credentials.json | tr -d '\n'
base64 -i token.json | tr -d '\n'
```

**Windows PowerShell:**
```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("credentials.json"))
[Convert]::ToBase64String([IO.File]::ReadAllBytes("token.json"))
```

Copy each output string. You'll paste them into Railway in step 6.

---

## 4. Local Development

Run the bot locally with long polling (no public URL needed):

```bash
cp .env.example .env
# Open .env and fill in real values for every variable.
# For local dev, you can leave GOOGLE_CREDENTIALS_JSON and GOOGLE_TOKEN_JSON
# blank — config.py automatically falls back to reading credentials.json
# and token.json from the project root.

python main.py
```

Send `/start` to your bot on Telegram to confirm it's responding.

To stop the bot: Ctrl+C.

---

## 5. GitHub Setup

Push the project to a GitHub repo so Railway can deploy from it.

**Files to push:**

```
main.py
agent.py
calendar_tools.py
database.py
config.py
prompts.py
prompts/...
requirements.txt
.env.example
.gitignore
Procfile
runtime.txt
README.md
generate_token.py
```

**NEVER push these (already in `.gitignore`):**

```
.env
credentials.json
token.json
*.db
```

```bash
git add .
git status                           # double-check nothing sensitive is staged
git commit -m "Initial commit"
git push origin <your-branch>
```

---

## 6. Railway Deployment

1. Go to [railway.app](https://railway.app) -> New Project -> Deploy from GitHub repo.
2. Authorize Railway to access your GitHub account; pick this repo.
3. Once it's connected, go to the service's **Variables** tab and add every variable from `.env.example`:
   - `TELEGRAM_BOT_TOKEN` — from BotFather
   - `TELEGRAM_ALLOWED_USER_ID` — your numeric Telegram user ID
   - `ANTHROPIC_API_KEY` — your Anthropic API key
   - `GOOGLE_CALENDAR_ID` — your Gmail address
   - `GOOGLE_CREDENTIALS_JSON` — paste the base64 string from step 3
   - `GOOGLE_TOKEN_JSON` — paste the base64 string from step 3
   - `USER_TIMEZONE` — e.g. `America/New_York`
   - `ENVIRONMENT` — `production`
   - `WEBHOOK_URL` — your Railway domain (e.g. `https://your-app-name.railway.app`)
   - `PORT` — `8080`
4. Generate a public domain: Settings -> Networking -> Generate Domain. Copy it into `WEBHOOK_URL`.
5. Railway auto-detects `Procfile` and `runtime.txt` and builds the service.
6. **Set your Telegram webhook.** Visit this URL once in your browser (replace placeholders):

   ```
   https://api.telegram.org/bot<YOUR_TELEGRAM_BOT_TOKEN>/setWebhook?url=https://<your-app>.railway.app/webhook
   ```

   You should see `{"ok":true,"result":true,"description":"Webhook was set"}`.
7. Send `/start` to your bot on Telegram.

---

## 7. Testing the Bot

In order, send these to verify everything works:

1. `/start` — should return Kiki's welcome message.
2. `/today` — today's schedule (probably empty if it's a new calendar).
3. `New task: review client proposal, should take 45 minutes` — Kiki checks calendar, proposes a slot.
4. `yes` — Kiki creates the event and confirms.
5. `/today` — the new event should appear.
6. `Move it to 4pm` — Kiki updates and confirms.
7. `Delete that event` — Kiki asks for confirmation.
8. `yes` — gone.

Then test the new behaviors:

9. On Google Calendar, create an all-day event for tomorrow titled `Office`. Then in Telegram: `Schedule a 1-hour meeting tomorrow afternoon`. Kiki should refuse and suggest another day.
10. `I prefer 2-hour deep work blocks` — Kiki saves the lesson.
11. `/lessons` — should list the saved preference.
12. Wait for 8am or 8pm EST — the briefing message should land automatically.

---

## 8. Troubleshooting

**Bot not responding to messages**
- Check Railway logs (Deployments tab -> View Logs).
- Verify the webhook is set: `https://api.telegram.org/bot<TOKEN>/getWebhookInfo`.
- Confirm `TELEGRAM_ALLOWED_USER_ID` matches the user actually messaging the bot. The bot replies "Unauthorized" to anyone else.

**`Calendar authentication failed` errors**
- Token expired or invalidated. Locally, run `python generate_token.py` again, base64-re-encode `token.json`, paste into `GOOGLE_TOKEN_JSON` in Railway, redeploy.

**Google API returns "calendar not found"**
- `GOOGLE_CALENDAR_ID` must match exactly. For your primary calendar, this is your Gmail address (e.g. `mikey@gmail.com`).

**Times appearing in the wrong timezone**
- `USER_TIMEZONE` must be a valid pytz string (e.g. `America/New_York`, `Europe/London`, `Asia/Tokyo`). See the [tz database list](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones).

**`base64: invalid input` when decoding env vars**
- The base64 string must be one line with no whitespace. Re-encode using the exact `tr -d '\n'` command above.

**Bot says "Something went wrong on my end"**
- Generic catch-all. Check Railway logs for the actual exception.

**Pending action keeps expiring**
- Pending actions auto-expire 10 minutes after they're staged. If you take longer to confirm, the bot just re-proposes when you ask again.

---

## How Blocked Days Work

Mikey marks days he can't take meetings as **all-day events** on his Google Calendar. Kiki recognizes any all-day event whose title (case-insensitive) contains one of the configured keywords and refuses to book over it.

**Default keywords:** `office`, `ooo`, `out of office`, `onsite`, `in-person`, `w2`, `do not schedule`. Override via the `BLOCKED_DAY_KEYWORDS` env var (comma-separated).

**Override behavior:** Tell Kiki explicitly — e.g. *"I know it's an office day, schedule it anyway"* — and she'll book over it. Default behavior is to refuse.

**Letting Kiki block a day for you:** *"Block off next Monday and Tuesday for OOO"* and she'll create the all-day events. The chosen label is auto-tagged with a do-not-schedule keyword so future scheduling respects it.

## How Lessons Work

When you tell Kiki she made a scheduling mistake, she calls `remember_scheduling_lesson` to persist a concise rule. Lessons are injected into her system prompt every conversation, so she applies them without you having to repeat yourself. Use `/lessons` to see what she's learned.

Examples that produce lessons:
- *"Don't book me before 9am on Mondays."*
- *"I want at least 30 min after my office days end."*
- *"Deep work blocks should be 2 hours minimum, not 90 minutes."*

## Daily Briefings

Kiki fires two scheduled messages at fixed Eastern Standard Time (UTC-5; ignores DST):

- **08:00 EST** — Morning briefing with today's schedule and an open question for adjustments.
- **20:00 EST** — Evening review asking what went wrong (so she can learn) and what's needed for tomorrow.

Both are sent via Telegram's JobQueue. They require Mikey to have sent `/start` to the bot at least once (Telegram restriction — bots can't message users who haven't initiated contact).

## Architecture

```
Telegram <-> main.py (handlers + security gate + JobQueue)
                |
                v
        agent.py (Claude Haiku + tool loop)
            /         \
           v           v
   calendar_tools.py   database.py
        |                  |
        v                  v
   Google Calendar      SQLite (history, pending actions, learnings)
```

- **agent.py** runs the Anthropic tool-use loop with 11 tools (8 calendar + check_day_blocked + block_day + remember_scheduling_lesson), stages proposals as pending actions, and injects saved lessons into every system prompt.
- **calendar_tools.py** wraps Google Calendar. Every function returns a structured `{success, ..., error}` dict — exceptions never reach the agent.
- **database.py** persists conversation history (last 15 turns), pending actions (10-min TTL), and long-term learnings (uncapped).
- **config.py** validates every environment variable at startup with descriptive errors.
- **main.py** registers the morning/evening JobQueue jobs at fixed EST times.

The bot uses `claude-haiku-4-5-20251001` with `temperature=0` for deterministic scheduling.
