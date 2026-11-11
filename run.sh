#!/usr/bin/env bash
#
# Start the Paravent development server.
#
#   ./run.sh              local only, auto-reload   -> http://localhost:8000
#   ./run.sh --lan        reachable from the iPad   -> http://<your-ip>:8000
#   ./run.sh --port 9000  a different port
#   ./run.sh --prod       no auto-reload
#
# Everything else is passed straight through to uvicorn.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

HOST="127.0.0.1"
PORT="8000"
RELOAD="--reload"
EXTRA=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --lan)   HOST="0.0.0.0"; shift ;;
    --prod)  RELOAD=""; shift ;;
    --port)  PORT="${2:?--port needs a number}"; shift 2 ;;
    -h|--help)
      sed -n '3,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *)       EXTRA+=("$1"); shift ;;
  esac
done

# --- the virtualenv -----------------------------------------------------------
# uvicorn is not on the global PATH; always run the venv's copy so the script
# works whether or not the venv happens to be activated.
if [[ ! -x .venv/bin/uvicorn ]]; then
  echo "No virtualenv found at .venv/" >&2
  echo "Create it with:  uv venv && uv pip install -e '.[dev]'" >&2
  exit 1
fi

# --- configuration ------------------------------------------------------------
# Paravent ships no 5e content; the corpus location comes from .env. Without it
# the server starts but every category loads zero entries, which looks like a bug
# rather than a missing config, so say so up front.
if [[ ! -f .env ]]; then
  echo "Warning: no .env found. Copy .env.example and set PARAVENT_REFERENCE_DIR" >&2
  echo "         to your 5eTools data, or the compendium will load empty." >&2
  echo >&2
fi

# --- port in use --------------------------------------------------------------
# A stale server from an earlier run is the common case. Report it by PID instead
# of letting uvicorn fail with "address already in use".
if lsof -ti:"$PORT" >/dev/null 2>&1; then
  echo "Port $PORT is already in use by PID(s): $(lsof -ti:"$PORT" | tr '\n' ' ')" >&2
  echo "Stop it with:  kill \$(lsof -ti:$PORT)      or use --port to pick another" >&2
  exit 1
fi

# --- where to point a browser -------------------------------------------------
if [[ "$HOST" == "0.0.0.0" ]]; then
  LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
  echo "Paravent -> http://${LAN_IP:-<this-machine>}:$PORT   (LAN: iPad, phone)"
else
  echo "Paravent -> http://localhost:$PORT"
fi
echo "API docs -> http://localhost:$PORT/docs"
echo

# macOS ships Bash 3.2, where `set -u` treats an empty array's expansion as an
# unbound variable. `${EXTRA[@]+"${EXTRA[@]}"}` expands to nothing when the array
# is empty and to the quoted elements otherwise.
exec .venv/bin/uvicorn app.main:app \
  --host "$HOST" --port "$PORT" $RELOAD ${EXTRA[@]+"${EXTRA[@]}"}
