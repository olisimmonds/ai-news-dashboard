#!/bin/bash
set -euo pipefail

log() {
    printf '%s start-session: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

DIAG_DIR="${MERIDIAN_BROWSER_DIAGNOSTICS_DIR:-/home/browser/.meridian/diagnostics}"
MOZILLA_DIR="${HOME}/.mozilla"
PROFILE_POINTER="${DIAG_DIR}/profile-path"
MARKER="${DIAG_DIR}/unclean-shutdown"
PROFILE_DIR=""
SHUTTING_DOWN=0
LAST_STATUS=0
FIREFOX_PID=""
XVNC_PID=""
WM_PID=""
NOVNC_PID=""
SLEEP_PID=""
NOVNC_HEALTH_FAILURES=0

absolute_profile_path() {
    local path="$1"
    case "$path" in
        /*) printf '%s\n' "$path" ;;
        *) printf '%s\n' "${MOZILLA_DIR}/firefox/${path}" ;;
    esac
}

profile_from_profiles_ini() {
    local ini="${MOZILLA_DIR}/firefox/profiles.ini"
    local path=""
    [ -f "$ini" ] || return 1
    path="$(awk -F= '
        /^\[Profile[0-9]+\]/ { in_profile=1; is_default=0; profile_path=""; next }
        /^\[/ { in_profile=0 }
        in_profile && $1=="Path" { profile_path=$2; if (is_default) { print profile_path; exit } }
        in_profile && $1=="Default" && $2=="1" { is_default=1; if (profile_path != "") { print profile_path; exit } }
    ' "$ini" 2>/dev/null || true)"
    [ -n "$path" ] || return 1
    absolute_profile_path "$path"
}

profile_from_installs_ini() {
    local ini="${MOZILLA_DIR}/firefox/installs.ini"
    local path=""
    [ -f "$ini" ] || return 1
    path="$(awk -F= '/^Default=/{print $2; exit}' "$ini" 2>/dev/null || true)"
    [ -n "$path" ] || return 1
    absolute_profile_path "$path"
}

usable_profile() {
    local candidate="$1"
    [ -n "$candidate" ] || return 1
    [ -d "$candidate" ] || return 1
    [ -f "${candidate}/prefs.js" ] || [ -f "${candidate}/times.json" ]
}

find_profile() {
    local candidate
    if [ -f "$PROFILE_POINTER" ]; then
        candidate="$(head -n 1 "$PROFILE_POINTER" 2>/dev/null || true)"
        if usable_profile "$candidate"; then
            printf '%s\n' "$candidate"
            return 0
        fi
    fi

    candidate="$(profile_from_installs_ini || true)"
    if usable_profile "$candidate"; then
        printf '%s\n' "$candidate"
        return 0
    fi

    candidate="$(profile_from_profiles_ini || true)"
    if usable_profile "$candidate"; then
        printf '%s\n' "$candidate"
        return 0
    fi

    candidate="$(
        find "${MOZILLA_DIR}/firefox" -mindepth 2 -maxdepth 2 -name prefs.js \
            -printf '%T@ %h\n' 2>/dev/null | sort -rn | head -n 1 | cut -d' ' -f2- || true
    )"
    if usable_profile "$candidate"; then
        printf '%s\n' "$candidate"
        return 0
    fi

    return 1
}

write_user_js() {
    local profile="$1"
    cat > "$profile/user.js" <<'PREFERENCES'
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("browser.startup.page", 0);
user_pref("browser.tabs.warnOnClose", false);
user_pref("browser.tabs.warnOnCloseOtherTabs", false);
user_pref("dom.ipc.processCount", 2);
user_pref("browser.tabs.unloadOnLowMemory", true);
PREFERENCES
    log "wrote managed user.js preferences"
}

collect_cgroup_metrics() {
    local name
    for name in \
        memory.events \
        memory.peak \
        memory.current \
        memory.max \
        memory.swap.current \
        pids.current \
        pids.max; do
        if [ -r "/sys/fs/cgroup/$name" ]; then
            printf '%s:\n' "$name"
            sed 's/^/  /' "/sys/fs/cgroup/$name" 2>/dev/null || true
        fi
    done
}

persist_snapshot() {
    local label="$1"
    local path="${DIAG_DIR}/${label}-$(date -u +%Y%m%dT%H%M%SZ).log"
    mkdir -p "$DIAG_DIR"
    {
        printf 'label=%s\n' "$label"
        printf 'captured_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf '%s\n' 'cgroup:'
        collect_cgroup_metrics
        printf '%s\n' 'processes:'
        ps -eo pid,ppid,stat,rss,comm,args 2>/dev/null || true
        printf '%s\n' 'profile-session-files:'
        if [ -n "$PROFILE_DIR" ] && [ -d "$PROFILE_DIR" ]; then
            find "$PROFILE_DIR" -maxdepth 2 \
                \( -name 'sessionstore*' -o -name 'sessionCheckpoints.json' \
                -o -path '*/minidumps/*' -o -path '*/crashreports/*' \) \
                -printf '%p %s bytes\n' 2>/dev/null || true
        fi
    } > "$path" 2>&1 || true
    log "wrote diagnostics to $path"
}

quarantine_session_state() {
    local profile="$1"
    local archive="${DIAG_DIR}/session-quarantine/$(date -u +%Y%m%dT%H%M%SZ)"
    local moved=0
    local name
    mkdir -p "$archive"
    for name in \
        sessionstore.jsonlz4 \
        sessionstore.js \
        sessionstore.bak \
        sessionCheckpoints.json; do
        if [ -e "$profile/$name" ]; then
            if mv "$profile/$name" "$archive/$name" 2>/dev/null; then
                moved=1
            else
                log "could not archive $profile/$name"
            fi
        fi
    done
    if [ -d "$profile/sessionstore-backups" ]; then
        if mv "$profile/sessionstore-backups" "$archive/sessionstore-backups" 2>/dev/null; then
            moved=1
        else
            log "could not archive $profile/sessionstore-backups"
        fi
    fi
    for name in minidumps crashreports; do
        if [ -d "$profile/$name" ]; then
            mkdir -p "$archive/$name"
            cp -a "$profile/$name/." "$archive/$name/" 2>/dev/null || true
        fi
    done
    if [ "$moved" -eq 1 ]; then
        log "archived previous session state in $archive"
    fi
}

prepare_profile() {
    local unclean=0
    mkdir -p "$DIAG_DIR"
    PROFILE_DIR="$(find_profile || true)"
    if [ -z "$PROFILE_DIR" ]; then
        PROFILE_DIR="${MOZILLA_DIR}/firefox/meridian.default"
    fi
    mkdir -p "$PROFILE_DIR"
    printf '%s\n' "$PROFILE_DIR" > "$PROFILE_POINTER"

    if [ -f "$MARKER" ]; then
        unclean=1
    fi

    if [ "$unclean" -eq 1 ]; then
        log "previous browser session ended uncleanly"
        find "$MOZILLA_DIR" -name lock -delete 2>/dev/null || true
        find "$MOZILLA_DIR" -name .parentlock -delete 2>/dev/null || true
        quarantine_session_state "$PROFILE_DIR"
    fi

    if [ ! -f "$PROFILE_DIR/user.js" ]; then
        write_user_js "$PROFILE_DIR"
    fi

    rm -f "$MARKER"
    : > "$MARKER"
    log "using Firefox profile $PROFILE_DIR"
    persist_snapshot "startup"
}

stop_child() {
    local pid="${1:-}"
    local name="${2:-process}"
    local grace="${3:-10}"
    local state=""
    local i
    LAST_STATUS=0

    [ -n "$pid" ] || return 0

    if ! kill -0 "$pid" 2>/dev/null; then
        if wait "$pid" 2>/dev/null; then
            LAST_STATUS=0
        else
            LAST_STATUS=$?
        fi
        return 0
    fi

    kill -TERM "$pid" 2>/dev/null || true
    for ((i = 0; i < grace * 5; i++)); do
        state="$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null || true)"
        if [ -z "$state" ] || [ "$state" = "Z" ]; then
            break
        fi
        sleep 0.2
    done

    state="$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null || true)"
    if [ -n "$state" ] && [ "$state" != "Z" ]; then
        log "$name did not stop within ${grace}s; sending SIGKILL"
        kill -KILL "$pid" 2>/dev/null || true
    fi

    if wait "$pid" 2>/dev/null; then
        LAST_STATUS=0
    else
        LAST_STATUS=$?
    fi
}

quit_firefox() {
    local window=""
    local state=""
    local i
    LAST_STATUS=0
    [ -n "$FIREFOX_PID" ] || return 0

    window="$(xdotool search --onlyvisible --limit 1 \
        'meridian-browser|MeridianBrowser|Firefox' 2>/dev/null | head -n 1 || true)"
    if [ -n "$window" ]; then
        xdotool windowactivate --sync "$window" >/dev/null 2>&1 || true
        xdotool key --clearmodifiers ctrl+q >/dev/null 2>&1 || true
        for ((i = 0; i < 50; i++)); do
            state="$(awk '{print $3}' "/proc/$FIREFOX_PID/stat" 2>/dev/null || true)"
            if [ -z "$state" ] || [ "$state" = "Z" ]; then
                break
            fi
            sleep 0.2
        done
    fi

    stop_child "$FIREFOX_PID" "Firefox" 10
}

shutdown() {
    local firefox_status=0
    if [ "$SHUTTING_DOWN" -eq 1 ]; then
        return
    fi
    SHUTTING_DOWN=1
    log "shutting down"

    quit_firefox
    firefox_status=$LAST_STATUS
    FIREFOX_PID=""
    if [ "$firefox_status" -eq 0 ]; then
        rm -f "$MARKER"
        log "Firefox shut down cleanly"
    else
        log "Firefox did not shut down cleanly (status $firefox_status)"
        persist_snapshot "firefox-shutdown-${firefox_status}"
    fi

    stop_child "$NOVNC_PID" "websockify" 5
    NOVNC_PID=""
    stop_child "$WM_PID" "openbox" 5
    WM_PID=""
    stop_child "$XVNC_PID" "Xvnc" 5
    XVNC_PID=""
    stop_child "$SLEEP_PID" "sleep" 1
    SLEEP_PID=""
}

cleanup() {
    local pid
    for pid in "$FIREFOX_PID" "$NOVNC_PID" "$WM_PID" "$XVNC_PID" "$SLEEP_PID"; do
        if [ -n "$pid" ]; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done
    wait 2>/dev/null || true
}

on_signal() {
    shutdown
    exit 0
}

trap on_signal INT TERM
trap cleanup EXIT

prepare_profile
rm -f /tmp/.X1-lock /tmp/.X11-unix/X1

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
    persist_snapshot "xvnc-start-failure"
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
        persist_snapshot "websockify-start-failure"
        exit 1
    fi
    NOVNC_HEALTH_FAILURES=0
}

start_websockify

log "starting Firefox with profile $PROFILE_DIR"
firefox-esr --no-remote --profile "$PROFILE_DIR" --name meridian-browser --class MeridianBrowser &
FIREFOX_PID=$!
log "Firefox pid=$FIREFOX_PID"

finished=""
while true; do
    if [ "$SHUTTING_DOWN" -eq 1 ]; then
        exit 0
    fi

    sleep 5 &
    SLEEP_PID=$!
    wait_status=0
    finished=""
    wait -n -p finished "$FIREFOX_PID" "$XVNC_PID" "$WM_PID" "$NOVNC_PID" "$SLEEP_PID" \
        || wait_status=$?

    if [ "$SHUTTING_DOWN" -eq 1 ]; then
        exit 0
    fi

    if [ "$finished" = "$SLEEP_PID" ]; then
        if ! timeout 5 xdpyinfo -display :1 >/dev/null 2>&1; then
            log "Xvnc health check failed"
            persist_snapshot "xvnc-health-failure"
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
                stop_child "$NOVNC_PID" "websockify" 5
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
        XVNC_PID=""
        persist_snapshot "xvnc-exit-${wait_status}"
        exit 1
    elif [ "$finished" = "$FIREFOX_PID" ]; then
        log "Firefox exited with status $wait_status"
        FIREFOX_PID=""
        if [ "$wait_status" -eq 0 ]; then
            rm -f "$MARKER"
            log "Firefox exited cleanly"
        else
            persist_snapshot "firefox-exit-${wait_status}"
        fi
        exit "$wait_status"
    fi
done
