#!/usr/bin/env bash
# backup.sh — Scally Tracker backup script
# 1. Copies inventory.db to external drive with a timestamp
# 2. Zips the entire project (excluding .git) to external drive with a timestamp
# 3. Commits all changes and pushes to origin main

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_SRC="$PROJECT_DIR/data/inventory.db"
BACKUP_DIR="/Volumes/Expansion/homelab/scally-tracker-backups"
TIMESTAMP="$(date '+%Y-%m-%d_%H-%M-%S')"
DB_DEST="$BACKUP_DIR/scally_tracker_${TIMESTAMP}.db"
ZIP_DEST="$BACKUP_DIR/scally_tracker_code_${TIMESTAMP}.zip"

DB_STATUS="FAILED"
DB_PATH=""
ZIP_STATUS="FAILED"
ZIP_PATH=""
GIT_STATUS="FAILED"
GIT_MSG=""

# ── 1. Create backup directory ────────────────────────────────
mkdir -p "$BACKUP_DIR"

# ── 2. Database backup ────────────────────────────────────────
if cp "$DB_SRC" "$DB_DEST"; then
  DB_STATUS="OK"
  DB_PATH="$DB_DEST"
else
  DB_STATUS="FAILED"
fi

# ── 3. Code zip backup ────────────────────────────────────────
if (cd "$PROJECT_DIR" && zip -r "$ZIP_DEST" . --exclude ".git/*" --exclude ".git" -q); then
  ZIP_STATUS="OK"
  ZIP_PATH="$ZIP_DEST"
else
  ZIP_STATUS="FAILED"
fi

# ── 4. Git commit & push ──────────────────────────────────────
cd "$PROJECT_DIR"
git add -A

if git diff --cached --quiet; then
  GIT_MSG="(no changes to commit)"
  GIT_STATUS="SKIPPED"
else
  CHANGED_FILES="$(git diff --cached --name-only | tr '\n' ' ' | sed 's/ $//')"
  COMMIT_MSG="chore: automated backup commit — ${TIMESTAMP}

Changed files: ${CHANGED_FILES}"

  if git commit -m "$COMMIT_MSG" && git push origin main; then
    GIT_STATUS="OK"
    GIT_MSG="$COMMIT_MSG"
  fi
fi

# ── 5. Summary ────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║              SCALLY TRACKER BACKUP SUMMARY               ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo "  DATABASE BACKUP:  $DB_STATUS"
if [ "$DB_STATUS" = "OK" ]; then
  echo "  Saved to:         $DB_PATH"
else
  echo "  Source:           $DB_SRC"
  echo "  Destination:      $DB_DEST"
  echo "  Check that /Volumes/Expansion is mounted and writable."
fi

echo ""
echo "  CODE ZIP BACKUP:  $ZIP_STATUS"
if [ "$ZIP_STATUS" = "OK" ]; then
  echo "  Saved to:         $ZIP_PATH"
else
  echo "  Destination:      $ZIP_DEST"
  echo "  Check that /Volumes/Expansion is mounted and writable."
fi

echo ""
echo "  GIT COMMIT/PUSH:  $GIT_STATUS"
if [ "$GIT_STATUS" = "OK" ]; then
  echo "  Commit message:"
  echo "$GIT_MSG" | sed 's/^/    /'
elif [ "$GIT_STATUS" = "SKIPPED" ]; then
  echo "  $GIT_MSG"
else
  echo "  Commit or push failed. Check git output above."
fi

echo ""

# Exit non-zero if any critical step failed
if [ "$DB_STATUS" != "OK" ] || [ "$ZIP_STATUS" != "OK" ] || [ "$GIT_STATUS" = "FAILED" ]; then
  exit 1
fi
