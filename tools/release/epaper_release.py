#!/usr/bin/env python3
"""One deployment round for the e-paper frame, with all evidence in outputs/<task>.

A round runs these steps in order; each one refuses when the evidence it builds
on is missing, and the steps before deploy refuse a task folder that already
holds deployment.json:

  tools/release/run_test_shards.ps1 -TaskDir outputs/<task>
  baseline       prove the live release against its round's manifest; record preflight + predeploy health
  build          package committed HEAD and prove its change set against the live release
  backup         re-package the live release as the next .backups/previous candidate
  deploy         16 MiB verified chunks + transactional inkypi-update (dry run unless --execute)
  private-fonts  install one registered owner font after a passed deploy (dry run unless --execute)
  health-gate    record postdeploy health and compare it with predeploy
  rebaseline-config  only when the release itself migrates saved settings: accept that
                 recorded migration as the acceptance configuration (reason required;
                 --added-plugins when it only adds one instance per named plugin,
                 --removed-plugins when it only removes every instance of named plugins)
  show-plugins   refresh and display named plugins once (LAN display control)
  snapshot       acceptance snapshot with configuration and source checks
  journal        summarize the service journal since the deployment
  rotate-backup  replace .backups/previous after display proof (dry run unless --execute)

Relative paths are taken from the repository root. Exit codes: 0 done,
1 a gate reported failure, 2 the step refused (nothing after the refusal ran).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True

from release_task import REPO_ROOT, ReleaseError, TaskContext  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    from release_private_fonts import font_policy

    parser = argparse.ArgumentParser(prog="epaper_release.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    def command(name: str, summary: str, device: bool = True) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=summary, description=summary)
        sub.add_argument("--task-dir", required=True,
                         help="evidence folder; must be a direct child of outputs/")
        if device:
            sub.add_argument("--address", help="device IPv4 for this run; default resolves ColoredEpaperFrame.local")
        return sub

    sub = command("baseline", "Prove the live release and record preflight.json and health-predeploy.json.")
    sub.add_argument("--base-manifest", type=Path,
                     help="current-manifest.json of the live release; default: the round whose deployment.json "
                          "deployed it")
    sub = command("build", "Package committed HEAD into the task folder.", device=False)
    sub.add_argument("--expect-changes", type=Path,
                     help="optional whitespace-separated list of package paths the release must change exactly")
    sub.add_argument("--expect-dependency-changes", type=Path,
                     help="reviewed 'name before after' lines ('-' for absent) the runtime lock must change "
                          "exactly; without it a changed lock is refused")
    command("backup", "Prepare the live release's archive for .backups/previous.", device=False)
    sub = command("deploy", "Upload in verified chunks and run the transactional updater.")
    sub.add_argument("--release-label", required=True,
                     help="short release name, e.g. layout -> deploy-<UTC>-layout-<commit8>")
    sub.add_argument("--execute", action="store_true", help="contact and change the device (default: dry run)")
    sub = command("private-fonts", "Install one device-only registered font into the deployed release's durable data.")
    role = sub.add_mutually_exclusive_group(required=True)
    role.add_argument("--league", choices=font_policy().LEAGUES)
    role.add_argument("--purpose", choices=tuple(font_policy().PURPOSES))
    sub.add_argument("--font", type=Path, required=True, help="owner-provided local raw TTF, OTF, WOFF or WOFF2; never packaged")
    sub.add_argument("--execute", action="store_true", help="install through the trusted updater (default: dry run)")
    sub = command("health-gate", "Record health after deployment and compare it with the predeploy record.")
    sub.add_argument("--before", default="predeploy")
    sub.add_argument("--after", default="postdeploy")
    sub.add_argument("--allow-not-ready", action="store_true", help="pass even if /readyz is not ready")
    sub = command("health-record", "Record health-<label>.json (read-only).")
    sub.add_argument("--label", required=True)
    sub = command("rebaseline-config",
                  "Accept the deployed release's own recorded settings migration as the acceptance configuration.")
    sub.add_argument("--reason", required=True, help="why this release changes the saved configuration")
    sub.add_argument("--migration", required=True, help="runtime_migrations key the release records")
    sub.add_argument("--added-plugins", nargs="+", default=[], metavar="plugin_id",
                     help="plugins the migration adds one new instance of; every other instance must be unchanged")
    sub.add_argument("--removed-plugins", nargs="+", default=[], metavar="plugin_id",
                     help="plugins whose every instance the migration removes; every other instance must be unchanged")
    sub = command("show-plugins", "Refresh and display each named plugin once and snapshot the hardware write.")
    sub.add_argument("plugins", nargs="+", metavar="plugin_id")
    sub = command("snapshot", "Save an acceptance snapshot with configuration and source checks.")
    sub.add_argument("--label", default="postdeploy")
    command("journal", "Summarize the service journal since the deployment completed.")
    sub = command("watch", "Wait for natural scheduler hardware commits on the deployed release.")
    sub.add_argument("--timeout", type=float, default=1050)
    sub.add_argument("--interval", type=float, default=25)
    sub.add_argument("--count", type=int, default=2)
    sub = command("probe", "Save the latest render of every instance of the named plugins (read-only).")
    sub.add_argument("--label", required=True)
    sub.add_argument("plugins", nargs="+", metavar="plugin_id")
    sub = command("rotate-backup", "Replace .backups/previous with the prepared baseline archive.", device=False)
    sub.add_argument("--acceptance-plugin", default="apod", help="plugin whose shown snapshot proves the display")
    sub.add_argument("--acceptance-label", help="snapshot label; default <plugin>-shown")
    sub.add_argument("--execute", action="store_true", help="rotate (default: dry run)")
    return parser


def repo_path(ctx: TaskContext, value: Path | None) -> Path | None:
    if value is None:
        return None
    return value if value.is_absolute() else ctx.repo / value


def run(args: argparse.Namespace, ctx: TaskContext) -> tuple[object, int]:
    import release_backup
    import release_build
    import release_checks
    import release_deploy
    import release_private_fonts
    from release_device import Device

    def device() -> Device:
        return Device.connect(ctx.repo, args.address)

    if args.command == "baseline":
        connected = device()
        record = release_build.baseline(ctx, connected, repo_path(ctx, args.base_manifest))
        health = release_checks.record_health(ctx, connected, "predeploy")
        return {**record, "health_predeploy": health}, 0
    if args.command == "build":
        expected = repo_path(ctx, args.expect_changes)
        reviewed = repo_path(ctx, args.expect_dependency_changes)
        return release_build.build(
            ctx, release_build.read_expected_changes(expected) if expected else None,
            release_build.read_expected_dependency_changes(reviewed) if reviewed else None), 0
    if args.command == "backup":
        return release_build.backup(ctx), 0
    if args.command == "deploy":
        return release_deploy.deploy(ctx, args.release_label, execute=args.execute, connect=device), 0
    if args.command == "private-fonts":
        return release_private_fonts.install(ctx, args.league, args.font, purpose=args.purpose, execute=args.execute, connect=device), 0
    if args.command == "health-gate":
        ctx.require_deployed()
        release_checks.record_health(ctx, device(), args.after)
        verdict = release_checks.health_gate(ctx, args.before, args.after, not args.allow_not_ready)
        return verdict, 0 if verdict["passed"] else 1
    if args.command == "health-record":
        return release_checks.record_health(ctx, device(), args.label), 0
    if args.command == "rebaseline-config":
        return release_checks.rebaseline_configuration(ctx, device(), args.reason, args.migration,
                                                       args.added_plugins, args.removed_plugins), 0
    if args.command == "show-plugins":
        return release_checks.show_plugins(ctx, device(), args.plugins), 0
    if args.command == "snapshot":
        ctx.require_deployed()
        record = release_checks.acceptance_snapshot(ctx, device(), args.label)
        return {key: record[key] for key in ("active_release", "previous_release", "releases", "healthz", "readyz",
                                             "source", "configuration", "image", "service")}, 0
    if args.command == "journal":
        return release_checks.journal_check(ctx, device()), 0
    if args.command == "watch":
        return release_checks.watch(ctx, device(), args.timeout, args.interval, args.count), 0
    if args.command == "probe":
        return release_checks.probe_renders(ctx, device(), args.label, args.plugins), 0
    if args.command == "rotate-backup":
        return release_backup.rotate(ctx, args.acceptance_plugin, args.acceptance_label, execute=args.execute), 0
    raise ReleaseError(f"Unknown command: {args.command}")


def main(argv: list[str] | None = None, repo: Path = REPO_ROOT) -> int:
    args = build_parser().parse_args(argv)
    try:
        ctx = TaskContext.open(args.task_dir, repo, create=args.command == "baseline")
        ctx.isolate_temp()
        result, code = run(args, ctx)
    except ReleaseError as error:
        print("refused: " + str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
