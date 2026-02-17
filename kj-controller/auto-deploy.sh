#!/bin/bash
# Auto-deploy kj-controller from GitHub
# Polls for new commits, pulls, and restarts the service

REPO_DIR="/opt/nomad/kjbox"
APP_DIR="/opt/nomad/kjbox/kj-controller"
POLL_INTERVAL=60

log() { echo "$(date "+%Y-%m-%d %H:%M:%S") - $1"; }

cd "$REPO_DIR" || exit 1

log "Auto-deploy started (polling every ${POLL_INTERVAL}s)"

while true; do
    git fetch origin main --quiet 2>/dev/null

    LOCAL=$(git rev-parse HEAD)
    REMOTE=$(git rev-parse origin/main)

    if [ "$LOCAL" != "$REMOTE" ]; then
        log "New commit detected: ${REMOTE:0:7}"

        # Check if requirements.txt changed before pulling
        REQ_CHANGED=$(git diff HEAD origin/main -- kj-controller/requirements.txt)

        git reset --hard origin/main --quiet

        # Install new dependencies if requirements changed
        if [ -n "$REQ_CHANGED" ]; then
            log "requirements.txt changed, installing dependencies..."
            "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
        fi

        log "Restarting kj-controller..."
        systemctl restart kj-controller
        systemctl is-active --quiet rotation-display && systemctl restart rotation-display
        log "Deploy complete (${REMOTE:0:7})"
    fi

    sleep "$POLL_INTERVAL"
done
