#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

require_command() {
    command -v "$1" >/dev/null 2>&1 || {
        printf 'required command is missing: %s\n' "$1" >&2
        exit 2
    }
}

if [[ "$(id -u)" != "0" && "${EPAPER_ALLOW_NON_ROOT:-}" != "1" ]]; then
    printf 'run this script as root so the encrypted archive and checksum share one trust boundary\n' >&2
    exit 2
fi

for command_name in age awk basename dirname docker git grep install sed sha256sum sleep sort stat sync tail tar tr; do
    require_command "$command_name"
done

expected_compose_version="5.3.1"
compose_version="$(docker compose version --short | tr -d '\r')"
compose_version="${compose_version#v}"
[[ "$compose_version" == "$expected_compose_version" ]] || {
    printf 'Docker Compose %s is required; found %s\n' "$expected_compose_version" "${compose_version:-unknown}" >&2
    exit 2
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
deploy_root="$(cd -- "$script_dir/.." && pwd -P)"
repo_root="$(cd -- "$deploy_root/../.." && pwd -P)"
cd -- "$deploy_root"

untracked="$(git -C "$repo_root" ls-files --others --exclude-standard)"
if ! git -C "$repo_root" diff --quiet --ignore-submodules -- \
    || ! git -C "$repo_root" diff --cached --quiet --ignore-submodules -- \
    || [[ -n "$untracked" ]]; then
    printf 'release checkout must be clean and fully tracked before backup\n' >&2
    exit 2
fi
git_revision="$(git -C "$repo_root" rev-parse HEAD)"
[[ "$git_revision" =~ ^[0-9a-f]{40,64}$ ]] || {
    printf 'git revision is invalid\n' >&2
    exit 2
}

recipients_file="${1:-/root/epaper-backup-recipients.txt}"
backup_root="${2:-$deploy_root/backups}"
[[ -f "$recipients_file" && ! -L "$recipients_file" ]] || {
    printf 'age recipients file is missing or link-like\n' >&2
    exit 2
}
[[ -f .env && ! -L .env ]] || {
    printf '.env is missing or link-like\n' >&2
    exit 2
}

env_value() {
    key="$1"
    fallback="$2"
    value="$(sed -n "s/^${key}=//p" .env | tail -n 1 | tr -d '\r')"
    printf '%s' "${value:-$fallback}"
}

[[ "$(env_value WEB_PORTAL_GIT_REVISION '')" == "$git_revision" ]] || {
    printf 'release .env must pin WEB_PORTAL_GIT_REVISION to the clean Git revision\n' >&2
    exit 2
}

for state_binding in \
    "WEB_PORTAL_CONFIG_DIR=./config" \
    "WEB_PORTAL_PUBLICATION_DIR=./publication" \
    "WEB_PORTAL_WORKER_DATA_DIR=./worker-data" \
    "WEB_PORTAL_SECURITY_DIR=./security" \
    "WEB_PORTAL_PROVIDER_SECRETS_DIR=./provider-secrets" \
    "WEB_PORTAL_SESSION_KEY_FILE=./session-key/web_portal_secret_key"; do
    state_key="${state_binding%%=*}"
    expected_path="${state_binding#*=}"
    actual_path="$(env_value "$state_key" "$expected_path")"
    [[ "$actual_path" == "$expected_path" ]] || {
        printf 'backup requires the canonical deploy/web state layout; unsupported override: %s\n' "$state_key" >&2
        exit 2
    }
done

state_paths=(.env config publication worker-data security provider-secrets session-key)
for state_path in "${state_paths[@]}"; do
    [[ -e "$state_path" && ! -L "$state_path" ]] || {
        printf 'required state path is missing or link-like: %s\n' "$state_path" >&2
        exit 2
    }
done

[[ ! -L "$backup_root" && ( ! -e "$backup_root" || -d "$backup_root" ) ]] || {
    printf 'backup root is not a safe directory\n' >&2
    exit 2
}
expected_owner=0
expected_group=0
application_owner=10001
application_group=10001
if [[ "${EPAPER_ALLOW_NON_ROOT:-}" == "1" ]]; then
    expected_owner="$(id -u)"
    expected_group="$(id -g)"
    application_owner="$expected_owner"
    application_group="$expected_group"
fi
recipients_mode="$(stat -c '%a' "$recipients_file")"
[[ "$(stat -c '%u:%g' "$recipients_file")" == "$expected_owner:$expected_group" ]] \
    && (( (8#$recipients_mode & 0022) == 0 )) || {
    printf 'age recipients file must have the trusted owner and no group/other write access\n' >&2
    exit 2
}
install -d -o "$expected_owner" -g "$expected_group" -m 0700 -- "$backup_root"
[[ "$(stat -c '%u:%g' "$backup_root")" == "$expected_owner:$expected_group" \
    && "$(stat -c '%a' "$backup_root")" == "700" ]] || {
    printf 'backup root must be owned by %s:%s with mode 0700\n' "$expected_owner" "$expected_group" >&2
    exit 2
}
backup_root="$(cd -- "$backup_root" && pwd -P)"
for state_path in config publication worker-data security provider-secrets session-key; do
    state_root="$(cd -- "$state_path" && pwd -P)"
    case "$backup_root/" in
        "$state_root"/*)
            printf 'backup root must not be nested under archived state: %s\n' "$state_path" >&2
            exit 2
            ;;
    esac
done
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
stage_dir="$backup_root/.web-$stamp.partial"
final_dir="$backup_root/web-$stamp"
[[ ! -e "$stage_dir" && ! -e "$final_dir" ]] || {
    printf 'backup destination already exists\n' >&2
    exit 2
}
for variable_name in ${!WEB_PORTAL_@}; do
    unset "$variable_name"
done
for variable_name in ${!COMPOSE_@}; do
    unset "$variable_name"
done
unset WEB_EDITION_RETENTION_PER_INSTANCE WEB_CATALOG_RETENTION WEB_WORKER_DEADLINE_SECONDS
export WEB_PORTAL_GIT_REVISION="$git_revision"
compose=(docker compose --project-name epapersystem-web --env-file .env -f compose.yaml)
worker_stop_timeout="${EPAPER_WORKER_STOP_TIMEOUT_SECONDS:-180}"
[[ "$worker_stop_timeout" =~ ^[0-9]+$ && "$worker_stop_timeout" -ge 130 && "$worker_stop_timeout" -le 3600 ]] || {
    printf 'worker stop timeout must be an integer between 130 and 3600 seconds\n' >&2
    exit 2
}
worker_restart_timeout="${EPAPER_WORKER_RESTART_TIMEOUT_SECONDS:-210}"
[[ "$worker_restart_timeout" =~ ^[0-9]+$ \
    && "$worker_restart_timeout" -ge 1 \
    && "$worker_restart_timeout" -le 3600 ]] || {
    printf 'worker restart timeout must be an integer between 1 and 3600 seconds\n' >&2
    exit 2
}
worker_was_running=0
worker_container_id=""

worker_restart_confirmed() {
    deadline=$((SECONDS + worker_restart_timeout))
    while (( SECONDS <= deadline )); do
        if "${compose[@]}" ps --status running --services 2>/dev/null | grep -Fxq worker; then
            running_id="$("${compose[@]}" ps -q worker 2>/dev/null)"
            health_status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$running_id" 2>/dev/null)"
            if [[ "$running_id" == "$worker_container_id" \
                && ( "$health_status" == "healthy" || "$health_status" == "none" ) ]]; then
                return 0
            fi
        fi
        sleep 2
    done
    return 1
}

backup_cleanup() {
    status=$?
    set +e
    if [[ "$worker_was_running" == "1" ]]; then
        "${compose[@]}" start worker >/dev/null
        restart_status=$?
        if [[ "$restart_status" != "0" ]] || ! worker_restart_confirmed; then
            printf 'worker restart was not confirmed after backup\n' >&2
            status=1
        fi
    fi
    if [[ -n "${stage_dir:-}" && -d "$stage_dir" && "$stage_dir" == "$backup_root"/.web-*.partial ]]; then
        rm -rf -- "$stage_dir"
        cleanup_status=$?
        if [[ "$cleanup_status" != "0" ]]; then
            printf 'partial backup cleanup failed\n' >&2
            status=1
        fi
    fi
    trap - EXIT
    exit "$status"
}
trap backup_cleanup EXIT
mkdir -- "$stage_dir"

require_healthy_service() {
    service="$1"
    container_id="$("${compose[@]}" ps -q "$service")"
    [[ "$container_id" =~ ^[0-9a-f]{12,64}$ ]] || {
        printf 'required service container is missing: %s\n' "$service" >&2
        exit 2
    }
    state="$(docker inspect --format '{{.State.Running}}:{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id")"
    [[ "$state" == "true:healthy" ]] || {
        printf 'required service is not running and healthy: %s\n' "$service" >&2
        exit 2
    }
    printf '%s' "$container_id"
}

web_container_id="$(require_healthy_service web)"
caddy_container_id="$(require_healthy_service caddy)"
worker_container_id="$(require_healthy_service worker)"
worker_was_running=1

require_current_compose_hash() {
    service="$1"
    container_id="$2"
    expected_hash="$("${compose[@]}" config --hash "$service" | awk -v service="$service" '$1 == service { print $2; exit }')"
    actual_hash="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.config-hash" }}' "$container_id")"
    [[ -n "$expected_hash" && "$actual_hash" == "$expected_hash" ]] || {
        printf 'running container does not match the resolved Compose service: %s\n' "$service" >&2
        exit 2
    }
}

require_current_compose_hash caddy "$caddy_container_id"
require_current_compose_hash web "$web_container_id"
require_current_compose_hash worker "$worker_container_id"

web_image_id="$(docker inspect --format '{{.Image}}' "$web_container_id")"
caddy_image_id="$(docker inspect --format '{{.Image}}' "$caddy_container_id")"
worker_image_id="$(docker inspect --format '{{.Image}}' "$worker_container_id")"
[[ "$web_image_id" =~ ^sha256:[0-9a-f]{64}$ && "$caddy_image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || {
    printf 'web and caddy images must already exist before backup\n' >&2
    exit 2
}
[[ "$worker_image_id" == "$web_image_id" ]] || {
    printf 'running worker image does not match the running web release\n' >&2
    exit 2
}

contract_env="$stage_dir/contract-env"
install -d -o "$application_owner" -g "$application_group" -m 0700 "$contract_env"
for service in caddy web worker; do
    case "$service" in
        caddy)
            container_id="$caddy_container_id"
            image_id="$caddy_image_id"
            ;;
        web)
            container_id="$web_container_id"
            image_id="$web_image_id"
            ;;
        worker)
            container_id="$worker_container_id"
            image_id="$worker_image_id"
            ;;
    esac
    container_inspect="$contract_env/$service.container.json"
    image_inspect="$contract_env/$service.image.json"
    install -o "$application_owner" -g "$application_group" -m 0600 /dev/null "$container_inspect"
    install -o "$application_owner" -g "$application_group" -m 0600 /dev/null "$image_inspect"
    docker inspect --type container "$container_id" > "$container_inspect"
    docker image inspect "$image_id" > "$image_inspect"
done
"${compose[@]}" --profile admin config --format json \
    | docker run --rm --network none -i \
        -v "$script_dir/compose_contract.py:/contract.py:ro" \
        -v "$contract_env:/scratch:rw" \
        "$web_image_id" python /contract.py \
        --expected-root "$deploy_root" --output-dir /scratch \
        --runtime-inspect-dir /scratch
rm -rf -- "$contract_env"
caddy_image_ref="$(awk '$1 == "image:" && $2 ~ /^caddy:2-alpine@sha256:[0-9a-f]{64}$/ { print $2; exit }' compose.yaml)"
[[ -n "$caddy_image_ref" ]] || exit 2
expected_caddy_image_id="$(docker image inspect --format '{{.Id}}' "$caddy_image_ref")"
[[ "$caddy_image_id" == "$expected_caddy_image_id" ]] || {
    printf 'running caddy image does not match the pinned release image\n' >&2
    exit 2
}
configured_images="$("${compose[@]}" --profile admin config --images)"
grep -Fxq "$caddy_image_ref" <<< "$configured_images"
configured_web_image_ref="$(printf '%s\n' "$configured_images" | grep -Fvx "$caddy_image_ref" | sort -u)"
[[ -n "$configured_web_image_ref" && "$configured_web_image_ref" != *$'\n'* ]] || exit 2
[[ "$configured_web_image_ref" == *":$git_revision" ]] || {
    printf 'configured web image is not tagged with the clean Git revision\n' >&2
    exit 2
}
configured_web_image_id="$(docker image inspect --format '{{.Id}}' "$configured_web_image_ref")"
[[ "$configured_web_image_id" == "$web_image_id" ]] || {
    printf 'configured web image reference does not match the running release\n' >&2
    exit 2
}
web_image_revision="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$web_image_id")"
[[ "$web_image_revision" == "$git_revision" ]] || {
    printf 'web image revision label does not match the clean checkout\n' >&2
    exit 2
}

"${compose[@]}" stop --timeout "$worker_stop_timeout" worker >/dev/null
if "${compose[@]}" ps --status running --services | grep -Fxq worker; then
    printf 'worker did not stop cleanly before backup\n' >&2
    exit 2
fi
worker_exit_code="$(docker inspect --format '{{.State.ExitCode}}' "$worker_container_id")"
[[ "$worker_exit_code" == "0" ]] || {
    printf 'worker did not stop cleanly before backup\n' >&2
    exit 2
}

archive="$stage_dir/state.tar.gz.age"
printf 'FORMAT_VERSION=1\nGIT_REVISION=%s\nWEB_IMAGE_ID=%s\nCADDY_IMAGE_ID=%s\n' \
    "$git_revision" "$web_image_id" "$caddy_image_id" > "$stage_dir/release.env"
chmod 0600 "$stage_dir/release.env"
tar --numeric-owner -czf - "${state_paths[@]}" -C "$stage_dir" release.env \
    | age -R "$recipients_file" -o "$archive"
chmod 0600 "$archive"
archive_digest="$(sha256sum "$archive" | awk '{print $1}')"
printf '%s  %s\n' "$archive_digest" "$(basename -- "$archive")" \
    > "$stage_dir/state.tar.gz.age.sha256"
chmod 0600 "$stage_dir/state.tar.gz.age.sha256" "$stage_dir/release.env"

sync -f "$archive" "$stage_dir/state.tar.gz.age.sha256" "$stage_dir/release.env"
sync -f "$stage_dir"
mv -- "$stage_dir" "$final_dir"
sync -f "$backup_root"
stage_dir=""
printf 'encrypted backup committed: %s\n' "$final_dir"
