#!/usr/bin/env bash
# ===================================================================
#  Brickonomy - collect fresh prices from BrickLink / eBay / BrickOwl.
#  Run:  ./scan.sh          (first time:  chmod +x scan.sh)
# ===================================================================
set -u
cd "$(dirname "$0")"

VPY=".venv/bin/python"
if [ ! -x "$VPY" ]; then
    echo " [X] Brickonomy is not set up yet on this machine."
    echo "     Run  ./run.sh  once first, then come back here."
    exit 1
fi

cat <<'EOF'

 ==========================================
   Brickonomy - price scan
 ==========================================

  Scanning opens a headless Chrome per source and waits a few seconds
  between requests on purpose, so the shops do not block us. Expect
  roughly half a minute per set. You can leave it running.

   1  My portfolio      - every set you own or want
   2  Stale only        - just the sets whose prices are past the TTL
   3  One set           - type a set or minifig number
   4  Everything        - the whole catalog (very slow, hours)
   5  Full LEGO catalog - re-download set/minifig names from Rebrickable
   6  Rebuild the published site (docs/)
   7  Sign in to eBay  - unlocks SOLD prices (one time, opens a browser)
   0  Quit

EOF

printf "  Choose [1]: "
read -r CHOICE || CHOICE=""
CHOICE="${CHOICE:-1}"

case "$CHOICE" in
    0) exit 0 ;;
    1) echo; echo " Scanning your portfolio..."
       "$VPY" -m brickonomy.refresh --scope portfolio ;;
    2) echo; echo " Scanning stale sets..."
       "$VPY" -m brickonomy.refresh --scope stale ;;
    3) printf "  Set or minifig number (e.g. 75192 or sw0879): "
       read -r ITEM || ITEM=""
       if [ -z "$ITEM" ]; then echo " Nothing entered."; exit 1; fi
       "$VPY" -m brickonomy.refresh --item "$ITEM" --force ;;
    4) echo " This scans EVERY item in the database and can run for hours."
       printf "  Continue? [y/N]: "
       read -r SURE || SURE=""
       case "$SURE" in [yY]*) "$VPY" -m brickonomy.refresh --scope all ;;
                       *) exit 0 ;; esac ;;
    5) echo; echo " Downloading the full LEGO catalog from Rebrickable..."
       "$VPY" -m brickonomy.rebrickable --minifigs ;;
    6) echo; echo " Rebuilding the static site into docs/ ..."
       "$VPY" -m brickonomy.export
       echo
       echo ' To publish it:  git add docs && git commit -m "Update site" && git push' ;;
    7) echo; echo " eBay only shows SOLD prices to a signed-in browser."
       echo " A Chrome window will open - sign in there, then come back here."
       "$VPY" -m brickonomy.ebay_login ;;
    *) echo " Not a valid choice."; exit 1 ;;
esac

echo
echo " Finished. Start the website with ./run.sh to see the new numbers."
