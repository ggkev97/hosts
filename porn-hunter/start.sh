#!/usr/bin/env bash
# One-step launcher for macOS and Linux: sets everything up (first run only), then starts the
# web app so you can open it from your phone.   Usage:  bash start.sh
set -e
cd "$(dirname "$0")"
PORT="${PORN_HUNTER_PORT:-8765}"

say() { printf '\n== %s\n' "$*"; }
fail() { printf '\nERROR: %s\n\n' "$*" >&2; exit 1; }

# --- 1. Python 3.11+ ------------------------------------------------------------------------
if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
else
  PY=""
  for c in python3.12 python3.13 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      PY="$c"; break
    fi
  done
  [ -n "$PY" ] || fail "Python 3.11 or newer was not found.
Install Python 3.12 from https://www.python.org/downloads/ (run the installer, accept the defaults),
then run this script again."
  say "Creating a private Python environment (first run only)"
  "$PY" -m venv .venv
  PY=.venv/bin/python
fi

# --- 2. Install packages (first run, or when requirements.txt changed) ------------------------
WANT="$("$PY" -c 'import hashlib; print(hashlib.sha256(open("requirements.txt","rb").read()).hexdigest())')"
HAVE="$(cat .venv/.requirements.sha 2>/dev/null || true)"
if [ "$WANT" != "$HAVE" ]; then
  say "Installing packages. This takes a few minutes the first time (it downloads PyTorch)."
  "$PY" -m pip install --upgrade pip >/dev/null
  "$PY" -m pip install -r requirements.txt || fail "Installing packages failed. Check your internet connection and run this script again."
  printf '%s' "$WANT" > .venv/.requirements.sha
fi

# --- 3. A password for the web app (created once, kept in .web_token) -------------------------
if [ ! -s .web_token ]; then
  "$PY" -c 'import secrets; print(secrets.token_urlsafe(12))' > .web_token
  chmod 600 .web_token 2>/dev/null || true
fi
TOKEN="$(tr -d '\r\n ' < .web_token)"
export PORN_HUNTER_WEB_TOKEN="$TOKEN"

# --- 4. Start ----------------------------------------------------------------------------------
cat <<BANNER

==========================================================================
  porn-hunter is starting.

  On your phone (same Wi-Fi as this computer) open the address printed below
  that starts with  http://192.168...  or  http://10...  and log in with:

      Password:  $TOKEN

  The first start downloads the search model (about 600 MB), so give it a few
  minutes. Leave this window open while you use the app. Press Ctrl+C to stop.
==========================================================================

BANNER
exec "$PY" -m porn_hunter web --host 0.0.0.0 --port "$PORT"
