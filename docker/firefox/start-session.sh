#!/bin/bash
set -euo pipefail

log() {
    printf '%s start-session: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

DIAG_DIR="${MERIDIAN_BROWSER_DIAGNOSTICS_DIR:-/home/browser/.meridian/diagnostics}"
MOZILLA_DIR="${HOME}/.mozilla"
PROFILE_POINTER="${DIAG_DIR}/profile-path"
PROFILE_DIR=""
SHUTTING_DOWN=0
SHUTDOWN_STATUS=0
LAST_STATUS=0
FIREFOX_PID=""
XVNC_PID=""
WM_PID=""
NOVNC_PID=""
SLEEP_PID=""
PROCESS_SAMPLES="${DIAG_DIR}/process-samples.log"
PROCESS_SAMPLE_LINES=4000

read_cgroup_value() {
    local name="$1"
    local controller="${2:-}"
    local path
    for path in "/sys/fs/cgroup/$name" ${controller:+/sys/fs/cgroup/$controller/$name}; do
        if [ -r "$path" ]; then
            cat "$path" 2>/dev/null || true
            return 0
        fi
    done
    return 0
}

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
user_pref("dom.ipc.processCount.file", 1);
user_pref("dom.ipc.processCount.extension", 1);
user_pref("dom.ipc.processCount.privilegedabout", 1);
user_pref("dom.ipc.processCount.privilegedmozilla", 1);
user_pref("dom.ipc.processCount.webIsolated", 1);
user_pref("dom.ipc.processCount.webServiceWorker", 1);
user_pref("browser.tabs.unloadOnLowMemory", true);
PREFERENCES
    log "wrote managed user.js preferences"
}

write_environment_info() {
    local path="${DIAG_DIR}/environment.log"
    mkdir -p "$DIAG_DIR"
    {
        printf 'captured_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf 'firefox_version=%s\n' "$(firefox-esr --version 2>&1 | head -n 1 || true)"
        printf 'firefox_package_version=%s\n' "$(dpkg-query -W -f='${Version}' firefox-esr 2>/dev/null || true)"
        printf 'image_firefox_version_file=%s\n' "$(cat /usr/share/meridian/firefox-esr-version 2>/dev/null || true)"
        printf 'kernel=%s\n' "$(uname -srmo 2>/dev/null || uname -a || true)"
        printf 'os_release=%s\n' "$(tr '\n' ' ' < /etc/os-release 2>/dev/null || true)"
        printf 'proc_cmdline=%s\n' "$(cat /proc/cmdline 2>/dev/null || true)"
        printf 'cgroup_type=%s\n' "$(stat -fc %T /sys/fs/cgroup 2>/dev/null || true)"
        printf 'cgroup_controllers=%s\n' \
            "$(cat /sys/fs/cgroup/cgroup.controllers 2>/dev/null || true)"
        printf 'pids_max=%s\n' "$(read_cgroup_value pids.max pids)"
        printf 'xvnc_version=%s\n' "$(Xvnc -version 2>&1 | head -n 1 || true)"
    } > "$path" 2>&1 || true
}

sample_processes() {
    mkdir -p "$DIAG_DIR"
    {
        printf '=== %s ===\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf 'tasks_current=%s\n' "$(read_cgroup_value pids.current pids)"
        printf 'tasks_max=%s\n' "$(read_cgroup_value pids.max pids)"
        ps -eo pid,ppid,nlwp,stat,etimes,rss,comm,args 2>/dev/null || true
    } >> "$PROCESS_SAMPLES" 2>/dev/null || true
    if [ "$(wc -l < "$PROCESS_SAMPLES" 2>/dev/null || printf '0')" -gt "$PROCESS_SAMPLE_LINES" ]; then
        tail -n $((PROCESS_SAMPLE_LINES / 2)) "$PROCESS_SAMPLES" > "${PROCESS_SAMPLES}.tmp" 2>/dev/null \
            && mv "${PROCESS_SAMPLES}.tmp" "$PROCESS_SAMPLES" 2>/dev/null || true
    fi
}

