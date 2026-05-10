"""One-time local helper to generate token.json from credentials.json.

Run this on your local machine BEFORE deploying to Railway:

    python generate_token.py

It opens a browser for Google OAuth, then writes token.json next to this script.
After it succeeds, base64-encode the file and paste into Railway's
GOOGLE_TOKEN_JSON env var (see README for the exact command).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow


SCOPES = ["https://www.googleapis.com/auth/calendar"]
PROJECT_ROOT = Path(__file__).resolve().parent
CREDENTIALS_PATH = PROJECT_ROOT / "credentials.json"
TOKEN_PATH = PROJECT_ROOT / "token.json"


def main() -> int:
    if not CREDENTIALS_PATH.exists():
        print(f"ERROR: credentials.json not found at {CREDENTIALS_PATH}", file=sys.stderr)
        print(
            "\nDownload it from Google Cloud Console -> APIs & Services -> "
            "Credentials -> your OAuth 2.0 Client ID -> Download JSON. "
            "Rename to credentials.json and place it next to this script.",
            file=sys.stderr,
        )
        return 1

    if TOKEN_PATH.exists():
        # Don't silently overwrite a working token; let the user opt in.
        reply = input(f"{TOKEN_PATH.name} already exists. Overwrite? [y/N]: ").strip().lower()
        if reply not in ("y", "yes"):
            print("Aborted.")
            return 0

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    # run_local_server picks an open localhost port and opens the user's browser.
    # If the browser can't open automatically (e.g., over SSH), the URL is printed
    # to stdout and the user can paste it into a browser on another machine.
    creds = flow.run_local_server(port=0)

    TOKEN_PATH.write_text(creds.to_json())
    os.chmod(TOKEN_PATH, 0o600)

    print(f"\nSuccess. Wrote {TOKEN_PATH}")
    print("\nNext steps:")
    print("  1. Base64-encode the token (commands in README.md).")
    print("  2. Paste the result into Railway's GOOGLE_TOKEN_JSON env var.")
    print("  3. Do NOT commit token.json to git (it's in .gitignore already).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
