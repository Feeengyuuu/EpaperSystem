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
    printf 'run this script as root; decrypted state must remain inside a root-only temporary directory\n' >&2
    exit 2
fi

for command_name in age awk cmp docker find git grep id install sed sha256sum sort sqlite3 stat tar tail tr; do
    require_command "$command_name"
done

expected_compose_version="5.3.1"
compose_version="$(docker compose version --short | tr -d '\r')"
compose_version="${compose_version#v}"
[[ "$compose_version" == "$expected_compose_version" ]] || {
    printf 'Docker Compose %s is required; found %s\n' "$expected_compose_version" "${compose_version:-unknown}" >&2
    exit 2
}

[[ $# -ge 2 && $# -le 3 ]] || {
    printf 'usage: %s BACKUP_DIRECTORY AGE_IDENTITY_FILE [RESTORE_PARENT]\n' "$0" >&2
    exit 2
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
deploy_root="$(cd -- "$script_dir/.." && pwd -P)"
repo_root="$(cd -- "$deploy_root/../.." && pwd -P)"
backup_dir="$(cd -- "$1" && pwd -P)"
identity_file="$2"
restore_parent="${3:-/opt/epapersystem-web}"
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

require_private_directory() {
    path="$1"
    label="$2"
    [[ -d "$path" && ! -L "$path" ]] || {
        printf '%s is missing or link-like\n' "$label" >&2
        exit 2
    }
    owner_group="$(stat -c '%u:%g' "$path")"
    mode="$(stat -c '%a' "$path")"
    [[ "$owner_group" == "$expected_owner:$expected_group" ]] || {
        printf '%s has an unexpected owner\n' "$label" >&2
        exit 2
    }
    (( (8#$mode & 0077) == 0 )) || {
        printf '%s must not be accessible by group or other users\n' "$label" >&2
        exit 2
    }
}

require_private_file() {
    path="$1"
    label="$2"
    [[ -f "$path" && ! -L "$path" ]] || {
        printf '%s is missing or link-like\n' "$label" >&2
        exit 2
    }
    owner_group="$(stat -c '%u:%g' "$path")"
    mode="$(stat -c '%a' "$path")"
    [[ "$owner_group" == "$expected_owner:$expected_group" ]] || {
        printf '%s has an unexpected owner\n' "$label" >&2
        exit 2
    }
    (( (8#$mode & 0077) == 0 )) || {
        printf '%s must not be accessible by group or other users\n' "$label" >&2
        exit 2
    }
}

require_private_directory "$backup_dir" "backup directory"
require_private_file "$identity_file" "age identity"
for file_name in state.tar.gz.age state.tar.gz.age.sha256 release.env; do
    require_private_file "$backup_dir/$file_name" "backup member $file_name"
done

[[ ! -L "$restore_parent" && ( ! -e "$restore_parent" || -d "$restore_parent" ) ]] || {
    printf 'restore parent is not a safe directory\n' >&2
    exit 2
}
if [[ ! -e "$restore_parent" ]]; then
    install -d -o "$expected_owner" -g "$expected_group" -m 0700 -- "$restore_parent"
fi
require_private_directory "$restore_parent" "restore parent"
restore_root="$(mktemp -d "$restore_parent/epaper-restore.XXXXXX")"
chmod 0700 "$restore_root"

remove_restore_root() {
    set +e
    if [[ -n "${restore_root:-}" && -d "$restore_root" && "$restore_root" == "$restore_parent"/epaper-restore.* ]]; then
        if rm -rf -- "$restore_root"; then
            restore_root=""
            return 0
        fi
        return 1
    fi
    return 0
}

restore_cleanup() {
    status=$?
    set +e
    if ! remove_restore_root; then
        printf 'restore cleanup failed; decrypted state may remain in the restore parent\n' >&2
        status=1
    fi
    trap - EXIT
    exit "$status"
}
trap restore_cleanup EXIT

for file_name in state.tar.gz.age state.tar.gz.age.sha256 release.env; do
    install -o "$expected_owner" -g "$expected_group" -m 0600 -- \
        "$backup_dir/$file_name" "$restore_root/$file_name"
done
install -o "$expected_owner" -g "$expected_group" -m 0600 -- \
    "$identity_file" "$restore_root/age-identity.txt"

checksum_line="$(tr -d '\r' < "$restore_root/state.tar.gz.age.sha256")"
if [[ ! "$checksum_line" =~ ^([0-9a-f]{64})[[:space:]][[:space:]]state\.tar\.gz\.age$ ]]; then
    printf 'checksum file has an invalid format\n' >&2
    exit 2
fi
(
    cd -- "$restore_root"
    printf '%s\n' "$checksum_line" | sha256sum -c -
)

clear_archive="$restore_root/state.tar.gz"
age --decrypt -i "$restore_root/age-identity.txt" \
    -o "$clear_archive" "$restore_root/state.tar.gz.age"
chmod 0600 "$clear_archive"
archive_list="$restore_root/archive.list"
archive_verbose="$restore_root/archive.verbose"
tar -tzf "$clear_archive" > "$archive_list"
tar -tvzf "$clear_archive" > "$archive_verbose"
if awk '
    { type = substr($1, 1, 1) }
    type != "-" && type != "d" { invalid = 1 }
    END { exit invalid ? 0 : 1 }
' "$archive_verbose"; then
    printf 'archive contains a non-regular entry\n' >&2
    exit 2
fi
while IFS= read -r archive_path; do
    normalized="${archive_path#./}"
    [[ -n "$normalized" && "$normalized" != /* ]] || {
        printf 'archive contains an absolute or empty path\n' >&2
        exit 2
    }
    case "/$normalized/" in
        */../*|*/./*)
            printf 'archive contains a non-canonical path\n' >&2
            exit 2
            ;;
    esac
    case "$normalized" in
        .env|release.env|config|config/*|publication|publication/*|worker-data|worker-data/*|security|security/*|provider-secrets|provider-secrets/*|session-key|session-key/*)
            ;;
        *)
            printf 'archive contains an unexpected path\n' >&2
            exit 2
            ;;
    esac
done < "$archive_list"

mkdir -- "$restore_root/state"
tar --same-owner --no-same-permissions -xzf "$clear_archive" -C "$restore_root/state"
state="$restore_root/state"
if find "$state" ! -type f ! -type d -print -quit | grep -q .; then
    printf 'restored state contains a non-regular entry\n' >&2
    exit 2
fi
[[ -f "$state/.env" \
    && -f "$state/release.env" \
    && -f "$state/publication/publication.sqlite3" \
    && -f "$state/config/web_config.json" \
    && -f "$state/config/required_secrets.json" \
    && -f "$state/security/admin_credentials.json" \
    && -f "$state/session-key/web_portal_secret_key" ]] || {
    printf 'restored state is incomplete\n' >&2
    exit 2
}
[[ "$(stat -c '%u:%g' "$state/security")" == "$application_owner:$application_group" \
    && "$(stat -c '%a' "$state/security")" == "700" ]] || {
    printf 'restored security directory ownership or mode is invalid\n' >&2
    exit 2
}
[[ "$(stat -c '%u:%g' "$state/security/admin_credentials.json")" == "$application_owner:$application_group" \
    && "$(stat -c '%a' "$state/security/admin_credentials.json")" == "600" ]] || {
    printf 'restored admin credentials ownership or mode is invalid\n' >&2
    exit 2
}
cmp --silent "$state/release.env" "$restore_root/release.env" || {
    printf 'encrypted and external release metadata differ\n' >&2
    exit 2
}
sqlite3 "$state/publication/publication.sqlite3" 'PRAGMA integrity_check;' | grep -qx ok

metadata_value() {
    key="$1"
    sed -n "s/^${key}=//p" "$state/release.env" | tail -n 1
}
git_revision="$(metadata_value GIT_REVISION)"
web_image_id="$(metadata_value WEB_IMAGE_ID)"
caddy_image_id="$(metadata_value CADDY_IMAGE_ID)"
[[ "$git_revision" =~ ^[0-9a-f]{40,64}$ ]] || exit 2
[[ "$web_image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 2
[[ "$caddy_image_id" =~ ^sha256:[0-9a-f]{64}$ ]] || exit 2
git -C "$repo_root" cat-file -e "$git_revision^{commit}"
release_root="$restore_root/release-source"
mkdir -- "$release_root"
git -C "$repo_root" archive "$git_revision" \
    deploy/web/compose.yaml deploy/web/Caddyfile deploy/web/Dockerfile \
    deploy/web/ops/compose_contract.py \
    | tar -xf - -C "$release_root"
release_deploy="$release_root/deploy/web"
docker image inspect "$web_image_id" "$caddy_image_id" >/dev/null
web_image_revision="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$web_image_id")"
[[ "$web_image_revision" == "$git_revision" ]] || {
    printf 'recorded web image does not match the recorded Git revision\n' >&2
    exit 2
}
caddy_image_ref="$(awk '$1 == "image:" && $2 ~ /^caddy:2-alpine@sha256:[0-9a-f]{64}$/ { print $2; exit }' "$release_deploy/compose.yaml")"
[[ -n "$caddy_image_ref" ]] || exit 2
expected_caddy_image_id="$(docker image inspect --format '{{.Id}}' "$caddy_image_ref")"
[[ "$caddy_image_id" == "$expected_caddy_image_id" ]] || {
    printf 'recorded caddy image does not match the recorded pinned release image\n' >&2
    exit 2
}
for variable_name in ${!WEB_PORTAL_@}; do
    unset "$variable_name"
done
for variable_name in ${!COMPOSE_@}; do
    unset "$variable_name"
done
unset WEB_EDITION_RETENTION_PER_INSTANCE WEB_CATALOG_RETENTION WEB_WORKER_DEADLINE_SECONDS
restore_compose=(docker compose --project-name epapersystem-web --env-file "$state/.env" -f "$release_deploy/compose.yaml")
WEB_PORTAL_GIT_REVISION="$git_revision" "${restore_compose[@]}" --profile admin config --quiet
configured_images="$(WEB_PORTAL_GIT_REVISION="$git_revision" "${restore_compose[@]}" --profile admin config --images)"
grep -Fxq "$caddy_image_ref" <<< "$configured_images"
configured_web_image_ref="$(printf '%s\n' "$configured_images" | grep -Fvx "$caddy_image_ref" | sort -u)"
[[ -n "$configured_web_image_ref" && "$configured_web_image_ref" != *$'\n'* ]] || exit 2
[[ "$configured_web_image_ref" == *":$git_revision" ]] || {
    printf 'restored Compose web image is not tagged with the recorded Git revision\n' >&2
    exit 2
}
configured_web_image_id="$(docker image inspect --format '{{.Id}}' "$configured_web_image_ref")"
[[ "$configured_web_image_id" == "$web_image_id" ]] || {
    printf 'restored Compose web image reference does not resolve to the recorded image\n' >&2
    exit 2
}

contract_env="$restore_root/contract-env"
install -d -o "$application_owner" -g "$application_group" -m 0700 "$contract_env"
WEB_PORTAL_GIT_REVISION="$git_revision" "${restore_compose[@]}" --profile admin config --format json \
    | docker run --rm --network none -i \
        -v "$release_deploy/ops/compose_contract.py:/contract.py:ro" \
        -v "$contract_env:/scratch:rw" \
        "$web_image_id" python /contract.py \
        --expected-root "$release_deploy" --output-dir /scratch
docker run --rm --network none \
    --env-file "$contract_env/caddy.env" \
    -v "$release_deploy/Caddyfile:/etc/caddy/Caddyfile:ro" \
    "$caddy_image_id" caddy adapt --config /etc/caddy/Caddyfile --validate >/dev/null

docker run --rm --network none \
    --env-file "$contract_env/web.env" \
    -e WEB_PORTAL_WORKER_STATE_FILE= \
    -v "$state/publication:/app/publication:ro" \
    -v "$state/security:/app/security:ro" \
    -v "$state/session-key/web_portal_secret_key:/run/session-key/web_portal_secret_key:ro" \
    "$web_image_id" python -c \
    'from web_portal.runtime import build_runtime_app; app=build_runtime_app(); credentials=app.extensions["web_portal_credentials"]; health=app.extensions["web_portal_health"].inspect(); raise SystemExit(0 if credentials.has_admin() and credentials.admin_session_revision() and health.status == "ready" else 1)'

docker run --rm --network none \
    -v "$state/config:/app/config:ro" \
    -v "$state/provider-secrets:/run/provider-secrets:ro" \
    "$web_image_id" python -c \
    'import json, re, stat; from pathlib import Path; document=json.loads(Path("/app/config/required_secrets.json").read_text(encoding="utf-8")); items=document.get("secrets"); assert isinstance(items, list); names=[item.get("name") for item in items if isinstance(item, dict)]; assert len(names) == len(items) and len(set(names)) == len(names); assert all(isinstance(name, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", name) for name in names); paths=[Path("/run/provider-secrets") / name for name in names]; assert all(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 65536 and path.stat().st_uid == 10001 and path.stat().st_gid == 10001 and not stat.S_IMODE(path.stat().st_mode) & 0o077 for path in paths); assert all(path.read_text(encoding="utf-8").strip() for path in paths)'

worker_scratch="$restore_root/worker-scratch"
install -d -o "$application_owner" -g "$application_group" -m 0700 \
    "$worker_scratch/publication" "$worker_scratch/data"
docker run --rm --network none \
    --env-file "$contract_env/worker.env" \
    -v "$state/config:/app/config:ro" \
    -v "$state/provider-secrets:/run/provider-secrets:ro" \
    -v "$worker_scratch/publication:/app/publication:rw" \
    -v "$worker_scratch/data:/app/worker-data:rw" \
    "$web_image_id" python -c \
    'from web_portal.worker import build_worker; build_worker()'

if ! remove_restore_root; then
    printf 'restore cleanup failed; decrypted state may remain in the restore parent\n' >&2
    trap - EXIT
    exit 1
fi
trap - EXIT
printf 'restore drill passed; decrypted state was removed without touching live services\n'