rotate_crash_artifacts() {
    local count
    count="$(find "$DIAG_DIR" -maxdepth 1 -mindepth 1 -type d -name 'crash-*' 2>/dev/null | wc -l)"
    if [ "$count" -gt 20 ]; then
        find "$DIAG_DIR" -maxdepth 1 -mindepth 1 -type d -name 'crash-*' \
            -printf '%T@ %p\n' 2>/dev/null \
            | sort -n | head -n $((count - 20)) | cut -d' ' -f2- \
            | while IFS= read -r stale; do rm -rf "$stale"; done
    fi
}

collect_crash_artifacts() {
    local reason="$1"
    local archive="${DIAG_DIR}/crash-$(date -u +%Y%m%dT%H%M%SZ)"
    local profile="${PROFILE_DIR:-}"
    local name
    mkdir -p "$archive"
    if [ -n "$profile" ] && [ -d "$profile" ]; then
        printf '%s\n' "$profile" > "$archive/profile-path.txt"
        for name in minidumps crashreports; do
            if [ -d "$profile/$name" ]; then
                mkdir -p "$archive/$name"
                cp -a "$profile/$name/." "$archive/$name/" 2>/dev/null || true
            fi
        done
        find "$profile" -maxdepth 3 -type f \( -name '*.extra' -o -name '*.dmp' \) \
            -exec cp -a {} "$archive/" \; 2>/dev/null || true
    fi
    if [ -f "$PROCESS_SAMPLES" ]; then
        tail -n 400 "$PROCESS_SAMPLES" > "$archive/process-samples-tail.log" 2>/dev/null || true
    fi
    {
        printf 'captured_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf 'pids_current=%s\n' "$(read_cgroup_value pids.current pids)"
        printf 'pids_max=%s\n' "$(read_cgroup_value pids.max pids)"
        printf '%s\n' 'pids_events:'
        read_cgroup_value pids.events pids | sed 's/^/  /'
        printf 'thread_count_hint=sum nlwp in the last process sample before the crash\n'
    } > "$archive/thread-summary.txt" 2>/dev/null || true
    {
        printf 'reason=%s\n' "$reason"
        printf 'captured_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf 'firefox_pid=%s\n' "${FIREFOX_PID:-}"
        find "$archive" -maxdepth 2 -type f ! -name summary.txt \
            -printf '%p %s bytes\n' 2>/dev/null || true
    } > "$archive/summary.txt"
    log "preserved crash artifacts in $archive"
    rotate_crash_artifacts
}

persist_crash_snapshot() {
    local label="$1"
    collect_crash_artifacts "$label"
    persist_snapshot "$label"
}

collect_cgroup_metrics() {
    local path
    printf 'type=%s\n' "$(stat -fc %T /sys/fs/cgroup 2>/dev/null || true)"
    printf 'controllers=%s\n' "$(cat /sys/fs/cgroup/cgroup.controllers 2>/dev/null || true)"
    printf 'proc_cmdline=%s\n' "$(cat /proc/cmdline 2>/dev/null || true)"
    for path in \
        /sys/fs/cgroup/memory.events \
        /sys/fs/cgroup/memory.peak \
        /sys/fs/cgroup/memory.current \
        /sys/fs/cgroup/memory.max \
        /sys/fs/cgroup/memory.swap.current \
        /sys/fs/cgroup/pids.current \
        /sys/fs/cgroup/pids.max \
        /sys/fs/cgroup/pids.events \
        /sys/fs/cgroup/memory/memory.limit_in_bytes \
        /sys/fs/cgroup/memory/memory.usage_in_bytes \
        /sys/fs/cgroup/pids/pids.current \
        /sys/fs/cgroup/pids/pids.max \
        /sys/fs/cgroup/pids/pids.events; do
        if [ -r "$path" ]; then
            printf '%s:\n' "$path"
            sed 's/^/  /' "$path" 2>/dev/null || true
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
        printf '%s\n' 'environment:'
        if [ -f "${DIAG_DIR}/environment.log" ]; then
            sed 's/^/  /' "${DIAG_DIR}/environment.log"
        fi
        printf '%s\n' 'tasks:'
        printf '  current=%s\n' "$(read_cgroup_value pids.current pids)"
        printf '  max=%s\n' "$(read_cgroup_value pids.max pids)"
        printf '%s\n' 'processes:'
        ps -eo pid,ppid,nlwp,stat,rss,comm,args 2>/dev/null || true
        printf '%s\n' 'profile-session-files:'
        if [ -n "$PROFILE_DIR" ] && [ -d "$PROFILE_DIR" ]; then
            find "$PROFILE_DIR" -maxdepth 2 \
                \( -name 'sessionstore*' -o -name 'sessionCheckpoints.json' \
                -o -path '*/minidumps/*' -o -path '*/crashreports/*' \) \
                -printf '%p %s bytes\n' 2>/dev/null || true
        fi
        printf '%s\n' 'recent-process-samples:'
        if [ -f "$PROCESS_SAMPLES" ]; then
            tail -n 80 "$PROCESS_SAMPLES"
        fi
    } > "$path" 2>&1 || true
    log "wrote diagnostics to $path"
}

