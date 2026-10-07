#!/bin/sh

# Wake the cloud publisher from the Epaper display transaction's tiny revision
# marker. The kernel watch stays resident; Python runs only while publishing.

set -u
umask 077

PUBLISHER_ROOT=${EPAPER_PUBLISHER_ROOT:-/home/feeengyuuu/.local/lib/epaper-publisher}
STATE_DIR=${EPAPER_PUBLISH_STATE_DIR:-/home/feeengyuuu/.local/state/epaper-publisher}
KEY_FILE=${EPAPER_PUBLISH_KEY_FILE:-/home/feeengyuuu/.config/epaper-publisher/publish.key}
REVISION_DIR=${INKYPI_REVISION_DIR:-/run/inkypi}
REVISION_NAME=${INKYPI_REVISION_NAME:-display_revision}
ENDPOINT=${EPAPER_PUBLISH_ENDPOINT:-https://e.superxfy.workers.dev}
DEVICE_URL=${INKYPI_DEVICE_URL:-http://127.0.0.1}
TIMEZONE_NAME=${EPAPER_TIMEZONE:-America/Los_Angeles}
PUBLISH_LOCK=${STATE_DIR}/publish.lock

log_error() {
    printf '%s\n' "epaper-publisher watcher: $1" >&2
}

publish_once() {
    if [ ! -r "$KEY_FILE" ]; then
        log_error "publish key is unavailable"
        return 2
    fi
    publish_key=$(/usr/bin/cat "$KEY_FILE") || return 2
    if [ -z "$publish_key" ]; then
        log_error "publish key is empty"
        return 2
    fi

    EPAPER_PUBLISH_KEY=$publish_key
    export EPAPER_PUBLISH_KEY
    /usr/bin/flock "$PUBLISH_LOCK" \
        /usr/bin/nice -n 15 \
        /usr/bin/python3 "$PUBLISHER_ROOT/current/sync_device_portal.py" \
        --endpoint "$ENDPOINT" \
        --state-dir "$STATE_DIR" \
        --device-url "$DEVICE_URL" \
        --timezone "$TIMEZONE_NAME" \
        --timeout 30
    status=$?
    unset EPAPER_PUBLISH_KEY
    publish_key=
    return "$status"
}

publish_with_retry() {
    retry_delay=5
    while ! publish_once; do
        log_error "publish failed; retrying after ${retry_delay}s"
        /usr/bin/sleep "$retry_delay"
        retry_delay=$((retry_delay * 3))
        if [ "$retry_delay" -gt 300 ]; then
            retry_delay=300
        fi
    done
}

cleanup_monitor() {
    exec 3<&- 2>/dev/null || true
    if [ -n "${monitor_pid:-}" ]; then
        /bin/kill "$monitor_pid" 2>/dev/null || true
        wait "$monitor_pid" 2>/dev/null || true
    fi
    if [ -n "${event_dir:-}" ] && [ -d "$event_dir" ]; then
        /bin/rm -f "$event_dir/events"
        /bin/rmdir "$event_dir" 2>/dev/null || true
    fi
}

trap 'cleanup_monitor; exit 0' HUP INT TERM

while :; do
    while [ ! -d "$REVISION_DIR" ]; do
        /usr/bin/sleep 2
    done

    event_dir=$(/usr/bin/mktemp -d "$STATE_DIR/watch-events.XXXXXX") || exit 2
    /usr/bin/mkfifo "$event_dir/events" || exit 2
    /usr/bin/inotifywait \
        --monitor \
        --quiet \
        --event create \
        --event moved_to \
        --event delete_self \
        --event move_self \
        --format '%e|%f' \
        "$REVISION_DIR" >"$event_dir/events" 2>/dev/null &
    monitor_pid=$!
    # Open only the read end. A RuntimeDirectory replacement emits a self
    # event; cleanup then retires this monitor before the outer loop reattaches.
    exec 3<"$event_dir/events"

    # Give inotifywait time to attach, then reconcile once. Any display event
    # arriving during this publication remains queued on descriptor 3.
    /usr/bin/sleep 1
    publish_with_retry

    while /bin/kill -0 "$monitor_pid" 2>/dev/null && IFS='|' read -r event_kind event_name <&3; do
        case "$event_kind" in
            *DELETE_SELF*|*MOVE_SELF*)
                break
                ;;
        esac
        if [ "$event_name" = "$REVISION_NAME" ]; then
            publish_with_retry
        fi
    done

    cleanup_monitor
    monitor_pid=
    event_dir=
    /usr/bin/sleep 2
done
