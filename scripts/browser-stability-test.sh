#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${IMAGE:-meridian-firefox:latest}"
DURATION="${DURATION:-300}"
INTERVAL="${INTERVAL:-30}"
MEMORY="${MEMORY:-1200m}"
EXPECTED_MEMORY_BYTES="${EXPECTED_MEMORY_BYTES:-1258291200}"
CPUS="${CPUS:-1.5}"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/data/browser-stability}"
NETWORK="${NETWORK:-meridian-browser-test}"
VOLUME_PREFIX="${VOLUME_PREFIX:-meridian-stability-home}"
PROFILE_MODE="${PROFILE_MODE:-clean}"
SITE="${SITE:-blank}"
URL="${URL:-}"
BBC_URL="${BBC_URL:-https://www.bbc.co.uk/news}"
OTHER_URL="${OTHER_URL:-https://www.theguardian.com/international}"
KEEP="${KEEP:-0}"
STRICT_MEMORY="${STRICT_MEMORY:-1}"
MATRIX=0
BUILD=0
RUNS=0
FAILURES=0

usage() {
    cat >&2 <<'USAGE'
Usage: browser-stability-test.sh [options]

Runs the Firefox image under the production container limits, optionally
navigates to a site, and preserves diagnostics plus minidumps before removing
the container.

Options:
  --matrix              Run clean/persistent x blank/BBC/other (6 runs)
  --profile MODE        clean (default) or persistent
  --site NAME           blank (default), bbc, other, or custom (requires --url)
  --url URL             URL for --site custom
  --duration SECONDS    Target run time per scenario (default: 300)
  --interval SECONDS    Sampling/log interval (default: 30)
  --image IMAGE         Firefox image (default: meridian-firefox:latest)
  --output DIR          Artifact directory (default: data/browser-stability)
  --build               Build the image before running
  --keep                Keep the container and volume after the run
  --help                Show this help

Environment overrides: MEMORY, EXPECTED_MEMORY_BYTES, CPUS, NETWORK,
BBC_URL, OTHER_URL, STRICT_MEMORY=0.
USAGE
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --matrix)
            MATRIX=1
            shift
            ;;
        --profile)
            PROFILE_MODE="$2"
            shift 2
            ;;
        --site)
            SITE="$2"
            shift 2
            ;;
        --url)
            URL="$2"
            SITE=custom
            shift 2
            ;;
        --duration)
            DURATION="$2"
            shift 2
            ;;
        --interval)
            INTERVAL="$2"
            shift 2
            ;;
        --image)
            IMAGE="$2"
            shift 2
            ;;
        --output)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --build)
            BUILD=1
            shift
            ;;
        --keep)
            KEEP=1
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown option: %s\n' "$1" >&2
            usage
            exit 2
            ;;
    esac
done

case "$PROFILE_MODE" in
    clean|persistent) ;;
    *)
        printf 'Invalid profile mode: %s\n' "$PROFILE_MODE" >&2
        exit 2
        ;;
esac

case "$SITE" in
    blank|bbc|other|custom) ;;
    *)
        printf 'Invalid site: %s\n' "$SITE" >&2
        exit 2
        ;;
esac

if [ "$SITE" = "custom" ] && [ -z "$URL" ]; then
    printf '--site custom requires --url\n' >&2
    exit 2
fi

case "$DURATION" in
    ''|*[!0-9]*)
        printf 'Duration must be a whole number of seconds\n' >&2
        exit 2
        ;;
esac

command -v docker >/dev/null 2>&1 || {
    printf 'docker is required\n' >&2
    exit 2
}

if [ "$BUILD" -eq 1 ]; then
    docker build -t "$IMAGE" "${REPO_ROOT}/docker/firefox"
fi

docker network inspect "$NETWORK" >/dev/null 2>&1 || docker network create \
    --opt com.docker.network.bridge.enable_icc=false "$NETWORK" >/dev/null

profile_volume() {
    local profile="$1"
    local run_id="$2"
    case "$profile" in
        clean) printf '%s\n' "${VOLUME_PREFIX}-clean-${run_id}" ;;
        persistent) printf '%s\n' "${VOLUME_PREFIX}-persistent" ;;
    esac
}