prepare_profile() {
    mkdir -p "$DIAG_DIR"
    PROFILE_DIR="$(find_profile || true)"
    if [ -z "$PROFILE_DIR" ]; then
        PROFILE_DIR="${MOZILLA_DIR}/firefox/meridian.default"
    fi
    mkdir -p "$PROFILE_DIR"
    printf '%s\n' "$PROFILE_DIR" > "$PROFILE_POINTER"

    find "$MOZILLA_DIR" -name lock -delete 2>/dev/null || true
    find "$MOZILLA_DIR" -name .parentlock -delete 2>/dev/null || true

    write_user_js "$PROFILE_DIR"

    log "using Firefox profile $PROFILE_DIR"
    write_environment_info
    sample_processes
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
    SHUTDOWN_STATUS=0
    log "shutting down"

    quit_firefox
    firefox_status=$LAST_STATUS
    FIREFOX_PID=""
    if [ "$firefox_status" -eq 0 ]; then
        log "Firefox shut down cleanly"
    else
        SHUTDOWN_STATUS=$firefox_status
        log "Firefox did not shut down cleanly (status $firefox_status)"
        persist_crash_snapshot "firefox-shutdown-${firefox_status}"
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
    exit "$SHUTDOWN_STATUS"
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

    sample_processes

    if [ "$finished" = "$SLEEP_PID" ]; then
        if ! timeout 5 xdpyinfo -display :1 >/dev/null 2>&1; then
            log "Xvnc health check failed"
            persist_snapshot "xvnc-health-failure"
            exit 1
        fi
        if ! curl --fail --silent --show-error --max-time 3 \
            http://127.0.0.1:6080/vnc_lite.html >/dev/null 2>&1; then
            log "websockify health check failed"
            persist_snapshot "websockify-health-failure"
            exit 1
        fi
    elif [ "$finished" = "$NOVNC_PID" ]; then
        log "websockify exited with status $wait_status"
        NOVNC_PID=""
        persist_snapshot "websockify-exit-${wait_status}"
        exit 1
    elif [ "$finished" = "$WM_PID" ]; then
        log "openbox exited with status $wait_status"
        WM_PID=""
        persist_snapshot "openbox-exit-${wait_status}"
        exit 1
    elif [ "$finished" = "$XVNC_PID" ]; then
        log "Xvnc exited with status $wait_status"
        XVNC_PID=""
        persist_snapshot "xvnc-exit-${wait_status}"
        exit 1
    elif [ "$finished" = "$FIREFOX_PID" ]; then
        log "Firefox exited with status $wait_status"
        FIREFOX_PID=""
        if [ "$wait_status" -eq 0 ]; then
            log "Firefox exited cleanly"
        else
            persist_crash_snapshot "firefox-exit-${wait_status}"
        fi
        exit "$wait_status"
    fi
done
