import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.github import github_stars  # noqa: E402
from plugins.github.github import GitHub  # noqa: E402

REPO = {
    "full_name": "octo/hello", "description": "A  small\n repository", "language": "Python",
    "license": {"spdx_id": "MIT"}, "stargazers_count": 1234, "forks_count": 56, "subscribers_count": 7,
    "open_issues_count": 8, "pushed_at": "2026-11-02T21:55:28Z", "default_branch": "main",
}
COMMITS = [{"sha": "0d8ecb87abcdef", "author": {"login": "octocat"},
            "commit": {"message": "Raise the cards as frosted glass\n\nBody text", "author": {
                "name": "Octo Cat", "date": "2026-11-02T21:25:48Z"}}}]


class Response:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class Session:
    def __init__(self, repo=REPO, commits=COMMITS, fail=False):
        self.repo, self.commits, self.fail, self.calls = repo, commits, fail, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.fail:
            raise RuntimeError("offline")
        return Response(self.commits if url.endswith("/commits") else self.repo)


class Device:
    def __init__(self, token=""):
        self.token = token

    def get_resolution(self):
        return (800, 480)

    def get_config(self, key=None, default=None):
        return {"orientation": "horizontal"}.get(key, default)

    def load_env_key(self, name):
        return self.token if name == "GITHUB_SECRET" else ""


def test_repository_figures_and_latest_commit_are_read_from_their_responses(monkeypatch):
    session = Session()
    monkeypatch.setattr(github_stars, "get_http_session", lambda: session)

    data = github_stars.fetch_repository("octo/hello", token="secret")

    assert data["repo"] == {"full_name": "octo/hello", "description": "A small repository", "language": "Python",
                            "licence": "MIT", "stars": 1234, "forks": 56, "watchers": 7, "open_issues": 8,
                            "pushed_at": "2026-11-02T21:55:28Z", "default_branch": "main"}
    assert data["commit"] == {"sha": "0d8ecb8", "subject": "Raise the cards as frosted glass", "author": "Octo Cat",
                              "date": "2026-11-02T21:25:48Z"}
    assert session.calls[1][1]["params"] == {"per_page": 1}
    assert all(kwargs["headers"]["Authorization"] == "Bearer secret" for _url, kwargs in session.calls)


def test_the_latest_commit_is_fetched_again_only_after_a_push(monkeypatch):
    session = Session()
    monkeypatch.setattr(github_stars, "get_http_session", lambda: session)
    cached = github_stars.fetch_repository("octo/hello")
    session.calls.clear()

    again = github_stars.fetch_repository("octo/hello", cached=cached)
    assert [url for url, _kwargs in session.calls] == ["https://api.github.com/repos/octo/hello"]
    assert again["commit"] == cached["commit"]

    session.repo = {**REPO, "pushed_at": "2026-11-03T01:00:00Z"}
    github_stars.fetch_repository("octo/hello", cached=cached)
    assert session.calls[-1][0].endswith("/commits")


def test_a_missing_commit_leaves_the_figures(monkeypatch):
    session = Session(commits=[])
    monkeypatch.setattr(github_stars, "get_http_session", lambda: session)

    data = github_stars.fetch_repository("octo/hello")

    assert data["commit"] is None and data["repo"]["stars"] == 1234


def test_ago_reads_minutes_hours_and_days():
    now = datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc)
    assert github_stars.ago("2026-11-02T23:30:00Z", now) == "30 分钟前"
    assert github_stars.ago("2026-11-02T21:55:28Z", now) == "2 小时前"
    assert github_stars.ago("2026-10-30T00:00:00Z", now) == "4 天前"
    assert github_stars.ago("", now) == ""


@pytest.mark.parametrize("mode", ["day", "night"])
def test_stars_page_renders_the_repository_with_pillow(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(github_stars, "get_http_session", lambda: Session())
    plugin = GitHub({"id": "github"})
    monkeypatch.setattr(plugin, "render_image", lambda *args, **kwargs: pytest.fail("no browser render"))

    image = plugin.generate_image({"githubType": "stars", "githubUsername": "octo", "githubRepository": "hello",
                                   "_inkypi_theme": {"mode": mode}}, Device())

    assert isinstance(image, Image.Image) and image.size == (800, 480)
    background = image.getpixel((5, 5))
    assert background == (github_stars.NIGHT if mode == "night" else github_stars.DAY)["paper"]
    assert len(image.getcolors(maxcolors=1_000_000)) > 50
    assert (tmp_path / "plugins" / "github" / "cache" / "stars.json").is_file()


def test_stars_page_shows_the_last_reading_when_github_is_down(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path))
    settings = {"githubType": "stars", "githubUsername": "octo", "githubRepository": "hello"}
    plugin = GitHub({"id": "github"})
    monkeypatch.setattr(github_stars, "get_http_session", lambda: Session())
    plugin.generate_image(settings, Device())
    monkeypatch.setattr(github_stars, "get_http_session", lambda: Session(fail=True))

    assert plugin.generate_image(settings, Device()).size == (800, 480)
    with pytest.raises(RuntimeError):
        plugin.generate_image({**settings, "githubRepository": "other"}, Device())
