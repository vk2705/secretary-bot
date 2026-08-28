#!/usr/bin/env bash
# backup.sh — hourly snapshot of state.json + bot_memory.db to a private
# GitHub repo (vk2705/secretary-bot-backups), separate from this repo so
# personal data (journal, tasks, behavioral memory) never touches the
# public secretary-bot code repository.
#
# Run manually or via cron:
#   0 * * * * /home/ec2-user/secretary-bot/backup.sh >> /home/ec2-user/secretary-bot/backup.log 2>&1
#
# Copies both files as-is (no stop/restart of the bot — a mid-write
# state.json read is at worst a skipped backup that hour, never a corrupted
# one: save_state() writes atomically via tempfile+os.replace, so any read
# of state.json on disk is always a complete, consistent version). Commits
# and pushes only if something actually changed.

set -euo pipefail

REPO=/home/ec2-user/secretary-bot
BACKUP_REPO=/home/ec2-user/secretary-bot-backups

if [ ! -d "$BACKUP_REPO/.git" ]; then
  echo "backup.sh: $BACKUP_REPO is not a git repo — run the one-time setup first" >&2
  exit 1
fi

cp "$REPO/state.json" "$BACKUP_REPO/state.json"
cp "$REPO/bot_memory.db" "$BACKUP_REPO/bot_memory.db"

cd "$BACKUP_REPO"

git add state.json bot_memory.db

if git diff --cached --quiet; then
  echo "$(date -u +%FT%TZ) no changes, skipping commit"
  exit 0
fi

git commit -q -m "Snapshot $(date -u +%FT%TZ)"
git push -q origin main
echo "$(date -u +%FT%TZ) backup committed and pushed"
