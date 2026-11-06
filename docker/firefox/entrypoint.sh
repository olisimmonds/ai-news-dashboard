#!/bin/sh
set -eu

mkdir -p /home/browser/Downloads /home/browser/.config /home/browser/.mozilla
chown -R browser:browser /home/browser

exec gosu browser /usr/local/bin/start-session
