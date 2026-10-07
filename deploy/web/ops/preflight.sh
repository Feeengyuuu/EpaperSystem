#!/usr/bin/env bash
set -Eeuo pipefail

require_command() {
    command -v "$1" >/dev/null 2>&1 || {
        printf 'required command is missing: %s\n' "$1" >&2
        exit 2
    }
}

for command_name in awk basename curl dirname docker git grep install mktemp rm sed sort stat tail tr wc; do
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
env_file="${1:-.env}"
public_url="${2:-}"
[[ -f "$env_file" && ! -L "$env_file" ]] || {
    printf 'deployment environment file is missing or link-like\n' >&2
    exit 2
}

require_clean_checkout() {
    untracked="$(git -C "$repo_root" ls-files --others --exclude-standard)"
    if ! git -C "$repo_root" diff --quiet --ignore-submodules -- \
        || ! git -C "$repo_root" diff --cached --quiet --ignore-submodules -- \
        || [[ -n "$untracked" ]]; then
        printf 'release checkout must be clean and fully tracked before build\n' >&2
        exit 2
    fi
}

require_clean_checkout
git_revision="$(git -C "$repo_root" rev-parse HEAD)"
[[ "$git_revision" =~ ^[0-9a-f]{40,64}$ ]] || exit 2
recorded_git_revision="$(sed -n 's/^WEB_PORTAL_GIT_REVISION=//p' "$env_file" | tail -n 1 | tr -d '\r')"
[[ "$recorded_git_revision" == "$git_revision" ]] || {
    printf 'release .env must pin WEB_PORTAL_GIT_REVISION to the clean Git revision\n' >&2
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

grep -Eq '^FROM python:3\.11-slim-bookworm@sha256:[0-9a-f]{64}$' Dockerfile
grep -Eq 'image: caddy:2-alpine@sha256:[0-9a-f]{64}' compose.yaml
config_dir="$deploy_root/config"
publication_dir="$deploy_root/publication"
worker_data_dir="$deploy_root/worker-data"
security_dir="$deploy_root/security"
provider_secrets_dir="$deploy_root/provider-secrets"
session_key_file="$deploy_root/session-key/web_portal_secret_key"
session_key_dir="$(dirname -- "$session_key_file")"
for state_path in \
    "$config_dir" \
    "$publication_dir" \
    "$worker_data_dir" \
    "$security_dir" \
    "$provider_secrets_dir" \
    "$session_key_dir"; do
    [[ -d "$state_path" && ! -L "$state_path" ]] || {
        printf 'required state directory is missing or link-like: %s\n' "$state_path" >&2
        exit 2
    }
done
for writable_state_dir in "$publication_dir" "$worker_data_dir"; do
    writable_mode="$(stat -c '%a' "$writable_state_dir")"
    [[ "$(stat -c '%u:%g' "$writable_state_dir")" == "10001:10001" ]] \
        && (( (8#$writable_mode & 0700) == 0700 )) || {
        printf 'worker state directory must be owned by 10001:10001 with owner read, write, and execute: %s\n' "$writable_state_dir" >&2
        exit 2
    }
done
worker_health_dir="$worker_data_dir/health"
[[ ! -L "$worker_health_dir" && ( ! -e "$worker_health_dir" || -d "$worker_health_dir" ) ]] || {
    printf 'worker health path is link-like or not a directory\n' >&2
    exit 2
}
install -d -o 10001 -g 10001 -m 0750 -- "$worker_health_dir"
[[ "$(stat -c '%u:%g' "$worker_health_dir")" == "10001:10001" ]] || {
    printf 'worker health directory must be owned by 10001:10001\n' >&2
    exit 2
}
worker_health_mode="$(stat -c '%a' "$worker_health_dir")"
(( (8#$worker_health_mode & 0200) != 0 )) || {
    printf 'worker health directory owner must have write permission\n' >&2
    exit 2
}
[[ -f "$session_key_file" && ! -L "$session_key_file" ]] || exit 2
[[ "$(wc -c < "$session_key_file")" -ge 32 ]] || exit 2
[[ "$(stat -c '%u:%g' "$session_key_file")" == "10001:10001" ]] || {
    printf 'session key must be owned by 10001:10001\n' >&2
    exit 2
}
session_key_mode="$(stat -c '%a' "$session_key_file")"
(( (8#$session_key_mode & 0077) == 0 )) || {
    printf 'session key must not be readable or writable by group or other users\n' >&2
    exit 2
}
[[ "$(stat -c '%u:%g' "$security_dir")" == "10001:10001" \
    && "$(stat -c '%a' "$security_dir")" == "700" ]] || {
    printf 'security directory must be owned by 10001:10001 with mode 0700\n' >&2
    exit 2
}
admin_credentials_file="$security_dir/admin_credentials.json"
[[ -f "$admin_credentials_file" && ! -L "$admin_credentials_file" \
    && "$(stat -c '%u:%g' "$admin_credentials_file")" == "10001:10001" \
    && "$(stat -c '%a' "$admin_credentials_file")" == "600" ]] || {
    printf 'admin credentials must be a private 10001:10001 regular file\n' >&2
    exit 2
}

compose=(docker compose --project-name epapersystem-web --env-file "$env_file" -f compose.yaml)
"${compose[@]}" --profile admin config --quiet
caddy_image_ref="$(awk '$1 == "image:" && $2 ~ /^caddy:2-alpine@sha256:[0-9a-f]{64}$/ { print $2; exit }' compose.yaml)"
[[ -n "$caddy_image_ref" ]] || exit 2
configured_images="$("${compose[@]}" --profile admin config --images)"
grep -Fxq "$caddy_image_ref" <<< "$configured_images"
web_image_ref="$(printf '%s\n' "$configured_images" | grep -Fvx "$caddy_image_ref" | sort -u)"
[[ -n "$web_image_ref" && "$web_image_ref" != *$'\n'* ]] || exit 2
[[ "$web_image_ref" == *":$git_revision" ]] || {
    printf 'WEB_PORTAL_IMAGE must use the immutable current Git revision as its tag\n' >&2
    exit 2
}
caddy_image_id="$(docker image inspect --format '{{.Id}}' "$caddy_image_ref")"
web_image_id="$(docker image inspect --format '{{.Id}}' "$web_image_ref")"
[[ "$caddy_image_id" =~ ^sha256:[0-9a-f]{64}$ \
    && "$web_image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 2
web_image_revision="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$web_image_id")"
[[ "$web_image_revision" == "$git_revision" ]] || {
    printf 'web image revision label does not match the clean checkout\n' >&2
    exit 2
}
preflight_scratch="$(mktemp -d "${TMPDIR:-/tmp}/epaper-preflight.XXXXXX")"
remove_preflight_scratch() {
    if [[ -n "${preflight_scratch:-}" && -d "$preflight_scratch" && "$preflight_scratch" == */epaper-preflight.* ]]; then
        if rm -rf -- "$preflight_scratch"; then
            preflight_scratch=""
            return 0
        fi
        return 1
    fi
    return 0
}

preflight_cleanup() {
    status=$?
    set +e
    if ! remove_preflight_scratch; then
        printf 'preflight scratch cleanup failed\n' >&2
        status=1
    fi
    trap - EXIT
    exit "$status"
}
trap preflight_cleanup EXIT
install -d -o 10001 -g 10001 -m 0700 \
    "$preflight_scratch/publication" \
    "$preflight_scratch/worker-data" \
    "$preflight_scratch/contract-env"

publication_source="$(cd -- "$publication_dir" && pwd -P)"
security_source="$(cd -- "$security_dir" && pwd -P)"
config_source="$(cd -- "$config_dir" && pwd -P)"
provider_secrets_source="$(cd -- "$provider_secrets_dir" && pwd -P)"
session_key_source="$(cd -- "$(dirname -- "$session_key_file")" && printf '%s/%s' "$(pwd -P)" "$(basename -- "$session_key_file")")"
worker_health_source="$(cd -- "$worker_health_dir" && pwd -P)"

"${compose[@]}" --profile admin config --format json \
    | docker run --rm --network none -i \
        -v "$script_dir/compose_contract.py:/contract.py:ro" \
        -v "$preflight_scratch/contract-env:/scratch:rw" \
        "$web_image_id" python /contract.py \
        --expected-root "$deploy_root" --output-dir /scratch

docker run --rm --network none \
    --env-file "$preflight_scratch/contract-env/caddy.env" \
    -v "$deploy_root/Caddyfile:/etc/caddy/Caddyfile:ro" \
    "$caddy_image_id" caddy adapt --config /etc/caddy/Caddyfile --validate >/dev/null

docker run --rm --network none \
    --env-file "$preflight_scratch/contract-env/web.env" \
    -v "$publication_source:/app/publication:ro" \
    -v "$security_source:/app/security:ro" \
    -v "$worker_health_source:/app/worker-health:ro" \
    -v "$session_key_source:/run/session-key/web_portal_secret_key:ro" \
    "$web_image_id" python -c \
    'from web_portal.runtime import build_runtime_app; build_runtime_app()'
docker run --rm --network none \
    --env-file "$preflight_scratch/contract-env/worker.env" \
    -v "$config_source:/app/config:ro" \
    -v "$provider_secrets_source:/run/provider-secrets:ro" \
    -v "$preflight_scratch/publication:/app/publication:rw" \
    -v "$preflight_scratch/worker-data:/app/worker-data:rw" \
    "$web_image_id" python -c \
    'from web_portal.worker import build_worker; build_worker()'
docker run --rm --network none \
    -v "$config_source:/app/config:ro" \
    -v "$provider_secrets_source:/run/provider-secrets:ro" \
    "$web_image_id" python -c \
    'import json, re, stat; from pathlib import Path; document=json.loads(Path("/app/config/required_secrets.json").read_text(encoding="utf-8")); items=document.get("secrets"); assert isinstance(items, list); names=[item.get("name") for item in items if isinstance(item, dict)]; assert len(names) == len(items) and len(set(names)) == len(names); assert all(isinstance(name, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", name) for name in names); paths=[Path("/run/provider-secrets") / name for name in names]; assert all(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 65536 and path.stat().st_uid == 10001 and path.stat().st_gid == 10001 and not stat.S_IMODE(path.stat().st_mode) & 0o077 for path in paths); assert all(path.read_text(encoding="utf-8").strip() for path in paths)'

if [[ -n "$public_url" ]]; then
    public_url="$(docker run --rm --network none \
        --env-file "$preflight_scratch/contract-env/caddy.env" \
        -e "PUBLIC_BASE_URL=$public_url" \
        "$web_image_id" python -c \
        'import os, urllib.parse; raw=os.environ["PUBLIC_BASE_URL"]; expected=os.environ["WEB_PORTAL_HOSTNAME"]; parsed=urllib.parse.urlsplit(raw); assert parsed.scheme == "https" and parsed.hostname and parsed.hostname.lower() == expected.lower(); assert parsed.username is None and parsed.password is None and parsed.port in (None, 443); assert parsed.path in ("", "/") and not parsed.query and not parsed.fragment; print(f"https://{expected}")')"
    curl --fail --silent --show-error --proto '=https' --tlsv1.2 \
        --connect-timeout 10 --max-time 20 "$public_url/livez" \
        | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'
    curl --fail --silent --show-error --proto '=https' --tlsv1.2 \
        --connect-timeout 10 --max-time 20 "$public_url/readyz" \
        | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ready"'
fi

if ! remove_preflight_scratch; then
    printf 'preflight scratch cleanup failed\n' >&2
    trap - EXIT
    exit 1
fi
trap - EXIT
printf 'preflight passed without starting or replacing the production stack\n'
