#!/bin/sh
set -eu

curl --fail --silent --show-error --max-time 4 \
    http://127.0.0.1:6080/vnc_lite.html >/dev/null
timeout 4 xdpyinfo -display :1 >/dev/null
pgrep -x Xvnc >/dev/null
pgrep -x openbox >/dev/null
pgrep -f "[w]ebsockify" >/dev/null
pgrep -f "[f]irefox-esr" >/dev/null
