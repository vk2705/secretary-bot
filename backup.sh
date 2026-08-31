#!/usr/bin/env bash
# backup.sh — hourly snapshot of state.json + bot_memory.db to a private
# GitHub repo (vk2705/secretary-bot-backups), separate from this repo so
# personal data (journal, tasks, behavioral memory) never touches the
# public secretary-bot code repository.
#
# Run manually or via cron:
#   0 * * * * /home/ec2-user/secretary-bot/backup.sh >> /home/ec2-user/secretary-bot/backup.log 2>&1
#
# Copies atomic state.json and creates a verified SQLite online snapshot, so
# committed WAL transactions are included without stopping either service.

set -euo pipefail

REPO=/home/ec2-user/secretary-bot
BACKUP_REPO=/home/ec2-user/secretary-bot-backups

if [ ! -d "$BACKUP_REPO/.git" ]; then
  echo "backup.sh: $BACKUP_REPO is not a git repo — run the one-time setup first" >&2
  exit 1
fi

cp "$REPO/state.json" "$BACKUP_REPO/state.json"
"$REPO/venv/bin/python" "$REPO/sqlite_snapshot.py" \
  "$REPO/bot_memory.db" "$BACKUP_REPO/bot_memory.db"

cd "$BACKUP_REPO"

git add state.json bot_memory.db

if git diff --cached --quiet; then
  echo "$(date -u +%FT%TZ) no data changes"
else
  git commit -q -m "Snapshot $(date -u +%FT%TZ)"
fi

if [ "$(git rev-list --count origin/main..HEAD)" -gt 0 ]; then
  git push -q origin main
  echo "$(date -u +%FT%TZ) backup pushed"
else
  echo "$(date -u +%FT%TZ) backup already current"
fi
