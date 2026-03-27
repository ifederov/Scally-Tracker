#!/bin/bash
# ================================================================
#  Scally Tracker — Clean Setup
#  Run: cd ~/scally-tracker && bash setup.sh
# ================================================================
set -e
GRN='\033[0;32m'; GLD='\033[0;33m'; BLU='\033[0;34m'; RED='\033[0;31m'; BLD='\033[1m'; NC='\033[0m'
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${GLD}${BLD}"
echo "  ╔══════════════════════════════════════╗"
echo "  ║      Scally Tracker — Clean Setup    ║"
echo "  ╚══════════════════════════════════════╝"
echo -e "${NC}  Folder: ${BLU}${APP_DIR}${NC}\n"

# Python
PYTHON=$(which python3 2>/dev/null || echo "")
[ -z "$PYTHON" ] && { echo -e "${RED}✗ python3 not found${NC}"; exit 1; }
echo -e "  ${GRN}✓ Python:${NC} $($PYTHON --version) at $PYTHON"

# Dependencies
echo -e "  ${GRN}✓ Installing dependencies…${NC}"
$PYTHON -m pip install flask requests --quiet 2>/dev/null || pip3 install flask requests --quiet 2>/dev/null || true
$PYTHON -c "import flask, requests" 2>/dev/null || { echo -e "${RED}✗ pip install flask requests${NC}"; exit 1; }

# Data dir
mkdir -p "${APP_DIR}/data"
echo -e "  ${GRN}✓ data/ ready${NC}"

# ntfy topic
echo ""
echo -e "${GLD}  Choose a unique private ntfy topic name — e.g. scally-yourname-42${NC}\n"
read -p "  ntfy topic name: " NTFY_TOPIC
[ -z "$NTFY_TOPIC" ] && { echo -e "${RED}✗ required${NC}"; exit 1; }
sed -i '' "s/scally-tracker-CHANGEME/${NTFY_TOPIC}/" "${APP_DIR}/poller.py"
echo -e "  ${GRN}✓ ntfy topic:${NC} ${NTFY_TOPIC}"

# Patch plists
for PL in "${APP_DIR}/com.scallytracker.poller.plist" "${APP_DIR}/com.scallytracker.web.plist"; do
  sed -i '' "s|PYTHON_PATH|${PYTHON}|g" "$PL"
  sed -i '' "s|APP_PATH|${APP_DIR}|g"   "$PL"
done
echo -e "  ${GRN}✓ plists configured${NC}"

# Remove ALL old agents (hat-tracker, bsc-tracker, scally-tracker variants)
AGENTS="${HOME}/Library/LaunchAgents"
for OLD in com.hattracker.poller.plist com.hattracker.web.plist \
           com.bsctracker.poller.plist  com.bsctracker.web.plist \
           com.scallytracker.poller.plist com.scallytracker.web.plist; do
  [ -f "${AGENTS}/${OLD}" ] && {
    launchctl unload "${AGENTS}/${OLD}" 2>/dev/null || true
    rm -f "${AGENTS}/${OLD}"
    echo -e "  ${GLD}  removed old: ${OLD}${NC}"
  }
done

# Load new agents
cp "${APP_DIR}/com.scallytracker.poller.plist" "$AGENTS/"
cp "${APP_DIR}/com.scallytracker.web.plist"    "$AGENTS/"
launchctl load "${AGENTS}/com.scallytracker.poller.plist"
launchctl load "${AGENTS}/com.scallytracker.web.plist"
echo -e "  ${GRN}✓ launchd agents loaded${NC}"

# Wait for web server
echo -e "  ${BLU}  Waiting for web server…${NC}"; sleep 3
curl -s http://localhost:5001 >/dev/null 2>&1 \
  && echo -e "  ${GRN}✓ Web server up at :5001${NC}" \
  || echo -e "  ${GLD}⚠ Web server starting — check: curl http://localhost:5001${NC}"

# First poll
echo ""
echo -e "${GLD}  Running first poll (baseline — no alerts will fire)…${NC}"
$PYTHON "${APP_DIR}/poller.py"

# Done
TS=$(tailscale ip -4 2>/dev/null || echo "")
echo ""
echo -e "${GLD}${BLD}  ✓ Scally Tracker is running!${NC}"
echo ""
echo -e "  Local:     ${BLU}http://localhost:5001${NC}"
[ -n "$TS" ] && echo -e "  Tailscale: ${BLU}http://${TS}:5001${NC}"
echo ""
echo -e "  ntfy topic: ${GLD}${NTFY_TOPIC}${NC}"
echo -e "  Poll schedule: 7am · 11am · 3pm · 7pm CT"
echo ""
echo -e "  Manual poll:  python3 ${APP_DIR}/poller.py"
echo -e "  Watch logs:   tail -f ${APP_DIR}/data/poller.log"
echo ""
