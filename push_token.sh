#!/bin/bash
# 0DTE Daily — Push fresh Schwab token to server
# Run this locally after completing reauth.py
# Usage: bash push_token.sh

SERVER="deploy@167.99.167.244"
REMOTE_DIR="/opt/zeroday"
TOKEN_FILE="schwab_token.json"

if [ ! -f "$TOKEN_FILE" ]; then
  echo "ERROR: $TOKEN_FILE not found locally."
  echo "Run 'python3 scripts/reauth.py' first."
  exit 1
fi

echo "==> Pushing $TOKEN_FILE to server..."
scp "$TOKEN_FILE" "$SERVER:/tmp/$TOKEN_FILE"
ssh "$SERVER" "sudo cp /tmp/$TOKEN_FILE $REMOTE_DIR/$TOKEN_FILE && sudo chown 10001:10001 $REMOTE_DIR/$TOKEN_FILE && rm /tmp/$TOKEN_FILE"
echo "==> Done. Token pushed to $SERVER:$REMOTE_DIR/$TOKEN_FILE"

# Restart container so it picks up the new token
echo "==> Restarting zeroday container..."
ssh "$SERVER" "cd /opt/zeroday && docker compose restart"
echo "==> Container restarted. You're good for 7 days."
