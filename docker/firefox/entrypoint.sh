#!/bin/sh
set -eu

mkdir -p \
    /home/browser/Downloads \
    /home/browser/.config \
    /home/browser/.mozilla \
    /home/browser/.meridian/diagnostics
chown -R browser:browser /home/browser

exec gosu browser /usr/local/bin/start-session
