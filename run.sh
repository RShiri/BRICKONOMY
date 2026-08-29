#!/usr/bin/env bash
# ===================================================================
#  Brickonomy - start the website on this Mac / Linux box.
#  Run:  ./run.sh          (first time:  chmod +x run.sh)
#  Safe to run again any time.
# ===================================================================
set -u
cd "$(dirname "$0")"

PORT="${PORT:-8000}"

echo
echo " =========================================="
echo "   Brickonomy - LEGO price tracker"
echo " =========================================="
echo

# --- 1. Find Python ---------------------------------------------------
PY=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
if [ -z "$PY" ]; then
    echo " [X] Python is not installed."
    echo "     macOS:  brew install python   (or https://www.python.org/downloads/)"
    echo "     Linux:  sudo apt install python3 python3-venv"
    exit 1
fi

if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    echo " [X] Your Python is too old. Version 3.9 or newer is needed."
    "$PY" --version
    exit 1
fi

# --- 2. Private environment for this app ------------------------------
if [ ! -x ".venv/bin/python" ]; then
    echo " [1/4] Creating a private Python environment (one time)..."
    if ! "$PY" -m venv .venv; then
        echo " [X] Could not create the environment."
        echo "     On Debian/Ubuntu you may need:  sudo apt install python3-venv"
        exit 1
    fi
else
    echo " [1/4] Environment ready."
fi
VPY=".venv/bin/python"

# --- 3. Dependencies --------------------------------------------------
echo " [2/4] Checking dependencies (quiet, may take a minute the first time)..."
"$VPY" -m pip install --quiet --upgrade pip
if ! "$VPY" -m pip install --quiet -r brickonomy/requirements.txt; then
    echo " [X] Could not install the dependencies (no internet?)."
    exit 1
fi

# --- 4. First run: build the database ---------------------------------
if [ ! -f "brickonomy.db" ]; then
    echo " [3/4] First run - preparing your data..."
    "$VPY" -m brickonomy.importer
    echo
    echo "     The full LEGO catalog is every set ever released (~34,000 items),"
    echo "     so search works like BrickLink. It needs internet and ~1 minute."
    printf "     Download it now? [Y/n]: "
    read -r GETCAT || GETCAT=""
    case "$GETCAT" in
        [nN]*) echo "     Skipped. You can add it later with ./scan.sh (option 5)." ;;
        *)     "$VPY" -m brickonomy.rebrickable --minifigs ;;
    esac
else
    echo " [3/4] Database ready."
fi

# --- 5. Serve ---------------------------------------------------------
echo " [4/4] Starting the website..."
echo
echo "     Open:  http://127.0.0.1:${PORT}"
echo "     Stop:  press Ctrl+C"
echo

# Open the browser a few seconds later, once uvicorn is actually listening.
( sleep 4
  if command -v open >/dev/null 2>&1; then open "http://127.0.0.1:${PORT}"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "http://127.0.0.1:${PORT}"
  fi ) >/dev/null 2>&1 &

exec "$VPY" -m uvicorn brickonomy.web.app:app --host 127.0.0.1 --port "$PORT"
