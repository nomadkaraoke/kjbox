#!/usr/bin/env bash
# kjbox "local stack" for the mobile app's E2E (spec §8.1): the REAL kj-controller
# Flask app on 127.0.0.1:8040, hermetic (no player, overlay, downloads, SMS, push or
# sheet sync), seeded with the 2026-09-24 fixture night (event code 2121), with the
# /sing/api/test/* API mounted. It verifies app bearers against gen's local stack
# (:8010) and sends sung songs to decide's local stack (:8030), both with the
# partner secret "local-stack-secret". It never touches the real box or ~/kjdata.
#
# Usage:
#   scripts/local-stack.sh up        start in the background; waits until
#                                    GET /sing/api/v1/night?t=2121 answers (401 without a bearer)
#   scripts/local-stack.sh down      stop it
#   scripts/local-stack.sh reset     stop, wipe the data dir, start fresh (fixture night re-seeded)
#   scripts/local-stack.sh status    JSON readiness (running, ready, test_api, gen, decide, …)
#   scripts/local-stack.sh logs      tail the server log
#   scripts/local-stack.sh serve     run in the FOREGROUND (CI / debugging)
#
# Env: KJBOX_LOCALSTACK_DIR (default ~/kjdata-localstack), KJBOX_LOCALSTACK_PORT (8040),
#      PYTHON (python3), KJ_CONFIG_OVERRIDES (extra JSON config, merged over the
#      stack's gen/decide settings). See docs/TESTING.md § "Local stack".

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KJ_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="${KJBOX_LOCALSTACK_DIR:-$HOME/kjdata-localstack}"
PORT="${KJBOX_LOCALSTACK_PORT:-8040}"
HOST="127.0.0.1"
PY="${PYTHON:-python3}"
PIDFILE="$DATA_DIR/server.pid"
LOG="$DATA_DIR/server.log"
FIXTURE="$KJ_DIR/tests/fixtures/localstack/night.json"
BASE="http://$HOST:$PORT"
GEN_URL="http://127.0.0.1:8010"
DECIDE_URL="http://127.0.0.1:8030"
SECRET="local-stack-secret"

