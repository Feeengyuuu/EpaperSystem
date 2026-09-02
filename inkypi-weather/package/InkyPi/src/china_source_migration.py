"""Prepare an exact offline config candidate; never mutate the running device."""
from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path
from uuid import UUID


MIGRATION = "china_box_office_official_v1"


def prepare_migration(document, *, instance_uuid, generation, revision):
    UUID(instance_uuid)
    if type(generation) is not int or type(revision) is not int or min(generation, revision) < 1:
        raise ValueError("Expected revisions must be positive integers")
    candidate = deepcopy(document)
    matches = [item for playlist in candidate["playlist_config"]["playlists"]
               for item in playlist["plugins"] if item.get("instance_uuid") == instance_uuid]
    if len(matches) != 1:
        raise ValueError("Expected exactly one target UUID")
    target = matches[0]
    if target.get("plugin_id") != "box_office_top_movies":
        raise ValueError("Target is not the mainland box office plugin")
    marker = {"instance_uuid": instance_uuid, "structural_generation": generation,
              "from_revision": revision, "to_revision": revision + 1}
    markers = candidate.setdefault("runtime_migrations", {})
    if target.get("structural_generation") != generation:
        raise ValueError("Target generation changed")
    if markers.get(MIGRATION) == marker:
        if target.get("settings_revision") == revision + 1 and target["plugin_settings"].get("sourceMode") == "official_china":
            return candidate
        raise ValueError("Migrated target changed")
    if MIGRATION in markers or target.get("settings_revision") != revision:
        raise ValueError("Target revision changed")
    if target["plugin_settings"].get("sourceMode") not in {"maoyan", "maoyan_china", "china", "china_mainland", "mainland_china"}:
        raise ValueError("Target no longer uses the expected mainland source")
    config_revision = candidate.get("config_revision", 0)
    if type(config_revision) is not int or config_revision < 0:
        raise ValueError("Invalid config revision")
    target["plugin_settings"]["sourceMode"] = "official_china"
    target["settings_revision"] = revision + 1
    candidate["config_revision"] = config_revision + 1
    markers[MIGRATION] = marker
    return candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--instance-uuid", required=True)
    parser.add_argument("--generation", type=int, required=True)
    parser.add_argument("--revision", type=int, required=True)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("Output must be a separate reviewable candidate file")
    payload = args.input.read_bytes()
    if len(payload) > 2 * 1024 * 1024 or hashlib.sha256(payload).hexdigest() != args.expected_sha256:
        parser.error("Input size or SHA-256 does not match the captured config")
    candidate = prepare_migration(json.loads(payload), instance_uuid=args.instance_uuid,
                                  generation=args.generation, revision=args.revision)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(candidate, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