site_url() {
    case "$1" in
        blank) printf '' ;;
        bbc) printf '%s\n' "$BBC_URL" ;;
        other) printf '%s\n' "$OTHER_URL" ;;
        custom) printf '%s\n' "$URL" ;;
    esac
}

wait_for_window() {
    local container="$1"
    local window=""
    local i
    for i in $(seq 1 90); do
        if ! docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null | grep -q '^true$'; then
            return 1
        fi
        window="$(docker exec --user 1000 --env DISPLAY=:1 \
            --env XAUTHORITY=/home/browser/.Xauthority "$container" \
            xdotool search --onlyvisible --limit 1 \
            'meridian-browser|MeridianBrowser|Firefox' 2>/dev/null | head -n 1 || true)"
        if [ -n "$window" ]; then
            printf '%s\n' "$window"
            return 0
        fi
        sleep 1
    done
    return 1
}

navigate() {
    local container="$1"
    local window="$2"
    local url="$3"
    docker exec --user 1000 --env DISPLAY=:1 \
        --env XAUTHORITY=/home/browser/.Xauthority "$container" \
        xdotool windowactivate --sync "$window" >/dev/null 2>&1 || true
    docker exec --user 1000 --env DISPLAY=:1 \
        --env XAUTHORITY=/home/browser/.Xauthority "$container" \
        xdotool key --clearmodifiers ctrl+l type --clearmodifiers --delay 2 \
        "$url" key Return >/dev/null 2>&1 || true
}

