#!/bin/bash
set -euo pipefail

log() {
    printf '%s start-session: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

cleanup() {
    kill "${FIREFOX_PID:-}" "${NOVNC_PID:-}" "${XVNC_PID:-}" "${WM_PID:-}" "${SLEEP_PID:-}" \
        2>/dev/null || true
}
trap cleanup EXIT INT TERM

rm -f /tmp/.X1-lock /tmp/.X11-unix/X1
find /home/browser/.mozilla -name lock -delete
find /home/browser/.mozilla -name .parentlock -delete

log "starting Xvnc"
Xvnc :1 \
    -localhost \
    -rfbport 5901 \
    -geometry 1280x800 \
    -depth 24 \
    -SecurityTypes None \
    -AlwaysShared \
    -AcceptKeyEvents \
    -AcceptPointerEvents \
    -nolisten tcp &
XVNC_PID=$!
log "Xvnc pid=$XVNC_PID"

ready=0
for _ in $(seq 1 50); do
    if xdpyinfo -display :1 >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 0.2
done
if [ "$ready" -ne 1 ]; then
    log "VNC display did not start"
    exit 1
fi

log "starting openbox"
openbox &
WM_PID=$!
log "openbox pid=$WM_PID"

start_websockify() {
    websockify --web=/usr/share/novnc 6080 127.0.0.1:5901 &
    NOVNC_PID=$!
    log "websockify pid=$NOVNC_PID"

    ready=0
    for _ in $(seq 1 50); do
        if curl --fail --silent --show-error --max-time 1 \
            http://127.0.0.1:6080/vnc_lite.html >/dev/null 2>&1; then
            ready=1
            break
        fi
        sleep 0.2
    done
    if [ "$ready" -ne 1 ]; then
        log "websockify did not start"
        exit 1
    fi
    NOVNC_HEALTH_FAILURES=0
}

start_websockify

log "starting Firefox"
firefox-esr --no-remote --name meridian-browser --class MeridianBrowser &
FIREFOX_PID=$!
log "Firefox pid=$FIREFOX_PID"

finished=""
while true; do
    sleep 5 &
    SLEEP_PID=$!
    wait_status=0
    wait -n -p finished "$FIREFOX_PID" "$XVNC_PID" "$WM_PID" "$NOVNC_PID" "$SLEEP_PID" \
        || wait_status=$?

    if [ "$finished" = "$SLEEP_PID" ]; then
        if ! timeout 5 xdpyinfo -display :1 >/dev/null 2>&1; then
            log "Xvnc health check failed"
            exit 1
        fi
        if curl --fail --silent --show-error --max-time 3 \
            http://127.0.0.1:6080/vnc_lite.html >/dev/null 2>&1; then
            NOVNC_HEALTH_FAILURES=0
        else
            NOVNC_HEALTH_FAILURES=$((NOVNC_HEALTH_FAILURES + 1))
            log "websockify health check failed (${NOVNC_HEALTH_FAILURES}/2)"
            if [ "$NOVNC_HEALTH_FAILURES" -ge 2 ]; then
                log "restarting unresponsive websockify"
                kill "$NOVNC_PID" 2>/dev/null || true
                wait "$NOVNC_PID" 2>/dev/null || true
                start_websockify
            fi
        fi
    elif [ "$finished" = "$NOVNC_PID" ]; then
        log "websockify exited with status $wait_status; restarting"
        start_websockify
    elif [ "$finished" = "$WM_PID" ]; then
        log "openbox exited with status $wait_status; restarting"
        openbox &
        WM_PID=$!
        log "openbox pid=$WM_PID"
    elif [ "$finished" = "$XVNC_PID" ]; then
        log "Xvnc exited with status $wait_status"
        exit 1
    elif [ "$finished" = "$FIREFOX_PID" ]; then
        log "Firefox exited with status $wait_status"
        exit "$wait_status"
    fi
done
