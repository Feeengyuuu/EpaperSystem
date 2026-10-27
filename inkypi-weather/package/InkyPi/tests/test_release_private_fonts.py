"""Private owner font delivery: local fonts and fake SSH only, no device access."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import shlex
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tools" / "release"))

import epaper_release  # noqa: E402
import release_build  # noqa: E402
import release_private_fonts as private  # noqa: E402
from release_task import PACKAGE, ReleaseError, TaskContext  # noqa: E402

COMMIT = "a" * 40
RELEASE = "deploy-20261027T120000Z-private-" + COMMIT[:8]
ARTIFACT = "b" * 64
FONT = ROOT / PACKAGE / "src/plugins/flight_radar/assets/fonts/Inter-Bold.ttf"


@pytest.fixture
def ctx(tmp_path):
    (tmp_path / "outputs").mkdir()
    context = TaskContext.open("outputs/round", tmp_path, create=True)
    context.save("deployment.json", {"status": "passed", "release_id": RELEASE, "commit": COMMIT})
    context.save("current-manifest.json", {"status": "passed", "commit": COMMIT, "artifact_sha256": ARTIFACT,
                                          "hashes": {private.HELPER: "c" * 64, private.UPDATER: "d" * 64}})
    return context


@pytest.fixture
def font(ctx):
    path = ctx.repo / "outputs" / "owner.ttf"
    path.write_bytes(FONT.read_bytes())
    return path


@pytest.fixture
def banknote_metadata(monkeypatch):
    """Exercise the policy without redistributing any private font bytes."""
    from PIL import ImageFont

    real = ImageFont.truetype(str(FONT), 32)
    fake = SimpleNamespace(getname=lambda: ("Banknote", "Regular"), getbbox=real.getbbox, getmask=real.getmask)
    monkeypatch.setattr(ImageFont, "truetype", lambda *args, **kwargs: fake)


class FakeDevice:
    def __init__(self, *, active=RELEASE, status="installed", failure=None, mutate=None):
        self.active = active
        self.status = status
        self.failure = failure
        self.mutate = mutate
        self.calls = []

    def resolved_link(self, link):
        assert link == "/opt/inkypi/current"
        return "/opt/inkypi/releases/" + self.active

    def run(self, command, *, input, timeout):
        self.calls.append((command, input, timeout))
        if self.failure:
            return self.failure
        args = shlex.split(command)
        assert args[:4] == ["sudo", "-n", "/usr/local/sbin/inkypi-update", "--private-score-font"]
        name, digest, size, release_id, commit, artifact_sha = args[4:]
        assert hashlib.sha256(input).hexdigest() == digest
        result = {"status": self.status, "destination": "/var/lib/inkypi/data/fonts/" + name,
                  "sha256": digest, "bytes": int(size), "release_id": release_id, "commit": commit,
                  "artifact_sha256": artifact_sha, "uid": 0, "gid": 984, "mode": "0640", "font": {"family": "Inter"}}
        if self.mutate:
            result.update(self.mutate)
        return SimpleNamespace(returncode=0, stdout=json.dumps(result).encode(), stderr=b"")


def no_device():
    pytest.fail("local validation/dry run contacted a device")


def test_cli_private_fonts_is_dry_run_and_only_fixed_sports_leagues():
    parser = epaper_release.build_parser()
    args = parser.parse_args(["private-fonts", "--task-dir", "outputs/r", "--league", "nba", "--font", "owner.woff2"])
    assert args.execute is False and args.address is None
    for league in private.font_policy().LEAGUES:
        accepted = parser.parse_args(["private-fonts", "--task-dir", "outputs/r", "--league", league, "--font", "owner.woff"])
        assert accepted.league == league and accepted.execute is False
    with pytest.raises(SystemExit):
        parser.parse_args(["private-fonts", "--task-dir", "outputs/r", "--league", "nfl", "--font", "owner.ttf"])


def test_cli_price_font_uses_a_separate_mutually_exclusive_purpose():
    parser = epaper_release.build_parser()
    base = ["private-fonts", "--task-dir", "outputs/r", "--font", "owner.ttf"]
    args = parser.parse_args(base + ["--purpose", "game_deals_price"])
    assert args.purpose == "game_deals_price" and args.league is None and args.execute is False
    for options in ([], ["--purpose", "unknown"], ["--league", "game_deals_price"],
                    ["--league", "nba", "--purpose", "game_deals_price"]):
        with pytest.raises(SystemExit):
            parser.parse_args(base + options)


def test_price_font_plan_has_fixed_destination_and_separate_audit_key(ctx, font, banknote_metadata):
    result = private.install(ctx, None, font, purpose="game_deals_price", connect=no_device)
    assert result["purpose"] == "game_deals_price" and "league" not in result
    assert result["destination"] == "/var/lib/inkypi/data/fonts/game-deals-price.ttf"
    assert result["font"]["family"] == "Banknote"
    assert result["font"]["required_glyphs"] == "0123456789$.,-"
    assert ctx.load("private-fonts-game_deals_price-plan.json") == result


def test_price_font_execute_preserves_original_bytes_and_receipt_guards(ctx, font, banknote_metadata):
    device = FakeDevice()
    result = private.install(ctx, None, font, purpose="game_deals_price", execute=True, connect=lambda: device)
    assert device.calls[0][1] == font.read_bytes()
    assert "game-deals-price.ttf" in shlex.split(device.calls[0][0])
    assert result["uid"] == 0 and result["mode"] == "0640"
    assert ctx.load("private-fonts-game_deals_price.json")["attempts"] == [result]
    assert not ctx.has("private-fonts-mode.json")


@pytest.mark.parametrize("mutation", [{"sha256": "f" * 64}, {"mode": "0666"}])
def test_price_receipt_failure_keeps_purpose_audit_filename(ctx, font, banknote_metadata, mutation):
    device = FakeDevice(mutate=mutation)
    with pytest.raises(ReleaseError):
        private.install(ctx, None, font, purpose="game_deals_price", execute=True, connect=lambda: device)
    assert ctx.load("private-fonts-game_deals_price.json")["attempts"][0]["status"] == "failed_or_unconfirmed"
    assert not ctx.has("private-fonts-mode.json") and not ctx.has("private-fonts-sha256.json")


@pytest.mark.parametrize("league,purpose", [(None, None), ("nba", "game_deals_price"), (None, "unknown")])
def test_invalid_role_refuses_before_device_contact(ctx, font, league, purpose):
    with pytest.raises(ReleaseError):
        private.install(ctx, league, font, purpose=purpose, execute=True, connect=no_device)


def test_price_font_rejects_unregistered_family_and_non_ttf_source(ctx, font):
    with pytest.raises(ReleaseError, match="family and style"):
        private.install(ctx, None, font, purpose="game_deals_price", execute=True, connect=no_device)
    other = font.with_suffix(".otf")
    other.write_bytes(font.read_bytes())
    with pytest.raises(ReleaseError, match="format is not allowed"):
        private.install(ctx, None, other, purpose="game_deals_price", execute=True, connect=no_device)


def test_dry_run_validates_original_bytes_without_transport_or_copy(ctx, font):
    result = private.install(ctx, "nba", font, connect=no_device)
    assert result["status"] == "dry_run"
    assert result["destination"] == "/var/lib/inkypi/data/fonts/sports-nba-score.ttf"
    assert result["font"]["digits"] == "0123456789"
    assert result["bytes"] == font.stat().st_size
    assert sorted(item.name for item in ctx.task_dir.iterdir()) == [
        "current-manifest.json", "deployment.json", "private-fonts-nba-plan.json"]
    assert ctx.load("private-fonts-nba-plan.json")["source"] == str(font)


@pytest.mark.parametrize("league", ["mls", "epl", "laliga", "bundesliga", "seriea", "ligue1", "f1"])
def test_new_fixed_leagues_keep_exact_private_destinations(ctx, font, league):
    result = private.install(ctx, league, font, connect=no_device)
    assert result["destination"] == f"/var/lib/inkypi/data/fonts/sports-{league}-score.ttf"


@pytest.mark.parametrize("change", ["not_passed", "commit", "artifact", "capability", "release"])
def test_invalid_round_refuses_before_contact(ctx, font, change):
    deployment, manifest = ctx.load("deployment.json"), ctx.load("current-manifest.json")
    if change == "not_passed":
        deployment["status"] = "deploying"
    elif change == "commit":
        manifest["commit"] = "e" * 40
    elif change == "artifact":
        manifest.pop("artifact_sha256")
    elif change == "capability":
        manifest["hashes"].pop(private.HELPER)
    else:
        deployment["release_id"] = "../../escape"
    ctx.save("deployment.json", deployment)
    ctx.save("current-manifest.json", manifest)
    with pytest.raises(ReleaseError):
        private.install(ctx, "nba", font, execute=True, connect=no_device)


@pytest.mark.parametrize("content,suffix", [(b"not a font", ".ttf"), (b"", ".ttf"), (b"x" * (2 * 1024 * 1024 + 1), ".ttf"),
                                            (b"font", ".ttc"), (b"wOF2invalid", ".woff2")],
                         ids=["invalid", "empty", "oversize", "collection", "invalid-woff2"])
def test_invalid_fonts_refuse_before_contact(ctx, content, suffix):
    path = ctx.repo / ("owner" + suffix)
    path.write_bytes(content)
    with pytest.raises(ReleaseError):
        private.install(ctx, "nba", path, execute=True, connect=no_device)


@pytest.mark.parametrize("status", ["installed", "unchanged"])
def test_execute_streams_original_font_and_records_only_metadata(ctx, font, status):
    device = FakeDevice(status=status)
    result = private.install(ctx, "nba", font, execute=True, connect=lambda: device)
    assert result["status"] == status and result["gid"] == 984
    assert len(device.calls) == 1 and device.calls[0][1] == font.read_bytes()
    assert ctx.load("private-fonts-nba.json")["attempts"][0] == result
    assert {p.suffix for p in ctx.task_dir.iterdir()} == {".json"}


def test_changed_active_release_refuses_without_upload(ctx, font):
    device = FakeDevice(active="another-release")
    with pytest.raises(ReleaseError, match="no longer"):
        private.install(ctx, "nba", font, execute=True, connect=lambda: device)
    assert not device.calls
    assert ctx.load("private-fonts-nba.json")["attempts"][0]["status"] == "failed_or_unconfirmed"


def test_remote_refusal_preserves_receipt_and_does_not_retry(ctx, font):
    failure = SimpleNamespace(returncode=2, stdout=b"", stderr=b'{"reason":"existing owner font differs; replacement is forbidden"}')
    device = FakeDevice(failure=failure)
    with pytest.raises(ReleaseError, match="replacement is forbidden"):
        private.install(ctx, "nba", font, execute=True, connect=lambda: device)
    assert len(device.calls) == 1
    assert ctx.load("private-fonts-nba.json")["attempts"][0]["exit_code"] == 2


def test_transport_failure_does_not_persist_stderr_secrets(ctx, font):
    device = FakeDevice(failure=SimpleNamespace(returncode=255, stdout=b"", stderr=b"password=private-value"))
    with pytest.raises(ReleaseError):
        private.install(ctx, "nba", font, execute=True, connect=lambda: device)
    assert "private-value" not in ctx.path("private-fonts-nba.json").read_text()


@pytest.mark.parametrize("mutation", [{"sha256": "f" * 64}, {"uid": 999}, {"mode": "0666"}, {"commit": "e" * 40}])
def test_untrusted_receipt_is_not_reported_as_success(ctx, font, mutation):
    device = FakeDevice(mutate=mutation)
    with pytest.raises(ReleaseError):
        private.install(ctx, "nba", font, execute=True, connect=lambda: device)
    assert ctx.load("private-fonts-nba.json")["attempts"][0]["status"] == "failed_or_unconfirmed"


def test_retry_keeps_prior_attempt_record(ctx, font):
    private.install(ctx, "nba", font, execute=True, connect=lambda: FakeDevice())
    private.install(ctx, "nba", font, execute=True, connect=lambda: FakeDevice(status="unchanged"))
    assert [item["status"] for item in ctx.load("private-fonts-nba.json")["attempts"]] == ["installed", "unchanged"]


def test_private_font_basenames_are_excluded_by_both_archive_builders(tmp_path):
    policy = release_build.load_archive_policy(ROOT)
    source = tmp_path / "source"
    source.mkdir()
    names = sorted(private.font_policy().FONT_NAMES) + ["Banknote Regular.ttf"]
    for name in names:
        (source / name).write_bytes(b"private")
        assert not release_build.is_packaged(policy, PurePosixPath("misplaced/assets/" + name))
    (source / "BarlowCondensed-Bold.ttf").write_bytes(b"public")
    target = tmp_path / "source.zip"
    policy.build_release_archive(source, target)
    with zipfile.ZipFile(target) as archive:
        assert archive.namelist() == ["BarlowCondensed-Bold.ttf"]
    # A clean-archive checkout has no .git; isolate ignore rules from its parent.
    ignore_repo = tmp_path / "ignore-repo"
    ignore_repo.mkdir()
    (ignore_repo / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
    subprocess.run(["git", "-C", str(ignore_repo), "init", "-q"], check=True, capture_output=True)
    for name in names:
        result = subprocess.run(["git", "-C", str(ignore_repo), "check-ignore", "--no-index", "--", name], capture_output=True)
        assert result.returncode == 0
