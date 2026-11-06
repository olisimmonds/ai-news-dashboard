#!/bin/sh
set -eu

cleanup() {
    kill "${FIREFOX_PID:-}" "${NOVNC_PID:-}" "${XVNC_PID:-}" "${WM_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

rm -f /tmp/.X1-lock /tmp/.X11-unix/X1
find /home/browser/.mozilla -name lock -delete
find /home/browser/.mozilla -name .parentlock -delete

Xvnc :1 \
    -localhost=1 \
    -rfbport=5901 \
    -geometry 1280x800 \
    -depth=24 \
    -SecurityTypes=None \
    -AlwaysShared \
    -AcceptKeyEvents \
    -AcceptPointerEvents \
    -xkb \
    -nolisten=tcp &
XVNC_PID=$!

ready=0
for _ in $(seq 1 50); do
    if xdpyinfo -display :1 >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 0.2
done
if [ "$ready" -ne 1 ]; then
    echo "VNC display did not start" >&2
    exit 1
fi

openbox &
WM_PID=$!

websockify --web=/usr/share/novnc 6080 127.0.0.1:5901 &
NOVNC_PID=$!

sleep 1
firefox-esr --no-remote --name meridian-browser --class MeridianBrowser &
FIREFOX_PID=$!

wait "$FIREFOX_PID"