run_scenario() {
    local profile="$1"
    local site="$2"
    local run_id volume container url out started_at now elapsed running
    local timed_out exit_code window profile_path minidumps crash_markers
    local host_memory cgroup_max status notes window_found navigation_failed

    run_id="$(date -u +%Y%m%dT%H%M%SZ)-$$-${RANDOM}"
    volume="$(profile_volume "$profile" "$run_id")"
    container="meridian-stability-${profile}-${site}-${run_id}"
    url="$(site_url "$site")"
    out="${OUTPUT_DIR}/${profile}-${site}-${run_id}"
    mkdir -p "$out"
    status=PASS
    notes=""
    window_found=0
    navigation_failed=0

    docker volume create "$volume" >/dev/null
    docker run --detach --init --name "$container" \
        --label meridian.browser.stability=1 \
        --network "$NETWORK" \
        --security-opt no-new-privileges=true \
        --cap-drop ALL \
        --cap-add CHOWN \
        --cap-add DAC_OVERRIDE \
        --cap-add SETUID \
        --cap-add SETGID \
        --pids-limit 256 \
        --memory "$MEMORY" \
        --cpus "$CPUS" \
        --shm-size 512m \
        --stop-timeout 30 \
        --publish 127.0.0.1::6080 \
        --volume "$volume:/home/browser" \
        --env MOZ_CRASHREPORTER_SHUTDOWN=0 \
        "$IMAGE" >/dev/null

    started_at="$(date +%s)"
    window="$(wait_for_window "$container" || true)"
    if [ -z "$window" ]; then
        notes="${notes}firefox-window-not-found;"
    else
        window_found=1
        if [ -n "$url" ] && ! navigate "$container" "$window" "$url"; then
            navigation_failed=1
            notes="${notes}navigation-failed;"
        fi
    fi

    timed_out=0
    while true; do
        running="$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || printf 'false\n')"
        if [ "$running" != "true" ]; then
            break
        fi
        now="$(date +%s)"
        if [ $((now - started_at)) -ge "$DURATION" ]; then
            timed_out=1
            break
        fi
        sleep "$INTERVAL"
    done
    elapsed=$(( $(date +%s) - started_at ))

    if [ "$timed_out" -eq 1 ]; then
        cgroup_max="$(docker exec "$container" cat /sys/fs/cgroup/memory.max 2>/dev/null || true)"
    else
        cgroup_max=""
    fi
    host_memory="$(docker inspect -f '{{.HostConfig.Memory}}' "$container" 2>/dev/null || printf 'unknown\n')"

    if [ "$timed_out" -eq 1 ]; then
        docker stop --time 30 "$container" >/dev/null 2>&1 || true
    fi
    exit_code="$(docker inspect -f '{{.State.ExitCode}}' "$container" 2>/dev/null || printf 'unknown\n')"

    docker logs --timestamps "$container" > "$out/container.log" 2>&1 || true
    docker inspect "$container" > "$out/inspect.json" 2>&1 || true
    docker top "$container" > "$out/top.txt" 2>&1 || true
    mkdir -p "$out/diagnostics"
    docker cp "$container:/home/browser/.meridian/diagnostics/." "$out/diagnostics" \
        >/dev/null 2>&1 || true
    profile_path="$(head -n 1 "$out/diagnostics/profile-path" 2>/dev/null || true)"
    if [ -n "$profile_path" ]; then
        docker cp "$container:${profile_path}/minidumps" "$out/minidumps" \
            >/dev/null 2>&1 || true
    fi

    minidumps="$(find "$out" -type f \( -name '*.dmp' -o -name '*.extra' \) 2>/dev/null \
        | wc -l | tr -d ' ')"
    crash_markers="$(grep -Eic 'SIGSEGV|signal 11|EXCEPTION_BREAKPOINT|content process.*crash' \
        "$out/container.log" 2>/dev/null || true)"
    [ -n "$minidumps" ] || minidumps=0
    [ -n "$crash_markers" ] || crash_markers=0

    if [ "$window_found" -eq 0 ]; then
        status=FAIL
    fi
    if [ "$navigation_failed" -eq 1 ]; then
        status=FAIL
    fi
    if [ "$timed_out" -eq 0 ] && [ "$exit_code" != "0" ]; then
        status=FAIL
        notes="${notes}container-exit-${exit_code};"
    fi
    if [ "$timed_out" -eq 0 ] && [ "$exit_code" = "0" ] && [ "$elapsed" -lt $((DURATION - 10)) ]; then
        status=FAIL
        notes="${notes}firefox-exited-early;"
    fi
    if [ "$minidumps" -gt 0 ]; then
        status=FAIL
        notes="${notes}minidumps=${minidumps};"
    fi
    if [ "$crash_markers" -gt 0 ]; then
        status=FAIL
        notes="${notes}crash-lines=${crash_markers};"
    fi
    if [ "$STRICT_MEMORY" = "1" ] && [ "$host_memory" != "$EXPECTED_MEMORY_BYTES" ]; then
        status=FAIL
        notes="${notes}memory=${host_memory};"
    fi

    {
        printf 'profile=%s\nsite=%s\nurl=%s\nimage=%s\n' \
            "$profile" "$site" "$url" "$IMAGE"
        printf 'duration=%s\nelapsed=%s\ntimed_out=%s\nexit_code=%s\n' \
            "$DURATION" "$elapsed" "$timed_out" "$exit_code"
        printf 'host_memory=%s\nexpected_memory=%s\ncgroup_memory_max=%s\n' \
            "$host_memory" "$EXPECTED_MEMORY_BYTES" "$cgroup_max"
        printf 'minidumps=%s\ncrash_markers=%s\nstatus=%s\nnotes=%s\n' \
            "$minidumps" "$crash_markers" "$status" "$notes"
    } > "$out/summary.txt"

    RUNS=$((RUNS + 1))
    if [ "$status" != "PASS" ]; then
        FAILURES=$((FAILURES + 1))
    fi
    printf '%-6s %-10s %-8s exit=%-4s elapsed=%-4ss minidumps=%-2s mem=%s/%s %s\n' \
        "$status" "$profile" "$site" "$exit_code" "$elapsed" "$minidumps" \
        "$host_memory" "$EXPECTED_MEMORY_BYTES" "$notes"

    if [ "$KEEP" -eq 1 ]; then
        printf 'kept container %s and volume %s\n' "$container" "$volume"
    else
        docker rm --force "$container" >/dev/null 2>&1 || true
        if [ "$profile" = "clean" ]; then
            docker volume rm "$volume" >/dev/null 2>&1 || true
        fi
    fi
}

run_matrix() {
    local profile site
    for profile in clean persistent; do
        for site in blank bbc other; do
            run_scenario "$profile" "$site"
        done
    done
}

if [ "$MATRIX" -eq 1 ]; then
    run_matrix
else
    run_scenario "$PROFILE_MODE" "$SITE"
fi

printf '\n%d run(s), %d failure(s). Artifacts: %s\n' "$RUNS" "$FAILURES" "$OUTPUT_DIR"
if [ "$FAILURES" -gt 0 ]; then
    exit 1
fi