guard_data_dir() {
    local real_data real_kj
    real_data="$(cd "$DATA_DIR" 2>/dev/null && pwd -P || echo "$DATA_DIR")"
    real_kj="$(cd "$HOME/kjdata" 2>/dev/null && pwd -P || echo "$HOME/kjdata")"
    if [[ -z "$DATA_DIR" || "$real_data" == "$HOME" || "$real_data" == "/" \
          || "$real_data" == "$real_kj" || "$real_data" == "$real_kj"/* ]]; then
        echo "refusing: KJBOX_LOCALSTACK_DIR=$DATA_DIR is not a local-stack dir" >&2
        exit 1
    fi
}

overrides() {
    # The stack's gen/decide wiring, with any KJ_CONFIG_OVERRIDES merged on top.
    "$PY" - "$GEN_URL" "$DECIDE_URL" "$SECRET" <<'EOF'
import json, os, sys
gen, decide, secret = sys.argv[1:4]
cfg = {"gen_api_url": gen, "gen_api_token": "local-stack-admin", "gen_kjbox_secret": secret,
       "decide_api_url": decide, "decide_kjbox_secret": secret}
extra = os.environ.get("KJ_CONFIG_OVERRIDES")
if extra:
    cfg.update(json.loads(extra))
print(json.dumps(cfg))
EOF
}

is_running() {
    [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

http_code() {
    curl -s -o /dev/null -m 3 -w '%{http_code}' "$1" 2>/dev/null || true
}

night_code() {
    http_code "$BASE/sing/api/v1/night?t=2121"
}

cmd_up() {
    guard_data_dir
    mkdir -p "$DATA_DIR"
    if is_running; then
        echo "kjbox local stack already running (pid $(cat "$PIDFILE")) at $BASE"
    else
        local busy
        busy="$(night_code)"
        if [[ "$busy" != "000" && -n "$busy" ]]; then
            echo "port $PORT is already in use by something else (HTTP $busy)" >&2
            exit 1
        fi
        (
            cd "$KJ_DIR"
            nohup env KJBOX_TEST_API=1 KJBOX_LOCALSTACK_DIR="$DATA_DIR" \
                KJ_CONFIG_OVERRIDES="$(overrides)" \
                "$PY" dev_server.py --db "$DATA_DIR/rotation.db" --port "$PORT" \
                --host "$HOST" --no-player --fixture "$FIXTURE" >>"$LOG" 2>&1 &
            echo $! >"$PIDFILE"
        )
    fi
    for _ in $(seq 1 90); do
        if [[ "$(night_code)" == "401" ]]; then
            echo "✅ kjbox local stack ready at $BASE (event code 2121, data $DATA_DIR)"
            return 0
        fi
        if ! is_running; then
            break
        fi
        sleep 1
    done
    echo "❌ kjbox local stack did not become ready at $BASE; last log lines:" >&2
    tail -n 40 "$LOG" >&2 || true
    exit 1
}

cmd_down() {
    if is_running; then
        local pid
        pid="$(cat "$PIDFILE")"
        kill "$pid" 2>/dev/null || true
        for _ in $(seq 1 20); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.5
        done
        kill -9 "$pid" 2>/dev/null || true
        echo "kjbox local stack stopped"
    else
        echo "kjbox local stack not running"
    fi
    rm -f "$PIDFILE"
}

cmd_reset() {
    guard_data_dir
    cmd_down
    if [[ -d "$DATA_DIR" ]]; then
        find "$DATA_DIR" -mindepth 1 -maxdepth 1 ! -name 'server.log' -exec rm -rf {} +
    fi
    cmd_up
}

cmd_status() {
    local running=false pid=null night test_api gen decide
    if is_running; then
        running=true
        pid="$(cat "$PIDFILE")"
    fi
    night="$(night_code)"
    test_api="$(http_code "$BASE/sing/api/test/state")"
    gen="$(http_code "$GEN_URL/")"
    decide="$(http_code "$DECIDE_URL/")"
    "$PY" - "$running" "$pid" "$night" "$test_api" "$gen" "$decide" "$BASE" "$DATA_DIR" "$LOG" <<'EOF'
import json, sys
running, pid, night, test_api, gen, decide, base, data_dir, log = sys.argv[1:10]
up = lambda code: code not in ("", "000")
print(json.dumps({
    "service": "kjbox",
    "running": running == "true",
    "pid": None if pid == "null" else int(pid),
    "url": base,
    "ready": night == "401",
    "night_status": int(night) if night.isdigit() and night != "000" else None,
    "test_api": test_api == "200",
    "event_code": "2121",
    "gen": {"url": "http://127.0.0.1:8010", "reachable": up(gen)},
    "decide": {"url": "http://127.0.0.1:8030", "reachable": up(decide)},
    "data_dir": data_dir,
    "log": log,
}))
EOF
}

cmd_serve() {
    guard_data_dir
    mkdir -p "$DATA_DIR"
    cd "$KJ_DIR"
    exec env KJBOX_TEST_API=1 KJBOX_LOCALSTACK_DIR="$DATA_DIR" \
        KJ_CONFIG_OVERRIDES="$(overrides)" \
        "$PY" dev_server.py --db "$DATA_DIR/rotation.db" --port "$PORT" \
        --host "$HOST" --no-player --fixture "$FIXTURE"
}

case "${1:-}" in
    up) cmd_up ;;
    down) cmd_down ;;
    reset) cmd_reset ;;
    status) cmd_status ;;
    logs) tail -n "${2:-100}" -f "$LOG" ;;
    serve) cmd_serve ;;
    *)
        echo "usage: $0 up|down|reset|status|logs|serve" >&2
        exit 2
        ;;
esac
