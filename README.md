# Mikey's Telegram Executive Assistant

A production-grade Telegram bot that manages a Google Calendar via natural-language conversation, powered by Claude Haiku.

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

1. `/start` — should return the welcome message.
2. `/today` — today's schedule (probably empty if it's a new calendar).
3. `New task: review client proposal, should take 45 minutes` — bot checks calendar, proposes a slot.
4. `yes` — bot creates the event and confirms.
5. `/today` — the new event should appear.
6. `Move it to 4pm` — bot updates and confirms.
7. `Delete that event` — bot asks for confirmation.
8. `yes` — gone.

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

## Architecture

```
Telegram <-> main.py (handlers + security gate)
                |
                v
        agent.py (Claude Haiku + tool loop)
            /         \
           v           v
   calendar_tools.py   database.py
        |                  |
        v                  v
   Google Calendar      SQLite
```

- **agent.py** runs the Anthropic tool-use loop, executes calendar tools, and stages proposals.
- **calendar_tools.py** wraps the Google Calendar API. Every function returns a structured `{success, ..., error}` dict — exceptions never reach the agent.
- **database.py** persists conversation history (last 15 turns) and pending actions (10-minute TTL).
- **config.py** validates every environment variable at startup with descriptive errors.

The bot uses `claude-haiku-4-5-20251001` with `temperature=0` for deterministic scheduling.
