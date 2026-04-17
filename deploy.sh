#!/bin/bash
# 0DTE Daily — Deploy to DigitalOcean droplet
# Usage: bash deploy.sh
# Run from: C:\Users\charl\Documents\Zero Day\

set -e

SERVER="deploy@167.99.167.244"
REMOTE_DIR="/opt/zeroday"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Deploying 0DTE Daily to $SERVER..."

# 1. Create remote directory structure and set ownership to container user (UID 10001).
# Requires the deploy user to have passwordless sudo for chown:
#   echo 'deploy ALL=(ALL) NOPASSWD: /bin/chown' | sudo tee /etc/sudoers.d/deploy-chown
ssh "$SERVER" "
  mkdir -p $REMOTE_DIR/daily_briefs $REMOTE_DIR/market_data $REMOTE_DIR/drafts
  touch $REMOTE_DIR/schwab_token.json
  sudo chown -R 10001:10001 $REMOTE_DIR/daily_briefs $REMOTE_DIR/market_data $REMOTE_DIR/drafts $REMOTE_DIR/schwab_token.json
"

# 2. Sync all project files via scp (Windows-compatible)
echo "==> Copying files..."

# Create a temp tar, copy it, extract on server
tar -czf /tmp/zeroday_deploy.tar.gz \
  --exclude=".git" \
  --exclude="__pycache__" \
  --exclude="*.pyc" \
  --exclude="schwab_token.json" \
  --exclude="daily_briefs" \
  --exclude="market_data" \
  --exclude="drafts" \
  --exclude="zero_day_draft_*.html" \
  -C "$LOCAL_DIR" .

scp /tmp/zeroday_deploy.tar.gz "$SERVER:/tmp/zeroday_deploy.tar.gz"
ssh "$SERVER" "tar -xzf /tmp/zeroday_deploy.tar.gz -C $REMOTE_DIR && rm /tmp/zeroday_deploy.tar.gz"
rm /tmp/zeroday_deploy.tar.gz

echo "==> Files synced."

# 3. Ensure .env exists on server (don't overwrite if already there)
ssh "$SERVER" "[ -f $REMOTE_DIR/.env ] && echo '.env already exists, skipping.' || (cp $REMOTE_DIR/.env.server.example $REMOTE_DIR/.env && echo 'Created .env from example — IMPORTANT: fill in values at $REMOTE_DIR/.env')"

# 4. Build and start Docker container
ssh "$SERVER" "
  cd $REMOTE_DIR
  docker compose build
  docker compose up -d
  docker compose ps
"

echo ""
echo "==> Deploy complete."
echo ""
echo "Next steps if first deploy:"
echo "  1. SSH in: ssh $SERVER"
echo "  2. Edit:   nano $REMOTE_DIR/.env"
echo "  3. Push token: bash push_token.sh"
echo "  4. Restart: cd /opt/transcriptautomation && docker compose restart zeroday"
echo ""
echo "Form:    https://optionpit-api.duckdns.org/0dte-daily/"
echo "Status:  https://optionpit-api.duckdns.org/0dte-daily/status"
