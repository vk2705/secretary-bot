#!/usr/bin/env bash
set -euo pipefail

# systemd's EnvironmentFile= cannot parse the `export KEY=value` lines in env,
# and duplicating the secrets into a second bare-KEY=value file would put the
# same credentials on disk twice — so this wrapper sources env itself instead.
cd /home/ec2-user/secretary-bot

if [ ! -r /home/ec2-user/secretary-bot/env ]; then
  echo "start_bot.sh: /home/ec2-user/secretary-bot/env is missing or not readable" >&2
  exit 1
fi

set -a
. /home/ec2-user/secretary-bot/env
set +a

exec /home/ec2-user/secretary-bot/venv/bin/python3 /home/ec2-user/secretary-bot/bot.py
